# scripts/migrate_xlsx_to_csv.py
"""
将 xlsx 翻译表迁移到 CSV 格式（faraplay 兼容窄列）。

迁移策略（与 stage2_export_text.py --format=csv 输出一致）：
    - EXCEL_SCN  → CSV_SCN_DIR/  下每 sheet 一个 CSV
    - EXCEL_TBL  → CSV_TBL_DIR/  下每 sheet 一个 CSV
    - EXCEL_ARM9 → CSV_ARM9_FILE 单文件

每个 xlsx sheet → 一个 CSV 文件（保持多人协作分工边界）。
邮件文件（File 含 _ML_）→ 独立的 {group_name}_ML.csv。

行顺序保证：
    xlsx 行顺序可能被人工编辑打乱，因此按 (extract_sort_key(File), Index)
    重新排序，确保 CSV 行顺序 = BBQ 字符串索引（注入器依赖此约定）。

用法：
    python scripts/migrate_xlsx_to_csv.py
    python scripts/migrate_xlsx_to_csv.py --dry-run    # 仅预览不写文件
    python scripts/migrate_xlsx_to_csv.py --scn-only   # 仅迁移 SCN
    python scripts/migrate_xlsx_to_csv.py --tbl-only   # 仅迁移 TBL
    python scripts/migrate_xlsx_to_csv.py --arm9-only  # 仅迁移 ARM9
"""
from __future__ import annotations

import os
import sys
import argparse
import re
from collections import defaultdict

# 让脚本能从项目根目录导入
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd  # noqa: E402

from config import (  # noqa: E402
    EXCEL_SCN, EXCEL_TBL, EXCEL_ARM9,
    CSV_SCN_DIR, CSV_TBL_DIR, CSV_ARM9_FILE,
)
from src.io.csv_handler import write_text_csv, write_arm9_csv, is_mail_file  # noqa: E402


def _extract_sort_key(filename: str) -> int:
    """提取文件名中的数字前缀用于排序（与 stage2_export_text 一致）"""
    match = re.search(r'(\d+)', filename)
    return int(match.group(1)) if match else 999999


def _extract_group_name(filename: str, is_scn: bool) -> str:
    """提取分组名（与 stage2_export_text.extract_group_name 一致）"""
    name = os.path.splitext(filename)[0]
    name = re.sub(r'^\d+_', '', name)
    name = re.sub(r'_MES$', '', name, flags=re.IGNORECASE)
    return name if is_scn else filename


def _safe_str(value, default: str = "") -> str:
    """pandas 单元格 → 字符串，NaN → 默认值"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    s = str(value)
    return default if s.lower() == "nan" else s


def migrate_bbq_xlsx(xlsx_path, output_dir, is_scn: bool, dry_run: bool = False) -> int:
    """
    迁移 BBQ xlsx（SCN/TBL）到 CSV 目录。

    Returns:
        写入的 CSV 文件数
    """
    if not xlsx_path.exists():
        print(f"⏭️  跳过：找不到 {xlsx_path}")
        return 0

    print(f"\n📖 读取 {xlsx_path.name} ...")
    xls = pd.read_excel(xlsx_path, sheet_name=None)

    # 收集所有行，按 (File, Index) 排序后重新分组
    # 这样无论 xlsx 行顺序是否被人工打乱，CSV 行顺序都 = BBQ 字符串索引
    all_rows: list[tuple[int, int, dict]] = []  # (sort_key, index, csv_row)

    for sheet_name, df in xls.items():
        if "File" not in df.columns or "Original_Text" not in df.columns:
            print(f"  ⚠️  跳过 sheet '{sheet_name}'：缺少 File/Original_Text 列")
            continue

        for _, row in df.iterrows():
            filename = _safe_str(row.get("File")).strip()
            if not filename:
                continue

            # Index 列：BBQ 字符串索引（行顺序依据）
            idx_raw = row.get("Index")
            try:
                idx = int(idx_raw) if not pd.isna(idx_raw) else 0
            except (ValueError, TypeError):
                idx = 0

            csv_row = {
                "Filename": filename,
                "Text": _safe_str(row.get("Original_Text")),
                "Translated_Text": _safe_str(row.get("Translated_Text")),
            }

            sort_key = _extract_sort_key(filename)
            all_rows.append((sort_key, idx, csv_row))

    # 排序：先按文件数字前缀，再按字符串索引
    all_rows.sort(key=lambda x: (x[0], x[1]))

    # 按 group_name 分组，邮件文件单独分组
    grouped: dict[str, list[dict]] = defaultdict(list)
    mail_groups: dict[str, list[dict]] = defaultdict(list)

    for _, _, csv_row in all_rows:
        filename = csv_row["Filename"]
        group_name = _extract_group_name(filename, is_scn)
        if is_mail_file(filename):
            mail_groups[group_name].append(csv_row)
        else:
            grouped[group_name].append(csv_row)

    if dry_run:
        print(f"  [DRY-RUN] 将生成 {len(grouped)} 个普通 CSV + {len(mail_groups)} 个邮件 CSV")
        for g in sorted(grouped.keys()):
            print(f"    -> {g}.csv ({len(grouped[g])} 行)")
        for g in sorted(mail_groups.keys()):
            print(f"    -> {g}_ML.csv ({len(mail_groups[g])} 行)")
        return len(grouped) + len(mail_groups)

    output_dir.mkdir(parents=True, exist_ok=True)
    total = 0

    for group_name, rows in sorted(grouped.items()):
        csv_path = output_dir / f"{group_name}.csv"
        count = write_text_csv(csv_path, rows)
        total += 1
        print(f"  -> {csv_path.name}: {count} 行")

    for group_name, rows in sorted(mail_groups.items()):
        csv_path = output_dir / f"{group_name}_ML.csv"
        count = write_text_csv(csv_path, rows)
        total += 1
        print(f"  -> {csv_path.name}: {count} 行 (邮件)")

    print(f"✅ {xlsx_path.name} 迁移完成：{total} 个 CSV → {output_dir}")
    return total


def migrate_arm9_xlsx(xlsx_path, output_path, dry_run: bool = False) -> int:
    """迁移 ARM9 xlsx 到单个 CSV 文件"""
    if not xlsx_path.exists():
        print(f"⏭️  跳过：找不到 {xlsx_path}")
        return 0

    print(f"\n📖 读取 {xlsx_path.name} ...")
    df = pd.read_excel(xlsx_path)

    required = {"Original_Text", "Translated_Text", "File", "Text_Offset"}
    missing = required - set(df.columns)
    if missing:
        print(f"  ⚠️  跳过：缺少列 {missing}")
        return 0

    rows = []
    for _, row in df.iterrows():
        filename = _safe_str(row.get("File")).strip()
        if not filename:
            continue

        max_bytes = row.get("Max_Bytes", "")
        if isinstance(max_bytes, float) and pd.isna(max_bytes):
            max_bytes = ""

        rows.append({
            "Original_Text": _safe_str(row.get("Original_Text")),
            "Translated_Text": _safe_str(row.get("Translated_Text")),
            "File": filename,
            "Text_Offset": _safe_str(row.get("Text_Offset")),
            "Max_Bytes": max_bytes,
        })

    if dry_run:
        print(f"  [DRY-RUN] 将生成 {output_path.name} ({len(rows)} 行)")
        return 1

    count = write_arm9_csv(output_path, rows)
    print(f"✅ {xlsx_path.name} 迁移完成：{count} 行 → {output_path}")
    return 1


def main():
    parser = argparse.ArgumentParser(
        description="xlsx → CSV 翻译表迁移（faraplay 兼容窄列格式）"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="仅预览将生成的文件，不实际写入")
    parser.add_argument("--scn-only", action="store_true", help="仅迁移 SCN")
    parser.add_argument("--tbl-only", action="store_true", help="仅迁移 TBL")
    parser.add_argument("--arm9-only", action="store_true", help="仅迁移 ARM9")
    args = parser.parse_args()

    # 没有指定 --xxx-only 时，迁移全部
    do_all = not (args.scn_only or args.tbl_only or args.arm9_only)

    print("=" * 60)
    print(f" xlsx → CSV 迁移工具{' [DRY-RUN]' if args.dry_run else ''}")
    print("=" * 60)

    total_files = 0

    if do_all or args.scn_only:
        total_files += migrate_bbq_xlsx(EXCEL_SCN, CSV_SCN_DIR, is_scn=True,
                                        dry_run=args.dry_run)

    if do_all or args.tbl_only:
        total_files += migrate_bbq_xlsx(EXCEL_TBL, CSV_TBL_DIR, is_scn=False,
                                        dry_run=args.dry_run)

    if do_all or args.arm9_only:
        total_files += migrate_arm9_xlsx(EXCEL_ARM9, CSV_ARM9_FILE,
                                         dry_run=args.dry_run)

    print(f"\n🎉 迁移结束：共生成 {total_files} 个 CSV 文件。")


if __name__ == "__main__":
    main()
