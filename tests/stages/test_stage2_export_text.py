# tests/stages/test_stage2_export_text.py
"""stage2_export_text 模块核心函数测试。

只测试纯函数 extract_sort_key / extract_group_name。
"""
from __future__ import annotations

import pytest

from src.stage2_export_text import (
    extract_sort_key,
    extract_group_name,
)


# ----------------------------------------------------------------------
# extract_sort_key
# ----------------------------------------------------------------------

class TestExtractSortKey:
    def test_simple_number_prefix(self) -> None:
        assert extract_sort_key("0001_A.bin") == 1

    def test_multi_digit(self) -> None:
        assert extract_sort_key("0100_file.BBQ") == 100

    def test_number_in_middle(self) -> None:
        # 取第一个数字序列
        assert extract_sort_key("abc_0050_def.bin") == 50

    def test_no_number(self) -> None:
        # 无数字时应返回 999999（排序到末尾）
        assert extract_sort_key("no_number.BBQ") == 999999

    def test_empty_string(self) -> None:
        assert extract_sort_key("") == 999999

    def test_sort_order(self) -> None:
        # 验证排序键正确排序
        files = ["0010_b.bin", "0001_a.bin", "0100_c.bin", "no_num.bin"]
        sorted_files = sorted(files, key=extract_sort_key)
        assert sorted_files == ["0001_a.bin", "0010_b.bin", "0100_c.bin", "no_num.bin"]


# ----------------------------------------------------------------------
# extract_group_name
# ----------------------------------------------------------------------

class TestExtractGroupName:
    def test_strips_number_prefix(self) -> None:
        assert extract_group_name("0001_A.bin") == "A"

    def test_strips_mes_suffix(self) -> None:
        # "0082_MES.bin" → 去扩展名 → "0082_MES" → 去 ^\d+_ → "MES" → 去 _MES$ → "MES"（已是 "MES"，无 _MES 后缀）
        # 结果是 "MES"
        assert extract_group_name("0082_MES.bin") == "MES"

    def test_correct_mes_handling(self) -> None:
        # 0001_A_MES.bin → 去扩展名 → 0001_A_MES → 去 ^\d+_ → A_MES → 去 _MES$ → A
        assert extract_group_name("0001_A_MES.bin") == "A"

    def test_simple_name(self) -> None:
        # 0050_TALK.bin → 0050_TALK → TALK → TALK（无 _MES 后缀）
        assert extract_group_name("0050_TALK.bin") == "TALK"

    def test_case_insensitive_mes(self) -> None:
        # _mes / _Mes / _MES 都应被去除
        assert extract_group_name("0001_A_mes.bin") == "A"
        assert extract_group_name("0001_A_Mes.bin") == "A"

    def test_no_extension(self) -> None:
        # 无扩展名的文件
        assert extract_group_name("0001_ABC") == "ABC"

    def test_no_number_prefix(self) -> None:
        # 无数字前缀
        assert extract_group_name("ABC.bin") == "ABC"

    def test_preserves_inner_underscore(self) -> None:
        # 内部下划线应保留
        # 0001_A_B_C.bin → A_B_C
        assert extract_group_name("0001_A_B_C.bin") == "A_B_C"
