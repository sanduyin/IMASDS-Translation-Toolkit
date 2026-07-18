# tests/utils/test_blz.py
"""BLZ (Backward LZ77) 压缩/解压算法单元测试。"""
from __future__ import annotations

import os
import struct

import pytest

from src.utils.blz import (
    MAX_REF_LEN,
    MAX_REF_DISP,
    BLZ_HEADER_SIZE,
    blz_decompress,
    blz_compress,
    blz_compress_arm9,
    blz_compress_overlay,
)


# ----------------------------------------------------------------------
# 常量校验
# ----------------------------------------------------------------------

class TestBLZConstants:
    def test_max_ref_len(self) -> None:
        assert MAX_REF_LEN == 18

    def test_max_ref_disp(self) -> None:
        assert MAX_REF_DISP == 4098

    def test_header_size(self) -> None:
        assert BLZ_HEADER_SIZE == 8


# ----------------------------------------------------------------------
# 解压
# ----------------------------------------------------------------------

class TestBLZDecompress:
    def test_decompress_short_data_raises(self) -> None:
        with pytest.raises(ValueError, match="数据过短"):
            blz_decompress(b"\x00\x00\x00\x00")

    def test_decompress_zero_footer_returns_input(self) -> None:
        # footer 全 0 视为 passthrough，返回原数据
        data = b"\x01\x02\x03\x04\x05\x06\x07\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        result = blz_decompress(data)
        assert result == data

    def test_decompress_invalid_header_size_raises(self) -> None:
        # header_size < 8 应报错
        # 构造一个 footer：compressed_and_header_size=10, header_size=4, extend_size=0
        footer = bytes([10, 0, 0, 4, 0, 0, 0, 0])
        data = b"\x00" * 16 + footer
        with pytest.raises(ValueError, match="header_size"):
            blz_decompress(data)

    def test_decompress_invalid_compressed_size_raises(self) -> None:
        # compressed_and_header_size < header_size 应报错
        footer = bytes([4, 0, 0, 8, 0, 0, 0, 0])  # ca=4, hs=8 → ca < hs
        data = b"\x00" * 16 + footer
        with pytest.raises(ValueError, match="compressed_and_header_size"):
            blz_decompress(data)

    def test_decompress_compressed_size_exceeds_input_raises(self) -> None:
        # compressed_and_header_size > in_length 应报错
        footer = bytes([0xFF, 0xFF, 0xFF, 8, 0, 0, 0, 0])
        data = b"\x00" * 16 + footer
        with pytest.raises(ValueError, match="in_length"):
            blz_decompress(data)


# ----------------------------------------------------------------------
# 压缩 → 解压往返
# ----------------------------------------------------------------------

class TestBLZCompressRoundTrip:
    def test_compress_small_data_may_return_none(self) -> None:
        # 极小数据无法压缩时返回 None
        result = blz_compress(b"\x00", min_uncompressed_region_size=0)
        # 1 字节无法压缩，应返回 None 或极短数据
        # 由于 BLZ 自带 8 字节 footer，1 字节必然无法压缩
        assert result is None

    def test_round_trip_repetitive_data(self) -> None:
        # 高度重复数据应能压缩并往返
        original = b"ABCD" * 256
        compressed = blz_compress(original, min_uncompressed_region_size=0)
        assert compressed is not None, "应能压缩重复数据"
        decompressed = blz_decompress(compressed)
        assert decompressed == original

    def test_round_trip_with_uncompressed_header(self) -> None:
        # 模拟 ARM9：头部 0x4000 字节不压缩
        header = os.urandom(0x4000)
        body = b"X" * 0x4000
        original = header + body
        compressed = blz_compress_arm9(original)
        assert compressed is not None, "ARM9 应能压缩"
        decompressed = blz_decompress(compressed)
        assert decompressed == original

    def test_round_trip_overlay(self) -> None:
        # Overlay：无固定头部
        original = (b"hello_world_" * 100) + os.urandom(64)
        compressed = blz_compress_overlay(original)
        # 随机后缀可能让压缩率不够，但前段重复应能压缩
        if compressed is not None:
            decompressed = blz_decompress(compressed)
            assert decompressed == original

    def test_round_trip_random_data_returns_none_or_preserves(self) -> None:
        # 纯随机数据无法压缩，应返回 None
        original = os.urandom(256)
        compressed = blz_compress(original, min_uncompressed_region_size=0)
        # 随机数据压缩后通常 >= 原始大小，应返回 None
        if compressed is None:
            return
        # 若返回了压缩数据，解压后应等于原文
        decompressed = blz_decompress(compressed)
        assert decompressed == original

    def test_compressed_smaller_than_original(self) -> None:
        # 压缩后应更小
        original = b"\x00" * 4096
        compressed = blz_compress(original, min_uncompressed_region_size=0)
        assert compressed is not None
        assert len(compressed) < len(original)


# ----------------------------------------------------------------------
# 边界与异常
# ----------------------------------------------------------------------

class TestBLZCompressEdgeCases:
    def test_min_uncompressed_region_too_large_raises(self) -> None:
        with pytest.raises(ValueError, match="uncompressed region"):
            blz_compress(b"\x00" * 10, min_uncompressed_region_size=20)

    def test_compress_empty_returns_none(self) -> None:
        # 空数据无法压缩
        assert blz_compress(b"", min_uncompressed_region_size=0) is None

    def test_compress_arm9_short_data_returns_none(self) -> None:
        # ARM9 数据过短（< 0x4000）应报错
        with pytest.raises(ValueError, match="uncompressed region"):
            blz_compress_arm9(b"\x00" * 100)
