# src/packez/ezt_parser.py
"""
EZT/EZP 头部与 entry 表的解析与生成。

参考实现：reference/dearlystars_tool/dearlystars/src/ez.rs

数据结构（全部 little-endian）：

    EzTableHeader (16 字节) — EZT/IDX 文件头部
        +0x00  magic1[8]          固定 EZT_MAGIC1
        +0x08  something1:u16    通常为 0x0001（pack id）
        +0x0A  magic2:u16        固定 0x0016
        +0x0C  entry_count:u32   entry 表条目数

    EzTableEntry (12 字节) — EZT/IDX entry
        +0x00  file_offset:u32              文件数据在 EZP 中的偏移
        +0x04  size_and_compressed_flag:u32 低 28 位 = decompressed_size
                                          bit 28    = is_compressed
        +0x08  name_offset:u32             名称在 names_buffer 中的偏移

    EzpHeader (16 字节) — EZP/BIN 文件头部
        +0x00  magic1[8]          固定 EZP_MAGIC1
        +0x08  something1:u16    与 EZT 头部一致
        +0x0A  magic2:u16        固定 0x0016
        +0x0C  names_offset:u32  names_buffer 在 EZP 中的起始偏移
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Sequence

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

EZT_MAGIC1: bytes = bytes([0x45, 0x5A, 0x54, 0x00, 0x0E, 0x07, 0xD9, 0x07])
"""EZT (IDX) 文件 magic1。注意 +0x02 处是 'T'(0x54)。"""

EZP_MAGIC1: bytes = bytes([0x45, 0x5A, 0x50, 0x00, 0x0E, 0x07, 0xD9, 0x07])
"""EZP (BIN) 文件 magic1。注意 +0x02 处是 'P'(0x50)。"""

EZ_MAGIC2: int = 0x16
"""EZT/EZP 头部 magic2 固定值。"""

EZT_HEADER_SIZE: int = 16
"""EZT 头部固定 16 字节。"""

EZ_ENTRY_SIZE: int = 12
"""EZT entry 固定 12 字节。"""

EZP_HEADER_SIZE: int = 16
"""EZP 头部固定 16 字节。"""

_COMPRESSED_FLAG: int = 0x10000000
_SIZE_MASK: int = 0x0FFFFFFF


# ----------------------------------------------------------------------
# 结构体
# ----------------------------------------------------------------------

@dataclass
class EzTableHeader:
    """EZT (IDX) 文件头部。"""
    something1: int
    entry_count: int

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> "EzTableHeader":
        if len(data) < EZT_HEADER_SIZE:
            raise ValueError(f"EZT 头部数据过短：{len(data)} < {EZT_HEADER_SIZE}")
        magic1 = bytes(data[0:8])
        if magic1 != EZT_MAGIC1:
            raise ValueError(f"EZT magic1 错误：{magic1.hex()} != {EZT_MAGIC1.hex()}")
        something1 = struct.unpack_from("<H", data, 8)[0]
        magic2 = struct.unpack_from("<H", data, 10)[0]
        if magic2 != EZ_MAGIC2:
            raise ValueError(f"EZT magic2 错误：0x{magic2:04X} != 0x{EZ_MAGIC2:04X}")
        entry_count = struct.unpack_from("<I", data, 12)[0]
        return cls(something1=something1, entry_count=entry_count)

    def to_bytes(self) -> bytes:
        out = bytearray(EZT_MAGIC1)
        out += struct.pack("<H", self.something1)
        out += struct.pack("<H", EZ_MAGIC2)
        out += struct.pack("<I", self.entry_count)
        return bytes(out)


@dataclass
class EzTableEntry:
    """EZT (IDX) entry：文件在 EZP 中的位置与大小信息。"""
    file_offset: int
    size_and_compressed_flag: int
    name_offset: int

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> "EzTableEntry":
        if len(data) < EZ_ENTRY_SIZE:
            raise ValueError(f"EZT entry 数据过短：{len(data)} < {EZ_ENTRY_SIZE}")
        file_offset, size_and_compressed_flag, name_offset = struct.unpack_from("<III", data, 0)
        return cls(
            file_offset=file_offset,
            size_and_compressed_flag=size_and_compressed_flag,
            name_offset=name_offset,
        )

    def to_bytes(self) -> bytes:
        return struct.pack(
            "<III",
            self.file_offset,
            self.size_and_compressed_flag,
            self.name_offset,
        )

    @property
    def decompressed_size(self) -> int:
        """解压后大小（低 28 位）。"""
        return self.size_and_compressed_flag & _SIZE_MASK

    @property
    def is_compressed(self) -> bool:
        """是否压缩（bit 28）。"""
        return (self.size_and_compressed_flag & _COMPRESSED_FLAG) != 0

    @classmethod
    def make(
        cls,
        file_offset: int,
        decompressed_size: int,
        is_compressed: bool,
        name_offset: int,
    ) -> "EzTableEntry":
        """按字段语义构造 entry（自动合成 size_and_compressed_flag）。"""
        flag = _COMPRESSED_FLAG if is_compressed else 0
        return cls(
            file_offset=file_offset,
            size_and_compressed_flag=(decompressed_size & _SIZE_MASK) | flag,
            name_offset=name_offset,
        )


@dataclass
class EzpHeader:
    """EZP (BIN) 文件头部。"""
    something1: int
    names_offset: int

    @classmethod
    def from_bytes(cls, data: bytes | bytearray) -> "EzpHeader":
        if len(data) < EZP_HEADER_SIZE:
            raise ValueError(f"EZP 头部数据过短：{len(data)} < {EZP_HEADER_SIZE}")
        magic1 = bytes(data[0:8])
        if magic1 != EZP_MAGIC1:
            raise ValueError(f"EZP magic1 错误：{magic1.hex()} != {EZP_MAGIC1.hex()}")
        something1 = struct.unpack_from("<H", data, 8)[0]
        magic2 = struct.unpack_from("<H", data, 10)[0]
        if magic2 != EZ_MAGIC2:
            raise ValueError(f"EZP magic2 错误：0x{magic2:04X} != 0x{EZ_MAGIC2:04X}")
        names_offset = struct.unpack_from("<I", data, 12)[0]
        return cls(something1=something1, names_offset=names_offset)

    def to_bytes(self) -> bytes:
        out = bytearray(EZP_MAGIC1)
        out += struct.pack("<H", self.something1)
        out += struct.pack("<H", EZ_MAGIC2)
        out += struct.pack("<I", self.names_offset)
        return bytes(out)


# ----------------------------------------------------------------------
# 解析函数
# ----------------------------------------------------------------------

def read_idx(data: bytes | bytearray) -> tuple[EzTableHeader, list[EzTableEntry]]:
    """
    读取 IDX 文件，返回 (header, entries)。

    与 faraplay/dearlystars::ez::read_idx 行为一致：
        - 解析 16 字节 EZT 头部
        - 读取 entry_count 个 EzTableEntry

    注意：IDX 文件末尾还有 1 个额外的 EzTableEntry
          (file_offset=names_offset, size=0, name_offset=0)，
          read_idx 不读取它，但 rebuild_bin 会写入它。

    Args:
        data: IDX 文件完整字节

    Returns:
        (EzTableHeader, [EzTableEntry, ...])
    """
    header = EzTableHeader.from_bytes(data)
    entries: list[EzTableEntry] = []
    offset = EZT_HEADER_SIZE
    for _ in range(header.entry_count):
        entries.append(EzTableEntry.from_bytes(data[offset:offset + EZ_ENTRY_SIZE]))
        offset += EZ_ENTRY_SIZE
    return header, entries


def build_idx(something1: int, entries: Sequence[EzTableEntry], names_offset: int) -> bytes:
    """
    生成 IDX 文件字节。

    与 faraplay/dearlystars::ez::rebuild_bin 末尾的 IDX 写入逻辑一致：
        - 写入 EZT 头部 (entry_count = len(entries))
        - 写入所有 entries
        - 写入 1 个额外 entry (file_offset=names_offset, size=0, name_offset=0)

    Args:
        something1:    EZT/EZP 头部 something1 字段
        entries:       entry 列表（不含末尾的额外 entry）
        names_offset:  EZP 中 names_buffer 的偏移，写入额外 entry 的 file_offset

    Returns:
        完整 IDX 文件字节
    """
    header = EzTableHeader(something1=something1, entry_count=len(entries))
    out = bytearray(header.to_bytes())
    for entry in entries:
        out += entry.to_bytes()
    # 末尾额外 entry：标记 names_buffer 位置
    out += EzTableEntry(
        file_offset=names_offset,
        size_and_compressed_flag=0,
        name_offset=0,
    ).to_bytes()
    return bytes(out)
