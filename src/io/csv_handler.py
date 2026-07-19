# src/io/csv_handler.py
"""
CSV 翻译表读写引擎，兼容 faraplay v0.5.2 列格式。

参考实现：
    - reference/dearlystars_tool/dearlystars/src/csv.rs (RFC 4180 解析)
    - reference/dearlystars_tool/dearlystars/src/bbq.rs (BBQ CSV 列格式)
    - reference/dearlystars_tool/dearlystars/src/arm9overlay.rs (ARM9 CSV 列格式)

## CSV 列格式

### 1. 普通文本 CSV（BBQ 文件）
    Filename,Text,Translated_Text
    - Filename:        .BBQ 文件名
    - Text:            原文（UTF-8）
    - Translated_Text: 译文（空则注入时回退到原文）

    每条字符串一行，行顺序 = 字符串索引（兼容 faraplay 行顺序匹配）。

### 2. ARM9 CSV
    Original_Text,Translated_Text,Filename,Text_Offset,Max_Bytes
    - Original_Text:    原文（仅供参考，注入时不读）
    - Translated_Text:  译文（空则跳过注入）
    - Filename:         目标文件名（arm9.bin / overlay0.bin 等）
    - Text_Offset:      文件内偏移（十六进制，支持 0x 前缀）
    - Max_Bytes:        最大可用字节数（十进制）

## RFC 4180 合规

用 Python 标准库 csv 模块，原生支持：
    - 转义引号 "" → "
    - 含逗号/换行的字段用引号包裹
    - 跨行 quoted 字段（faraplay 的 nom 解析器无法处理，本实现可正确往返）

## 编码

    - 文件编码：UTF-8（与 faraplay 一致，无 BOM）
    - 行尾：\\n（与 faraplay 一致）

## 邮件文件识别

    文件名第 6 字符起为 "ML_" 的文件识别为邮件（faraplay 约定）。
    邮件文件输出为独立的 {basename}_ML.csv，使用 5 列 ML 专用格式：
        Filename,Mail,Translated Mail,Reply,Translated Reply
    每个邮件 BBQ 文件占 1 行，Mail/Reply 列用 \\n 拼接多行字符串放在 quoted 单元格内。
    详见 src/utils/bbq_mail.py 和 write_mail_csv / read_mail_csv。
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

# CSV 编码：UTF-8（与 faraplay 一致，无 BOM）
CSV_ENCODING = "utf-8"


# ----------------------------------------------------------------------
# 格式检测
# ----------------------------------------------------------------------

def is_csv_path(path: os.PathLike[str] | str) -> bool:
    """判断路径是否为 CSV 文件。"""
    return str(path).lower().endswith(".csv")


def is_mail_file(filename: str) -> bool:
    """
    判断文件名是否为邮件文件（faraplay 约定）。

    faraplay: file_name.len() > 5 && file_name[5..].starts_with("ML_")
    即跳过前 5 字符（如 "0082_"），第 6 字符起为 "ML_"。

    本实现更宽松：只要文件名包含 "_ML_" 即视为邮件文件，
    覆盖 "0082_ML_xxx.BBQ" 和 "ML_xxx.BBQ" 两种命名风格。
    """
    return "_ML_" in filename or filename.startswith("ML_")


# ----------------------------------------------------------------------
# 普通文本 CSV（BBQ 文件）
# ----------------------------------------------------------------------

# 表头
_TEXT_HEADER = ["Filename", "Text", "Translated_Text"]


def write_text_csv(path: os.PathLike[str] | str, rows: Iterable[dict[str, Any]]) -> int:
    """
    写入普通文本 CSV。

    Args:
        path: 输出 CSV 文件路径
        rows: iterable of dict，每个 dict 至少包含:
            - Filename:        str
            - Text:            str（原文）
            - Translated_Text: str（译文，可为空字符串）

    Returns:
        写入的数据行数（不含表头）
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(_TEXT_HEADER)
        for row in rows:
            writer.writerow([
                row.get("Filename", ""),
                row.get("Text", ""),
                row.get("Translated_Text", ""),
            ])
            count += 1
    return count


def read_text_csv(path: os.PathLike[str] | str) -> list[dict[str, Any]]:
    """
    读取普通文本 CSV。

    Args:
        path: CSV 文件路径

    Returns:
        list of dict，每个 dict 包含:
            - Filename:        str
            - Text:            str（原文）
            - Translated_Text: str（译文，空字符串表示未翻译）
            - Index:           int（行号，从 0 开始，对应 BBQ 字符串索引）
    """
    results: list[dict[str, Any]] = []
    with open(path, "r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return results

        # 列索引映射（兼容表头大小写差异）
        col_map = {name.strip(): i for i, name in enumerate(header)}
        idx_filename = col_map.get("Filename", 0)
        idx_text = col_map.get("Text", 1)
        idx_trans = col_map.get("Translated_Text", 2)

        for row_idx, row in enumerate(reader):
            if not row:
                continue
            results.append({
                "Filename": row[idx_filename] if idx_filename < len(row) else "",
                "Text": row[idx_text] if idx_text < len(row) else "",
                "Translated_Text": row[idx_trans] if idx_trans < len(row) else "",
                "Index": row_idx,
            })
    return results


# ----------------------------------------------------------------------
# ARM9 CSV
# ----------------------------------------------------------------------

# 表头（与 faraplay arm9overlay.rs 列顺序一致）
_ARM9_HEADER = ["Original_Text", "Translated_Text", "Filename", "Text_Offset", "Max_Bytes"]


def write_arm9_csv(path: os.PathLike[str] | str, rows: Iterable[dict[str, Any]]) -> int:
    """
    写入 ARM9/Overlay CSV。

    Args:
        path: 输出 CSV 文件路径
        rows: iterable of dict，每个 dict 至少包含:
            - Original_Text:   str
            - Translated_Text: str
            - File:            str（文件名）
            - Text_Offset:     str（十六进制，如 "0x1A2B"）
            - Max_Bytes:       int 或 str

    Returns:
        写入的数据行数（不含表头）
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(_ARM9_HEADER)
        for row in rows:
            writer.writerow([
                row.get("Original_Text", ""),
                row.get("Translated_Text", ""),
                row.get("File", ""),
                row.get("Text_Offset", ""),
                row.get("Max_Bytes", ""),
            ])
            count += 1
    return count


def read_arm9_csv(path: os.PathLike[str] | str) -> list[dict[str, Any]]:
    """
    读取 ARM9/Overlay CSV。

    Args:
        path: CSV 文件路径

    Returns:
        list of dict，每个 dict 包含:
            - Original_Text:   str
            - Translated_Text: str
            - File:            str
            - Text_Offset:     str（十六进制）
            - Max_Bytes:       str（十进制）
    """
    results: list[dict[str, Any]] = []
    with open(path, "r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return results

        col_map = {name.strip(): i for i, name in enumerate(header)}
        idx_orig = col_map.get("Original_Text", 0)
        idx_trans = col_map.get("Translated_Text", 1)
        idx_file = col_map.get("Filename", 2)
        idx_offset = col_map.get("Text_Offset", 3)
        idx_max = col_map.get("Max_Bytes", 4)

        for row in reader:
            if not row:
                continue
            results.append({
                "Original_Text": row[idx_orig] if idx_orig < len(row) else "",
                "Translated_Text": row[idx_trans] if idx_trans < len(row) else "",
                "File": row[idx_file] if idx_file < len(row) else "",
                "Text_Offset": row[idx_offset] if idx_offset < len(row) else "",
                "Max_Bytes": row[idx_max] if idx_max < len(row) else "",
            })
    return results


# ----------------------------------------------------------------------
# 邮件 CSV（faraplay 5 列 ML 格式）
# ----------------------------------------------------------------------
# 表头与 faraplay bbq.rs 第 412 行一致（注意"Translated Mail"中间有空格，无下划线）
_ML_HEADER = ["Filename", "Mail", "Translated Mail", "Reply", "Translated Reply"]


def write_mail_csv(
    path: os.PathLike[str] | str,
    rows: Iterable[dict[str, Any]],
) -> int:
    """写入邮件 CSV（faraplay 5 列 ML 格式）。

    每个邮件 BBQ 文件占 1 行。Mail/Reply 列内容是 \\n 拼接的多行字符串，
    csv.writer 会自动用引号包裹并转义内部引号。

    Args:
        path: 输出 CSV 文件路径
        rows: iterable of dict，每个 dict 至少包含:
            - Filename:         str（.BBQ 文件名）
            - Mail:             str（邮件原文，\\n 分隔多行）
            - Translated Mail:  str（邮件译文，空则注入时回退到原文）
            - Reply:            str（回信原文，\\n 分隔多行）
            - Translated Reply: str（回信译文，空则注入时回退到原文）

    Returns:
        写入的数据行数（不含表头）
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(_ML_HEADER)
        for row in rows:
            writer.writerow([
                row.get("Filename", ""),
                row.get("Mail", ""),
                row.get("Translated Mail", ""),
                row.get("Reply", ""),
                row.get("Translated Reply", ""),
            ])
            count += 1
    return count


def read_mail_csv(path: os.PathLike[str] | str) -> dict[str, tuple[str, str, bool, bool]]:
    """读取邮件 CSV，返回 {filename: (final_mail, final_reply, mail_translated, reply_translated)}。

    译文为空时回退到原文（与 faraplay row_to_mail 逻辑一致）。
    mail_translated/reply_translated 标记译文列是否有内容，注入器据此决定
    是否需要重建 BBQ（未翻译时直接复制原文件以保留原始字节）。

    Args:
        path: 邮件 CSV 文件路径

    Returns:
        {filename: (mail_text, reply_text, mail_translated, reply_translated)}
        - mail_text/reply_text: 注入时直接使用的最终文本（译文非空则用译文，否则原文）
        - mail_translated/reply_translated: 译文列是否有内容
    """
    result: dict[str, tuple[str, str, bool, bool]] = {}
    with open(path, "r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return result

        col_map = {name.strip(): i for i, name in enumerate(header)}
        idx_filename = col_map.get("Filename", 0)
        idx_mail = col_map.get("Mail", 1)
        idx_trans_mail = col_map.get("Translated Mail", 2)
        idx_reply = col_map.get("Reply", 3)
        idx_trans_reply = col_map.get("Translated Reply", 4)

        for row in reader:
            if not row:
                continue
            filename = row[idx_filename] if idx_filename < len(row) else ""
            if not filename:
                continue

            mail_orig = row[idx_mail] if idx_mail < len(row) else ""
            mail_trans = row[idx_trans_mail] if idx_trans_mail < len(row) else ""
            reply_orig = row[idx_reply] if idx_reply < len(row) else ""
            reply_trans = row[idx_trans_reply] if idx_trans_reply < len(row) else ""

            # 译文非空则用译文，否则回退原文（faraplay row_to_mail 语义）
            mail_translated = bool(mail_trans.strip())
            reply_translated = bool(reply_trans.strip())
            final_mail = mail_trans if mail_translated else mail_orig
            final_reply = reply_trans if reply_translated else reply_orig

            result[filename] = (final_mail, final_reply, mail_translated, reply_translated)

    return result


# ----------------------------------------------------------------------
# 统一读取入口（注入器用）
# ----------------------------------------------------------------------

def read_translation_table(
    path: os.PathLike[str] | str, is_arm9: bool = False
) -> list[dict[str, Any]] | dict[str, dict[int, str]]:
    """
    统一读取翻译表，自动检测 CSV / xlsx 格式。

    Args:
        path:    翻译表路径（.csv 或 .xlsx）
        is_arm9: 是否为 ARM9 翻译表（影响列格式）

    Returns:
        对于 BBQ 翻译表（is_arm9=False）:
            {filename: {index: translated_text}}
        对于 ARM9 翻译表（is_arm9=True）:
            [{"File": str, "Text_Offset": str, "Translated_Text": str, "Original_Text": str, "Max_Bytes": str}]

    注意：BBQ 翻译表的 index 是字符串索引（行顺序），与 parse_bbq_file 的 Index 字段一致。
    """
    path = Path(path)
    if not path.exists():
        return {} if not is_arm9 else []

    if is_csv_path(path):
        if is_arm9:
            rows = read_arm9_csv(path)
            # 过滤掉译文为空的行
            return [r for r in rows if r.get("Translated_Text", "").strip()]
        else:
            rows = read_text_csv(path)
            # 按 Filename 分组后，用组内行号作为字符串索引。
            # 这保证 CSV 含多个 Filename 时，每个文件的索引都从 0 开始，
            # 与 parse_bbq_file 的 Index 字段一致（注入器依赖此约定）。
            # 前提：CSV 中同一 Filename 的行必须连续（_export_csv 已保证）。
            trans_db: dict[str, dict[int, str]] = {}
            for row in rows:
                fname = row["Filename"].strip()
                if not fname:
                    continue
                if fname not in trans_db:
                    trans_db[fname] = {}
                # 组内索引 = 该文件已收集的行数
                idx = len(trans_db[fname])
                trans_str = row.get("Translated_Text", "")
                # 空译文或 EMPTY 标记 → 空字符串（注入时回退到原文）
                if trans_str in ("", *EMPTY_MARKERS_LOCAL):
                    final_text = ""
                else:
                    final_text = trans_str
                trans_db[fname][idx] = final_text
            return trans_db
    else:
        # xlsx 格式：委托给 pandas
        import pandas as pd
        if is_arm9:
            df = pd.read_excel(path)
            df = df.dropna(subset=["Translated_Text", "Text_Offset"])
            return cast(list[dict[str, Any]], df.to_dict("records"))
        else:
            trans_db = {}
            xls = pd.read_excel(path, sheet_name=None)
            for sheet_name, df in xls.items():
                # 邮件 sheet（2 行布局：行 0 邮件、行 1 回信）由 stage4 邮件分支
                # 单独读取重建，不能按普通文本注入（会把整封邮件按 Index 拆成
                # 独立 Type7 字符串写入，破坏邮件 Type5/Type7 结构）
                if is_mail_file(sheet_name):
                    continue
                if "File" not in df.columns:
                    continue
                for _, row_series in df.iterrows():
                    fname = str(row_series["File"]).strip()
                    if not fname or fname.lower() == "nan":
                        continue
                    raw_trans = row_series.get("Translated_Text")
                    if pd.isna(raw_trans):
                        continue
                    trans_str = str(raw_trans)
                    final_text = "" if trans_str in EMPTY_MARKERS_LOCAL else trans_str
                    if fname not in trans_db:
                        trans_db[fname] = {}
                    trans_db[fname][int(row_series["Index"])] = final_text
            return trans_db


# 空译文标记（与 config.EMPTY_MARKERS 一致，避免循环导入）
EMPTY_MARKERS_LOCAL = ["{EMPTY}", "{empty}", " ", "　"]
