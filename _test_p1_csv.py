# _test_p1_csv.py
"""
P1-4/P1-5 CSV 翻译表 roundtrip 测试。

测试覆盖：
    1. CSV 文本 roundtrip（BBQ 解析 → write_text_csv → read_text_csv → 校验）
    2. RFC 4180 边界用例（逗号/引号/换行/CR）
    3. 邮件文件识别与 _ML.csv 分组
    4. ARM9 CSV roundtrip
    5. 行顺序 = 字符串索引（skip_empty=False 保证）
    6. 迁移脚本 --dry-run（若 xlsx 不存在则跳过）

运行：python _test_p1_csv.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.bbq_format import parse_bbq_file
from src.io.csv_handler import (
    write_text_csv, read_text_csv,
    write_arm9_csv, read_arm9_csv,
    read_translation_table,
    is_csv_path, is_mail_file,
)

# 测试用 BBQ 文件
TBL_DIR = PROJECT_ROOT / "game_data" / "1_Extracted" / "TBL"
SCN_DIR = PROJECT_ROOT / "game_data" / "1_Extracted" / "SCN"

TEST_BBQ_FILES = [
    (TBL_DIR / "0002_BGMTABLE.BBQ", False),   # 普通系统表
    (TBL_DIR / "0082_ML_AIH_B01_MAIL01.BBQ", False),  # 邮件文件
    (SCN_DIR / "0062_GAMETITLE.BBQ", True),   # SCN 剧情文件
]

PASS = 0
FAIL = 0


def ok(name: str):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}")


def fail(name: str, detail: str = ""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name} {detail}")


# ----------------------------------------------------------------------
# 测试 1：CSV 文本 roundtrip
# ----------------------------------------------------------------------

def test_text_csv_roundtrip():
    print("\n=== 测试 1：CSV 文本 roundtrip ===")

    for bbq_path, is_scn in TEST_BBQ_FILES:
        if not bbq_path.exists():
            print(f"  ⏭️  跳过（文件不存在）：{bbq_path.name}")
            continue

        # 1. 解析 BBQ（skip_empty=False 保留空行，确保行顺序=索引）
        entries = parse_bbq_file(bbq_path, is_scn=is_scn, skip_empty=False)
        if not entries:
            # 空文件（如占位符）跳过，不算失败
            print(f"  ⏭️  跳过（无文本）：{bbq_path.name}")
            continue

        # 2. 构造 CSV 行
        filename = bbq_path.name
        csv_rows = [{
            "Filename": filename,
            "Text": e["Original_Text"],
            "Translated_Text": e.get("Translated_Text", ""),
        } for e in entries]

        # 3. 写入临时 CSV
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / f"{bbq_path.stem}.csv"
            count = write_text_csv(csv_path, csv_rows)

            if count != len(entries):
                fail(f"roundtrip {bbq_path.name}",
                     f"(行数不符: 写入 {count} ≠ 原始 {len(entries)})")
                continue

            # 4. 读回 CSV
            read_rows = read_text_csv(csv_path)
            if len(read_rows) != len(entries):
                fail(f"roundtrip {bbq_path.name}",
                     f"(读取行数 {len(read_rows)} ≠ {len(entries)})")
                continue

            # 5. 逐行校验 Text 字段
            mismatch = 0
            for i, (orig, read) in enumerate(zip(entries, read_rows)):
                if orig["Original_Text"] != read["Text"]:
                    if mismatch < 3:  # 只打印前 3 个不匹配
                        print(f"     行 {i}: 原始 {orig['Original_Text']!r} ≠ 读取 {read['Text']!r}")
                    mismatch += 1

            if mismatch == 0:
                ok(f"roundtrip {bbq_path.name} ({len(entries)} 行)")
            else:
                fail(f"roundtrip {bbq_path.name}",
                     f"({mismatch}/{len(entries)} 行不匹配)")


# ----------------------------------------------------------------------
# 测试 2：RFC 4180 边界用例
# ----------------------------------------------------------------------

def test_rfc4180_edge_cases():
    print("\n=== 测试 2：RFC 4180 边界用例（逗号/引号/换行）===")

    # 构造含特殊字符的测试数据
    test_cases = [
        # (描述, 原文, 译文)
        ("普通文本", "Hello", "你好"),
        ("含逗号", "a,b,c", "甲,乙,丙"),
        ("含引号", 'say "hi"', '说"嗨"'),
        ("含换行", "line1\nline2", "第一行\n第二行"),
        ("含CR", "col1\r\ncol2", "列1\r\n列2"),
        ("含逗号+引号+换行", 'a,"b\nc', '甲,"乙\n丙'),
        ("空字符串", "", ""),
        ("仅空格", " ", " "),
        ("日文+特殊", "こんにちは、世界", "你好，\"世界\""),
        ("tab字符", "tab\there", "制表\t此处"),
    ]

    csv_rows = [{
        "Filename": "TEST_EDGE.BBQ",
        "Text": tc[1],
        "Translated_Text": tc[2],
    } for tc in test_cases]

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "edge.csv"
        write_text_csv(csv_path, csv_rows)
        read_rows = read_text_csv(csv_path)

        if len(read_rows) != len(test_cases):
            fail("RFC 4180 行数", f"({len(read_rows)} ≠ {len(test_cases)})")
            return

        all_match = True
        for i, (desc, orig_text, orig_trans) in enumerate(test_cases):
            if read_rows[i]["Text"] != orig_text:
                print(f"     {desc}: Text 不匹配 {read_rows[i]['Text']!r} ≠ {orig_text!r}")
                all_match = False
            if read_rows[i]["Translated_Text"] != orig_trans:
                print(f"     {desc}: Translated_Text 不匹配 {read_rows[i]['Translated_Text']!r} ≠ {orig_trans!r}")
                all_match = False

        if all_match:
            ok(f"RFC 4180 边界用例 ({len(test_cases)} 例)")
        else:
            fail("RFC 4180 边界用例")


# ----------------------------------------------------------------------
# 测试 3：邮件文件识别
# ----------------------------------------------------------------------

def test_mail_file_detection():
    print("\n=== 测试 3：邮件文件识别 ===")

    cases = [
        ("0082_ML_AIH_B01_MAIL01.BBQ", True),
        ("0119_ML_SYS_MAIL_FST_01.BBQ", True),
        ("ML_xxx.BBQ", True),
        ("0002_BGMTABLE.BBQ", False),
        ("0000_JUMP.BBQ", False),
        ("MAILTABLE.BBQ", False),  # 包含 MAIL 但不含 _ML_
    ]

    all_ok = True
    for filename, expected in cases:
        result = is_mail_file(filename)
        if result != expected:
            print(f"     {filename}: 期望 {expected}，实际 {result}")
            all_ok = False

    if all_ok:
        ok(f"邮件文件识别 ({len(cases)} 例)")
    else:
        fail("邮件文件识别")


# ----------------------------------------------------------------------
# 测试 4：ARM9 CSV roundtrip
# ----------------------------------------------------------------------

def test_arm9_csv_roundtrip():
    print("\n=== 测试 4：ARM9 CSV roundtrip ===")

    test_rows = [
        {
            "Original_Text": "New Game",
            "Translated_Text": "新游戏",
            "File": "arm9.bin",
            "Text_Offset": "0x1A2B",
            "Max_Bytes": 16,
        },
        {
            "Original_Text": "Continue",
            "Translated_Text": "继续",
            "File": "overlay_0000.bin",
            "Text_Offset": "0x3C4D",
            "Max_Bytes": 12,
        },
        {
            "Original_Text": 'has "quotes"',
            "Translated_Text": "含\"引号\"",
            "File": "overlay_0001.bin",
            "Text_Offset": "0x5E6F",
            "Max_Bytes": 20,
        },
    ]

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "arm9_test.csv"
        write_arm9_csv(csv_path, test_rows)
        read_rows = read_arm9_csv(csv_path)

        if len(read_rows) != len(test_rows):
            fail("ARM9 roundtrip", f"(行数 {len(read_rows)} ≠ {len(test_rows)})")
            return

        all_match = True
        for orig, read in zip(test_rows, read_rows):
            if orig["Original_Text"] != read["Original_Text"]:
                print(f"     Original_Text: {read['Original_Text']!r} ≠ {orig['Original_Text']!r}")
                all_match = False
            if orig["Translated_Text"] != read["Translated_Text"]:
                print(f"     Translated_Text: {read['Translated_Text']!r} ≠ {orig['Translated_Text']!r}")
                all_match = False
            if orig["File"] != read["File"]:
                print(f"     File: {read['File']!r} ≠ {orig['File']!r}")
                all_match = False
            if orig["Text_Offset"] != read["Text_Offset"]:
                print(f"     Text_Offset: {read['Text_Offset']!r} ≠ {orig['Text_Offset']!r}")
                all_match = False

        if all_match:
            ok(f"ARM9 CSV roundtrip ({len(test_rows)} 行)")
        else:
            fail("ARM9 CSV roundtrip")


# ----------------------------------------------------------------------
# 测试 5：read_translation_table 统一入口
# ----------------------------------------------------------------------

def test_read_translation_table():
    print("\n=== 测试 5：read_translation_table 统一入口 ===")

    # 构造测试数据：2 个文件，各 3 条（验证组内索引从 0 开始）
    csv_rows = [
        {"Filename": "FILE_A.BBQ", "Text": "text_a_0", "Translated_Text": "译_a_0"},
        {"Filename": "FILE_A.BBQ", "Text": "text_a_1", "Translated_Text": ""},
        {"Filename": "FILE_A.BBQ", "Text": "text_a_2", "Translated_Text": "译_a_2"},
        {"Filename": "FILE_B.BBQ", "Text": "text_b_0", "Translated_Text": "{EMPTY}"},
        {"Filename": "FILE_B.BBQ", "Text": "text_b_1", "Translated_Text": "译_b_1"},
        {"Filename": "FILE_B.BBQ", "Text": "text_b_2", "Translated_Text": "译_b_2"},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "test_tbl.csv"
        write_text_csv(csv_path, csv_rows)

        trans_db = read_translation_table(csv_path, is_arm9=False)

        # FILE_A 应有 3 条记录，组内索引 0/1/2
        if "FILE_A.BBQ" not in trans_db:
            fail("read_translation_table", "(缺少 FILE_A.BBQ)")
            return
        if len(trans_db["FILE_A.BBQ"]) != 3:
            fail("read_translation_table",
                 f"(FILE_A 条目数 {len(trans_db['FILE_A.BBQ'])} ≠ 3)")
            return

        # FILE_A 组内索引：0 = 译_a_0，1 = ""（空译文），2 = 译_a_2
        if trans_db["FILE_A.BBQ"][0] != "译_a_0":
            fail("read_translation_table", f"(A[0]: {trans_db['FILE_A.BBQ'][0]!r})")
            return
        if trans_db["FILE_A.BBQ"][1] != "":
            fail("read_translation_table", f"(A[1] 应为空: {trans_db['FILE_A.BBQ'][1]!r})")
            return
        if trans_db["FILE_A.BBQ"][2] != "译_a_2":
            fail("read_translation_table", f"(A[2]: {trans_db['FILE_A.BBQ'][2]!r})")
            return

        # FILE_B 也应有 3 条记录，组内索引 0/1/2（不是全局行号 3/4/5）
        if "FILE_B.BBQ" not in trans_db:
            fail("read_translation_table", "(缺少 FILE_B.BBQ)")
            return
        if len(trans_db["FILE_B.BBQ"]) != 3:
            fail("read_translation_table",
                 f"(FILE_B 条目数 {len(trans_db['FILE_B.BBQ'])} ≠ 3)")
            return

        # FILE_B 索引 0 = ""（{EMPTY} 标记 → 空字符串），不是全局行号 3
        if trans_db["FILE_B.BBQ"][0] != "":
            fail("read_translation_table",
                 f"({{EMPTY}} 标记未转换: {trans_db['FILE_B.BBQ'][0]!r})")
            return
        if trans_db["FILE_B.BBQ"][1] != "译_b_1":
            fail("read_translation_table", f"(B[1]: {trans_db['FILE_B.BBQ'][1]!r})")
            return
        if trans_db["FILE_B.BBQ"][2] != "译_b_2":
            fail("read_translation_table", f"(B[2]: {trans_db['FILE_B.BBQ'][2]!r})")
            return

        ok("read_translation_table 统一入口（组内索引）")


# ----------------------------------------------------------------------
# 测试 6：行顺序 = 字符串索引（skip_empty=False）
# ----------------------------------------------------------------------

def test_row_order_preservation():
    print("\n=== 测试 6：行顺序 = 字符串索引 ===")

    # 用邮件文件测试（含空行，验证 skip_empty=False 的作用）
    bbq_path = TBL_DIR / "0082_ML_AIH_B01_MAIL01.BBQ"
    if not bbq_path.exists():
        print(f"  ⏭️  跳过（文件不存在）：{bbq_path.name}")
        return

    # skip_empty=False 保留空行
    entries = parse_bbq_file(bbq_path, is_scn=False, skip_empty=False)
    if not entries:
        fail("行顺序", "(无文本)")
        return

    # 构造 CSV 行（含空行）
    csv_rows = [{
        "Filename": bbq_path.name,
        "Text": e["Original_Text"],
        "Translated_Text": e.get("Translated_Text", ""),
    } for e in entries]

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / f"{bbq_path.stem}.csv"
        write_text_csv(csv_path, csv_rows)
        read_rows = read_text_csv(csv_path)

        if len(read_rows) != len(entries):
            fail("行顺序", f"(行数 {len(read_rows)} ≠ {len(entries)})")
            return

        # 逐行校验：CSV 第 i 行的 Text 应等于 entries[i] 的 Original_Text
        mismatch = 0
        for i, (orig, read) in enumerate(zip(entries, read_rows)):
            if orig["Original_Text"] != read["Text"]:
                if mismatch < 3:
                    print(f"     索引 {i}: {orig['Original_Text']!r} ≠ {read['Text']!r}")
                mismatch += 1

        if mismatch == 0:
            ok(f"行顺序 = 字符串索引 ({len(entries)} 行，含空行)")
        else:
            fail("行顺序", f"({mismatch} 行错位)")


# ----------------------------------------------------------------------
# 测试 7：迁移脚本 --dry-run
# ----------------------------------------------------------------------

def test_migration_dry_run():
    print("\n=== 测试 7：迁移脚本 --dry-run ===")

    from config import EXCEL_SCN, EXCEL_TBL, EXCEL_ARM9

    if not (EXCEL_SCN.exists() or EXCEL_TBL.exists() or EXCEL_ARM9.exists()):
        print("  ⏭️  跳过（无 xlsx 文件可迁移）")
        return

    # 以子进程方式运行迁移脚本
    import subprocess
    script = PROJECT_ROOT / "scripts" / "migrate_xlsx_to_csv.py"
    result = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )

    if result.returncode != 0:
        fail("迁移脚本 --dry-run", f"(退出码 {result.returncode})")
        print(f"     stderr: {result.stderr[:500]}")
        return

    if "迁移结束" not in result.stdout:
        fail("迁移脚本 --dry-run", "(未输出完成标记)")
        return

    ok("迁移脚本 --dry-run")


# ----------------------------------------------------------------------
# 测试 8：格式检测
# ----------------------------------------------------------------------

def test_format_detection():
    print("\n=== 测试 8：格式检测 is_csv_path ===")

    cases = [
        ("foo.csv", True),
        ("foo.CSV", True),
        ("foo.xlsx", False),
        ("foo.txt", False),
        ("path/to/bar.csv", True),
        ("path/to/bar.xlsx", False),
    ]

    all_ok = True
    for path, expected in cases:
        if is_csv_path(path) != expected:
            print(f"     {path}: 期望 {expected}，实际 {is_csv_path(path)}")
            all_ok = False

    if all_ok:
        ok(f"格式检测 ({len(cases)} 例)")
    else:
        fail("格式检测")


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------

def main():
    print("=" * 60)
    print(" P1-4/P1-5 CSV 翻译表 roundtrip 测试")
    print("=" * 60)

    test_text_csv_roundtrip()
    test_rfc4180_edge_cases()
    test_mail_file_detection()
    test_arm9_csv_roundtrip()
    test_read_translation_table()
    test_row_order_preservation()
    test_migration_dry_run()
    test_format_detection()

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f" 测试结果：{PASS}/{total} PASS" + (f"，{FAIL} FAIL" if FAIL else ""))
    print("=" * 60)

    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
