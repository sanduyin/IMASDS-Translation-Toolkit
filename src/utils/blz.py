# src/utils/blz.py
"""
BLZ (Backward LZ77) 压缩/解压算法。

参考实现：dearlystars_tool (Rust) ndstool/blz.rs

BLZ 是 NDS 用于 ARM9/Overlay 的 LZ77 变体：
    - 数据从尾部向头部反向压缩
    - 末尾 8 字节是 header：
        +0..+2 (3B)  compressed_and_header_size (24-bit LE)
        +3    (1B)  header_size (pad + 8)
        +4..+7 (4B)  extend_size (32-bit LE) = decompressed_size - (out_buf_len + 8)
    - flag byte 每位代表一个 token：bit=1 表示 2 字节回指，bit=0 表示 1 字节原文
    - 回指 token (2B little-endian)：
        length  = (b1 >> 4) + 3       (3..18)
        disp    = ((b1 & 0xF) << 8 | b0) + 3  (3..4098)

BLZ 是 NDS BLZ 标准算法，与 ndspy.codeCompression.decompress 兼容。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Optional

MAX_REF_LEN: int = 0x0F + 3          # 18
MAX_REF_DISP: int = 0x0FFF + 3       # 4098
"""BLZ 最大回指长度 / 距离。"""

BLZ_HEADER_SIZE: int = 8
"""BLZ footer 固定 8 字节。"""


# ----------------------------------------------------------------------
# 解压
# ----------------------------------------------------------------------

def blz_decompress(in_data: bytes | bytearray) -> bytes:
    """
    BLZ 解压。

    与 faraplay/ndstool::blz::blz_decompress 行为一致：返回完整的解压后数据，
    包含原始未压缩头部（如 ARM9 的 0x4000 字节）+ BLZ 解压主体。

    输入必须包含末尾 8 字节 BLZ footer。
    """
    in_length = len(in_data)
    if in_length < BLZ_HEADER_SIZE:
        raise ValueError(
            f"BLZ 数据过短：{in_length} < {BLZ_HEADER_SIZE} 字节 footer"
        )

    # 读取 footer
    compressed_and_header_size = (
        in_data[in_length - 8]
        | (in_data[in_length - 7] << 8)
        | (in_data[in_length - 6] << 16)
    )
    header_size = in_data[in_length - 5]
    extend_size = (
        in_data[in_length - 4]
        | (in_data[in_length - 3] << 8)
        | (in_data[in_length - 2] << 16)
        | (in_data[in_length - 1] << 24)
    )

    # ===== footer 合法性校验（H1/M2/M6 修复）=====
    # 某些"未压缩 passthrough"文件 footer 全 0，按惯例直接返回原数据
    if compressed_and_header_size == 0 and header_size == 0 and extend_size == 0:
        return bytes(in_data)

    if header_size < BLZ_HEADER_SIZE:
        raise ValueError(
            f"BLZ footer 非法：header_size={header_size} < {BLZ_HEADER_SIZE}"
        )
    if compressed_and_header_size < header_size:
        raise ValueError(
            f"BLZ footer 非法：compressed_and_header_size={compressed_and_header_size} "
            f"< header_size={header_size}"
        )
    if compressed_and_header_size > in_length:
        raise ValueError(
            f"BLZ footer 非法：compressed_and_header_size={compressed_and_header_size} "
            f"> in_length={in_length}"
        )
    # extend_size 合理性上限：防止恶意 footer 触发 4GB 内存分配
    # 实际 ARM9 解压后通常 < 2MB，overlay 更小；32MB 是宽松上限
    if extend_size > 0x02000000:
        raise ValueError(
            f"BLZ footer 非法：extend_size={extend_size} 超出合理上限 (32MB)"
        )

    # 压缩数据起始（相对 bytes 切片起点）
    compressed_data_index = compressed_and_header_size - header_size
    # 解压数据写入位置（相对 bytes 切片起点）
    decompressed_data_index = compressed_and_header_size + extend_size

    # 扩展缓冲区：原始数据 + extend_size 个 0
    all_bytes = bytearray(in_data)
    all_bytes.extend(b"\x00" * extend_size)

    # Rust 切片：bytes = &mut all_bytes[in_length - compressed_and_header_size ..]
    # 即所有 bytes[i] 在 all_bytes 中对应 all_bytes[start + i]
    start = in_length - compressed_and_header_size

    while True:
        compressed_data_index -= 1
        if compressed_data_index < 0:
            raise ValueError("BLZ 数据损坏：compressed_data_index 变负")
        flags = all_bytes[start + compressed_data_index]
        for _ in range(8):
            if flags & 0x80 != 0:
                compressed_data_index -= 2
                if compressed_data_index < 0:
                    raise ValueError("BLZ 数据损坏：回指 token 越界")
                ref0 = all_bytes[start + compressed_data_index]
                ref1 = all_bytes[start + compressed_data_index + 1]
                length = (ref1 >> 4) + 3
                disp = (((ref1 & 0x0F) << 8) + ref0 + 3)

                decompressed_data_index -= length
                if decompressed_data_index < 0:
                    raise ValueError("BLZ 数据损坏：decompressed_data_index 变负")
                # 拷贝 length 字节（从高索引向低索引，与 Rust 一致，支持重叠拷贝）
                base = start + decompressed_data_index
                src_base = start + decompressed_data_index + disp
                for i in range(length - 1, -1, -1):
                    all_bytes[base + i] = all_bytes[src_base + i]
            else:
                decompressed_data_index -= 1
                compressed_data_index -= 1
                if compressed_data_index < 0 or decompressed_data_index < 0:
                    raise ValueError("BLZ 数据损坏：索引越界")
                all_bytes[start + decompressed_data_index] = \
                    all_bytes[start + compressed_data_index]
            flags = (flags << 1) & 0xFF
            if decompressed_data_index == 0:
                # 返回整个 all_bytes：包含未压缩头部 + BLZ 解压主体
                # 与 Rust 行为一致：return Ok(all_bytes);
                return bytes(all_bytes)
            if decompressed_data_index < 0:
                raise ValueError("BLZ 数据损坏：decompressed_data_index 跳过 0")


# ----------------------------------------------------------------------
# 压缩
# ----------------------------------------------------------------------

def _backwards_match_length(test_slice: bytes | bytearray, my_slice: bytes | bytearray) -> int:
    """
    从尾部向前比较，返回匹配长度。

    H2 修复：调用方应只传入末尾 test_length 字节的小切片（而非整个前缀），
    避免每次候选检查 O(n) 内存拷贝。本函数对入参做最小假设，仅比较末尾对齐字节。
    """
    test_length = len(test_slice)
    my_length = len(my_slice)
    # 比较至多 test_length 字节；my_slice 至少需有 test_length 字节才可全匹配
    cmp_len = min(test_length, my_length)
    for i in range(cmp_len):
        if my_slice[my_length - 1 - i] != test_slice[test_length - 1 - i]:
            return i
    return cmp_len


def blz_compress(
    decompressed_bytes: bytes | bytearray,
    min_uncompressed_region_size: int = 0,
    max_search_positions: int = 0,
) -> Optional[bytes]:
    """
    BLZ 压缩。

    与 faraplay/ndstool::blz::blz_compress 行为一致（包括候选项 disp 升序遍历、
    严格 > 比较选最小 disp），加入 3-gram 哈希加速以避免纯 Python 在大文件
    （如 ARM9 545KB）上的 O(n²) 性能问题。

    Args:
        decompressed_bytes: 待压缩数据
        min_uncompressed_region_size: 数据头部不参与压缩的字节数
            （ARM9 通常为 0x4000，Overlay 为 0x12）
        max_search_positions: 每个 position 最多检查多少个哈希命中的候选 disp
            （0 表示不限制，与 Rust 行为完全一致；默认 0）
            注：Rust 暴力搜索会检查窗口内全部候选，本实现用哈希表达到相同覆盖。

    Returns:
        压缩后的 bytes（含 footer），或 None 表示数据无法压缩到更小（保持原样）。
    """
    decompressed_size = len(decompressed_bytes)

    if decompressed_size < min_uncompressed_region_size:
        raise ValueError(
            "Minimum uncompressed region size 大于数据总长度！"
        )

    compress_buffer = bytearray()
    position = decompressed_size
    flags: int = 0
    flags_count = 0
    mini_buffer = bytearray()

    best_total_size = decompressed_size
    best_total_sizes_list: list[tuple[int, int]] = []

    # 3-gram 哈希：key = (b[i-3], b[i-2], b[i-1]) → list of positions i
    # 用于快速找到 "末尾 3 字节与当前位置之前某处末尾 3 字节相同" 的候选 disp
    # 注意：BLZ 是反向匹配，匹配的是 test_slice (position-test_length..position) 的末尾
    # 与 my_slice (..position+disp) 的末尾。我们用末尾 3 字节作为哈希键。
    hash3: dict[tuple[int, int, int], list[int]] = {}

    def _hash_key_end(pos: int) -> tuple[int, int, int]:
        """取 decompressed_bytes[pos-3..pos] 这 3 字节作为 key。"""
        return (
            decompressed_bytes[pos - 3],
            decompressed_bytes[pos - 2],
            decompressed_bytes[pos - 1],
        )

    # 预填充哈希：所有 i > min_uncompressed_region_size + 2 都加入
    # 哈希键是 decompressed_bytes[i-3..i]（即末尾 3 字节）
    # 因为 BLZ 是反向匹配，搜索时我们想找：在 position+3..position+max_disp+3 范围内，
    # 末尾 3 字节 == decompressed_bytes[position-3..position] 的所有 disp
    # disp = candidate_pos - position
    for i in range(min_uncompressed_region_size + 3, decompressed_size + 1):
        key = _hash_key_end(i)
        hash3.setdefault(key, []).append(i)

    while position > min_uncompressed_region_size:
        if flags_count == 8:
            compress_buffer.append(flags)
            compress_buffer.extend(mini_buffer)
            mini_buffer.clear()
            flags = 0
            flags_count = 0

        test_length = min(position - min_uncompressed_region_size, MAX_REF_LEN)
        max_disp = min(decompressed_size - position, MAX_REF_DISP)

        best_disp = 0
        best_length = 2

        # 哈希加速：找所有候选 disp = candidate_pos - position
        # candidate_pos 范围：position+3 .. position+max_disp
        # 我们要找 candidate_pos 满足 decompressed_bytes[candidate_pos-3..candidate_pos]
        # == decompressed_bytes[position-3..position]
        if position >= 3 and max_disp >= 3:
            key = _hash_key_end(position)
            candidates = hash3.get(key)
            if candidates:
                # M3 修复：用 bisect 直接定位 [lo, hi] 窗口，避免线性扫描窗口外候选
                lo = position + 3
                hi = position + max_disp
                left = bisect_left(candidates, lo)
                right = bisect_right(candidates, hi)
                # M1 修复：升序遍历（disp 从小到大），与 Rust `for disp in 3..(max_disp+1)` 一致
                # 长度并列时严格 > 比较 → 选最小 disp
                count = 0
                for idx in range(left, right):
                    candidate_pos = candidates[idx]
                    disp = candidate_pos - position
                    if disp < 3:
                        continue
                    # H2 修复：只传末尾 test_length 字节，避免 O(n) 内存拷贝
                    # my_slice 末尾对齐：取 [position+disp-test_length .. position+disp]
                    this_length = min(
                        _backwards_match_length(
                            decompressed_bytes[position - test_length : position],
                            decompressed_bytes[position + disp - test_length : position + disp],
                        ),
                        disp,
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
            mini_buffer.append((((best_length - 3) << 4) | ((best_disp - 3) >> 8)) & 0xFF)
            mini_buffer.append(((best_disp - 3) & 0xFF))
            position -= best_length
            flags |= 1
        else:
            position -= 1
            mini_buffer.append(decompressed_bytes[position])

        # 计算如果在此处停止的总大小（不含 footer）
        total_size = position + len(compress_buffer) + 1 + len(mini_buffer)

        if total_size < best_total_size:
            best_total_size = total_size
            best_total_sizes_list.append((position, total_size))

    # 写入最后的 flags 字节 + mini_buffer
    # flags << (8 - flags_count) 是因为高位先输出
    compress_buffer.append((flags << (8 - flags_count)) & 0xFF)
    compress_buffer.extend(mini_buffer)

    total_size = min_uncompressed_region_size + len(compress_buffer)

    if total_size > best_total_size:
        # 找到第一个比当前 total_size 严格更小的记录
        better_position: Optional[int] = None
        better_total_size: Optional[int] = None
        for pos, sz in best_total_sizes_list:
            if sz < total_size:
                better_position = pos
                better_total_size = sz
                break
        if better_position is None or better_total_size is None:
            # 不应发生（best_total_size < total_size 时列表非空）
            better_position = min_uncompressed_region_size
            better_total_size = total_size
        # 截断到 better_total_size - better_position 字节
        compress_buffer = compress_buffer[: better_total_size - better_position]
    else:
        better_position = min_uncompressed_region_size
        better_total_size = total_size

    # 向上对齐 4 字节，判断是否能压缩
    aligned = (better_total_size + 3) & ~3  # next_multiple_of(4)
    if aligned + BLZ_HEADER_SIZE >= decompressed_size:
        # 压缩后不会更小，返回 None（保持未压缩）
        return None

    compress_buffer.reverse()
    pad_byte_count = aligned - better_total_size
    compress_buffer.extend(b"\xFF" * pad_byte_count)

    header_size = pad_byte_count + BLZ_HEADER_SIZE
    compressed_and_header_size = len(compress_buffer) + BLZ_HEADER_SIZE

    out_buffer = bytearray(decompressed_bytes[:better_position])
    out_buffer.extend(compress_buffer)
    extend_size = decompressed_size - (len(out_buffer) + BLZ_HEADER_SIZE)

    if compressed_and_header_size > 0x00FFFFFF:
        raise ValueError("compressed_and_header_size 超出 24 位！")
    if extend_size > 0xFFFFFFFF:
        raise ValueError("extend_size 超出 32 位！")

    # 写 footer
    out_buffer.append(compressed_and_header_size & 0xFF)
    out_buffer.append((compressed_and_header_size >> 8) & 0xFF)
    out_buffer.append((compressed_and_header_size >> 16) & 0xFF)
    out_buffer.append(header_size & 0xFF)
    out_buffer.append(extend_size & 0xFF)
    out_buffer.append((extend_size >> 8) & 0xFF)
    out_buffer.append((extend_size >> 16) & 0xFF)
    out_buffer.append((extend_size >> 24) & 0xFF)

    return bytes(out_buffer)


# ----------------------------------------------------------------------
# ARM9 / Overlay 专用便捷函数
# ----------------------------------------------------------------------

def blz_compress_arm9(decompressed_bytes: bytes | bytearray) -> Optional[bytes]:
    """压缩 ARM9 二进制（头部 0x4000 字节不压缩）。"""
    return blz_compress(decompressed_bytes, min_uncompressed_region_size=0x4000)


def blz_compress_overlay(decompressed_bytes: bytes | bytearray) -> Optional[bytes]:
    """压缩 Overlay 二进制（与 Rust blz_compress(&decompressed_overlay, 0) 一致，头部 0 字节不压缩）。"""
    return blz_compress(decompressed_bytes, min_uncompressed_region_size=0)
