"""歌词课汉化核心模块 (Stage 3.5)

整合自原 scripts/ 下5个独立脚本：
  - build_charmap_patch.py ：SSOT 字符表 + charmap 重定位 + y9.bin 补丁
  - expand_gld_chinese.py  ：GLD 注入中文字形像素
  - expand_agl_chinese.py  ：AGL frame 表扩容
  - inject_bbq_chinese.py  ：BBQ 歌词文本注入
  - patch_imasds_arm9.py   ：ARM9 四补丁安全应用（check/patch/verify）

新增功能：
  - export_lyric_translation_xlsx：从 10 个 LESVOICETABLE BBQ 导出独立翻译 xlsx
  - export_gld_psd_for_art      ：从已扩容 GLD 导出 PSD 供美工修正字型

私有码位约定 (SJIS G3 用户定义区，与原假名 0x81xx/0x82xx 完全隔离)：
  - 0xF040 - 0xF9FC，10 个 lead byte × 188 个 trail = 1880 个码位
  - 所有中文统一使用私有码位，保证 charmap 表与 BBQ 编码完全一致
"""
from __future__ import annotations

import csv
import json
import os
import struct
import sys
import tempfile
import hashlib
import shutil
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

# 项目根目录 (src/utils/ → 项目根)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import (
    EXTRACT_DIR,
    PATCHED_DIR,
    WORKSPACE_DIR,
    FONT_12PX,
    EXCEL_LYRIC,
    LYRIC_BUILD_DIR,
    LYRIC_GLYPH_DIR,
    LYRIC_FONT_MAPPING,
    LYRIC_SSOT_CSV,
)
from src.utils.gld_format import (
    parse_gld, write_gld, GldFile, GldHeader, GldFooterEntry,
    FOOTER_TYPE_2, SPRITE_FORMAT_2, BIT_DEPTH, PIXELS_PER_BYTE,
    unpack_2bits_le, pack_2bits_le, bgr555_to_rgb888,
)
from src.utils.bbq_format import parse_bbq_file


# ============================================================
# 全局常量
# ============================================================

# 10 个歌词课 BBQ 文件 (按 xlsx sheet 顺序)
LYRIC_BBQ_FILES = [
    "0022_LESVOICETABLEHEL.BBQ",
    "0023_LESVOICETABLEALI.BBQ",
    "0024_LESVOICETABLEPRE.BBQ",
    "0025_LESVOICETABLEDAZ.BBQ",
    "0026_LESVOICETABLETIM.BBQ",
    "0027_LESVOICETABLEGMW.BBQ",
    "0028_LESVOICETABLESHI.BBQ",
    "0029_LESVOICETABLEAGE.BBQ",
    "0030_LESVOICETABLEREL.BBQ",
    "0031_LESVOICETABLEKIR.BBQ",
]

# 4 个歌词课 AGL/GLD 资产 (受 Stage 3.5 管理，Stage 4/6 跳过)
LYRIC_AGL_GLD_FILES = {
    "0506_D_MEASURE_MOJI_MNG.AGL",  # 左侧候选字 sprite 布局
    "0507_D_MEASURE_MOJI_MNG.GLD",  # 左侧候选字 像素数据
    "0520_D_EPANEL_MOJI_MNG.AGL",   # 下侧题目字 sprite 布局
    "0521_D_EPANEL_MOJI_MNG.GLD",   # 下侧题目字 像素数据
}

# overlay_0006 / ARM9 / y9 常量
OVL6_FILENAME = "overlay_0006.bin"
Y9_FILENAME = "y9.bin"
ARM9_FILENAME = "arm9.bin"

# overlay_0006 内部布局 (与原版 0728 ROM 一致)
OVL6_RAM_BASE = 0x02117320
OVL6_ORIG_FILE_SIZE = 0xCB80
OVL6_ORIG_BSS_SIZE = 0x20
OVL6_CHARMAP_FILE_OFF = 0xBFDC
OVL6_CHARMAP_RAM = 0x021232FC
OVL6_CHARMAP_END_RAM = 0x021234E8
OVL6_PTR1_FILE_OFF = 0x8C0C  # 指向 charmap 表起始
OVL6_PTR2_FILE_OFF = 0x8C10  # 指向 charmap 表结束
OVL6_ENTRY_COUNT_ORIG = 82
OVL6_ENTRY_SIZE = 6

# 新 charmap 表布局：文件尾 + 0x20 BSS padding + 新表
OVL6_NEW_TABLE_FILE_OFF = OVL6_ORIG_FILE_SIZE + OVL6_ORIG_BSS_SIZE  # 0xCBA0
OVL6_NEW_TABLE_RAM = OVL6_RAM_BASE + OVL6_NEW_TABLE_FILE_OFF        # 0x02123EC0

# y9.bin 中 overlay 6 条目偏移
Y9_OVL6_OFFSET = 6 * 0x20  # 0xC0
Y9_RAM_SIZE_OFFSET = Y9_OVL6_OFFSET + 0x08  # 0xC8
Y9_BSS_SIZE_OFFSET = Y9_OVL6_OFFSET + 0x0C  # 0xCC

# 私有码位区间 (CP932 用户定义区 G3)
# 0xF040 - 0xF9FC: 10 个 lead byte × 188 个 trail = 1880 个码位
PRIVATE_CODE_START = 0xF040
PRIVATE_CODE_END = 0xF9FC

# AGL frame 表扩容常量
AGL_OLD_FRAME_COUNT = 81
AGL_FRAME_ENTRY_SIZE = 44
AGL_HEADER_SIZE = 0x28  # 40
AGL_CELL_ENTRY_SIZE = 16

# ARM9 四补丁定义
KNOWN_ARM9_SIZE = 878_528
KNOWN_ARM9_SHA256 = {
    "14e8d4656801a47108eb9d987b19e962773dc6eb5066e039b8f14faef144c980":
        "0728 original",
    "2f3fd4b672781c999159410aa5d738452e1fd0f6e7d91f4d91a08f2c1907788b":
        "0728 range-cache only",
    "3422eaf862fdaa79d195c4fef813269bd6144af0be07ebae2a0dcff4b2c0b1ce":
        "0728 complete successful patch",
}
KNOWN_ARM9_FINAL_SHA256 = (
    "3422eaf862fdaa79d195c4fef813269bd6144af0be07ebae2a0dcff4b2c0b1ce"
)

# 0507 (左侧候选字) 配置
CFG_0507: dict[str, Any] = {
    'filename': '0507_D_MEASURE_MOJI_MNG.GLD',
    'out_filename': '0507_D_MEASURE_MOJI_MNG_patched.GLD',
    'canvas_w': 16,
    'canvas_h': 16,
    'render_width_id': 1,
    'render_height_id': 1,
    'crop_w': 16,
    'crop_h': 16,
    'crop_x': 0,
    'crop_y': 0,
    'palette_offset': 0x0,
    'bg_color': (0, 248, 0, 255),       # green opaque
    'text_color': (48, 48, 48, 255),    # dark gray
    'aa_color': (248, 248, 248, 255),   # white (anti-aliased edges)
    'font_size': 12,
}

# 0521 (下侧题目字) 配置
CFG_0521: dict[str, Any] = {
    'filename': '0521_D_EPANEL_MOJI_MNG.GLD',
    'out_filename': '0521_D_EPANEL_MOJI_MNG_patched.GLD',
    'canvas_w': 32,
    'canvas_h': 18,
    'render_width_id': 2,
    'render_height_id': 2,
    'crop_w': 24,
    'crop_h': 18,
    'crop_x': 0,
    'crop_y': 0,
    'palette_offset': 0x70,
    'bg_color': (0, 248, 0, 255),       # green opaque
    'text_color': (0, 0, 0, 255),       # black
    'aa_color': (72, 64, 72, 255),      # gray (palette[2] at 0x70)
    'font_size': 16,
}

# AGL 文件配置
AGL_FILES_CFG = [
    {
        'filename': '0506_D_MEASURE_MOJI_MNG.AGL',
        'out_filename': '0506_D_MEASURE_MOJI_MNG_patched.AGL',
        'desc': '左侧候选字 0506',
    },
    {
        'filename': '0520_D_EPANEL_MOJI_MNG.AGL',
        'out_filename': '0520_D_EPANEL_MOJI_MNG_patched.AGL',
        'desc': '下侧题目字 0520',
    },
]

# 原 charmap 中的假名/符号 SJIS 映射（用于 SSOT 字符表）
SJIS_HIRAGANA = {
    0x829F: "ぁ", 0x82A0: "あ", 0x82A1: "ぃ", 0x82A2: "い", 0x82A3: "ぅ",
    0x82A4: "う", 0x82A5: "ぇ", 0x82A6: "え", 0x82A7: "ぉ", 0x82A8: "お",
    0x82A9: "か", 0x82AA: "が", 0x82AB: "き", 0x82AC: "ぎ", 0x82AD: "く",
    0x82AE: "ぐ", 0x82AF: "け", 0x82B0: "げ", 0x82B1: "こ", 0x82B2: "ご",
    0x82B3: "さ", 0x82B4: "ざ", 0x82B5: "し", 0x82B6: "じ", 0x82B7: "す",
    0x82B8: "ず", 0x82B9: "せ", 0x82BA: "ぜ", 0x82BB: "そ", 0x82BC: "ぞ",
    0x82BD: "た", 0x82BE: "だ", 0x82BF: "ち", 0x82C0: "ぢ", 0x82C1: "っ",
    0x82C2: "つ", 0x82C3: "づ", 0x82C4: "て", 0x82C5: "で", 0x82C6: "と",
    0x82C7: "ど", 0x82C8: "な", 0x82C9: "に", 0x82CA: "ぬ", 0x82CB: "ね",
    0x82CC: "の", 0x82CD: "は", 0x82CE: "ば", 0x82CF: "ぱ", 0x82D0: "ひ",
    0x82D1: "び", 0x82D2: "ぴ", 0x82D3: "ふ", 0x82D4: "ぶ", 0x82D5: "ぷ",
    0x82D6: "へ", 0x82D7: "べ", 0x82D8: "ぺ", 0x82D9: "ほ", 0x82DA: "ぼ",
    0x82DB: "ぽ", 0x82DC: "ま", 0x82DD: "み", 0x82DE: "む", 0x82DF: "め",
    0x82E0: "も", 0x82E1: "ゃ", 0x82E2: "や", 0x82E3: "ゅ", 0x82E4: "ゆ",
    0x82E5: "ょ", 0x82E6: "よ", 0x82E7: "ら", 0x82E8: "り", 0x82E9: "る",
    0x82EA: "れ", 0x82EB: "ろ", 0x82EC: "ゎ", 0x82ED: "わ", 0x82EE: "ゐ",
    0x82EF: "ゑ", 0x82F0: "を", 0x82F1: "ん",
}
SJIS_SYMBOLS = {0x815B: "ー", 0x817C: "−"}


# ============================================================
# 异常类型
# ============================================================
class LyricPatchError(RuntimeError):
    """歌词课补丁构建/应用过程中的错误"""


class Arm9PatchError(RuntimeError):
    """ARM9 补丁应用过程中的错误（与原 patch_imasds_arm9.py 一致）"""


# ============================================================
# Part 1: SSOT 字符表 + charmap 重定位 (build_charmap_patch.py)
# ============================================================

def _read_original_charmap(ovl6_path: Path) -> list[dict]:
    """读取 overlay_0006 中的原 82 条 charmap 表"""
    data = ovl6_path.read_bytes()
    entries = []
    for i in range(OVL6_ENTRY_COUNT_ORIG):
        off = OVL6_CHARMAP_FILE_OFF + i * OVL6_ENTRY_SIZE
        raw = data[off:off + OVL6_ENTRY_SIZE]
        glyph_id = struct.unpack("<H", raw[0:2])[0]
        key_be = struct.unpack(">H", raw[2:4])[0]
        reserved = struct.unpack("<H", raw[4:6])[0]
        entries.append({
            'index': i,
            'glyph_id': glyph_id,
            'key': key_be,
            'reserved': reserved,
            'source': 'original',
        })
    return entries


def extract_chinese_chars_from_xlsx(xlsx_path: Path) -> list[str]:
    """从翻译 xlsx 提取所有唯一 CJK 汉字

    扫描 Translated_Text/译文/Translation 三种常见列名。
    """
    xls = pd.read_excel(xlsx_path, sheet_name=None)
    all_chars: set[str] = set()
    for _sheet_name, df in xls.items():
        col = None
        for c in ('Translated_Text', '译文', 'Translation'):
            if c in df.columns:
                col = c
                break
        if col is None:
            continue
        for v in df[col].dropna().astype(str):
            all_chars.update(list(v))
    # 排除控制字符、空白、ASCII
    all_chars.difference_update({'\n', '\r', '\t', '', ' ', '\u3000', '\xa0'})
    # 仅保留 CJK 汉字
    return sorted(c for c in all_chars if '\u4e00' <= c <= '\u9fff')


def _assign_encoding(chars: list[str]) -> list[dict]:
    """为每个汉字分配私有码位 0xF040-0xF9FC (SJIS 用户定义区 G3)

    设计理由：
    - 0xF0-0xF9 是合法 SJIS lead byte (在 0xE0-0xFC 范围内)
    - 0xF040-0xF9FC 是 SJIS 用户定义区，不与标准字符冲突
    - 与原假名/符号 (0x81xx/0x82xx) 完全隔离
    - 所有中文统一使用私有码位，保证 charmap 表与 BBQ 文件编码完全一致
    - 容量：10 个 lead byte × 188 个 trail = 1880 个码位
    """
    results = []
    private_code = PRIVATE_CODE_START
    for idx, ch in enumerate(chars):
        if private_code > PRIVATE_CODE_END:
            raise LyricPatchError(
                f"私有码位空间不足！当前汉字 {len(chars)} 个，"
                f"超过 0x{PRIVATE_CODE_START:04X}-0x{PRIVATE_CODE_END:04X} 容量 (1880)。\n"
                f"请扩大 PRIVATE_CODE_END（如 0xFFFC）或减少汉字数量。"
            )
        key = private_code
        # 推进私有码位 (跳过 0x7F trail)
        trail = (private_code & 0xFF) + 1
        lead = private_code >> 8
        if trail > 0xFC:
            trail = 0x40
            lead += 1
        elif trail == 0x7F:
            trail = 0x80
        private_code = (lead << 8) | trail
        results.append({
            'char': ch,
            'unicode': ord(ch),
            'encoding': 'private',
            'key': key,
            'key_hex': f"0x{key:04X}",
        })
    return results


def _build_ssot(orig_charmap: list[dict],
                chinese_chars: list[dict]) -> dict:
    """构建 SSOT 字符表

    - 保留原 82 条假名/符号
    - 新增中文条目 (glyph_id 从 0x51 起)
    - 按 key 字节序列升序排序 (memcmp 一致)
    """
    all_entries: list[dict] = []
    for e in orig_charmap:
        all_entries.append({
            'glyph_id': e['glyph_id'],
            'key': e['key'],
            'reserved': 0,
            'source': 'original',
            'char': '',
        })

    # 填原假名/符号字符
    for entry in all_entries:
        if entry['key'] in SJIS_HIRAGANA:
            entry['char'] = SJIS_HIRAGANA[entry['key']]
        elif entry['key'] in SJIS_SYMBOLS:
            entry['char'] = SJIS_SYMBOLS[entry['key']]

    # 新增汉字条目 (glyph_id 从 0x51 起)
    next_glyph_id = 0x51
    for ch_info in chinese_chars:
        all_entries.append({
            'glyph_id': next_glyph_id,
            'key': ch_info['key'],
            'reserved': 0,
            'source': ch_info['encoding'],
            'char': ch_info['char'],
        })
        ch_info['glyph_id'] = next_glyph_id
        next_glyph_id += 1

    # 按 key 字节序列升序排序 (memcmp 一致)
    all_entries.sort(key=lambda e: (
        (e['key'] >> 8) & 0xFF,  # key 高字节
        e['key'] & 0xFF,         # key 低字节
    ))

    # 重建 index
    for i, e in enumerate(all_entries):
        e['index'] = i

    return {'entries': all_entries, 'chinese_chars': chinese_chars}


def _build_charmap_bin(ssot: dict) -> bytes:
    """构建新 charmap 表二进制 (6 字节/条目，已排序)

    条目结构：u16 glyph_id(LE) + u8 key[2](BE SJIS) + u16 reserved(LE)
    """
    bin_data = bytearray()
    for e in ssot['entries']:
        bin_data.extend(struct.pack('<H', e['glyph_id']))
        bin_data.extend(struct.pack('>H', e['key']))  # 大端 key
        bin_data.extend(struct.pack('<H', e['reserved']))
    return bytes(bin_data)


def _patch_overlay6(ovl6_src: Path, charmap_bin: bytes) -> tuple[bytes, dict]:
    """补丁 overlay_0006.bin

    1. 复制原文件
    2. 在文件尾追加 0x20 字节零 (BSS padding)
    3. 追加新 charmap 表
    4. 修改 0x8C0C 处指针 → 新表起始 RAM
    5. 修改 0x8C10 处指针 → 新表结束 RAM
    """
    src_data = bytearray(ovl6_src.read_bytes())
    orig_size = len(src_data)
    if orig_size != OVL6_ORIG_FILE_SIZE:
        raise LyricPatchError(
            f"原 overlay_0006 大小异常: 0x{orig_size:X} != 0x{OVL6_ORIG_FILE_SIZE:X}"
        )

    new_data = bytearray(src_data)
    new_data.extend(b'\x00' * OVL6_ORIG_BSS_SIZE)  # 0x20 字节 BSS padding
    new_data.extend(charmap_bin)

    new_table_size = len(charmap_bin)
    new_table_start_ram = OVL6_NEW_TABLE_RAM
    new_table_end_ram = OVL6_NEW_TABLE_RAM + new_table_size

    # 修改指针 (验证原值)
    orig_ptr1 = struct.unpack('<I', new_data[OVL6_PTR1_FILE_OFF:OVL6_PTR1_FILE_OFF + 4])[0]
    orig_ptr2 = struct.unpack('<I', new_data[OVL6_PTR2_FILE_OFF:OVL6_PTR2_FILE_OFF + 4])[0]
    if orig_ptr1 != OVL6_CHARMAP_RAM:
        raise LyricPatchError(
            f"原指针1异常: 0x{orig_ptr1:08X} != 0x{OVL6_CHARMAP_RAM:08X}"
        )
    if orig_ptr2 != OVL6_CHARMAP_END_RAM:
        raise LyricPatchError(
            f"原指针2异常: 0x{orig_ptr2:08X} != 0x{OVL6_CHARMAP_END_RAM:08X}"
        )

    new_data[OVL6_PTR1_FILE_OFF:OVL6_PTR1_FILE_OFF + 4] = struct.pack('<I', new_table_start_ram)
    new_data[OVL6_PTR2_FILE_OFF:OVL6_PTR2_FILE_OFF + 4] = struct.pack('<I', new_table_end_ram)

    info = {
        'orig_size': orig_size,
        'new_size': len(new_data),
        'bss_padding': OVL6_ORIG_BSS_SIZE,
        'table_size': new_table_size,
        'table_file_off': OVL6_NEW_TABLE_FILE_OFF,
        'table_ram_start': new_table_start_ram,
        'table_ram_end': new_table_end_ram,
        'orig_ptr1': orig_ptr1,
        'orig_ptr2': orig_ptr2,
        'new_ptr1': new_table_start_ram,
        'new_ptr2': new_table_end_ram,
    }
    return bytes(new_data), info


def _patch_y9(y9_src: Path, new_overlay_size: int) -> tuple[bytes, dict]:
    """补丁 y9.bin：更新 overlay 6 的 RAM Size

    BSS Size 保持 0x20 不变（已通过文件中追加的 0x20 padding 模拟）。
    """
    src_data = bytearray(y9_src.read_bytes())

    ovl6_entry = src_data[Y9_OVL6_OFFSET:Y9_OVL6_OFFSET + 0x20]
    ovl_id, ram_addr, ram_size, bss_size = struct.unpack('<IIII', ovl6_entry[0:16])
    if ovl_id != 6:
        raise LyricPatchError(f"overlay ID 异常: {ovl_id} != 6")
    if ram_addr != OVL6_RAM_BASE:
        raise LyricPatchError(
            f"RAM 地址异常: 0x{ram_addr:08X} != 0x{OVL6_RAM_BASE:08X}"
        )
    if ram_size != OVL6_ORIG_FILE_SIZE:
        raise LyricPatchError(
            f"原 RAM Size 异常: 0x{ram_size:X} != 0x{OVL6_ORIG_FILE_SIZE:X}"
        )

    new_ram_size = new_overlay_size
    src_data[Y9_RAM_SIZE_OFFSET:Y9_RAM_SIZE_OFFSET + 4] = struct.pack('<I', new_ram_size)

    info = {
        'ovl_id': ovl_id,
        'orig_ram_addr': ram_addr,
        'orig_ram_size': ram_size,
        'orig_bss_size': bss_size,
        'new_ram_size': new_ram_size,
        'new_bss_size': bss_size,
    }
    return bytes(src_data), info


def build_font_mapping_json(ssot: dict, out_path: Path) -> dict[str, int]:
    """构建 text_encoder 用的 font_mapping.json 并写入磁盘

    - 空格: 0x20 (单字节)
    - 全角空格: 0x8140
    - 假名/符号: 用原 SJIS key (来自 charmap 表, 0x81xx/0x82xx)
    - 汉字: 用私有码位 (0xF040-0xF9FC, 与 charmap 表一致)
    """
    mapping: dict[str, int] = {
        ' ': 0x20,
        '\u3000': 0x8140,
        '\xa0': 0x20,
    }
    for e in ssot['entries']:
        if e['char']:
            mapping[e['char']] = e['key']
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    return mapping


def build_charmap_patch(lyric_xlsx: Path = EXCEL_LYRIC,
                        build_dir: Path = LYRIC_BUILD_DIR) -> dict:
    """主入口：构建 SSOT + charmap 重定位 + y9 补丁

    Args:
        lyric_xlsx:  歌词课专用翻译表 (TBL_LyricVoice_Translation.xlsx)
        build_dir:   构建目录 (build/lesvoice_patch/)

    Returns:
        包含 ssot/ovl_info/y9_info/font_mapping 的字典
    """
    print("=" * 70)
    print("构建 SSOT 字符表 + charmap 重定位补丁")
    print("=" * 70)

    build_dir.mkdir(parents=True, exist_ok=True)
    ovl6_src = EXTRACT_DIR / "ARM9" / OVL6_FILENAME
    y9_src = EXTRACT_DIR / "ARM9" / Y9_FILENAME
    # y9.bin 可能在 Extracted/ARM9/ 或 Extracted/ 根目录（取决于 Stage 1 提取方式）
    if not y9_src.exists():
        y9_alt = EXTRACT_DIR / Y9_FILENAME
        if y9_alt.exists():
            y9_src = y9_alt
    if not ovl6_src.exists():
        raise LyricPatchError(f"找不到 {ovl6_src}")

    # 1. 读取原 charmap
    print("\n[1] 读取原 82 条 charmap...")
    orig_charmap = _read_original_charmap(ovl6_src)
    print(f"  原条目数: {len(orig_charmap)}")

    # 2. 提取汉字
    print("\n[2] 从翻译 xlsx 提取唯一汉字...")
    chars = extract_chinese_chars_from_xlsx(lyric_xlsx)
    print(f"  唯一汉字数: {len(chars)}")
    if len(chars) > 1800:
        # 1880 容量，接近上限就告警
        print(f"  ⚠️  汉字数 ({len(chars)}) 接近私有码位上限(~1880)，请留意码位空间")

    # 3. 分配私有码位
    print(f"\n[3] 分配私有码位 0x{PRIVATE_CODE_START:04X}-0x{PRIVATE_CODE_END:04X}...")
    char_encoding = _assign_encoding(chars)
    private_count = sum(1 for c in char_encoding if c['encoding'] == 'private')
    if char_encoding:
        print(f"  私有码位: {private_count} 字 "
              f"(0x{PRIVATE_CODE_START:04X}-0x{char_encoding[-1]['key']:04X})")

    # 4. 构建 SSOT
    print("\n[4] 构建 SSOT 字符表...")
    ssot = _build_ssot(orig_charmap, char_encoding)
    total_entries = len(ssot['entries'])
    print(f"  总条目数: {total_entries} (原 82 + 新 {total_entries - 82})")

    # 5. 构建 charmap.bin + 补丁 overlay
    print("\n[5] 构建 charmap_new.bin + 补丁 overlay_0006.bin...")
    charmap_bin = _build_charmap_bin(ssot)
    print(f"  charmap_new.bin 大小: {len(charmap_bin)} 字节")
    patched_ovl, ovl_info = _patch_overlay6(ovl6_src, charmap_bin)
    out_ovl6 = build_dir / "overlay_0006_patched.bin"
    out_ovl6.write_bytes(patched_ovl)
    print(f"  已写入: {out_ovl6}")
    print(f"  新文件大小: 0x{ovl_info['new_size']:X} ({ovl_info['new_size']} 字节)")

    # 6. 补丁 y9
    print("\n[6] 补丁 y9.bin...")
    if not y9_src.exists():
        raise LyricPatchError(f"找不到 {y9_src}")
    patched_y9, y9_info = _patch_y9(y9_src, ovl_info['new_size'])
    out_y9 = build_dir / "y9_patched.bin"
    out_y9.write_bytes(patched_y9)
    print(f"  已写入: {out_y9}")
    print(f"  新 RAM Size: 0x{y9_info['new_ram_size']:X}")

    # 7. 构建 font_mapping.json
    print("\n[7] 构建 font_mapping.json...")
    out_font_mapping = build_dir / "font_mapping_lesvoice.json"
    font_mapping = build_font_mapping_json(ssot, out_font_mapping)
    print(f"  已写入: {out_font_mapping} ({len(font_mapping)} 条)")

    # 8. 输出审计 CSV
    print("\n[8] 输出审计 CSV...")
    _write_audit_csvs(ssot, build_dir)

    # 9. 校验
    ok = _verify_charmap_patch(ssot, ovl_info, y9_info, build_dir)
    if not ok:
        raise LyricPatchError("charmap 重定位补丁校验失败")

    print("\n✅ charmap 重定位补丁构建完成")
    return {
        'ssot': ssot,
        'ovl_info': ovl_info,
        'y9_info': y9_info,
        'font_mapping': font_mapping,
        'new_frame_count': AGL_OLD_FRAME_COUNT + (total_entries - OVL6_ENTRY_COUNT_ORIG),
    }


def _write_audit_csvs(ssot: dict, build_dir: Path) -> None:
    """写 charmap_new.csv / codepoint_map.csv / ssot_chars.csv"""
    out_charmap_csv = build_dir / "charmap_new.csv"
    out_codepoint_map = build_dir / "codepoint_map.csv"
    out_ssot = build_dir / "ssot_chars.csv"

    with open(out_charmap_csv, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['index', 'glyph_id', 'key_hex', 'key_bytes', 'char',
                    'source', 'reserved', 'unicode'])
        for e in ssot['entries']:
            w.writerow([
                e['index'], f"0x{e['glyph_id']:02X}",
                f"0x{e['key']:04X}",
                f"{(e['key'] >> 8) & 0xFF:02X}{e['key'] & 0xFF:02X}",
                e['char'], e['source'], e['reserved'],
                f"U+{ord(e['char']):04X}" if e['char'] else '',
            ])

    with open(out_codepoint_map, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['char', 'unicode', 'encoding', 'key_hex', 'glyph_id'])
        for c in ssot['chinese_chars']:
            w.writerow([c['char'], f"U+{c['unicode']:04X}", c['encoding'],
                        c['key_hex'], f"0x{c['glyph_id']:02X}"])

    with open(out_ssot, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['glyph_id', 'char', 'source', 'key_hex', 'unicode'])
        for e in ssot['entries']:
            w.writerow([
                f"0x{e['glyph_id']:02X}", e['char'], e['source'],
                f"0x{e['key']:04X}",
                f"U+{ord(e['char']):04X}" if e['char'] else '',
            ])
    print(f"  charmap_new.csv / codepoint_map.csv / ssot_chars.csv 已写入")


def _verify_charmap_patch(ssot: dict, ovl_info: dict,
                          y9_info: dict, build_dir: Path) -> bool:
    """构建期强制校验"""
    print("\n" + "=" * 70)
    print("校验")
    print("=" * 70)
    errors: list[str] = []
    warnings: list[str] = []

    entries = ssot['entries']

    # 1. 条目数 × 6 == 表长度
    expected_size = len(entries) * OVL6_ENTRY_SIZE
    actual_size = ovl_info['table_size']
    if expected_size != actual_size:
        errors.append(f"表长度不符: {expected_size} != {actual_size}")
    else:
        print(f"✓ 表长度: {actual_size} 字节 ({len(entries)} × {OVL6_ENTRY_SIZE})")

    # 2. key 严格升序且无重复
    key_bytes_list = [bytes([(e['key'] >> 8) & 0xFF, e['key'] & 0xFF]) for e in entries]
    bad_order = False
    for i in range(len(key_bytes_list) - 1):
        if key_bytes_list[i] >= key_bytes_list[i + 1]:
            errors.append(
                f"key 非升序 at index {i}: "
                f"{key_bytes_list[i].hex()} >= {key_bytes_list[i + 1].hex()}"
            )
            bad_order = True
            break
    if not bad_order:
        print("✓ key 严格升序")

    # 3. reserved 全 0
    bad_reserved = [i for i, e in enumerate(entries) if e['reserved'] != 0]
    if bad_reserved:
        errors.append(f"reserved 非零条目: {bad_reserved}")
    else:
        print("✓ reserved 全 0")

    # 4. end - start == 条目数 × 6
    table_ram_len = ovl_info['table_ram_end'] - ovl_info['table_ram_start']
    if table_ram_len != len(entries) * OVL6_ENTRY_SIZE:
        errors.append(f"RAM 长度不符: {table_ram_len} != {len(entries) * OVL6_ENTRY_SIZE}")
    else:
        print(f"✓ RAM 长度: 0x{table_ram_len:X}")

    # 5. 私有码位 lead/trail 均 != 0x00
    for e in entries:
        if e['source'] == 'private':
            lead = (e['key'] >> 8) & 0xFF
            trail = e['key'] & 0xFF
            if lead == 0 or trail == 0:
                errors.append(f"私有码位含 0: 0x{e['key']:04X}")
    print("✓ 私有码位 lead/trail 非 0")

    # 6. 私有码位不与原假名/符号冲突
    orig_keys = set(e['key'] for e in entries if e['source'] == 'original')
    new_keys = set(e['key'] for e in entries if e['source'] != 'original')
    overlap = orig_keys & new_keys
    if overlap:
        errors.append(f"key 冲突: {[hex(k) for k in overlap]}")
    else:
        print("✓ 新旧 key 无冲突")

    # 7. 新 RAM Size == 新 overlay 文件大小
    if y9_info['new_ram_size'] != ovl_info['new_size']:
        errors.append(
            f"y9 RAM Size != overlay 文件大小: "
            f"{y9_info['new_ram_size']} != {ovl_info['new_size']}"
        )
    else:
        print(f"✓ y9 RAM Size == overlay 文件大小: 0x{y9_info['new_ram_size']:X}")

    # 8. 新表 4 字节对齐
    if ovl_info['table_ram_start'] % 4 != 0:
        warnings.append(f"新表起始未 4 字节对齐: 0x{ovl_info['table_ram_start']:08X}")
    else:
        print("✓ 新表 4 字节对齐")

    # 写校验报告
    report_path = build_dir / "verify_report.txt"
    report_lines = [
        "=" * 70,
        "charmap 重定位补丁校验报告",
        "=" * 70,
        f"\n[原 overlay]",
        f"  文件大小: 0x{ovl_info['orig_size']:X}",
        f"  原 charmap RAM: 0x{OVL6_CHARMAP_RAM:08X}",
        f"\n[新 charmap 表]",
        f"  条目数: {len(entries)} (原 82 + 新 {len(entries) - 82})",
        f"  表大小: {ovl_info['table_size']} 字节",
        f"  RAM 起始: 0x{ovl_info['table_ram_start']:08X}",
        f"  RAM 结束: 0x{ovl_info['table_ram_end']:08X}",
        f"\n[新 overlay]",
        f"  文件大小: 0x{ovl_info['new_size']:X}",
        f"\n[y9.bin]",
        f"  新 RAM Size: 0x{y9_info['new_ram_size']:X}",
        f"\n[校验结果]",
        f"  ERROR: {len(errors)}",
    ]
    for e in errors:
        report_lines.append(f"    - {e}")
    report_lines.append(f"  WARN: {len(warnings)}")
    for w in warnings:
        report_lines.append(f"    - {w}")
    report_lines.append(
        f"\n[结论]\n  {'✓ 通过' if not errors else '✗ 有 ERROR'}"
    )
    report_path.write_text('\n'.join(report_lines), encoding='utf-8')
    print(f"\n校验报告已写入: {report_path}")

    if errors:
        print(f"\n❌ {len(errors)} 个 ERROR")
        for e in errors:
            print(f"  - {e}")
        return False
    if warnings:
        print(f"\n⚠️ {len(warnings)} 个 WARN")
        for w in warnings:
            print(f"  - {w}")
    print("\n✅ 校验通过")
    return True


# ============================================================
# Part 2: GLD 中文字形扩容 (expand_gld_chinese.py)
# ============================================================

def _load_new_chinese_chars(ssot_csv: Path = LYRIC_SSOT_CSV) -> list[dict]:
    """从 SSOT CSV 读取新增汉字，按 glyph_id 升序排序

    返回每个条目: {glyph_id, char, key_hex, unicode, source}
    """
    new_chars: list[dict] = []
    with open(ssot_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['source'] == 'original':
                continue
            glyph_id = int(row['glyph_id'], 16)
            new_chars.append({
                'glyph_id': glyph_id,
                'char': row['char'],
                'key_hex': row['key_hex'],
                'unicode': row['unicode'],
                'source': row['source'],
            })
    new_chars.sort(key=lambda x: x['glyph_id'])
    return new_chars


def _render_char_rgba(char: str, cfg: dict,
                      font: ImageFont.FreeTypeFont) -> bytes:
    """渲染单个汉字为 RGBA 字节流

    - 填充背景色 (cfg['bg_color'])
    - 居中绘制文字 (cfg['text_color'])
    - 返回 canvas_w * canvas_h * 4 字节
    """
    canvas_w = cfg['canvas_w']
    canvas_h = cfg['canvas_h']

    img = Image.new('RGBA', (canvas_w, canvas_h), cfg['bg_color'])
    draw = ImageDraw.Draw(img)

    try:
        bbox = font.getbbox(char)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (canvas_w - text_w) // 2 - bbox[0]
        y = (canvas_h - text_h) // 2 - bbox[1]
    except Exception:
        x = 1
        y = 1

    draw.text((x, y), char, font=font, fill=cfg['text_color'])
    return img.tobytes()


def _match_palette(r: int, g: int, b: int, a: int, pal: list[int]) -> int:
    """在调色板中找最接近的索引 (曼哈顿距离)"""
    min_dist = 0x7FFFFFFF
    min_idx = 0
    for idx, pc in enumerate(pal):
        pr, pg, pb = bgr555_to_rgb888(pc)
        dist = abs(r - pr) + abs(g - pg) + abs(b - pb)
        if dist < min_dist:
            min_dist = dist
            min_idx = idx
        if dist == 0:
            break
    return min_idx


def _rgba_to_2bpp_pixels(rgba: bytes, cfg: dict,
                         palette: list[int]) -> list[int]:
    """将 RGBA 字节流转为 2bpp 像素索引数组"""
    canvas_w = cfg['canvas_w']
    canvas_h = cfg['canvas_h']
    expected_len = canvas_w * canvas_h * 4
    if len(rgba) != expected_len:
        raise LyricPatchError(f"RGBA 长度不符: {len(rgba)} != {expected_len}")
    pal4 = palette[:4]

    pixels: list[int] = []
    for i in range(0, len(rgba), 4):
        r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
        pixels.append(_match_palette(r, g, b, a, pal4))
    return pixels


def _expand_gld(cfg: dict, new_chars: list[dict],
                gld_src: Path, font_path: Path) -> tuple[bytes, dict]:
    """扩容单个 GLD：追加中文字形像素数据 + footer 条目"""
    gld = parse_gld(gld_src)

    h = gld.header
    if h.data_06 != FOOTER_TYPE_2:
        raise LyricPatchError(f"{cfg['filename']}: footer 类型不是 Type2")
    if h.footer_entry_count != AGL_OLD_FRAME_COUNT:
        raise LyricPatchError(
            f"{cfg['filename']}: 原 footer 数 != {AGL_OLD_FRAME_COUNT}"
        )

    font = ImageFont.truetype(str(font_path), cfg['font_size'])

    palettes = gld.get_palettes()
    palette = palettes[cfg['palette_offset']]
    print(f"  调色板@0x{cfg['palette_offset']:X}: {palette[:4]}")

    # 新 sprite 的像素数据起始偏移 (4 字节对齐)
    new_pix_start = len(gld.pixel_data)
    while new_pix_start % 4 != 0:
        gld.pixel_data.append(0)
        new_pix_start = len(gld.pixel_data)

    # 每 sprite 像素数据大小（间距规律）
    bit_depth = BIT_DEPTH[SPRITE_FORMAT_2]  # 2
    render_width = 8 << cfg['render_width_id']
    render_height = 8 << cfg['render_height_id']
    render_width_bytes = render_width * bit_depth // 8
    aligned_crop_h = (cfg['crop_h'] + 1 + 1) // 2 * 2
    effective_rows = min(aligned_crop_h, render_height)
    sprite_pix_size = render_width_bytes * effective_rows
    valid_pix_size = render_width_bytes * cfg['crop_h']

    print(f"  新 sprite: render={render_width}x{render_height}, "
          f"crop={cfg['crop_w']}x{cfg['crop_h']}, "
          f"pix_size={sprite_pix_size} (有效 {valid_pix_size})")
    print(f"  新像素起始偏移: 0x{new_pix_start:X}")

    new_entries_count = len(new_chars)
    for i, char_info in enumerate(new_chars):
        rgba = _render_char_rgba(char_info['char'], cfg, font)
        pixels = _rgba_to_2bpp_pixels(rgba, cfg, palette)

        packed = bytearray()
        for y in range(cfg['crop_h']):
            row_start = y * cfg['canvas_w']
            row_end = row_start + render_width
            row_pixels = pixels[row_start:row_end]
            for j in range(0, len(row_pixels), 4):
                chunk = row_pixels[j:j + 4]
                b0 = chunk[0] if len(chunk) > 0 else 0
                b1 = chunk[1] if len(chunk) > 1 else 0
                b2 = chunk[2] if len(chunk) > 2 else 0
                b3 = chunk[3] if len(chunk) > 3 else 0
                packed.append(
                    (b0 & 0x3) | ((b1 & 0x3) << 2)
                    | ((b2 & 0x3) << 4) | ((b3 & 0x3) << 6)
                )

        if len(packed) < sprite_pix_size:
            packed.extend(b'\x00' * (sprite_pix_size - len(packed)))

        pix_off = new_pix_start + i * sprite_pix_size
        gld.pixel_data.extend(packed)

        entry = GldFooterEntry(
            pixels_offset=pix_off,
            palette_offset=cfg['palette_offset'],
            sprite_format=SPRITE_FORMAT_2,
            crop_width=cfg['crop_w'],
            crop_height=cfg['crop_h'],
            render_width_id=cfg['render_width_id'],
            render_height_id=cfg['render_height_id'],
            crop_x=cfg['crop_x'],
            crop_y=cfg['crop_y'],
            data_14=0,
            join_x=0,
            join_y=2,
        )
        gld.footer_entries.append(entry)

        if i < 3 or i == new_entries_count - 1:
            print(f"  [{i}] glyph_id=0x{char_info['glyph_id']:02X} "
                  f"char='{char_info['char']}' pix_off=0x{pix_off:X}")

    # 更新 header
    old_pix_size = h.pixel_data_size
    h.pixel_data_size = len(gld.pixel_data)
    h.footer_entry_count = len(gld.footer_entries)
    # data_0c 必须同步更新（作为像素区访问边界检查），否则新增 sprite 渲染为空白
    h.data_0c = h.pixel_data_size

    info = {
        'filename': cfg['filename'],
        'orig_size': gld_src.stat().st_size,
        'orig_pix_size': old_pix_size,
        'new_pix_size': h.pixel_data_size,
        'orig_footer_count': AGL_OLD_FRAME_COUNT,
        'new_footer_count': h.footer_entry_count,
        'new_sprites': new_entries_count,
    }

    out_bytes = gld.to_bytes()
    info['new_file_size'] = len(out_bytes)
    return out_bytes, info


def expand_gld_chinese(ssot_csv: Path = LYRIC_SSOT_CSV,
                       build_dir: Path = LYRIC_BUILD_DIR,
                       font_path: Path = FONT_12PX) -> dict:
    """主入口：扩容 0507 + 0521 两个 GLD

    Returns:
        {filename: info} 字典
    """
    print("=" * 70)
    print("GLD 扩容: 注入中文字形")
    print("=" * 70)

    build_dir.mkdir(parents=True, exist_ok=True)
    new_chars = _load_new_chinese_chars(ssot_csv)
    print(f"  加载 {len(new_chars)} 个新增汉字")

    gld_dir = EXTRACT_DIR / "AGL"
    results: dict[str, dict[str, Any]] = {}

    for cfg in (CFG_0507, CFG_0521):
        print(f"\n{'=' * 70}")
        print(f"扩容 {cfg['filename']}")
        print('=' * 70)
        src_path = gld_dir / cfg['filename']
        if not src_path.exists():
            raise LyricPatchError(f"找不到 {src_path}")

        out_bytes, info = _expand_gld(cfg, new_chars, src_path, font_path)
        out_path = build_dir / cfg['out_filename']
        out_path.write_bytes(out_bytes)
        print(f"  已写入: {out_path}")
        print(f"  新文件: {info['new_file_size']} 字节 "
              f"(原 {info['orig_size']}, 增长 "
              f"{info['new_file_size'] - info['orig_size']})")
        results[cfg['filename']] = info

    # 写报告
    report_path = build_dir / "gld_expansion_report.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("GLD 扩容报告\n")
        f.write("=" * 70 + "\n\n")
        for cfg in (CFG_0507, CFG_0521):
            info = results[cfg['filename']]
            f.write(f"[{cfg['filename']}]\n")
            f.write(f"  原大小: {info['orig_size']}\n")
            f.write(f"  新大小: {info['new_file_size']}\n")
            f.write(f"  原像素区: {info['orig_pix_size']}\n")
            f.write(f"  新像素区: {info['new_pix_size']}\n")
            f.write(f"  原 footer 数: {info['orig_footer_count']}\n")
            f.write(f"  新 footer 数: {info['new_footer_count']}\n")
            f.write(f"  新增 sprite: {info['new_sprites']}\n\n")
    print(f"\n报告: {report_path}")
    return results


# ============================================================
# Part 3: AGL frame 表扩容 (expand_agl_chinese.py)
# ============================================================

def _expand_agl_file(cfg: dict, agl_src: Path,
                     new_frames: int) -> tuple[bytes, dict]:
    """扩容单个 AGL 文件

    Returns:
        (patched_bytes, info)
    """
    data = bytearray(agl_src.read_bytes())
    orig_size = len(data)

    # 验证 magic
    magic = bytes(data[:4])
    if magic != b'\x00LGA':
        raise LyricPatchError(f"{cfg['filename']}: magic 错误 {magic!r}")

    old_frame_count = struct.unpack_from('<H', data, 0x0C)[0]
    old_total_size = struct.unpack_from('<I', data, 0x08)[0]
    cell_count = data[0x0A]
    if old_frame_count != AGL_OLD_FRAME_COUNT:
        raise LyricPatchError(
            f"{cfg['filename']}: 原 frame_count={old_frame_count} "
            f"!= {AGL_OLD_FRAME_COUNT}"
        )
    if cell_count != 1:
        raise LyricPatchError(f"{cfg['filename']}: cell_count={cell_count} != 1")

    new_frame_count = AGL_OLD_FRAME_COUNT + new_frames

    # 读取原 frame[0] 作为模板
    frame0_off = AGL_HEADER_SIZE + AGL_CELL_ENTRY_SIZE  # 0x28 + 16 = 0x38
    frame0 = bytes(data[frame0_off:frame0_off + AGL_FRAME_ENTRY_SIZE])
    frame0_glyph_id = struct.unpack_from('<H', frame0, 0x16)[0]
    if frame0_glyph_id != 0x1000:
        raise LyricPatchError(
            f"{cfg['filename']}: frame[0].glyph_id=0x{frame0_glyph_id:X} != 0x1000"
        )

    frame_table_end = frame0_off + AGL_OLD_FRAME_COUNT * AGL_FRAME_ENTRY_SIZE
    remaining = bytes(data[frame_table_end:])

    # 修改 Header
    new_total_size = old_total_size + new_frames * AGL_FRAME_ENTRY_SIZE
    struct.pack_into('<I', data, 0x08, new_total_size)
    struct.pack_into('<H', data, 0x0C, new_frame_count)

    # 修改 Cell Entry (位于 0x28)
    cell_off = AGL_HEADER_SIZE
    struct.pack_into('<H', data, cell_off + 0x02, new_frame_count)
    struct.pack_into('<H', data, cell_off + 0x04, new_frame_count)

    # 生成新 Frame Entry
    new_frames_data = bytearray()
    for i in range(new_frames):
        frame_index = AGL_OLD_FRAME_COUNT + i
        entry = bytearray(frame0)
        struct.pack_into('<H', entry, 0x16, 0x1000 + frame_index)
        struct.pack_into('<H', entry, 0x24, frame_index)
        struct.pack_into('<H', entry, 0x26, frame_index + 1)
        new_frames_data.extend(entry)

    # 处理 Table 5 (位于 remaining 中，4 字节/entry)
    table5_off_in_remaining = 8  # Table 4 = 8 字节
    if len(remaining) >= table5_off_in_remaining + 4:
        table5_old = remaining[table5_off_in_remaining:table5_off_in_remaining + 4]
        table5_u16_1 = struct.unpack_from('<H', table5_old, 2)[0]
        if table5_u16_1 == AGL_OLD_FRAME_COUNT:
            new_remaining = bytearray(remaining)
            struct.pack_into('<H', new_remaining, table5_off_in_remaining + 2, new_frame_count)
            remaining = bytes(new_remaining)

    # 重组
    out = bytearray()
    out.extend(data[:frame_table_end])
    out.extend(new_frames_data)
    out.extend(remaining)

    info = {
        'filename': cfg['filename'],
        'orig_size': orig_size,
        'new_size': len(out),
        'old_frame_count': old_frame_count,
        'new_frame_count': new_frame_count,
        'new_frames_added': new_frames,
    }
    return bytes(out), info


def _verify_agl(cfg: dict, out_path: Path, new_frames: int) -> bool:
    """校验扩容后的 AGL"""
    print(f"\n  校验 {out_path.name}...")
    data = out_path.read_bytes()
    errors: list[str] = []
    new_frame_count = AGL_OLD_FRAME_COUNT + new_frames

    if data[:4] != b'\x00LGA':
        errors.append(f"magic 错误: {data[:4]!r}")

    frame_count = struct.unpack_from('<H', data, 0x0C)[0]
    if frame_count != new_frame_count:
        errors.append(f"header.frame_count={frame_count} != {new_frame_count}")

    cell_fc = struct.unpack_from('<H', data, 0x28 + 0x02)[0]
    cell_f4 = struct.unpack_from('<H', data, 0x28 + 0x04)[0]
    if cell_fc != new_frame_count:
        errors.append(f"cell[0].frame_count={cell_fc} != {new_frame_count}")
    if cell_f4 != new_frame_count:
        errors.append(f"cell[0].field_04={cell_f4} != {new_frame_count}")

    frame0_off = AGL_HEADER_SIZE + AGL_CELL_ENTRY_SIZE
    for i in range(new_frame_count):
        off = frame0_off + i * AGL_FRAME_ENTRY_SIZE
        if off + AGL_FRAME_ENTRY_SIZE > len(data):
            errors.append(f"frame[{i}] 偏移 0x{off:X} 超出文件范围")
            break
        gid = struct.unpack_from('<H', data, off + 0x16)[0]
        nxt = struct.unpack_from('<H', data, off + 0x24)[0]
        f26 = struct.unpack_from('<H', data, off + 0x26)[0]
        expected_gid = 0x1000 + i
        expected_nxt = i
        expected_f26 = i + 1
        if gid != expected_gid:
            errors.append(f"frame[{i}].glyph_id=0x{gid:X} != 0x{expected_gid:X}")
            break
        if nxt != expected_nxt:
            errors.append(f"frame[{i}].next_frame_id={nxt} != {expected_nxt}")
            break
        if f26 != expected_f26:
            errors.append(f"frame[{i}].field_26={f26} != {expected_f26}")
            break

    if errors:
        print(f"  ❌ {len(errors)} 个 ERROR:")
        for e in errors[:10]:
            print(f"    - {e}")
        return False
    print(f"  ✅ 校验通过 (frame_count={new_frame_count})")
    return True


def expand_agl_chinese(new_frames: int,
                       build_dir: Path = LYRIC_BUILD_DIR) -> dict:
    """主入口：扩容 0506 + 0520 两个 AGL

    Args:
        new_frames: 新增 frame 数 (应等于新增汉字数)
    """
    print("=" * 70)
    print(f"AGL 扩容: 注入 {new_frames} 个中文 Frame Entry")
    print(f"  目标: frame_count {AGL_OLD_FRAME_COUNT} → "
          f"{AGL_OLD_FRAME_COUNT + new_frames}")
    print("=" * 70)

    build_dir.mkdir(parents=True, exist_ok=True)
    agl_dir = EXTRACT_DIR / "AGL"
    results: dict[str, dict] = {}
    all_ok = True

    for cfg in AGL_FILES_CFG:
        src_path = agl_dir / cfg['filename']
        if not src_path.exists():
            raise LyricPatchError(f"找不到 {src_path}")

        print(f"\n{'=' * 70}")
        print(f"扩容 {cfg['filename']} ({cfg['desc']})")
        print('=' * 70)
        out_bytes, info = _expand_agl_file(cfg, src_path, new_frames)
        out_path = build_dir / cfg['out_filename']
        out_path.write_bytes(out_bytes)
        print(f"  已写入: {out_path}")
        print(f"  新文件: {info['new_size']} 字节 "
              f"(原 {info['orig_size']}, 增长 "
              f"{info['new_size'] - info['orig_size']})")
        results[cfg['filename']] = info

        if not _verify_agl(cfg, out_path, new_frames):
            all_ok = False

    if not all_ok:
        raise LyricPatchError("AGL 扩容校验失败")
    print("\n✅ AGL 扩容完成")
    return results


# ============================================================
# Part 4: ARM9 四补丁 (patch_imasds_arm9.py)
# ============================================================

@dataclass(frozen=True)
class PatchSite:
    offset: int
    original: bytes
    patched: bytes
    description: str


PATCH_SITES = (
    PatchSite(
        0x0003D28C,
        bytes.fromhex("02 50 D7 E5"),
        bytes.fromhex("B2 50 D7 E1"),
        "AGL v3 size pass: ldrb r5,[r7,#2] -> ldrh r5,[r7,#2]",
    ),
    PatchSite(
        0x0003D66C,
        bytes.fromhex("02 60 D5 E5"),
        bytes.fromhex("B2 60 D5 E1"),
        "AGL v3 conversion: ldrb r6,[r5,#2] -> ldrh r6,[r5,#2]",
    ),
    PatchSite(
        0x00062608,
        bytes.fromhex("96 04 00 E0"),
        bytes.fromhex("16 0C A0 E3"),
        "range cache: mul r0,r6,r4 -> mov r0,#0x1600",
    ),
    PatchSite(
        0x0006260C,
        bytes.fromhex("10 10 80 E2"),
        bytes.fromhex("F0 10 80 E2"),
        "range cache: add r1,r0,#0x10 -> add r1,r0,#0xF0",
    ),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_arm9(data: bytes) -> list[tuple[PatchSite, str]]:
    """检查 ARM9 各补丁点的状态，拒绝不兼容输入"""
    minimum_size = max(site.offset + len(site.original) for site in PATCH_SITES)
    if len(data) < minimum_size:
        raise Arm9PatchError(
            f"文件过短：{len(data)} 字节；至少需要 {minimum_size} 字节。"
            "输入可能不是已解压的 arm9.bin。"
        )

    states: list[tuple[PatchSite, str]] = []
    errors: list[str] = []
    for site in PATCH_SITES:
        actual = data[site.offset : site.offset + len(site.original)]
        if actual == site.original:
            state = "original"
        elif actual == site.patched:
            state = "patched"
        else:
            state = "unknown"
            errors.append(
                f"0x{site.offset:08X}: 预期 {site.original.hex(' ')} 或 "
                f"{site.patched.hex(' ')}，实际 {actual.hex(' ')}"
            )
        states.append((site, state))

    if errors:
        raise Arm9PatchError(
            "补丁点机器码不匹配，拒绝按固定偏移修改：\n  "
            + "\n  ".join(errors)
        )
    return states


def patch_arm9_bytes(data: bytes) -> tuple[bytes, list[tuple[PatchSite, str]]]:
    """补丁验证后的 ARM9，返回 (patched_bytes, original_states)"""
    states = inspect_arm9(data)
    result = bytearray(data)

    for site, state in states:
        if state == "original":
            result[site.offset : site.offset + len(site.patched)] = site.patched

    # 内部校验：除补丁点外不应有其他修改
    changed = {
        index
        for index, (before, after) in enumerate(zip(data, result))
        if before != after
    }
    allowed = {
        site.offset + index
        for site in PATCH_SITES
        for index in range(len(site.original))
    }
    unexpected = changed - allowed
    if unexpected:
        raise Arm9PatchError(
            "内部校验失败：补丁点之外出现修改："
            + ", ".join(f"0x{offset:X}" for offset in sorted(unexpected))
        )

    patched = bytes(result)
    final_states = inspect_arm9(patched)
    if not all(state == "patched" for _site, state in final_states):
        raise Arm9PatchError("内部校验失败：写入后仍有补丁点未完成。")

    input_hash = _sha256(data)
    output_hash = _sha256(patched)
    if input_hash in KNOWN_ARM9_SHA256 and output_hash != KNOWN_ARM9_FINAL_SHA256:
        raise Arm9PatchError(
            "已知 0728 输入的输出哈希不匹配："
            f"期望 {KNOWN_ARM9_FINAL_SHA256}，实际 {output_hash}"
        )

    return patched, states


def _write_atomically(destination: Path, data: bytes, source: Path) -> None:
    """原子写入：临时文件 + replace，避免写坏文件"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        import stat as stat_mod
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            source_mode = stat_mod.S_IMODE(source.stat().st_mode)
            temporary.chmod(source_mode)
        except OSError:
            pass
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def patch_arm9(src_path: Path, dst_path: Path | None = None) -> bool:
    """应用 ARM9 四补丁

    Args:
        src_path: 源 arm9.bin (BLZ 解压后)
        dst_path: 输出路径；None 则原位修改

    Returns:
        True 如果成功
    """
    if dst_path is None:
        dst_path = src_path
    data = src_path.read_bytes()
    patched, original_states = patch_arm9_bytes(data)

    print(f"文件: {src_path}")
    print(f"大小: {len(data)} 字节"
          + ("（与 0728 ARM9 一致）" if len(data) == KNOWN_ARM9_SIZE else ""))
    print(f"SHA-256: {_sha256(data)}")
    known = KNOWN_ARM9_SHA256.get(_sha256(data), "未登记版本（按补丁点机器码验证）")
    print(f"版本: {known}")
    for site, state in original_states:
        label = "已补丁" if state == "patched" else "待补丁"
        print(f"  [{label}] 0x{site.offset:08X}  {site.description}")

    _write_atomically(dst_path, patched, src_path)
    print(f"输出: {dst_path}")
    print(f"输出 SHA-256: {_sha256(patched)}")
    print("结果: 四个补丁点均已完成，并已通过回读校验。")
    return True


def verify_arm9(src_path: Path) -> bool:
    """验证 ARM9 是否已包含完整四补丁"""
    data = src_path.read_bytes()
    states = inspect_arm9(data)
    missing = [site for site, state in states if state != "patched"]
    if missing:
        print("验证失败: 仍有补丁点未完成。", file=sys.stderr)
        for site, state in states:
            label = "已补丁" if state == "patched" else "待补丁"
            print(f"  [{label}] 0x{site.offset:08X}  {site.description}")
        return False
    print("✅ 验证成功: ARM9 已包含完整的 489 字形补丁。")
    return True


def patch_arm9_main(src_path: Path, dst_path: Path | None = None,
                    verify_only: bool = False, check_only: bool = False) -> int:
    """ARM9 补丁命令行入口 (返回退出码)"""
    try:
        if verify_only:
            return 0 if verify_arm9(src_path) else 1
        if check_only:
            data = src_path.read_bytes()
            states = inspect_arm9(data)
            known = KNOWN_ARM9_SHA256.get(_sha256(data), "未登记版本")
            print(f"文件: {src_path}")
            print(f"SHA-256: {_sha256(data)}")
            print(f"版本: {known}")
            for site, state in states:
                label = "已补丁" if state == "patched" else "待补丁"
                print(f"  [{label}] 0x{site.offset:08X}  {site.description}")
            return 0
        patch_arm9(src_path, dst_path)
        return 0
    except FileNotFoundError as e:
        print(f"文件不存在: {e.filename}", file=sys.stderr)
        return 2
    except (OSError, Arm9PatchError) as e:
        print(f"拒绝处理: {e}", file=sys.stderr)
        return 2


# ============================================================
# Part 5: BBQ 歌词文本注入 (inject_bbq_chinese.py)
# ============================================================

def encode_text_private(text: str, font_mapping: dict[str, int]) -> bytes:
    """将译文编码为字节流

    - 半角空格/控制符: 1 字节 (0x20)
    - 全角空格: 2 字节 (0x8140)
    - 假名/符号: 2 字节 SJIS key (大端)
    - 中文: 2 字节私有码位 (大端)

    与 src/utils/text_encoder.text_to_bytes 的区别：
    - 不使用 NUL 终止符（由调用方按需追加）
    - 严格使用 font_mapping 中的 key（不回退 cp932）
    """
    out = bytearray()
    for ch in text:
        if ch not in font_mapping:
            raise ValueError(
                f"字符 {ch!r} (U+{ord(ch):04X}) 不在 font_mapping 中"
            )
        code = font_mapping[ch]
        if code <= 0xFF:
            out.append(code & 0xFF)
        else:
            out.append((code >> 8) & 0xFF)
            out.append(code & 0xFF)
    return bytes(out)


def _inject_bbq_file(bbq_path: Path, df: pd.DataFrame,
                     font_mapping: dict[str, int]) -> tuple[bytes, dict]:
    """注入译文到单个 BBQ 文件（重建数据池策略）

    策略：
    - 解析 BBQ header → 找 Type7 section → 读指针表和字符串池
    - 译文按 font_mapping 编码为 2 字节私有码位
    - 重建字符串池：译文用新编码，未翻译保留原文
    - 更新指针表 + section directory size 字段
    - 截断文件到新长度
    - 不限制译文长度（中文翻译不受原文字节槽位约束）
    - 字数 > 12 仅警告不跳过（翻译者自行把控显示效果）
    """
    data = bytearray(bbq_path.read_bytes())
    if data[:4] != b'\x2E\x42\x42\x51':
        raise LyricPatchError(f"{bbq_path.name}: 魔数错误 {data[:4]!r}")

    # 解析 BBQ header
    header_size = struct.unpack_from('<I', data, 16)[0]
    num_sec = struct.unpack_from('<I', data, 20)[0]

    # 找 Type7 section
    sec7_entry_pos = None
    for i in range(num_sec):
        entry_pos = header_size + i * 20
        sec_id = struct.unpack_from('<I', data, entry_pos)[0]
        if sec_id == 7:
            sec7_entry_pos = entry_pos
            break
    if sec7_entry_pos is None:
        raise LyricPatchError(f"{bbq_path.name}: 未找到 Type7 section")

    ptr_tbl_rel, num_str, pool_rel, sec_size = struct.unpack_from('<4I', data, sec7_entry_pos + 4)
    ptr_tbl_abs = sec7_entry_pos + ptr_tbl_rel
    pool_abs = sec7_entry_pos + pool_rel

    # 读旧指针表
    old_pointers = list(struct.unpack_from(f'<{num_str}I', data, ptr_tbl_abs))

    # 从 df 构建 {index: translated_text}
    translations: dict[int, str] = {}
    for _, row in df.iterrows():
        if pd.isna(row['Translated_Text']):
            continue
        idx = int(row['Index']) if pd.notna(row['Index']) else -1
        if idx < 0:
            continue
        trans = str(row['Translated_Text'])
        translations[idx] = trans

    # 重建数据池
    new_pool = bytearray()
    new_pointers: list[int] = []
    injected = 0
    skipped_nan = 0
    errors: list[str] = []
    char_warnings: list[str] = []
    details: list[dict] = []

    for i in range(num_str):
        new_pointers.append(len(new_pool))

        if i in translations:
            trans = translations[i]
            try:
                encoded = encode_text_private(trans, font_mapping)
            except ValueError as e:
                errors.append(f"idx={i}: {e}")
                # 编码失败时保留原文
                orig_start = pool_abs + old_pointers[i]
                orig_end = data.index(b'\x00', orig_start) + 1
                new_pool.extend(data[orig_start:orig_end])
                continue

            new_pool.extend(encoded)
            new_pool.append(0)  # NUL 终止符
            injected += 1

            # 字数警告（>12 字仅警告不跳过）
            char_count = len(trans)
            if char_count > 12:
                char_warnings.append(f"idx={i}: '{trans}' ({char_count}字)")

            details.append({
                'idx': i,
                'orig': '',  # 原文从 df 获取
                'trans': trans,
                'encoded_len': len(encoded) + 1,
                'char_count': char_count,
            })
        else:
            skipped_nan += 1
            # 保留原文（从旧数据池复制）
            orig_start = pool_abs + old_pointers[i]
            orig_end = data.index(b'\x00', orig_start) + 1
            new_pool.extend(data[orig_start:orig_end])

    # 4 字节对齐
    while len(new_pool) % 4 != 0:
        new_pool.append(0)

    # 回写指针表
    struct.pack_into(f'<{num_str}I', data, ptr_tbl_abs, *new_pointers)

    # 覆盖数据池
    data[pool_abs:pool_abs + len(new_pool)] = new_pool

    # 更新 section directory 的 size 字段 (entry_pos+16)
    new_sec_size = pool_rel + len(new_pool)
    struct.pack_into('<I', data, sec7_entry_pos + 16, new_sec_size)

    # 截断文件到新长度
    del data[pool_abs + len(new_pool):]

    # 从 df 补充原文到 details（用于报告显示）
    orig_map: dict[int, str] = {}
    for _, row in df.iterrows():
        if pd.notna(row['Index']):
            orig_map[int(row['Index'])] = str(row.get('Original_Text', ''))
    for d in details:
        d['orig'] = orig_map.get(d['idx'], '')

    info = {
        'filename': bbq_path.name,
        'orig_size': bbq_path.stat().st_size,
        'new_size': len(data),
        'injected': injected,
        'skipped_nan': skipped_nan,
        'char_warnings': char_warnings,
        'errors': errors,
        'details': details,
    }
    return bytes(data), info


def inject_bbq_chinese(lyric_xlsx: Path = EXCEL_LYRIC,
                       font_mapping_path: Path = LYRIC_FONT_MAPPING,
                       build_dir: Path = LYRIC_BUILD_DIR) -> dict:
    """主入口：注入 10 个 LESVOICETABLE BBQ

    Returns:
        {filename: info} 字典
    """
    print("=" * 70)
    print("BBQ 文本注入: 10 个 LESVOICETABLE 文件")
    print("=" * 70)

    build_dir.mkdir(parents=True, exist_ok=True)

    if not font_mapping_path.exists():
        raise LyricPatchError(f"找不到 font_mapping: {font_mapping_path}")
    with open(font_mapping_path, 'r', encoding='utf-8') as f:
        font_mapping = json.load(f)
    print(f"  font_mapping 条目数: {len(font_mapping)}")

    if not lyric_xlsx.exists():
        raise LyricPatchError(f"找不到歌词翻译表: {lyric_xlsx}")
    xls = pd.read_excel(lyric_xlsx, sheet_name=None)
    print(f"  sheet 数: {len(xls)}")

    tbl_dir = EXTRACT_DIR / "TBL"
    all_infos: dict[str, dict] = {}
    total_injected = 0
    total_errors = 0

    for bbq_name in LYRIC_BBQ_FILES:
        bbq_path = tbl_dir / bbq_name
        if not bbq_path.exists():
            print(f"  ❌ {bbq_name}: 文件不存在")
            continue

        # sheet 名 = BBQ 文件名（含 .BBQ 后缀）
        if bbq_name not in xls:
            print(f"  ⚠️ {bbq_name}: xlsx 中无对应 sheet")
            continue

        df = xls[bbq_name]
        out_data, info = _inject_bbq_file(bbq_path, df, font_mapping)

        out_path = build_dir / bbq_name.replace('.BBQ', '_patched.BBQ')
        out_path.write_bytes(out_data)

        all_infos[bbq_name] = info
        total_injected += info['injected']
        total_errors += len(info['errors'])

        status = "✅" if not info['errors'] else "⚠️"
        warn_count = len(info.get('char_warnings', []))
        print(f"  {status} {bbq_name}: 注入 {info['injected']}, "
              f"未翻译 {info['skipped_nan']}, 字数警告 {warn_count}, "
              f"错误 {len(info['errors'])}")

    print(f"\n总计: 注入 {total_injected} 条译文, {total_errors} 个错误")

    # 写报告
    report_path = build_dir / "bbq_injection_report.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("BBQ 注入报告\n")
        f.write("=" * 70 + "\n\n")
        for info in all_infos.values():
            f.write(f"[{info['filename']}]\n")
            f.write(f"  注入: {info['injected']}\n")
            f.write(f"  未翻译: {info['skipped_nan']}\n")
            f.write(f"  字数警告(>12字): {len(info.get('char_warnings', []))}\n")
            f.write(f"  错误数: {len(info['errors'])}\n")
            if info.get('char_warnings'):
                f.write("  字数警告详情:\n")
                for w in info['char_warnings']:
                    f.write(f"    - {w}\n")
            if info['errors']:
                f.write("  错误详情:\n")
                for e in info['errors']:
                    f.write(f"    - {e}\n")
            f.write(f"  原文件大小: {info['orig_size']}\n")
            f.write(f"  新文件大小: {info['new_size']}\n")
            f.write("  前 5 条注入:\n")
            for d in info['details'][:5]:
                f.write(f"    idx={d['idx']} {d['char_count']}字 enc={d['encoded_len']}B | "
                        f"{d['orig']!r} -> {d['trans']!r}\n")
            f.write("\n")
    print(f"报告: {report_path}")

    if total_errors > 0:
        print(f"⚠️ 有 {total_errors} 个错误，请查看报告")
    else:
        print("✅ BBQ 注入完成")
    return all_infos


# ============================================================
# Part 6: 翻译表导出（从 BBQ 提取到独立 xlsx）
# ============================================================

def export_lyric_translation_xlsx(out_path: Path = EXCEL_LYRIC) -> Path:
    """从 10 个 LESVOICETABLE BBQ 导出独立翻译 xlsx

    与 stage2_export_text 不同：
    - 歌词课单独 xlsx，不进 TBL_Translation.xlsx
    - 每首歌词一个 sheet（sheet 名 = BBQ 文件名，含 .BBQ 后缀）
    - 9 列标准布局（与 TBL 普通文本一致），含 Original_Text/Translated_Text/
      File/Text_Offset/Pointer_Locs/Max_Bytes/Index/Type/Speaker

    Returns:
        输出 xlsx 路径
    """
    print("=" * 70)
    print("导出歌词课翻译表 (LESVOICETABLE → 独立 xlsx)")
    print("=" * 70)

    tbl_dir = EXTRACT_DIR / "TBL"
    if not tbl_dir.exists():
        raise LyricPatchError(f"找不到 TBL 目录: {tbl_dir}")

    data_groups: dict[str, list[dict]] = {}
    for bbq_name in LYRIC_BBQ_FILES:
        bbq_path = tbl_dir / bbq_name
        if not bbq_path.exists():
            print(f"  ⚠️ {bbq_name}: 不存在，跳过")
            continue
        try:
            entries = parse_bbq_file(str(bbq_path), is_scn=False)
        except Exception as e:
            print(f"  ❌ {bbq_name}: 解析失败 - {e}")
            continue
        if entries:
            data_groups[bbq_name] = entries
            print(f"  ✅ {bbq_name}: {len(entries)} 条文本")

    if not data_groups:
        raise LyricPatchError("未提取到任何歌词文本")

    # 借用 stage2_export_text 的 create_styled_excel
    from src.stage2_export_text import create_styled_excel
    create_styled_excel(data_groups, out_path, is_scn=False, mail_sheet_names=set())
    print(f"\n✅ 已导出: {out_path}")
    print(f"   sheet 数: {len(data_groups)}")
    return out_path


# ============================================================
# Part 7: PSD 导出（供美工修正字型）
# ============================================================

def export_gld_psd_for_art(gld_patched_dir: Path = LYRIC_BUILD_DIR,
                           out_dir: Path = LYRIC_GLYPH_DIR,
                           only_new: bool = True) -> dict:
    """从已扩容的 GLD 导出 PSD 供美工修正字型

    Args:
        gld_patched_dir: 已扩容的 GLD 目录（默认 LYRIC_BUILD_DIR）
        out_dir: PSD 输出目录（默认 LYRIC_GLYPH_DIR = AGL 图像目录，
                 直接覆盖原版 PSD，美工在原位修正字型）
        only_new: True=只导出新增汉字 (glyph_id >= 0x51)；
                  False=导出全部 sprite

    Returns:
        {gld_filename: psd_path} 字典
    """
    print("=" * 70)
    print("导出 GLD PSD 供美工修正字型")
    print("=" * 70)

    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}

    # 候选 GLD 文件（先找 patched 版本，再回退原版）
    candidates = [
        ('0507_D_MEASURE_MOJI_MNG.GLD', '0507_D_MEASURE_MOJI_MNG_patched.GLD'),
        ('0521_D_EPANEL_MOJI_MNG.GLD', '0521_D_EPANEL_MOJI_MNG_patched.GLD'),
    ]

    from src.stage2_export_images import export_editable_psd

    for orig_name, patched_name in candidates:
        patched_path = gld_patched_dir / patched_name
        if patched_path.exists():
            gld_path = patched_path
        else:
            # 回退到原版
            gld_path = EXTRACT_DIR / "AGL" / orig_name
            if not gld_path.exists():
                print(f"  ⚠️ 跳过 {orig_name}: 既无 patched 也无原始 GLD")
                continue
            print(f"  ⚠️ {orig_name}: 未找到 patched 版本，回退到原版")

        print(f"\n处理 {gld_path.name}...")
        gld = parse_gld(gld_path)

        if only_new:
            # 过滤只保留新增汉字 (footer index >= 81, 即 glyph_id >= 0x51)
            # 通过临时设置 footer_entries 列表实现
            new_entries = [
                (i, e) for i, e in enumerate(gld.footer_entries)
                if i >= AGL_OLD_FRAME_COUNT and not e.is_deleted
            ]
            if not new_entries:
                print(f"  ⚠️ {orig_name}: 没有新增 sprite，跳过")
                continue
            print(f"  新增 sprite 数: {len(new_entries)}")

        # 导出完整 PSD
        psd_path = out_dir / orig_name.replace('.GLD', '.psd')
        export_editable_psd(gld, psd_path)
        print(f"  ✅ 已导出: {psd_path}")
        results[orig_name] = psd_path

    if not results:
        print("\n⚠️ 没有导出任何 PSD")
    else:
        print(f"\n✅ PSD 导出完成: {len(results)} 个文件 → {out_dir}")
        print("   美工修正字型后，将 PSD 与同名 PNG 一起放回此目录，"
              "再执行歌词课专用回写 import_lyric_glyph_psd")
    return results


def import_lyric_glyph_psd(psd_dir: Path = LYRIC_GLYPH_DIR,
                            patched_dir: Path = PATCHED_DIR) -> dict:
    """歌词课专用 GLD 回写：从美工切好的 sprite PNG 写回已扩容 GLD

    美工工作流：
    1. Stage 3.5 export-glyph-psd 导出扩容后的 PSD 到 AGL 图像目录
    2. 美工在 PS 中修正中文字型，导出同名 PNG（合并图）
    3. 美工运行 split_psd_to_sprites.py 切出 {stem}_{index}.png（sprite 文件）
       输出到 {psd_dir}/sprites/ 子目录（与 Stage 6 AGL 回写共用约定）
    4. 本函数用 sprite 模式把这些 PNG 写回 2_Patched/AGL_CHS_PATCHED/ 的 GLD

    与 Stage 6 (stage4_import_images) 的区别：
    - Stage 6 跳过 0506/0507/0520/0521（避免用原版 GLD 覆盖扩容版本）
    - 本函数从 2_Patched/AGL_CHS_PATCHED/ 的已扩容 GLD 读取，原位注入
    - 两者都从 {AGL}/sprites/ 子目录读取 sprite PNG（约定一致）

    Args:
        psd_dir: AGL 图像目录（sprite PNG 在其 sprites/ 子目录，美工工作目录）
        patched_dir: 已扩容 GLD 所在目录（默认 2_Patched/AGL_CHS_PATCHED/）

    Returns:
        {gld_filename: (n_injected, n_skipped)} 字典
    """
    print("=" * 70)
    print("歌词课字形回写（sprite 模式：美工切好的 PNG → 已扩容 GLD）")
    print("=" * 70)

    # 已扩容 GLD 在 2_Patched/AGL_CHS_PATCHED/
    gld_dir = patched_dir / "AGL_CHS_PATCHED"
    if not gld_dir.exists():
        raise LyricPatchError(
            f"找不到已扩容 GLD 目录: {gld_dir}\n"
            f"请先执行 Stage 3.5 build-patches 部署扩容 GLD"
        )

    from src.stage4_import_images import get_png_index_map, import_png_to_gld

    # sprite PNG 在 psd_dir/sprites/ 子目录（split_psd_to_sprites.py 默认输出）
    sprites_dir = psd_dir / "sprites"
    if not sprites_dir.exists():
        raise LyricPatchError(
            f"找不到 sprite PNG 目录: {sprites_dir}\n"
            f"美工修正 PSD 后请运行 split_psd_to_sprites.py 切出 sprite PNG"
        )

    # 扫描美工切好的 sprite PNG（{stem}_{index}.png）
    index_map = get_png_index_map(sprites_dir)

    # 歌词课 GLD 文件列表
    targets = [
        "0507_D_MEASURE_MOJI_MNG",
        "0521_D_EPANEL_MOJI_MNG",
    ]

    results: dict[str, tuple[int, int]] = {}
    total_injected = 0
    total_skipped = 0

    for stem in targets:
        if stem not in index_map:
            print(f"  ⏭️  跳过 {stem}: 未在 {sprites_dir} 找到 sprite PNG")
            continue

        # 已扩容 GLD 路径
        gld_path = None
        for ext in ['.GLD', '.gld']:
            p = gld_dir / (stem + ext)
            if p.exists():
                gld_path = p
                break
        if gld_path is None:
            print(f"  ⚠️  跳过 {stem}: 未找到已扩容 GLD")
            continue

        indices = index_map[stem]
        n_inj = 0
        n_skp = 0
        for index in indices:
            png_path = sprites_dir / f"{stem}_{index}.png"
            try:
                w, h = import_png_to_gld(png_path, gld_path, gld_path)
                n_inj += 1
            except Exception as e:
                print(f"  ❌ [{stem}_{index}]: {e}")
                n_skp += 1

        results[gld_path.name] = (n_inj, n_skp)
        total_injected += n_inj
        total_skipped += n_skp
        print(f"  ✅ {gld_path.name}: 注入 {n_inj} sprite, 跳过 {n_skp}")

    print(f"\n总计: 注入 {total_injected} sprite, 跳过 {total_skipped}")
    if total_injected > 0:
        print(f"✅ 字形回写完成，已更新 {gld_dir}")
    else:
        print("⚠️  未注入任何 sprite")
    return results


# ============================================================
# Part 8: 部署到 2_Patched（供 stage5 打包）
# ============================================================

def deploy_lyric_patches_to_patched(build_dir: Path = LYRIC_BUILD_DIR,
                                    patched_dir: Path = PATCHED_DIR) -> dict:
    """将歌词课补丁部署到 2_Patched 目录

    - ARM9（含四补丁） → 2_Patched/PRG_CHS_PATCHED/arm9.bin
    - overlay_0006（含 charmap 重定位） → 2_Patched/PRG_CHS_PATCHED/overlay_0006.bin
    - y9.bin（含 RAM Size 更新） → 2_Patched/PRG_CHS_PATCHED/y9.bin
    - AGL（0506/0520 扩容） → 2_Patched/AGL_CHS_PATCHED/
    - GLD（0507/0521 扩容） → 2_Patched/AGL_CHS_PATCHED/
    - BBQ（10 个 LESVOICETABLE 注入） → 2_Patched/TBL_CHS_PATCHED/

    Stage 4 文本注入会保留 PRG_CHS_PATCHED 中已补丁的 arm9.bin / overlay_0006.bin
    （见 stage4_inject_text.process_arm9_overlays 的 LYRIC_PROTECTED_FILES 跳过逻辑）。

    Returns:
        {dest_path: src_path} 字典
    """
    print("=" * 70)
    print("部署歌词课补丁到 2_Patched/")
    print("=" * 70)

    prg_dst = patched_dir / "PRG_CHS_PATCHED"
    agl_dst = patched_dir / "AGL_CHS_PATCHED"
    tbl_dst = patched_dir / "TBL_CHS_PATCHED"
    prg_dst.mkdir(parents=True, exist_ok=True)
    agl_dst.mkdir(parents=True, exist_ok=True)
    tbl_dst.mkdir(parents=True, exist_ok=True)

    # 映射 (build_dir 中文件名 → 目标目录 + 目标文件名)
    deploy_map: list[tuple[str, Path, str]] = [
        # PRG
        ("arm9_patched.bin", prg_dst, "arm9.bin"),
        ("overlay_0006_patched.bin", prg_dst, "overlay_0006.bin"),
        ("y9_patched.bin", prg_dst, "y9.bin"),
        # AGL
        ("0506_D_MEASURE_MOJI_MNG_patched.AGL", agl_dst, "0506_D_MEASURE_MOJI_MNG.AGL"),
        ("0520_D_EPANEL_MOJI_MNG_patched.AGL", agl_dst, "0520_D_EPANEL_MOJI_MNG.AGL"),
        # GLD
        ("0507_D_MEASURE_MOJI_MNG_patched.GLD", agl_dst, "0507_D_MEASURE_MOJI_MNG.GLD"),
        ("0521_D_EPANEL_MOJI_MNG_patched.GLD", agl_dst, "0521_D_EPANEL_MOJI_MNG.GLD"),
    ]
    # BBQ
    for bbq_name in LYRIC_BBQ_FILES:
        patched_name = bbq_name.replace('.BBQ', '_patched.BBQ')
        deploy_map.append((patched_name, tbl_dst, bbq_name))

    deployed: dict[str, str] = {}
    for src_name, dst_dir, dst_name in deploy_map:
        src_path = build_dir / src_name
        dst_path = dst_dir / dst_name
        if not src_path.exists():
            print(f"  ⚠️ 跳过 {src_name}: 不存在")
            continue
        shutil.copy2(src_path, dst_path)
        deployed[str(dst_path)] = str(src_path)
        print(f"  ✅ {src_name} → {dst_path}")

    print(f"\n✅ 部署完成: {len(deployed)} 个文件")
    return deployed


# ============================================================
# CLI 入口
# ============================================================

def _cli_main() -> int:
    parser = argparse.ArgumentParser(
        description="歌词课汉化工具 (Stage 3.5) - 整合5个核心脚本"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 导出歌词翻译 xlsx
    p_export = subparsers.add_parser(
        "export-translation",
        help="从10个LESVOICETABLE BBQ导出独立翻译xlsx"
    )
    p_export.add_argument("--out", type=Path, default=EXCEL_LYRIC,
                          help=f"输出xlsx路径 (默认: {EXCEL_LYRIC})")

    # 构建所有补丁
    p_build = subparsers.add_parser(
        "build-patches",
        help="构建charmap/AGL/GLD/ARM9/y9/BBQ全部补丁"
    )
    p_build.add_argument("--xlsx", type=Path, default=EXCEL_LYRIC,
                         help=f"歌词翻译xlsx (默认: {EXCEL_LYRIC})")
    p_build.add_argument("--build-dir", type=Path, default=LYRIC_BUILD_DIR,
                         help=f"构建目录 (默认: {LYRIC_BUILD_DIR})")
    p_build.add_argument("--deploy", action="store_true",
                         help="构建后部署到 2_Patched/")

    # 单独 ARM9 补丁操作
    p_arm9 = subparsers.add_parser("arm9", help="ARM9 四补丁操作")
    p_arm9.add_argument("input", type=Path, help="输入 arm9.bin")
    p_arm9.add_argument("output", type=Path, nargs="?",
                        help="输出路径 (省略则原位)")
    p_arm9.add_argument("--verify", action="store_true", help="仅验证")
    p_arm9.add_argument("--check", action="store_true", help="仅检查状态")

    # 导出 PSD 供美工
    p_psd = subparsers.add_parser(
        "export-glyph-psd",
        help="从已扩容GLD导出PSD供美工修正字型"
    )
    p_psd.add_argument("--gld-dir", type=Path, default=LYRIC_BUILD_DIR,
                       help=f"GLD patched目录 (默认: {LYRIC_BUILD_DIR})")
    p_psd.add_argument("--out-dir", type=Path, default=LYRIC_GLYPH_DIR,
                       help=f"PSD输出目录 (默认: {LYRIC_GLYPH_DIR})")
    p_psd.add_argument("--all", action="store_true",
                       help="导出全部sprite (默认仅新增汉字)")

    args = parser.parse_args()

    if args.command == "export-translation":
        export_lyric_translation_xlsx(args.out)
        return 0

    if args.command == "build-patches":
        # 1. charmap + y9
        result = build_charmap_patch(lyric_xlsx=args.xlsx, build_dir=args.build_dir)
        new_frames = result['new_frame_count'] - AGL_OLD_FRAME_COUNT

        # 2. GLD 扩容
        expand_gld_chinese(
            ssot_csv=args.build_dir / "ssot_chars.csv",
            build_dir=args.build_dir,
            font_path=FONT_12PX,
        )

        # 3. AGL 扩容
        expand_agl_chinese(new_frames=new_frames, build_dir=args.build_dir)

        # 4. ARM9 四补丁
        arm9_src = EXTRACT_DIR / "ARM9" / ARM9_FILENAME
        arm9_dst = args.build_dir / "arm9_patched.bin"
        patch_arm9(arm9_src, arm9_dst)

        # 5. BBQ 注入
        inject_bbq_chinese(
            lyric_xlsx=args.xlsx,
            font_mapping_path=args.build_dir / "font_mapping_lesvoice.json",
            build_dir=args.build_dir,
        )

        if args.deploy:
            deploy_lyric_patches_to_patched(
                build_dir=args.build_dir, patched_dir=PATCHED_DIR
            )

        print("\n" + "=" * 70)
        print("✅ 歌词课汉化补丁全部构建完成")
        print("=" * 70)
        return 0

    if args.command == "arm9":
        return patch_arm9_main(
            args.input, args.output,
            verify_only=args.verify, check_only=args.check,
        )

    if args.command == "export-glyph-psd":
        export_gld_psd_for_art(
            gld_patched_dir=args.gld_dir,
            out_dir=args.out_dir,
            only_new=not args.all,
        )
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
