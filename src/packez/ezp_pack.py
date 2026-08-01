# src/packez/ezp_pack.py
"""
EZP (BIN) 数据封包：解包与重建。

参考实现：dearlystars_tool (Rust) ez.rs (extract_bin / rebuild_bin)

关键行为：
    - 目录用 _BEGIN/_START (开始) 与 _END (结束) 标记，二者 entry 的 decompressed_size 必须为 0
    - do_not_compress 列表：.S14/.SSS/.NFTR/.BIN/.IDX 及特定前缀的 .NSBCA 永不压缩
    - padding_alignment：.NCLR/.NSCR/.NCER 对齐 0x10，其他对齐 0x20
    - 压缩后体积 < 解压体积才使用压缩数据，并设置 0x10000000 标志位
    - names_buffer 中每个名称后填充 0 到 (len+1) 的下一个 4 字节倍数
      （即至少 1 字节 null terminator，再对齐到 4 字节）
    - IDX 末尾写入 1 个额外 entry，file_offset = names_offset，用于定位 names_buffer
"""
from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Sequence

from ..utils.lz10 import lz10_compress, lz10_decompress
from .ezt_parser import (
    EZP_HEADER_SIZE,
    EzTableEntry,
    EzpHeader,
    build_idx,
    read_idx,
)


# ----------------------------------------------------------------------
# 排除压缩 / 对齐规则
# ----------------------------------------------------------------------

_NSBCA_NO_COMPRESS_PREFIXES = ("AI_", "ERI_", "RYO_", "DNC_", "ACT_")


def do_not_compress(name: str) -> bool:
    """
    判断该文件名是否应排除压缩（与 Rust do_not_compress 一致）。

    排除列表：
        - 后缀 .S14 / .SSS / .NFTR / .BIN / .IDX
        - 后缀 .NSBCA 且前缀为 AI_ / ERI_ / RYO_ / DNC_ / ACT_
    """
    if (
        name.endswith(".S14")
        or name.endswith(".SSS")
        or name.endswith(".NFTR")
        or name.endswith(".BIN")
        or name.endswith(".IDX")
    ):
        return True
    if name.endswith(".NSBCA") and name.startswith(_NSBCA_NO_COMPRESS_PREFIXES):
        return True
    return False


def padding_alignment(name: str) -> int:
    """
    返回该文件名在 EZP 中应使用的对齐字节数（与 Rust padding_alignment 一致）。

        - .NCLR / .NSCR / .NCER: 0x10
        - 其他:                   0x20
    """
    if name.endswith(".NCLR") or name.endswith(".NSCR") or name.endswith(".NCER"):
        return 0x10
    return 0x20


# ----------------------------------------------------------------------
# 缓冲区辅助
# ----------------------------------------------------------------------

def pad_to_alignment(buf: bytearray, alignment: int, pad_byte: int = 0) -> int:
    """
    将 buf 用 pad_byte 填充到 alignment 的下一个倍数，返回对齐后的偏移。

    与 Rust util::pad_to_alignment 一致：当 len 已是 alignment 倍数时不填充。
    """
    current = len(buf)
    # next_multiple_of(alignment)
    padded = ((current + alignment - 1) // alignment) * alignment
    if padded > current:
        buf.extend(bytes([pad_byte]) * (padded - current))
    return padded


def _next_multiple_of(n: int, m: int) -> int:
    """返回 >= n 的最小 m 倍数（等价于 Rust usize::next_multiple_of）。"""
    return ((n + m - 1) // m) * m


def _indent(count: int) -> str:
    """生成 4*count 个空格（与 Rust indent 一致）。"""
    return " " * (4 * count)


def _read_cstring(buf: bytes | bytearray | memoryview, offset: int) -> str:
    """
    从 buf[offset] 读取 0 结尾字符串并 decode 为 UTF-8。

    兼容 bytes / bytearray / memoryview：memoryview 无 .find() 方法，
    故采用手动线性扫描。名称通常很短，扫描成本可忽略；
    同时避免对整个 names_buffer 做一次 bytes() 拷贝。
    """
    # L2 修复：越界时报错而非返回空串
    if offset < 0 or offset >= len(buf):
        raise ValueError(f"_read_cstring 越界：offset={offset}, len(buf)={len(buf)}")
    end = offset
    n = len(buf)
    while end < n and buf[end] != 0:
        end += 1
    # memoryview 切片仍是 memoryview，decode 需 bytes；名称很短，拷贝可忽略
    return bytes(buf[offset:end]).decode("utf-8")


# ----------------------------------------------------------------------
# 目录标记处理
# ----------------------------------------------------------------------

def _strip_dir_marker(name: str) -> tuple[str | None, str | None]:
    """
    解析目录标记。

    Returns:
        (kind, dir_name)
        kind = "begin" / "end" / None
        dir_name = 去掉标记后的目录名（None 表示非目录标记）
    """
    if name.endswith("_BEGIN") or name.endswith("_START"):
        # L6 修复：原 `6 if ... else 6` 死分支；两者都是 6 字符后缀
        return "begin", name[:-6]
    if name.endswith("_END"):
        return "end", name[:-4]
    return None, None


# ----------------------------------------------------------------------
# extract_bin
# ----------------------------------------------------------------------

def extract_bin(
    bin_data: bytes | bytearray,
    entries: Sequence[EzTableEntry],
    out_dir: os.PathLike[str] | str,
) -> None:
    """
    解包 EZP (BIN) 文件到目录。

    与 faraplay/dearlystars::ez::extract_bin 行为一致：
        - 解析 16 字节 EZP 头部
        - 从 names_offset 读取 names_buffer
        - 遍历 entries，按 _BEGIN/_START/_END 维护 path_stack
        - 普通文件按 file_offset 读取，压缩则 LZ10 解压
        - 写入 .index 文件：第 1 行 = something1 的 4 位 hex，
          后续每行 = indent(path_stack 深度) + name

    Args:
        bin_data: EZP (BIN) 文件完整字节
        entries:  从 read_idx 得到的 entry 列表
        out_dir:  输出目录
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    header = EzpHeader.from_bytes(bin_data[:EZP_HEADER_SIZE])
    # M4 修复：用 memoryview 避免每个 entry 复制尾部全部字节
    bin_view = memoryview(bin_data) if not isinstance(bin_data, memoryview) else bin_data
    names_buffer = bin_view[header.names_offset:]

    path_stack: list[str] = []
    index_lines: list[str] = [f"{header.something1:04X}"]

    for index, entry in enumerate(entries):
        name = _read_cstring(names_buffer, entry.name_offset)
        kind, dir_name = _strip_dir_marker(name)

        if kind == "begin":
            if entry.decompressed_size != 0:
                raise ValueError(f"BEGIN/START entry {index} ({name}) 的 decompressed_size != 0")
            index_lines.append(f"{_indent(len(path_stack))}{name}")
            path_stack.append(dir_name)  # type: ignore[arg-type]
            (out_dir / Path(*path_stack)).mkdir(parents=True, exist_ok=True)
            continue

        if kind == "end":
            if entry.decompressed_size != 0:
                raise ValueError(f"END entry {index} ({name}) 的 decompressed_size != 0")
            if not path_stack or path_stack[-1] != dir_name:
                raise ValueError(
                    f"END entry {index} ({name}) 与当前目录栈顶不匹配："
                    f"期望 {path_stack[-1] if path_stack else None}, 得到 {dir_name}"
                )
            path_stack.pop()
            index_lines.append(f"{_indent(len(path_stack))}{name}")
            continue

        # 普通文件
        entry_path = out_dir / Path(*path_stack) / f"{index:04}_{name}"
        if entry.is_compressed:
            data = lz10_decompress(bytes(bin_view[entry.file_offset:]))
            # L3 修复：校验解压后长度
            if len(data) != entry.decompressed_size:
                raise ValueError(
                    f"entry {index} ({name}) LZ10 解压长度不符："
                    f"期望 {entry.decompressed_size}, 实际 {len(data)}"
                )
        else:
            size = entry.decompressed_size
            # L3 修复：校验切片不越界
            if entry.file_offset + size > len(bin_view):
                raise ValueError(
                    f"entry {index} ({name}) 数据区越界："
                    f"offset={entry.file_offset}, size={size}, bin_len={len(bin_view)}"
                )
            data = bytes(bin_view[entry.file_offset:entry.file_offset + size])
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_bytes(data)
        index_lines.append(f"{_indent(len(path_stack))}{name}")

    # 写入 .index 文件（与 Rust writeln! 一致，用 \n 分隔，末尾也有 \n）
    (out_dir / ".index").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------
# rebuild_bin
# ----------------------------------------------------------------------

def rebuild_bin(
    in_dir: os.PathLike[str] | str,
) -> tuple[bytes, bytes]:
    """
    从目录重建 EZP (BIN) 与 EZT (IDX)。

    与 faraplay/dearlystars::ez::rebuild_bin 行为一致：
        - 读取 .index 文件：第 1 行 = something1 (hex)，后续每行 = name (trim_start)
        - 写入 EZP 头部（names_offset 占位）
        - 遍历 entry_names：
            * 维护 names_buffer，每条 name 后填充 0 到 (len+1) 的下一个 4 字节倍数
            * _BEGIN/_START: 写入 file_offset=current_pos, size=0 的 entry，push dir
            * _END:          写入 file_offset=current_pos, size=0 的 entry，pop dir
            * 普通文件:      pad_to_alignment 后写入数据，size = decompressed_size
                            若 do_not_compress 为真则不压缩；否则 LZ10 压缩，
                            仅当压缩后更小才使用压缩并置 0x10000000 标志
        - 写入 names_buffer，回填 EZP 头部的 names_offset
        - 生成 EZT：头部 + entries + 1 个额外 entry (file_offset=names_offset)

    Args:
        in_dir: 输入目录（必须包含 .index 文件）

    Returns:
        (bin_bytes, idx_bytes)
    """
    in_dir = Path(in_dir)
    index_path = in_dir / ".index"
    if not index_path.exists():
        raise FileNotFoundError(f"找不到 .index 文件：{index_path}")

    index_text = index_path.read_text(encoding="utf-8")
    index_lines = index_text.split("\n")
    # 去掉末尾空行（write_text 末尾的 \n 会产生一个空字符串）
    while index_lines and index_lines[-1] == "":
        index_lines.pop()

    if not index_lines:
        raise ValueError(".index 文件为空")

    something1 = int(index_lines[0], 16)
    # L4 修复：lstrip() 处理所有空白（含 tab），与 Rust trim_start() 一致
    entry_names = [line.lstrip() for line in index_lines[1:]]

    # bin 写入缓冲
    bin_buf = bytearray()
    # 写入 EZP 头部（names_offset 占位为 0）
    ezp_header = EzpHeader(something1=something1, names_offset=0)
    bin_buf += ezp_header.to_bytes()

    names_buffer = bytearray()
    path_stack: list[str] = []
    entries: list[EzTableEntry] = []

    for index, name in enumerate(entry_names):
        # 维护 names_buffer
        name_offset = len(names_buffer)
        names_buffer.extend(name.encode("utf-8"))
        # 填充到 (len+1) 的下一个 4 字节倍数（与 Rust 一致，至少 1 字节 null）
        pad_count = _next_multiple_of(len(names_buffer) + 1, 4) - len(names_buffer)
        names_buffer.extend(b"\x00" * pad_count)

        kind, dir_name = _strip_dir_marker(name)

        if kind == "begin":
            entries.append(EzTableEntry(
                file_offset=len(bin_buf),
                size_and_compressed_flag=0,
                name_offset=name_offset,
            ))
            path_stack.append(dir_name)  # type: ignore[arg-type]
            continue

        if kind == "end":
            entries.append(EzTableEntry(
                file_offset=len(bin_buf),
                size_and_compressed_flag=0,
                name_offset=name_offset,
            ))
            if not path_stack or path_stack[-1] != dir_name:
                raise ValueError(
                    f"END entry {index} ({name}) 与当前目录栈顶不匹配："
                    f"期望 {path_stack[-1] if path_stack else None}, 得到 {dir_name}"
                )
            path_stack.pop()
            continue

        # 普通文件
        entry_path = in_dir / Path(*path_stack) / f"{index:04}_{name}"
        if not entry_path.exists():
            raise FileNotFoundError(f"找不到文件：{entry_path}")
        decompressed_data = entry_path.read_bytes()
        decompressed_size = len(decompressed_data)
        if decompressed_size > 0x0FFFFFFF:
            raise ValueError(f"文件过大无法加入 .bin：{entry_path} ({decompressed_size} 字节)")

        if decompressed_size == 0:
            entries.append(EzTableEntry(
                file_offset=len(bin_buf),
                size_and_compressed_flag=0,
                name_offset=name_offset,
            ))
            continue

        file_offset = pad_to_alignment(bin_buf, padding_alignment(name), 0)

        if do_not_compress(name):
            is_compressed = False
            bin_buf.extend(decompressed_data)
        else:
            compressed = lz10_compress(decompressed_data)
            if len(compressed) < decompressed_size:
                is_compressed = True
                bin_buf.extend(compressed)
            else:
                is_compressed = False
                bin_buf.extend(decompressed_data)

        entries.append(EzTableEntry.make(
            file_offset=file_offset,
            decompressed_size=decompressed_size,
            is_compressed=is_compressed,
            name_offset=name_offset,
        ))

    # 写入 names_buffer
    names_offset = len(bin_buf)
    bin_buf.extend(names_buffer)

    # 回填 EZP 头部的 names_offset（位于 +0x0C）
    struct.pack_into("<I", bin_buf, 0x0C, names_offset)

    # 生成 IDX
    idx_buf = build_idx(something1=something1, entries=entries, names_offset=names_offset)

    return bytes(bin_buf), bytes(idx_buf)


# ----------------------------------------------------------------------
# 顶层便捷 API
# ----------------------------------------------------------------------

def extract_pack(ezt_path: os.PathLike[str] | str, ezp_path: os.PathLike[str] | str, out_dir: os.PathLike[str] | str) -> None:
    """
    便捷 API：读取 EZT (IDX) + EZP (BIN)，解包到 out_dir。

    Args:
        ezt_path: IDX 文件路径
        ezp_path: BIN 文件路径
        out_dir:  输出目录
    """
    ezt_data = Path(ezt_path).read_bytes()
    ezp_data = Path(ezp_path).read_bytes()
    _header, entries = read_idx(ezt_data)
    extract_bin(ezp_data, entries, out_dir)


def build_pack(in_dir: os.PathLike[str] | str, ezt_out: os.PathLike[str] | str, ezp_out: os.PathLike[str] | str) -> None:
    """
    便捷 API：从 in_dir 重建 EZP (BIN) + EZT (IDX)，写入指定路径。

    Args:
        in_dir:   输入目录（含 .index）
        ezt_out:  输出 IDX 文件路径
        ezp_out:  输出 BIN 文件路径
    """
    bin_bytes, idx_bytes = rebuild_bin(in_dir)
    Path(ezp_out).write_bytes(bin_bytes)
    Path(ezt_out).write_bytes(idx_bytes)
