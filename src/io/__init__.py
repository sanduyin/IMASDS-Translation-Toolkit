# src/io/__init__.py
"""
io - 翻译表输入输出引擎。

支持两种格式：
    - xlsx: 现有 Excel 格式（9 列，条件格式，多人协作友好）
    - csv:  faraplay 兼容窄列格式（RFC 4180，diff 友好，版本控制友好）

CSV 列格式（与 faraplay v0.5.2 兼容）：
    普通文本: Filename,Text,Translated_Text
    ARM9:     Original_Text,Translated_Text,Filename,Text_Offset,Max_Bytes

子模块：
    csv_handler - CSV 读写（RFC 4180 合规）
"""

from .csv_handler import (
    CSV_ENCODING,
    write_text_csv,
    read_text_csv,
    write_arm9_csv,
    read_arm9_csv,
    read_translation_table,
    is_csv_path,
    is_mail_file,
)

__all__ = [
    "CSV_ENCODING",
    "write_text_csv",
    "read_text_csv",
    "write_arm9_csv",
    "read_arm9_csv",
    "read_translation_table",
    "is_csv_path",
    "is_mail_file",
]
