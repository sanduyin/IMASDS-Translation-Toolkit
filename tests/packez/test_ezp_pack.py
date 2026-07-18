# tests/packez/test_ezp_pack.py
"""packez/ezp_pack 模块单元测试（纯函数部分）。"""
from __future__ import annotations

import pytest

from src.packez.ezp_pack import (
    do_not_compress,
    padding_alignment,
    pad_to_alignment,
    _next_multiple_of,
    _indent,
    _read_cstring,
    _strip_dir_marker,
    _NSBCA_NO_COMPRESS_PREFIXES,
)


# ----------------------------------------------------------------------
# do_not_compress
# ----------------------------------------------------------------------

class TestDoNotCompress:
    def test_s14_excluded(self) -> None:
        assert do_not_compress("test.S14") is True

    def test_sss_excluded(self) -> None:
        assert do_not_compress("test.SSS") is True

    def test_nftr_excluded(self) -> None:
        assert do_not_compress("test.NFTR") is True

    def test_bin_excluded(self) -> None:
        assert do_not_compress("test.BIN") is True

    def test_idx_excluded(self) -> None:
        assert do_not_compress("test.IDX") is True

    def test_nsbca_with_excluded_prefix(self) -> None:
        for prefix in ("AI_", "ERI_", "RYO_", "DNC_", "ACT_"):
            assert do_not_compress(f"{prefix}file.NSBCA") is True

    def test_nsbca_with_other_prefix_not_excluded(self) -> None:
        assert do_not_compress("OTHER_file.NSBCA") is False

    def test_normal_file_not_excluded(self) -> None:
        assert do_not_compress("test.BBQ") is False
        assert do_not_compress("data.DAT") is False

    def test_case_sensitive(self) -> None:
        # 后缀匹配应大小写敏感（与 Rust ends_with 一致）
        assert do_not_compress("test.s14") is False
        assert do_not_compress("test.nftr") is False


# ----------------------------------------------------------------------
# padding_alignment
# ----------------------------------------------------------------------

class TestPaddingAlignment:
    def test_nclr_alignment(self) -> None:
        assert padding_alignment("test.NCLR") == 0x10

    def test_nscr_alignment(self) -> None:
        assert padding_alignment("test.NSCR") == 0x10

    def test_ncer_alignment(self) -> None:
        assert padding_alignment("test.NCER") == 0x10

    def test_default_alignment(self) -> None:
        assert padding_alignment("test.BIN") == 0x20
        assert padding_alignment("test.BBQ") == 0x20
        assert padding_alignment("anything.DAT") == 0x20


# ----------------------------------------------------------------------
# pad_to_alignment
# ----------------------------------------------------------------------

class TestPadToAlignment:
    def test_already_aligned(self) -> None:
        buf = bytearray(b"\x00" * 16)
        result = pad_to_alignment(buf, 16)
        assert result == 16
        assert len(buf) == 16  # 未填充

    def test_pad_to_next_multiple(self) -> None:
        buf = bytearray(b"\x00" * 17)
        result = pad_to_alignment(buf, 16)
        assert result == 32
        assert len(buf) == 32  # 填充了 15 字节

    def test_pad_with_custom_byte(self) -> None:
        buf = bytearray(b"\x00" * 1)
        pad_to_alignment(buf, 4, pad_byte=0xFF)
        assert len(buf) == 4
        assert buf[1] == 0xFF
        assert buf[2] == 0xFF
        assert buf[3] == 0xFF

    def test_empty_buf(self) -> None:
        buf = bytearray()
        result = pad_to_alignment(buf, 16)
        assert result == 0
        assert len(buf) == 0

    def test_alignment_1_no_padding(self) -> None:
        buf = bytearray(b"\x00" * 5)
        result = pad_to_alignment(buf, 1)
        assert result == 5
        assert len(buf) == 5


# ----------------------------------------------------------------------
# _next_multiple_of
# ----------------------------------------------------------------------

class TestNextMultipleOf:
    def test_already_multiple(self) -> None:
        assert _next_multiple_of(16, 4) == 16

    def test_next_multiple(self) -> None:
        assert _next_multiple_of(17, 4) == 20

    def test_zero(self) -> None:
        assert _next_multiple_of(0, 4) == 0

    def test_one(self) -> None:
        assert _next_multiple_of(1, 4) == 4


# ----------------------------------------------------------------------
# _indent
# ----------------------------------------------------------------------

class TestIndent:
    def test_zero(self) -> None:
        assert _indent(0) == ""

    def test_one(self) -> None:
        assert _indent(1) == "    "

    def test_three(self) -> None:
        assert _indent(3) == "            "  # 12 空格


# ----------------------------------------------------------------------
# _read_cstring
# ----------------------------------------------------------------------

class TestReadCString:
    def test_simple_string(self) -> None:
        buf = b"hello\x00world"
        assert _read_cstring(buf, 0) == "hello"

    def test_with_offset(self) -> None:
        buf = b"XXXXhello\x00rest"
        assert _read_cstring(buf, 4) == "hello"

    def test_empty_string_at_immediate_null(self) -> None:
        buf = b"\x00abc"
        assert _read_cstring(buf, 0) == ""

    def test_string_at_end_of_buffer(self) -> None:
        # 无 NUL 终止符时读到末尾
        buf = b"abcdef"
        assert _read_cstring(buf, 0) == "abcdef"

    def test_out_of_bounds_raises(self) -> None:
        with pytest.raises(ValueError, match="越界"):
            _read_cstring(b"abc", 10)

    def test_negative_offset_raises(self) -> None:
        with pytest.raises(ValueError, match="越界"):
            _read_cstring(b"abc", -1)

    def test_works_with_bytearray(self) -> None:
        buf = bytearray(b"hello\x00")
        assert _read_cstring(buf, 0) == "hello"

    def test_works_with_memoryview(self) -> None:
        buf = memoryview(b"hello\x00")
        assert _read_cstring(buf, 0) == "hello"

    def test_utf8_multibyte(self) -> None:
        # 中文 UTF-8 编码
        buf = "你好".encode("utf-8") + b"\x00"
        assert _read_cstring(buf, 0) == "你好"


# ----------------------------------------------------------------------
# _strip_dir_marker
# ----------------------------------------------------------------------

class TestStripDirMarker:
    def test_begin_marker(self) -> None:
        kind, name = _strip_dir_marker("FOLDER_BEGIN")
        assert kind == "begin"
        assert name == "FOLDER"

    def test_start_marker(self) -> None:
        kind, name = _strip_dir_marker("FOLDER_START")
        assert kind == "begin"
        assert name == "FOLDER"

    def test_end_marker(self) -> None:
        kind, name = _strip_dir_marker("FOLDER_END")
        assert kind == "end"
        assert name == "FOLDER"

    def test_non_marker(self) -> None:
        kind, name = _strip_dir_marker("regular_file.BIN")
        assert kind is None
        assert name is None

    def test_empty_string(self) -> None:
        kind, name = _strip_dir_marker("")
        assert kind is None
        assert name is None
