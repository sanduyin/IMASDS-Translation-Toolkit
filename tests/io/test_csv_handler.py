# tests/io/test_csv_handler.py
"""csv_handler 模块单元测试。"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.io.csv_handler import (
    CSV_ENCODING,
    is_csv_path,
    is_mail_file,
    write_text_csv,
    read_text_csv,
    write_arm9_csv,
    read_arm9_csv,
    read_translation_table,
)


# ----------------------------------------------------------------------
# is_csv_path / is_mail_file
# ----------------------------------------------------------------------

class TestFormatDetection:
    def test_is_csv_path_lowercase(self) -> None:
        assert is_csv_path("foo.csv") is True

    def test_is_csv_path_uppercase(self) -> None:
        assert is_csv_path("FOO.CSV") is True

    def test_is_csv_path_mixed_case(self) -> None:
        assert is_csv_path("Foo.CsV") is True

    def test_is_csv_path_non_csv(self) -> None:
        assert is_csv_path("foo.xlsx") is False
        assert is_csv_path("foo.txt") is False
        assert is_csv_path("foo") is False

    def test_is_csv_path_with_path_object(self) -> None:
        assert is_csv_path(Path("/tmp/test.csv")) is True
        assert is_csv_path(Path("/tmp/test.xlsx")) is False

    def test_is_mail_file_with_ml_underscore(self) -> None:
        assert is_mail_file("0082_ML_xxx.BBQ") is True

    def test_is_mail_file_starts_with_ml(self) -> None:
        assert is_mail_file("ML_xxx.BBQ") is True

    def test_is_mail_file_non_mail(self) -> None:
        assert is_mail_file("0001_A.bin") is False
        assert is_mail_file("regular_file.BBQ") is False

    def test_is_mail_file_empty_string(self) -> None:
        assert is_mail_file("") is False


# ----------------------------------------------------------------------
# write_text_csv / read_text_csv 往返
# ----------------------------------------------------------------------

class TestTextCsvRoundTrip:
    def test_write_creates_file_with_header(self, tmp_path: Path) -> None:
        path = tmp_path / "test.csv"
        count = write_text_csv(path, [{"Filename": "f.bin", "Text": "hello", "Translated_Text": "你好"}])
        assert count == 1
        assert path.exists()
        # 验证表头
        lines = path.read_text(encoding=CSV_ENCODING).splitlines()
        assert lines[0] == "Filename,Text,Translated_Text"

    def test_write_creates_parent_dir(self, tmp_path: Path) -> None:
        path = tmp_path / "subdir" / "test.csv"
        write_text_csv(path, [])
        assert path.exists()

    def test_round_trip_simple(self, tmp_path: Path) -> None:
        path = tmp_path / "simple.csv"
        original_rows = [
            {"Filename": "f1.bin", "Text": "Hello", "Translated_Text": "你好"},
            {"Filename": "f1.bin", "Text": "World", "Translated_Text": "世界"},
        ]
        write_text_csv(path, original_rows)
        read_rows = read_text_csv(path)
        assert len(read_rows) == 2
        assert read_rows[0]["Filename"] == "f1.bin"
        assert read_rows[0]["Text"] == "Hello"
        assert read_rows[0]["Translated_Text"] == "你好"
        assert read_rows[0]["Index"] == 0
        assert read_rows[1]["Index"] == 1

    def test_round_trip_empty_translation(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_trans.csv"
        original_rows = [
            {"Filename": "f.bin", "Text": "Hello", "Translated_Text": ""},
        ]
        write_text_csv(path, original_rows)
        read_rows = read_text_csv(path)
        assert read_rows[0]["Translated_Text"] == ""

    def test_round_trip_unicode(self, tmp_path: Path) -> None:
        path = tmp_path / "unicode.csv"
        original_rows = [
            {"Filename": "f.bin", "Text": "こんにちは", "Translated_Text": "你好世界"},
        ]
        write_text_csv(path, original_rows)
        read_rows = read_text_csv(path)
        assert read_rows[0]["Text"] == "こんにちは"
        assert read_rows[0]["Translated_Text"] == "你好世界"

    def test_round_trip_special_chars(self, tmp_path: Path) -> None:
        # 含逗号、引号的字段应正确处理（RFC 4180）
        path = tmp_path / "special.csv"
        original_rows = [
            {"Filename": "f.bin", "Text": 'He said "Hi"', "Translated_Text": "他说\"你好\""},
        ]
        write_text_csv(path, original_rows)
        read_rows = read_text_csv(path)
        assert read_rows[0]["Text"] == 'He said "Hi"'
        assert read_rows[0]["Translated_Text"] == "他说\"你好\""

    def test_round_trip_newline_in_field(self, tmp_path: Path) -> None:
        # 含换行的字段应正确处理（RFC 4180 quoted field）
        path = tmp_path / "newline.csv"
        original_rows = [
            {"Filename": "f.bin", "Text": "line1\nline2", "Translated_Text": "第一行\n第二行"},
        ]
        write_text_csv(path, original_rows)
        read_rows = read_text_csv(path)
        assert read_rows[0]["Text"] == "line1\nline2"
        assert read_rows[0]["Translated_Text"] == "第一行\n第二行"

    def test_read_empty_csv_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.csv"
        path.write_text("", encoding=CSV_ENCODING)
        assert read_text_csv(path) == []

    def test_read_csv_with_only_header(self, tmp_path: Path) -> None:
        path = tmp_path / "header_only.csv"
        path.write_text("Filename,Text,Translated_Text\n", encoding=CSV_ENCODING)
        assert read_text_csv(path) == []

    def test_write_empty_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "no_data.csv"
        count = write_text_csv(path, [])
        assert count == 0
        # 应只有表头
        lines = path.read_text(encoding=CSV_ENCODING).splitlines()
        assert len(lines) == 1
        assert lines[0] == "Filename,Text,Translated_Text"

    def test_line_terminator_is_lf(self, tmp_path: Path) -> None:
        # faraplay 兼容：行尾必须是 \n 而非 \r\n
        path = tmp_path / "lf.csv"
        write_text_csv(path, [{"Filename": "f", "Text": "a", "Translated_Text": "b"}])
        raw = path.read_bytes()
        assert b"\r\n" not in raw
        assert b"\n" in raw

    def test_encoding_is_utf8_no_bom(self, tmp_path: Path) -> None:
        path = tmp_path / "bom.csv"
        write_text_csv(path, [{"Filename": "f", "Text": "a", "Translated_Text": "b"}])
        raw = path.read_bytes()
        # UTF-8 BOM = EF BB BF
        assert not raw.startswith(b"\xef\xbb\xbf")


# ----------------------------------------------------------------------
# write_arm9_csv / read_arm9_csv 往返
# ----------------------------------------------------------------------

class TestArm9CsvRoundTrip:
    def test_write_creates_file_with_header(self, tmp_path: Path) -> None:
        path = tmp_path / "arm9.csv"
        count = write_arm9_csv(path, [{
            "Original_Text": "orig", "Translated_Text": "trans",
            "File": "arm9.bin", "Text_Offset": "0x1000", "Max_Bytes": 32,
        }])
        assert count == 1
        lines = path.read_text(encoding=CSV_ENCODING).splitlines()
        assert lines[0] == "Original_Text,Translated_Text,Filename,Text_Offset,Max_Bytes"

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "arm9_rt.csv"
        original_rows = [
            {"Original_Text": "Hello", "Translated_Text": "你好",
             "File": "arm9.bin", "Text_Offset": "0x1000", "Max_Bytes": 32},
            {"Original_Text": "World", "Translated_Text": "世界",
             "File": "overlay_0000.bin", "Text_Offset": "0x2000", "Max_Bytes": 16},
        ]
        write_arm9_csv(path, original_rows)
        read_rows = read_arm9_csv(path)
        assert len(read_rows) == 2
        assert read_rows[0]["Original_Text"] == "Hello"
        assert read_rows[0]["Translated_Text"] == "你好"
        assert read_rows[0]["File"] == "arm9.bin"
        assert read_rows[0]["Text_Offset"] == "0x1000"
        assert read_rows[0]["Max_Bytes"] == "32"

    def test_round_trip_max_bytes_as_int(self, tmp_path: Path) -> None:
        # Max_Bytes 可能是 int 或 str
        path = tmp_path / "mb.csv"
        write_arm9_csv(path, [{
            "Original_Text": "a", "Translated_Text": "b",
            "File": "f", "Text_Offset": "0x10", "Max_Bytes": 64,
        }])
        read_rows = read_arm9_csv(path)
        # 读取后应为字符串
        assert read_rows[0]["Max_Bytes"] == "64"


# ----------------------------------------------------------------------
# read_translation_table 统一入口
# ----------------------------------------------------------------------

class TestReadTranslationTable:
    def test_nonexistent_path_returns_empty(self, tmp_path: Path) -> None:
        # BBQ 模式
        result = read_translation_table(tmp_path / "no.csv", is_arm9=False)
        assert result == {}
        # ARM9 模式
        result = read_translation_table(tmp_path / "no.csv", is_arm9=True)
        assert result == []

    def test_csv_bbq_mode_groups_by_filename(self, tmp_path: Path) -> None:
        path = tmp_path / "bbq.csv"
        write_text_csv(path, [
            {"Filename": "f1.bin", "Text": "A", "Translated_Text": "甲"},
            {"Filename": "f1.bin", "Text": "B", "Translated_Text": "乙"},
            {"Filename": "f2.bin", "Text": "C", "Translated_Text": "丙"},
        ])
        result = read_translation_table(path, is_arm9=False)
        # 返回 dict[str, dict[int, str]]
        assert isinstance(result, dict)
        assert "f1.bin" in result
        assert "f2.bin" in result
        # f1.bin 应有索引 0 和 1
        assert result["f1.bin"][0] == "甲"
        assert result["f1.bin"][1] == "乙"
        # f2.bin 应有索引 0
        assert result["f2.bin"][0] == "丙"

    def test_csv_arm9_mode_filters_empty_translation(self, tmp_path: Path) -> None:
        path = tmp_path / "arm9.csv"
        write_arm9_csv(path, [
            {"Original_Text": "A", "Translated_Text": "甲",
             "File": "arm9.bin", "Text_Offset": "0x10", "Max_Bytes": 32},
            {"Original_Text": "B", "Translated_Text": "",  # 空译文应被过滤
             "File": "arm9.bin", "Text_Offset": "0x20", "Max_Bytes": 32},
        ])
        result = read_translation_table(path, is_arm9=True)
        # 返回 list[dict]，空译文被过滤
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["Translated_Text"] == "甲"

    def test_csv_bbq_mode_empty_marker_treated_as_empty(self, tmp_path: Path) -> None:
        # {EMPTY} 标记应被视为空译文
        path = tmp_path / "markers.csv"
        write_text_csv(path, [
            {"Filename": "f.bin", "Text": "A", "Translated_Text": "{EMPTY}"},
            {"Filename": "f.bin", "Text": "B", "Translated_Text": "正常"},
        ])
        result = read_translation_table(path, is_arm9=False)
        # {EMPTY} → ""
        assert result["f.bin"][0] == ""
        assert result["f.bin"][1] == "正常"
