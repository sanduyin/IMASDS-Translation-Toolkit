# tests/stages/test_stage2_export_arm9.py
"""stage2_export_arm9 模块核心函数测试。

只测试纯函数 get_base_address / analyze_chars / strict_filter。
"""
from __future__ import annotations

import pytest

from src.stage2_export_arm9 import (
    get_base_address,
    analyze_chars,
    strict_filter,
)


# ----------------------------------------------------------------------
# get_base_address
# ----------------------------------------------------------------------

class TestGetBaseAddress:
    def test_arm9_filename(self) -> None:
        assert get_base_address("arm9.bin", 0x02000000, {}) == 0x02000000

    def test_arm9_uppercase(self) -> None:
        assert get_base_address("ARM9.BIN", 0x02000000, {}) == 0x02000000

    def test_overlay_filename(self) -> None:
        bases = {0: 0x02004000, 1: 0x02008000}
        assert get_base_address("overlay_0000.bin", 0, bases) == 0x02004000
        assert get_base_address("overlay_0001.bin", 0, bases) == 0x02008000

    def test_overlay_uppercase(self) -> None:
        bases = {5: 0x02010000}
        assert get_base_address("OVERLAY_0005.BIN", 0, bases) == 0x02010000

    def test_unknown_filename_returns_none(self) -> None:
        assert get_base_address("unknown.bin", 0x02000000, {}) is None

    def test_overlay_not_in_bases_returns_none(self) -> None:
        # overlay ID 不在 bases 中应返回 None
        assert get_base_address("overlay_0099.bin", 0, {0: 0x1000}) is None


# ----------------------------------------------------------------------
# analyze_chars
# ----------------------------------------------------------------------

class TestAnalyzeChars:
    def test_empty_string(self) -> None:
        stats = analyze_chars("")
        assert stats["length"] == 0
        assert stats["hiragana"] == 0
        assert stats["kanji"] == 0

    def test_pure_ascii(self) -> None:
        stats = analyze_chars("Hello123")
        assert stats["length"] == 8
        assert stats["ascii_letter"] == 5
        assert stats["ascii_digit"] == 3
        assert stats["jp_total"] == 0

    def test_hiragana(self) -> None:
        stats = analyze_chars("こんにちは")
        assert stats["length"] == 5
        assert stats["hiragana"] == 5
        assert stats["kana"] == 5
        assert stats["jp_total"] == 5

    def test_katakana(self) -> None:
        stats = analyze_chars("カタカナ")
        assert stats["katakana"] == 4
        assert stats["kana"] == 4

    def test_kanji(self) -> None:
        stats = analyze_chars("日本語")
        assert stats["kanji"] == 3
        assert stats["jp_total"] == 3

    def test_mixed(self) -> None:
        stats = analyze_chars("Hello世界123")
        assert stats["ascii_letter"] == 5
        assert stats["ascii_digit"] == 3
        assert stats["kanji"] == 2

    def test_newline_excluded_from_length(self) -> None:
        stats = analyze_chars("AB\nCD")
        # 换行不计入 length
        assert stats["length"] == 4

    def test_fullwidth(self) -> None:
        # 全角字符
        stats = analyze_chars("ＡＢＣ")
        assert stats["fullwidth"] == 3

    def test_halfwidth_kana(self) -> None:
        # 半角片假名 ｱｲｳ
        stats = analyze_chars("ｱｲｳ")
        assert stats["hw_kana"] == 3


# ----------------------------------------------------------------------
# strict_filter
# ----------------------------------------------------------------------

class TestStrictFilter:
    def test_empty_string_filtered(self) -> None:
        assert strict_filter("") is False

    def test_sdk_prefix_filtered(self) -> None:
        assert strict_filter("[SDK version 5.5") is False
        assert strict_filter("SDK+ something") is False

    def test_windows_path_filtered(self) -> None:
        assert strict_filter("C:\\project\\src\\main.c") is False

    def test_unix_path_filtered(self) -> None:
        assert strict_filter("/home/user/project/file.h") is False

    def test_source_file_extension_filtered(self) -> None:
        assert strict_filter("something.c") is False
        assert strict_filter("something.h") is False
        assert strict_filter("something.cpp") is False

    def test_short_ascii_filtered(self) -> None:
        # 长度 < 4 的纯 ASCII 被过滤
        assert strict_filter("AB") is False

    def test_halfwidth_kana_filtered(self) -> None:
        # 含半角片假名的文本被过滤
        assert strict_filter("ｱｲｳｴ") is False

    def test_japanese_text_passes(self) -> None:
        # 含日文字符的文本应通过
        assert strict_filter("こんにちは") is True
        assert strict_filter("日本語のテスト") is True

    def test_long_ascii_passes(self) -> None:
        # 长度 >= 4 的纯 ASCII 文本应通过（除非符号比例过高）
        assert strict_filter("Hello World") is True

    def test_high_symbol_ratio_filtered(self) -> None:
        # 符号比例 > 30% 的纯 ASCII 被过滤
        # "AB.CD-EF": 6 字母 + 2 符号 = 25% < 30%，应通过
        assert strict_filter("AB.CD-EF") is True
        # "...!!!,,,": 全符号，比例 100% > 30%，应被过滤
        assert strict_filter("...!!!,,,") is False
