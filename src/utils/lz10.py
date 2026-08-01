# src/utils/lz10.py
"""
LZ10 (NDS LZ77) 压缩/解压算法。

参考实现：dearlystars_tool (Rust) lz10.rs

LZ10 是 NDS 通用 LZ77 变体，用于压缩游戏数据文件（F_*.BIN 等）：
    - 头部 4 字节：0x10 magic + 3 字节 decompressed_size (LE)
    - 8 个 token 一组，前面有 1 字节 flags（MSB 优先）
    - flag bit=1：2 字节回指（big-endian）
        length = (b0 >> 4) + 3       (3..18)
        disp   = ((b0 & 0xF) << 8 | b1) + 1  (1..4096)
    - flag bit=0：1 字节原文

与 BLZ 的区别：
    - LZ10 是前向压缩（从前往后），BLZ 是反向压缩
    - LZ10 disp 范围 [1, 4096]，BLZ disp 范围 [3, 4098]
    - LZ10 disp=1 不被允许（Rust 实现中的特殊行为，从 disp=2 开始搜索）
    - LZ10 回指是 big-endian，BLZ 是 little-endian
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Optional

LZ10_MAGIC: int = 0x10
"""LZ10 头部 magic byte。"""

LZ10_HEADER_SIZE: int = 4
"""LZ10 头部固定 4 字节。"""

MAX_REF_LEN: int = 0x0F + 3       # 18
"""LZ10 最大回指长度。"""

MAX_REF_DISP: int = 0x0FFF + 1    # 4096
"""LZ10 最大回指距离。"""

MIN_MATCH_LEN: int = 3
"""LZ10 最小匹配长度（>2 才值得回指）。"""


# ----------------------------------------------------------------------
# 解压
# ----------------------------------------------------------------------

def lz10_decompress(data: bytes | bytearray) -> bytes:
    """
    LZ10 解压。

    与 faraplay/dearlystars::lz10::decompress 行为一致：
        - 读取 4 字节头部，校验 magic == 0x10
        - 按 flags 字节解析 8 个 token
        - 达到 decompressed_size 时立即返回

    Args:
        data: 包含头部 + 压缩流的完整 LZ10 数据

    Returns:
        解压后的原始数据

    Raises:
        ValueError: magic 错误或写入字节数超出预期
    """
    if len(data) < LZ10_HEADER_SIZE:
        raise ValueError(f"LZ10 数据过短：{len(data)} < {LZ10_HEADER_SIZE}")

    header = data[0] | (data[1] << 8) | (data[2] << 16) | (data[3] << 24)
    if (header & 0xFF) != LZ10_MAGIC:
        raise ValueError(f"LZ10 header magic 错误：0x{header & 0xFF:02X} != 0x{LZ10_MAGIC:02X}")

    decompressed_size = header >> 8

    # L5 修复：空输入直接返回（避免要求 flags 字节，与 lz10_compress 空输入自洽）
    if decompressed_size == 0:
        return b""

    out_buffer = bytearray()
    pos = LZ10_HEADER_SIZE
    data_len = len(data)

    while True:
        if pos >= data_len:
            raise ValueError("LZ10 数据不完整：缺少 flags 字节")
        flags = data[pos]
        pos += 1

        for _ in range(8):
            if flags & 0x80 != 0:
                # 回指 token
                if pos + 1 >= data_len:
                    raise ValueError("LZ10 数据不完整：缺少回指 token")
                ref0 = data[pos]
                ref1 = data[pos + 1]
                pos += 2
                length = (ref0 >> 4) + 3
                disp = (((ref0 & 0x0F) << 8) + ref1) + 1

                if disp > len(out_buffer):
                    raise ValueError(
                        f"LZ10 回指 disp={disp} 超出已解压缓冲区长度 {len(out_buffer)}"
                    )

                source_index = len(out_buffer) - disp
                # M5 性能优化：disp >= length 时无重叠，可用切片整体扩展
                if disp >= length:
                    out_buffer.extend(out_buffer[source_index : source_index + length])
                else:
                    # 重叠拷贝，必须逐字节
                    for _ in range(length):
                        out_buffer.append(out_buffer[source_index])
                        source_index += 1
            else:
                # 原文字节
                if pos >= data_len:
                    raise ValueError("LZ10 数据不完整：缺少原文字节")
                out_buffer.append(data[pos])
                pos += 1

            flags = (flags << 1) & 0xFF

            if len(out_buffer) >= decompressed_size:
                if len(out_buffer) > decompressed_size:
                    raise ValueError(
                        f"LZ10 解压写出 {len(out_buffer)} 字节，超出预期 {decompressed_size}"
                    )
                return bytes(out_buffer)


# ----------------------------------------------------------------------
# 压缩
# ----------------------------------------------------------------------

def _match_length(test_slice: bytes | bytearray, my_slice: bytes | bytearray) -> int:
    """从前向后比较，返回匹配长度。"""
    test_length = len(test_slice)
    for i in range(test_length):
        if my_slice[i] != test_slice[i]:
            return i
    return test_length


def lz10_compress(
    decompressed_bytes: bytes | bytearray,
    max_search_positions: int = 0,
) -> bytes:
    """
    LZ10 压缩。

    与 faraplay/dearlystars::lz10::compress 行为一致：
        - 从前往后扫描，每 8 个 token 输出一组
        - 对每个位置，从 disp=max_disp 向下搜索到 disp=2，找最长匹配
        - 找到 best_length == test_length 时提前 break
        - disp=1 不被允许（与 Rust 实现一致）

    加入 3-gram 哈希加速以避免纯 Python 在大文件上的 O(n²) 性能问题。

    Args:
        decompressed_bytes: 待压缩数据
        max_search_positions: 每个 position 最多检查多少个哈希命中的候选 disp
            （0 表示不限制，与 Rust 行为完全一致）

    Returns:
        压缩后的 bytes（含 4 字节头部）
    """
    decompressed_size = len(decompressed_bytes)
    if decompressed_size > 0x00FFFFFF:
        raise ValueError("LZ10 解压后数据 > 16MB，超出 24 位长度字段")

    # 头部：0x10 magic + 3 字节 decompressed_size (LE)
    out_buffer = bytearray([
        LZ10_MAGIC,
        decompressed_size & 0xFF,
        (decompressed_size >> 8) & 0xFF,
        (decompressed_size >> 16) & 0xFF,
    ])

    # 3-gram 哈希：key = (b[i], b[i+1], b[i+2]) → list of positions i
    # 用于快速找到 "当前位置开头 3 字节与历史某处开头 3 字节相同" 的候选 disp
    # disp = position - candidate_pos，要求 disp ∈ [2, min(position, 4096)]
    # 预填充所有位置的 3-gram，确保与 Rust 暴力搜索行为一致（不漏掉任何候选）
    hash3: dict[tuple[int, int, int], list[int]] = {}

    def _hash_key_start(pos: int) -> tuple[int, int, int]:
        """取 decompressed_bytes[pos..pos+3] 这 3 字节作为 key。"""
        return (
            decompressed_bytes[pos],
            decompressed_bytes[pos + 1],
            decompressed_bytes[pos + 2],
        )

    # 预填充哈希：所有 i 满足 i + 2 < decompressed_size
    for i in range(decompressed_size - 2):
        hash3.setdefault(_hash_key_start(i), []).append(i)

    position = 0
    flags: int = 0
    flags_count = 0
    mini_buffer = bytearray()

    while position < decompressed_size:
        if flags_count == 8:
            out_buffer.append(flags)
            out_buffer.extend(mini_buffer)
            mini_buffer.clear()
            flags = 0
            flags_count = 0

        test_length = min(decompressed_size - position, MAX_REF_LEN)
        max_disp = min(position, MAX_REF_DISP)

        best_disp = 0
        best_length = MIN_MATCH_LEN - 1  # 2，与 Rust 一致：best_length > 2 才用回指

        # 只有当剩余数据 >= 3 字节时才尝试匹配
        if test_length >= MIN_MATCH_LEN and max_disp >= 2:
            test_slice = bytes(decompressed_bytes[position : position + test_length])

            # 哈希加速：找所有候选 disp = position - candidate_pos
            # candidate_pos 范围：position - max_disp .. position - 2
            # 我们要找 candidate_pos 满足 decompressed_bytes[candidate_pos..candidate_pos+3]
            # == decompressed_bytes[position..position+3]
            key = _hash_key_start(position)
            candidates = hash3.get(key)
            if candidates:
                # M3 修复：用 bisect 定位 [lo, hi] 窗口，避免线性扫描
                # Rust: for disp in (2..(max_disp + 1)).rev()  即从 max_disp 到 2
                # 对应 candidate_pos 从 position - max_disp 到 position - 2（升序）
                # 即从小到大遍历 candidates（candidate_pos 小 → disp 大）
                lo = position - max_disp
                hi = position - 2
                left = bisect_left(candidates, lo)
                right = bisect_right(candidates, hi)
                count = 0
                for idx in range(left, right):
                    candidate_pos = candidates[idx]
                    disp = position - candidate_pos
                    if disp < 2:
                        continue
                    this_length = _match_length(
                        test_slice,
                        decompressed_bytes[candidate_pos : candidate_pos + test_length],
                    )
                    if this_length > best_length:
                        best_length = this_length
                        best_disp = disp
                        if best_length == test_length:
                            break
                    count += 1
                    if max_search_positions > 0 and count >= max_search_positions:
                        break

        flags <<= 1
        flags_count += 1
        if best_length > 2:
            # 回指 token（big-endian）
            mini_buffer.append((((best_length - 3) << 4) | ((best_disp - 1) >> 8)) & 0xFF)
            mini_buffer.append(((best_disp - 1) & 0xFF))
            position += best_length
            flags |= 1
        else:
            # 原文字节
            mini_buffer.append(decompressed_bytes[position])
            position += 1

    # 写入最后的 flags 字节 + mini_buffer
    # flags << (8 - flags_count) 是因为高位先输出
    if flags_count > 0:
        out_buffer.append((flags << (8 - flags_count)) & 0xFF)
        out_buffer.extend(mini_buffer)

    return bytes(out_buffer)
