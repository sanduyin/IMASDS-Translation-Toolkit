# tests/utils/test_lz10.py
"""LZ10 压缩/解压算法单元测试。"""
from __future__ import annotations

import os
import struct

import pytest

from src.utils.lz10 import (
    LZ10_MAGIC,
    LZ10_HEADER_SIZE,
    MAX_REF_LEN,
    MAX_REF_DISP,
    MIN_MATCH_LEN,
    lz10_decompress,
    lz10_compress,
)


# ----------------------------------------------------------------------
# 常量校验
# ----------------------------------------------------------------------

class TestLZ10Constants:
    def test_magic_value(self) -> None:
        assert LZ10_MAGIC == 0x10

    def test_header_size(self) -> None:
        assert LZ10_HEADER_SIZE == 4

    def test_max_ref_len(self) -> None:
        assert MAX_REF_LEN == 18

    def test_max_ref_disp(self) -> None:
        assert MAX_REF_DISP == 4096

    def test_min_match_len(self) -> None:
        assert MIN_MATCH_LEN == 3


# ----------------------------------------------------------------------
# 解压
# ----------------------------------------------------------------------

class TestLZ10Decompress:
    def test_decompress_empty_data(self) -> None:
        # decompressed_size=0 时直接返回空 bytes（L5 修复）
        data = struct.pack("<I", 0x10)  # magic=0x10, length=0
        assert lz10_decompress(data) == b""

    def test_decompress_short_data_raises(self) -> None:
        with pytest.raises(ValueError, match="数据过短"):
            lz10_decompress(b"\x10\x00")

    def test_decompress_bad_magic_raises(self) -> None:
        # magic != 0x10
        bad_header = struct.pack("<I", 0x20)  # magic=0x20
        with pytest.raises(ValueError, match="magic 错误"):
            lz10_decompress(bad_header)

    def test_decompress_single_literal(self) -> None:
        # header (magic + size=1) + flags=0x00 + 1 literal byte
        data = struct.pack("<I", 0x110) + b"\x00\xAB"
        # 0x110 = (1 << 8) | 0x10
        result = lz10_decompress(data)
        assert result == b"\xAB"

    def test_decompress_multiple_literals(self) -> None:
        # size=4, flags=0x00 (全 literal), 4 bytes
        data = struct.pack("<I", 0x410) + b"\x00\x01\x02\x03\x04"
        result = lz10_decompress(data)
        assert result == b"\x01\x02\x03\x04"

    def test_decompress_with_backreference(self) -> None:
        # 构造：ABC + 回指(len=3, disp=3) → ABCABC
        # size=6, flags=0b00010000=0x10 (第 4 个 token 是回指，MSB 优先)
        # tokens: A(lit) B(lit) C(lit) [ref len=3 disp=3]
        # ref token (big-endian): ((len-3) << 4 | (disp-1) >> 8) << 8 | (disp-1) & 0xFF
        # len=3 → (0 << 4), disp=3 → disp-1=2 = 0x0002 → bytes 0x00 0x02
        data = struct.pack("<I", 0x610) + bytes([0b00010000, 0x41, 0x42, 0x43, 0x00, 0x02])
        result = lz10_decompress(data)
        assert result == b"ABCABC"

    def test_decompress_incomplete_flags_raises(self) -> None:
        # 只有 header，缺少 flags 字节
        data = struct.pack("<I", 0x1010)
        with pytest.raises(ValueError, match="不完整"):
            lz10_decompress(data)


# ----------------------------------------------------------------------
# 压缩
# ----------------------------------------------------------------------

class TestLZ10Compress:
    def test_compress_empty(self) -> None:
        result = lz10_compress(b"")
        # 应只有 4 字节 header，length=0
        assert result == struct.pack("<I", 0x10)

    def test_compress_header_format(self) -> None:
        result = lz10_compress(b"abc")
        # magic + 3 字节长度（LE）
        assert result[0] == 0x10
        assert result[1] == 3
        assert result[2] == 0
        assert result[3] == 0

    def test_compress_too_large_raises(self) -> None:
        # > 16MB（0x00FFFFFF）应报错
        too_large = b"\x00" * (0x00FFFFFF + 1)
        with pytest.raises(ValueError, match="16MB"):
            lz10_compress(too_large)

    def test_round_trip_simple(self) -> None:
        original = b"Hello, World!"
        compressed = lz10_compress(original)
        decompressed = lz10_decompress(compressed)
        assert decompressed == original

    def test_round_trip_repetitive(self) -> None:
        original = b"ABCD" * 100
        compressed = lz10_compress(original)
        decompressed = lz10_decompress(compressed)
        assert decompressed == original

    def test_round_trip_random_data(self) -> None:
        original = os.urandom(512)
        compressed = lz10_compress(original)
        decompressed = lz10_decompress(compressed)
        assert decompressed == original

    def test_round_trip_long_run(self) -> None:
        # 长回指（接近 MAX_REF_LEN=18）
        original = b"X" * 200
        compressed = lz10_compress(original)
        decompressed = lz10_decompress(compressed)
        assert decompressed == original

    def test_round_trip_dispersed_pattern(self) -> None:
        # 分散模式：混合重复与唯一字节
        original = (b"prefix_" + b"ABCDEFGH" + b"_suffix_") * 10
        compressed = lz10_compress(original)
        decompressed = lz10_decompress(compressed)
        assert decompressed == original

    def test_compression_ratio_for_repetitive(self) -> None:
        # 重复数据应显著压缩（LZ10 最大回指 18 字节，4096 字节约压缩到 500 字节）
        original = b"\x00" * 4096
        compressed = lz10_compress(original)
        # 至少压缩到 1/4 以下
        assert len(compressed) < len(original) / 4

    def test_compression_incompressible_grows_at_most_header(self) -> None:
        # 不可压缩数据至多增长 1/8 + 头部
        original = os.urandom(64)
        compressed = lz10_compress(original)
        # 每个 8 字节组需 1 字节 flags，故最大增长 1/8
        max_expected = len(original) + len(original) // 8 + LZ10_HEADER_SIZE + 1
        assert len(compressed) <= max_expected
