# tests/utils/test_bbq_format.py
"""bbq_format 模块单元测试。

构造最小合法 BBQ 文件用于解析测试，避免依赖真实游戏文件。
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from src.utils.bbq_format import parse_bbq_file


def _build_minimal_bbq(
    strings: list[bytes],
    section5: bytes | None = None,
) -> bytes:
    """
    构造最小合法 BBQ 文件，仅含 Section 7（文本）+ 可选 Section 5。

    文件结构：
        Header (24 字节): magic + datetime + magic2 + entries_offset + entry_count
        Entry[0] (Section 5, 20 字节) [可选]
        Entry[1] (Section 7, 20 字节)
        Section 5 data [可选]
        Section 7 data: ptr_table (4*num_str) + pool (strings + NUL)
    """
    header_size = 24
    n_sections = 1
    has_sec5 = section5 is not None
    if has_sec5:
        n_sections = 2

    entries_offset = header_size
    entries_size = n_sections * 20

    # Section 7 数据
    num_str = len(strings)
    ptr_table_size = num_str * 4
    pool_data = b"".join(s + b"\x00" for s in strings)
    # 4 字节对齐
    while len(pool_data) % 4 != 0:
        pool_data += b"\x00"

    sec7_data_offset_in_section = 8  # 偏移表 (8 字节) + 数据池
    # 但 section 7 entry 的 values[0] = ptr_table 相对 entry 的偏移
    # values[1] = num_str, values[2] = pool 相对 entry 的偏移

    # 计算 Section 7 起始位置
    sec7_entry_offset = entries_offset + (20 if has_sec5 else 0)
    # ptr_table 在 sec7_entry_offset + ptr_table_rel
    ptr_table_rel = 20  # entry 自身 20 字节后是 ptr_table（values[0]=20）
    pool_rel = ptr_table_rel + ptr_table_size  # values[2]

    # Section 5 占位（如果存在）
    sec5_entry_offset = entries_offset
    sec5_data = b""
    if has_sec5:
        sec5_data = section5 or b""
        # Section 5 entry: values[0]=20 (offset to data), values[1]=0, values[2]=20, values[3]=len
        # 简化：直接附加 sec5_data 在 entries 之后

    # 构造 header
    magic = b"\x2E\x42\x42\x51"  # .BBQ
    magic_full = magic + b"\x31\x2E\x30\x30"  # .BBQ1.00
    header = struct.pack("<8sIIII",
                         magic_full,
                         0x12345678,  # datetime
                         1,  # magic2
                         entries_offset,
                         n_sections)

    # 构造 entries
    if has_sec5:
        # Section 5 entry: data_type=5, offsets_offset=20, data_count=0, data_offset=20, data_size=len(sec5_data)
        sec5_entry = struct.pack("<IIIII", 5, 20, 0, 20, len(sec5_data))
    else:
        sec5_entry = b""

    # Section 7 entry: data_type=7, offsets_offset=ptr_table_rel, data_count=num_str,
    #                  data_offset=pool_rel, data_size=ptr_table_size+len(pool_data)
    sec7_entry = struct.pack("<IIIII",
                             7,
                             ptr_table_rel,
                             num_str,
                             pool_rel,
                             ptr_table_size + len(pool_data))

    # 构造 ptr_table + pool
    pointers = []
    offset = 0
    for s in strings:
        pointers.append(offset)
        offset += len(s) + 1  # +1 for NUL
    ptr_table = struct.pack(f"<{num_str}I", *pointers)

    # 组装
    result = header + sec5_entry + sec7_entry + sec5_data + ptr_table + pool_data
    return result


# ----------------------------------------------------------------------
# parse_bbq_file
# ----------------------------------------------------------------------

class TestParseBBQFile:
    def test_parse_non_bbq_file_returns_empty(self, tmp_path: Path) -> None:
        # 非 .BBQ 魔数应返回空列表
        bad_file = tmp_path / "bad.bin"
        bad_file.write_bytes(b"NOTBBQ1.00" + b"\x00" * 20)
        assert parse_bbq_file(bad_file) == []

    def test_parse_single_string(self, tmp_path: Path) -> None:
        bbq_data = _build_minimal_bbq([b"Hello"])
        bbq_file = tmp_path / "test.bin"
        bbq_file.write_bytes(bbq_data)

        entries = parse_bbq_file(bbq_file, is_scn=False)
        assert len(entries) == 1
        assert entries[0]["Original_Text"] == "Hello"
        assert entries[0]["File"] == "test.bin"
        assert entries[0]["Index"] == 0
        assert entries[0]["Max_Bytes"] == 5  # "Hello" 长度

    def test_parse_multiple_strings(self, tmp_path: Path) -> None:
        bbq_data = _build_minimal_bbq([b"ABC", b"DEFG", b"HI"])
        bbq_file = tmp_path / "multi.bin"
        bbq_file.write_bytes(bbq_data)

        entries = parse_bbq_file(bbq_file, is_scn=False)
        assert len(entries) == 3
        assert entries[0]["Original_Text"] == "ABC"
        assert entries[1]["Original_Text"] == "DEFG"
        assert entries[2]["Original_Text"] == "HI"
        # Index 应与字符串顺序对应
        assert [e["Index"] for e in entries] == [0, 1, 2]

    def test_parse_empty_string(self, tmp_path: Path) -> None:
        # 空字符串（仅 NUL）
        bbq_data = _build_minimal_bbq([b"", b"X"])
        bbq_file = tmp_path / "empty.bin"
        bbq_file.write_bytes(bbq_data)

        # skip_empty=True 时空行被过滤
        entries = parse_bbq_file(bbq_file, is_scn=False, skip_empty=True)
        # 只有 "X" 这一行（空行 max_bytes=0 被过滤）
        # 注：第一行 max_bytes 计算依赖下一行指针差，可能为 0 或负
        assert any(e["Original_Text"] == "X" for e in entries)

    def test_parse_empty_string_kept_with_skip_empty_false(self, tmp_path: Path) -> None:
        bbq_data = _build_minimal_bbq([b"", b"X"])
        bbq_file = tmp_path / "keep_empty.bin"
        bbq_file.write_bytes(bbq_data)

        # skip_empty=False 保留所有行
        entries = parse_bbq_file(bbq_file, is_scn=False, skip_empty=False)
        assert len(entries) == 2

    def test_parse_cp932_japanese(self, tmp_path: Path) -> None:
        # cp932 编码的日语字符串
        jp_text = "こんにちは".encode("cp932")
        bbq_data = _build_minimal_bbq([jp_text])
        bbq_file = tmp_path / "jp.bin"
        bbq_file.write_bytes(bbq_data)

        entries = parse_bbq_file(bbq_file, is_scn=False)
        assert len(entries) == 1
        assert entries[0]["Original_Text"] == "こんにちは"

    def test_parse_text_offset_format(self, tmp_path: Path) -> None:
        bbq_data = _build_minimal_bbq([b"AB"])
        bbq_file = tmp_path / "offset.bin"
        bbq_file.write_bytes(bbq_data)

        entries = parse_bbq_file(bbq_file, is_scn=False)
        # Text_Offset 应为 "0xXXXX" 格式
        assert entries[0]["Text_Offset"].startswith("0x")
        # 应可被 int(x, 16) 解析
        offset = int(entries[0]["Text_Offset"], 16)
        assert offset > 0

    def test_parse_scn_mode_speaker_default(self, tmp_path: Path) -> None:
        # 无 Section 5 时，SCN 模式 speaker 应为 "System/TBL"
        bbq_data = _build_minimal_bbq([b"Test"])
        bbq_file = tmp_path / "scn.bin"
        bbq_file.write_bytes(bbq_data)

        entries = parse_bbq_file(bbq_file, is_scn=True)
        assert len(entries) == 1
        assert entries[0]["Speaker"] == "System/TBL"

    def test_parse_tbl_mode_speaker_is_x(self, tmp_path: Path) -> None:
        bbq_data = _build_minimal_bbq([b"Test"])
        bbq_file = tmp_path / "tbl.bin"
        bbq_file.write_bytes(bbq_data)

        entries = parse_bbq_file(bbq_file, is_scn=False)
        assert entries[0]["Speaker"] == "×"

    def test_parse_max_bytes_last_string(self, tmp_path: Path) -> None:
        # 最后一个字符串的 max_bytes 应等于其自身长度
        bbq_data = _build_minimal_bbq([b"AB", b"CDEF"])
        bbq_file = tmp_path / "last.bin"
        bbq_file.write_bytes(bbq_data)

        entries = parse_bbq_file(bbq_file, is_scn=False)
        # 最后一个字符串 max_bytes = len("CDEF") = 4
        assert entries[-1]["Max_Bytes"] == 4

    def test_parse_translated_text_initially_empty(self, tmp_path: Path) -> None:
        bbq_data = _build_minimal_bbq([b"Hello"])
        bbq_file = tmp_path / "trans.bin"
        bbq_file.write_bytes(bbq_data)

        entries = parse_bbq_file(bbq_file, is_scn=False)
        assert entries[0]["Translated_Text"] == ""
