# tests/utils/test_text_encoder.py
"""text_encoder 模块单元测试。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.utils.text_encoder import (
    PROTECTED_RANGES,
    is_protected,
    load_mapping,
    text_to_bytes,
)


# ----------------------------------------------------------------------
# is_protected
# ----------------------------------------------------------------------

class TestIsProtected:
    def test_ascii_space_is_protected(self) -> None:
        assert is_protected(0x20) is True

    def test_ascii_uppercase_is_protected(self) -> None:
        # 'A' = 0x41, 在 [0x20, 0xDF] 内
        assert is_protected(0x41) is True

    def test_ascii_lowercase_is_protected(self) -> None:
        # 'z' = 0x7A, 在 [0x20, 0xDF] 内
        assert is_protected(0x7A) is True

    def test_halfwidth_katakana_is_protected(self) -> None:
        # 半角片假名区域 0xA1-0xDF
        assert is_protected(0xB1) is True  # ｱ
        assert is_protected(0xDF) is True  # 边界

    def test_below_protected_range_is_not_protected(self) -> None:
        assert is_protected(0x1F) is False
        assert is_protected(0x00) is False

    def test_above_first_range_below_second_is_not_protected(self) -> None:
        # 0xE0 - 0x813F 之间不在保护区内
        assert is_protected(0xE0) is False
        assert is_protected(0x1000) is False

    def test_fullwidth_punctuation_is_protected(self) -> None:
        # 0x8140 起始，全角标点
        assert is_protected(0x8140) is True
        assert is_protected(0x8240) is True

    def test_fullwidth_upper_boundary(self) -> None:
        # 0x8799 是第二区间末尾
        assert is_protected(0x8799) is True
        assert is_protected(0x8800) is False

    def test_protected_ranges_constant_matches_doc(self) -> None:
        # 验证 PROTECTED_RANGES 常量符合注入器约定
        assert PROTECTED_RANGES == [(0x20, 0xDF), (0x8140, 0x8799)]


# ----------------------------------------------------------------------
# load_mapping
# ----------------------------------------------------------------------

class TestLoadMapping:
    def test_load_valid_mapping(self, tmp_path: Path) -> None:
        mapping = {"あ": 0x82A0, "い": 0x82A2, "A": 0x41}
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(json.dumps(mapping), encoding="utf-8")
        result = load_mapping(mapping_file)
        assert result == mapping

    def test_load_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        result = load_mapping(tmp_path / "nonexistent.json")
        assert result == {}

    def test_load_empty_mapping_file(self, tmp_path: Path) -> None:
        mapping_file = tmp_path / "empty.json"
        mapping_file.write_text("{}", encoding="utf-8")
        result = load_mapping(mapping_file)
        assert result == {}

    def test_load_accepts_string_path(self, tmp_path: Path) -> None:
        mapping = {"X": 0x58}
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps(mapping), encoding="utf-8")
        # 传入字符串而非 Path 对象
        result = load_mapping(str(mapping_file))
        assert result == mapping


# ----------------------------------------------------------------------
# text_to_bytes
# ----------------------------------------------------------------------

class TestTextToBytes:
    def test_single_byte_character(self) -> None:
        mapping = {"A": 0x41}
        result = text_to_bytes("A", mapping)
        # 0x41 + NUL
        assert result == bytearray([0x41, 0x00])

    def test_double_byte_character(self) -> None:
        mapping = {"あ": 0x82A0}
        result = text_to_bytes("あ", mapping)
        # big-endian 双字节 + NUL
        assert result == bytearray([0x82, 0xA0, 0x00])

    def test_mixed_single_and_double_byte(self) -> None:
        mapping = {"A": 0x41, "あ": 0x82A0}
        result = text_to_bytes("Aあ", mapping)
        assert result == bytearray([0x41, 0x82, 0xA0, 0x00])

    def test_unmapped_char_replaced_with_question_mark(self) -> None:
        mapping = {}
        result = text_to_bytes("X", mapping)
        # 未映射字符 → 0x3F ('?') + NUL
        assert result == bytearray([0x3F, 0x00])

    def test_newline_converted_to_0x0a(self) -> None:
        mapping = {"A": 0x41}
        result = text_to_bytes("A\nA", mapping)
        assert result == bytearray([0x41, 0x0A, 0x41, 0x00])

    def test_crlf_normalized_to_lf(self) -> None:
        mapping = {"A": 0x41}
        result = text_to_bytes("A\r\nA", mapping)
        # \r\n → \n → 0x0A
        assert result == bytearray([0x41, 0x0A, 0x41, 0x00])

    def test_lone_cr_normalized_to_lf(self) -> None:
        mapping = {"A": 0x41}
        result = text_to_bytes("A\rA", mapping)
        assert result == bytearray([0x41, 0x0A, 0x41, 0x00])

    def test_empty_string_returns_just_null(self) -> None:
        result = text_to_bytes("", {})
        assert result == bytearray([0x00])

    def test_none_input_treated_as_empty(self) -> None:
        result = text_to_bytes(None, {})
        assert result == bytearray([0x00])

    def test_always_ends_with_null_terminator(self) -> None:
        mapping = {"A": 0x41}
        result = text_to_bytes("AAA", mapping)
        assert result[-1] == 0x00
        assert len(result) == 4  # 3 字符 + NUL

    def test_text_to_bytes_returns_bytearray_type(self) -> None:
        result = text_to_bytes("A", {"A": 0x41})
        assert isinstance(result, bytearray)
