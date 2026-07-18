# tests/utils/test_binary_io.py
"""binary_io 模块单元测试。"""
from __future__ import annotations

import io
import struct

import pytest

from src.utils.binary_io import read_uint32, read_uint16, read_string_bytes, nlzss_compress


# ----------------------------------------------------------------------
# read_uint32 / read_uint16
# ----------------------------------------------------------------------

class TestReadUInt32:
    def test_read_uint32_bytes_offset_zero(self) -> None:
        data = struct.pack("<I", 0xDEADBEEF)
        assert read_uint32(data) == 0xDEADBEEF

    def test_read_uint32_bytes_with_offset(self) -> None:
        data = b"\x00\x00\x00\x00" + struct.pack("<I", 0x12345678)
        assert read_uint32(data, offset=4) == 0x12345678

    def test_read_uint32_bytearray(self) -> None:
        data = bytearray(struct.pack("<I", 0xCAFEF00D))
        assert read_uint32(data) == 0xCAFEF00D

    def test_read_uint32_memoryview(self) -> None:
        data = memoryview(struct.pack("<I", 0x11223344))
        assert read_uint32(data) == 0x11223344

    def test_read_uint32_file_object(self) -> None:
        data = struct.pack("<I", 0xABCDEF01)
        f = io.BytesIO(data)
        assert read_uint32(f) == 0xABCDEF01

    def test_read_uint32_file_object_with_seek(self) -> None:
        data = b"\x00" * 4 + struct.pack("<I", 0x99999999)
        f = io.BytesIO(data)
        assert read_uint32(f, offset=4) == 0x99999999

    def test_read_uint32_file_object_short_read_returns_zero(self) -> None:
        # 文件剩余不足 4 字节时返回 0
        f = io.BytesIO(b"\x01\x02")
        assert read_uint32(f) == 0


class TestReadUInt16:
    def test_read_uint16_bytes(self) -> None:
        data = struct.pack("<H", 0xBEEF)
        assert read_uint16(data) == 0xBEEF

    def test_read_uint16_bytes_with_offset(self) -> None:
        data = b"\x00\x00" + struct.pack("<H", 0xF00D)
        assert read_uint16(data, offset=2) == 0xF00D

    def test_read_uint16_bytearray(self) -> None:
        data = bytearray(struct.pack("<H", 0x4242))
        assert read_uint16(data) == 0x4242

    def test_read_uint16_memoryview(self) -> None:
        data = memoryview(struct.pack("<H", 0x1234))
        assert read_uint16(data) == 0x1234

    def test_read_uint16_file_object(self) -> None:
        f = io.BytesIO(struct.pack("<H", 0xABCD))
        assert read_uint16(f) == 0xABCD

    def test_read_uint16_file_short_read_returns_zero(self) -> None:
        f = io.BytesIO(b"\x01")
        assert read_uint16(f) == 0


# ----------------------------------------------------------------------
# read_string_bytes
# ----------------------------------------------------------------------

class TestReadStringBytes:
    def test_read_simple_string(self) -> None:
        f = io.BytesIO(b"hello\x00world")
        result = read_string_bytes(f, 0)
        assert result == b"hello"

    def test_read_preserves_file_position(self) -> None:
        # 文件指针应在调用前后保持不变
        f = io.BytesIO(b"XXXXhello\x00YYYY")
        f.seek(4)  # 指向 'h' 之前
        pos_before = f.tell()
        result = read_string_bytes(f, 4)
        pos_after = f.tell()
        assert result == b"hello"
        assert pos_before == pos_after == 4

    def test_read_empty_string_at_immediate_null(self) -> None:
        f = io.BytesIO(b"\x00abc")
        assert read_string_bytes(f, 0) == b""

    def test_read_string_at_eof(self) -> None:
        # 读取位置超过文件末尾时返回空 bytes
        f = io.BytesIO(b"abc\x00")
        assert read_string_bytes(f, 10) == b""

    def test_read_string_no_null_terminator(self) -> None:
        # 无 NUL 终止符时读到文件末尾停止
        f = io.BytesIO(b"abcdef")
        assert read_string_bytes(f, 0) == b"abcdef"

    def test_read_string_with_non_ascii_bytes(self) -> None:
        # cp932 字节流应原样读取
        f = io.BytesIO(b"\x82\xb1\x82\xf1\x82\xc9\x82\xbf\x82\xcd\x00")
        result = read_string_bytes(f, 0)
        assert result == b"\x82\xb1\x82\xf1\x82\xc9\x82\xbf\x82\xcd"


# ----------------------------------------------------------------------
# nlzss_compress
# ----------------------------------------------------------------------

class TestNLZSSCompress:
    def test_compress_empty_input(self) -> None:
        assert nlzss_compress(b"") == b""

    def test_compress_preserves_data_via_round_trip(self) -> None:
        # 压缩 → 用 ndspy 或本工具的解压器解压 → 应得到原始数据
        # 这里直接用 lz10_decompress 验证往返（两者均为 NDS LZ77 变体）
        from src.utils.lz10 import lz10_decompress
        original = b"Hello, World! Hello, World! Hello, World!" * 4
        compressed = nlzss_compress(original)
        # nlzss_compress 头部格式与 LZ10 一致（0x10 magic + 3字节长度 LE）
        decompressed = lz10_decompress(compressed)
        assert decompressed == original

    def test_compress_header_format(self) -> None:
        # 头部应为 (length << 8) | 0x10，4 字节小端
        original = b"abc"
        compressed = nlzss_compress(original)
        # length=3, header = (3 << 8) | 0x10 = 0x310
        expected_header = struct.pack("<I", 0x310)
        assert compressed[:4] == expected_header

    def test_compress_incompressible_data(self) -> None:
        # 随机数据应能正确压缩并往返
        from src.utils.lz10 import lz10_decompress
        import os
        original = os.urandom(256)
        compressed = nlzss_compress(original)
        decompressed = lz10_decompress(compressed)
        assert decompressed == original

    def test_compress_repetitive_data_is_smaller(self) -> None:
        # 高度重复数据应被压缩
        original = b"A" * 1024
        compressed = nlzss_compress(original)
        assert len(compressed) < len(original)
