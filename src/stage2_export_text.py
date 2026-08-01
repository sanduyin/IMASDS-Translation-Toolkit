# src/stage2_export_text.py
from __future__ import annotations

import os
import sys
import re
import argparse
from pathlib import Path
from typing import Any
from collections import defaultdict

import pandas as pd

# 导入全局配置和工具类
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, EXCEL_SCN, EXCEL_TBL, CSV_SCN_DIR, CSV_TBL_DIR
from src.utils.bbq_format import parse_bbq_file
from src.utils.bbq_mail import parse_mail_bbq, parse_mail_bbq_meta
from src.io.csv_handler import write_text_csv, write_mail_csv, is_mail_file

def extract_sort_key(filename: str) -> int:
    """提取文件名中的数字前缀用于排序，例如 '0001_A.bin' 返回 1"""
    match = re.search(r'(\d+)', filename)
    return int(match.group(1)) if match else 999999

def extract_group_name(filename: str) -> str:
    """去除文件名中的数字前缀和后缀，提取纯字母标识用于划分 Excel Sheet 页"""
    name = os.path.splitext(filename)[0]
    name = re.sub(r'^\d+_', '', name)
    name = re.sub(r'_MES$', '', name, flags=re.IGNORECASE)
    return name

def _make_mail_entries(filename: str, mail1: str, mail2: str,
                       idx1: int, idx2: int) -> list[dict[str, Any]]:
    """构建邮件 sheet 的 2 行标准格式 entries（行 0 = 邮件，行 1 = 回信）。

    与普通文本 sheet 共用 9 列布局；Text_Offset/Pointer_Locs/Max_Bytes 留空
    （邮件注入按整封邮件重建 Type7 池，不消费这些列；留空也避免触发长度校验红线）。
    Index 列为首行字符串在 Type7 池中的真实索引（仅展示用）。
    """
    rows = [(mail1, idx1), (mail2, idx2)]
    return [{
        'Original_Text': text,
        'Speaker': '×',
        'Translated_Text': '',
        'File': filename,
        'Text_Offset': '',
        'Pointer_Locs': '',
        'Max_Bytes': '',
        'Index': idx,
        'Type': '系统文本',
    } for text, idx in rows]


def create_styled_excel(data_groups: dict[str, list[dict[str, Any]]],
                        output_path: Path, is_scn: bool = False,
                        mail_sheet_names: set[str] | None = None) -> None:
    """
    将提取的文本数据写入 Excel，并应用严格的条件格式和样式规则。

    Args:
        data_groups: 普通文本数据 {sheet_name: [entry, ...]}（邮件 sheet 也在其中，
                    每个邮件 BBQ 一个 sheet，2 行标准格式：行 0 = 邮件，行 1 = 回信）
        output_path: 输出 xlsx 路径
        is_scn: 是否为 SCN（影响条件格式和冻结列）
        mail_sheet_names: 邮件 sheet 名集合（sheet 名 = 邮件文件名，含 .BBQ 后缀）。
                    邮件 sheet 跳过长度校验规则（Max_Bytes 留空），
                    仅保留"已翻译"绿色标记，并按内容行数自动调整行高。
    """
    print(f"正在导出表格至: {output_path}")
    if mail_sheet_names is None:
        mail_sheet_names = set()

    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        workbook = writer.book

        # 定义样式格式
        fmt_green = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100'})
        fmt_blue = workbook.add_format({'bg_color': '#BDD7EE', 'font_color': '#1F497D'})
        fmt_red = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006'})
        fmt_purple = workbook.add_format({'bg_color': '#E4D7F5'})
        fmt_text = workbook.add_format({'text_wrap': True, 'valign': 'top'})

        # 定义列顺序
        columns_order =[
            'Original_Text', 'Speaker', 'Translated_Text',
            'File', 'Text_Offset', 'Pointer_Locs', 'Max_Bytes', 'Index', 'Type'
        ]

        for sheet_name, entries in data_groups.items():
            # Excel Sheet 名称最多 31 个字符
            safe_sheet_name = sheet_name[:31]
            df = pd.DataFrame(entries)[columns_order]
            df.to_excel(writer, sheet_name=safe_sheet_name, index=False)

            ws = writer.sheets[safe_sheet_name]
            last_row = len(df) + 1

            # 设置基础列宽和文本换行
            ws.set_column('A:A', 40, fmt_text)
            ws.set_column('B:B', 12)
            ws.set_column('C:C', 40, fmt_text)
            ws.set_column('D:G', 15)

            if sheet_name in mail_sheet_names:
                # 邮件 sheet：按内容行数自动调整行高（邮件/回信通常多行，给足空间）
                for row_idx, entry in enumerate(entries):
                    lines = str(entry['Original_Text']).count('\n') + 1
                    ws.set_row(row_idx + 1, min(lines, 30) * 15)
                # 仅保留"已翻译"绿色标记（邮件 Max_Bytes 留空，长度校验规则不适用）
                ws.conditional_format(1, 2, last_row - 1, 2, {
                    'type': 'formula',
                    'criteria': '=$C2<>""',
                    'format': fmt_green,
                })
                # 冻结首行和第一列
                ws.freeze_panes(1, 1)
                continue

            # SCN 文件特有的系统/选项文本高亮规则 (通过检查文件名是否包含 _MES)
            if is_scn:
                purple_range_1 = f"A2:B{last_row}"
                purple_range_2 = f"D2:I{last_row}"
                # 如果 D 列(File)不包含 "_MES"，则将其视为系统文本，标为紫色
                purple_condition = {'type': 'formula', 'criteria': '=ISERROR(SEARCH("_MES", $D2))', 'format': fmt_purple}
                ws.conditional_format(purple_range_1, purple_condition)
                ws.conditional_format(purple_range_2, purple_condition)

            # 翻译文本长度校验规则 (应用在 C 列 Translated_Text)
            # 规则1: 字节数大于 H 列(Max_Bytes) 限制，或大于 40 时标红 (溢出)
            ws.conditional_format(1, 2, last_row - 1, 2, {
                'type': 'formula',
                'criteria': '=LENB($C2)>$G2' if not is_scn else '=LENB($C2)>40',
                'format': fmt_red
            })
            # 规则2: 翻译长度大于原文长度但在安全范围内，标蓝
            ws.conditional_format(1, 2, last_row - 1, 2, {
                'type': 'formula',
                'criteria': '=AND(LENB($C2)>LENB($A2), LENB($C2)<=$G2)' if not is_scn else '=AND(LENB($C2)>LENB($A2), LENB($C2)<=40)',
                'format': fmt_blue
            })
            # 规则3: 翻译完成且长度短于或等于原文，标绿
            ws.conditional_format(1, 2, last_row - 1, 2, {
                'type': 'formula',
                'criteria': '=AND(LENB($C2)<=$LENB($A2), $C2<>"")',
                'format': fmt_green
            })

            # 冻结首行和前三列 (TBL不需要冻结前三列)
            ws.freeze_panes(1, 3 if is_scn else 1)

def export_bbq_directory(input_folder: Path, output_excel: Path,
                         is_scn: bool = False, fmt: str = "xlsx") -> None:
    """遍历指定目录读取所有 bbq/bin 文件并导出"""
    if not input_folder.exists():
        print(f"找不到文件夹，请先执行解包: {input_folder}")
        return

    print(f"开始解析目录: {input_folder}")
    all_files =[]

    for root, _, files in os.walk(input_folder):
        for file in files:
            if file.lower().endswith(('.bbq', '.bin')):
                # 跳过歌词课 BBQ：歌词课翻译表独立导出（见 stage3_5_lyric.py /
                # lesvoice.export_lyric_translation_xlsx），不进入 TBL_Translation.xlsx
                if "LESVOICETABLE" in file.upper():
                    continue
                all_files.append(os.path.join(root, file))

    # 按文件名前缀的数字序号严格排序
    all_files.sort(key=lambda x: extract_sort_key(os.path.basename(x)))

    if fmt == "csv":
        _export_csv(all_files, is_scn, output_excel)
    else:
        grouped_data: dict[str, list[dict[str, Any]]] = defaultdict(list)
        mail_sheet_names: set[str] = set()  # 邮件 sheet 名（= 邮件文件名，含 .BBQ）

        for file_path in all_files:
            filename = os.path.basename(file_path)

            # 邮件 BBQ 单独处理：每个邮件一个 sheet，2 行标准格式（行 0 = 邮件，行 1 = 回信）
            if is_mail_file(filename):
                try:
                    mail1, mail2, idx1, idx2 = parse_mail_bbq_meta(file_path)
                except Exception as e:
                    print(f"  ⚠️ 邮件解析失败 {filename}: {e}，跳过")
                    continue
                grouped_data[filename] = _make_mail_entries(filename, mail1, mail2, idx1, idx2)
                mail_sheet_names.add(filename)
                continue

            # 普通文本 BBQ
            entries = parse_bbq_file(file_path, is_scn=is_scn)
            if entries:
                group_name = extract_group_name(filename) if is_scn else filename
                grouped_data[group_name].extend(entries)

        if grouped_data:
            create_styled_excel(grouped_data, output_excel, is_scn, mail_sheet_names)
            if mail_sheet_names:
                print(f"  📧 邮件 sheet: {len(mail_sheet_names)} 个")
        else:
            print(f"未在 {input_folder} 中提取到有效文本。")


def _export_csv(all_files: list[str], is_scn: bool, output_dir: Path) -> None:
    """
    CSV 模式导出：每个 sheet（一组 BBQ）→ 一个 CSV 文件。

    - 普通文件 → {group_name}.csv（3 列 faraplay 兼容窄列）
    - 邮件文件 → {basename}_ML.csv（5 列 faraplay ML 格式，所有邮件合并到一个文件）
      邮件分支调用 parse_mail_bbq + write_mail_csv，每个邮件 BBQ 占 1 行，
      Mail/Reply 列用 \\n 拼接多行字符串放在 quoted 单元格内。
    - skip_empty=False：保留空行，行顺序=字符串索引（兼容 faraplay 行顺序匹配）

    output_dir 参数实际为 CSV 目录（CSV_SCN_DIR 或 CSV_TBL_DIR）。
    basename 取自 output_dir 名称（"SCN" 或 "TBL"），与 faraplay 文件命名一致。
    """
    # 按文件名数字前缀排序后，逐文件解析并按 group_name 分组
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mail_rows: list[dict[str, Any]] = []  # 所有邮件合并到一个列表

    for file_path in all_files:
        filename = os.path.basename(file_path)

        if is_mail_file(filename):
            # 邮件分支：调用 parse_mail_bbq 获取 (mail1, mail2)
            try:
                mail1, mail2 = parse_mail_bbq(file_path)
            except Exception as e:
                print(f"  ⚠️ 邮件解析失败 {filename}: {e}，跳过")
                continue
            mail_rows.append({
                "Filename": filename,
                "Mail": mail1,
                "Translated Mail": "",
                "Reply": mail2,
                "Translated Reply": "",
            })
            continue

        # 普通文本分支：parse_bbq_file + 3 列 CSV
        entries = parse_bbq_file(file_path, is_scn=is_scn, skip_empty=False)
        if not entries:
            continue

        csv_rows = [{
            "Filename": filename,
            "Text": e["Original_Text"],
            "Translated_Text": e.get("Translated_Text", ""),
        } for e in entries]

        group_name = extract_group_name(filename) if is_scn else filename
        grouped[group_name].extend(csv_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    total_files = 0

    # 普通文本 CSV
    for group_name, rows in sorted(grouped.items()):
        csv_path = output_dir / f"{group_name}.csv"
        count = write_text_csv(csv_path, rows)
        total_files += 1
        print(f"  -> {csv_path.name}: {count} 行")

    # 邮件 CSV（所有邮件合并到一个文件，命名 {basename}_ML.csv，与 faraplay 一致）
    if mail_rows:
        # basename 取自 output_dir 名称（如 "TBL" / "SCN"）
        basename = output_dir.name
        ml_csv_path = output_dir / f"{basename}_ML.csv"
        count = write_mail_csv(ml_csv_path, mail_rows)
        total_files += 1
        print(f"  -> {ml_csv_path.name}: {count} 个邮件 (5 列 ML 格式)")

    print(f"✅ CSV 导出完毕：{total_files} 个文件 → {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SCN/TBL 文本导出")
    parser.add_argument("--format", choices=["xlsx", "csv"], default="xlsx",
                        help="输出格式：xlsx（默认，9列带条件格式）或 csv（faraplay 兼容窄列）")
    parser.add_argument("--skip-lyric", action="store_true",
                        help="跳过歌词课翻译表导出（默认会一并导出 TBL_LyricVoice_Translation.xlsx）")
    args = parser.parse_args()

    fmt = args.format
    print(f"导出格式: {fmt}")

    # 1. 导出 SCN 剧情文本 (开启 is_scn=True, 会解析角色且冻结多列)
    scn_dir = EXTRACT_DIR / "SCN"
    scn_output = CSV_SCN_DIR if fmt == "csv" else EXCEL_SCN
    export_bbq_directory(scn_dir, scn_output, is_scn=True, fmt=fmt)

    # 2. 导出 TBL 系统文本 (开启 is_scn=False, 忽略角色名，Speaker 全填 ×)
    #    LESVOICETABLE 文件已在 export_bbq_directory 中跳过，由独立流程导出
    tbl_dir = EXTRACT_DIR / "TBL"
    tbl_output = CSV_TBL_DIR if fmt == "csv" else EXCEL_TBL
    export_bbq_directory(tbl_dir, tbl_output, is_scn=False, fmt=fmt)

    # 3. 导出歌词课翻译表（独立 xlsx，与 TBL 普通文本分离）
    #    仅 xlsx 模式导出（歌词课强制使用 xlsx，因为含汉字需要 9 列布局）
    if not args.skip_lyric and fmt == "xlsx":
        print("\n" + "=" * 50)
        print(" 导出歌词课翻译表 (LESVOICETABLE → 独立 xlsx)")
        print("=" * 50)
        try:
            from config import EXCEL_LYRIC
            from src.utils import lesvoice
            lesvoice.export_lyric_translation_xlsx(EXCEL_LYRIC)
        except Exception as e:
            print(f"  ⚠️ 歌词课翻译表导出失败: {e}")
            print(f"     可后续手动执行: python -m src.stage3_5_lyric export-translation")

    print("✅ SCN/TBL 文本导出流程执行完毕。")

if __name__ == "__main__":
    main()