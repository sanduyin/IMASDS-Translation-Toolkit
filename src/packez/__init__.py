# src/packez/__init__.py
"""
packez - BIN/IDX (EZP/EZT) 封包引擎。

目标：替代 stage1_unpack.py / stage5_build_rom.py 中手工 EZP 解析逻辑，
提供与 faraplay/dearlystars::ez (Rust) 等价的解包/封包能力。

格式概述：
    EZT (IDX) 文件：索引表
        - EzTableHeader (16 字节)
        - EzTableEntry[] (每个 12 字节，数量 = entry_count)
        - 末尾额外 1 个 EzTableEntry (file_offset=names_offset, 标记 names_buffer 位置)

    EZP (BIN) 文件：数据 + 名称表
        - EzpHeader (16 字节)
        - 文件数据区 (按 entry.file_offset 索引)
        - names_buffer (从 names_offset 开始到文件末尾)

子模块：
    ezt_parser - EZT/EZP 头部与 entry 表的解析与生成
    ezp_pack   - extract_bin (解包) 与 rebuild_bin (封包)
"""

from .ezt_parser import (
    EZT_MAGIC1,
    EZP_MAGIC1,
    EZ_MAGIC2,
    EZT_HEADER_SIZE,
    EZ_ENTRY_SIZE,
    EZP_HEADER_SIZE,
    EzTableHeader,
    EzTableEntry,
    EzpHeader,
    read_idx,
    build_idx,
)
from .ezp_pack import (
    do_not_compress,
    padding_alignment,
    pad_to_alignment,
    extract_bin,
    rebuild_bin,
    extract_pack,
    build_pack,
)

__all__ = [
    # 常量
    "EZT_MAGIC1",
    "EZP_MAGIC1",
    "EZ_MAGIC2",
    "EZT_HEADER_SIZE",
    "EZ_ENTRY_SIZE",
    "EZP_HEADER_SIZE",
    # 结构体
    "EzTableHeader",
    "EzTableEntry",
    "EzpHeader",
    # 解析
    "read_idx",
    "build_idx",
    # 封包
    "do_not_compress",
    "padding_alignment",
    "pad_to_alignment",
    "extract_bin",
    "rebuild_bin",
    "extract_pack",
    "build_pack",
]
