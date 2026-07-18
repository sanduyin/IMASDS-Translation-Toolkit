# src/stage3_build_font.py
from __future__ import annotations

import os
import sys
import json
import struct
import subprocess
import glob
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageFont, ImageDraw

try:
    import opencc
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "opencc"])
    import opencc  # type: ignore

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    EXCEL_SCN, EXCEL_TBL, EXCEL_ARM9, MAPPING_FILE, WORKSPACE_DIR,
    FONT_12PX, FONT_10PX, ORIGINAL_LC12, ORIGINAL_LC10,
    PATCHED_LC12, PATCHED_LC10,
    CSV_SCN_DIR, CSV_TBL_DIR, CSV_ARM9_FILE
)
from src.utils.text_encoder import PROTECTED_RANGES, is_protected
from src.utils.binary_io import read_uint16, read_uint32
from src.io.csv_handler import read_text_csv, read_arm9_csv, is_csv_path
from src.utils.mapping_backup import backup_mapping

converter_jp2t = opencc.OpenCC('jp2t.json')
converter_t2s = opencc.OpenCC('t2s.json')

def convert_to_simp(jis_char: str) -> str:
    try: return str(converter_t2s.convert(converter_jp2t.convert(jis_char)))
    except: return jis_char

NFTR_SPECS = {
    'LC12': {
        'original': ORIGINAL_LC12, 'font_file': FONT_12PX, 'output': PATCHED_LC12,
        'cell_width': 12, 'cell_height': 11, 'font_size': 12, 'y_offset': -1,
        'glyph_bytes': 17, 'cjk_glyph_w': 11, 'cjk_advance': 12,
        'space_w': 4, 'space_advance': 5,
    },
    'LC10': {
        'original': ORIGINAL_LC10, 'font_file': FONT_10PX, 'output': PATCHED_LC10,
        'cell_width': 10, 'cell_height': 9, 'font_size': 10, 'y_offset': -1,
        'glyph_bytes': 12, 'cjk_glyph_w': 9, 'cjk_advance': 10,
        'space_w': 3, 'space_advance': 4,
    },
}

FALLBACK_FONTS =['msyh.ttc', 'simhei.ttf', 'simsun.ttc', 'Arial Unicode MS.ttf']
fallback_cache: dict[int, Any] = {}

def get_fallback_font(size: int) -> Any:
    if size in fallback_cache: return fallback_cache[size]
    for font_name in FALLBACK_FONTS:
        try:
            font = ImageFont.truetype(font_name, size)
            fallback_cache[size] = font
            return font
        except: continue
    return None

def is_char_missing(font: Any, char: str, spec: dict[str, Any]) -> bool:
    if char in (' ', '\u3000', '\n', '\r', '\t', '\xa0', '\u2002', '\u2003'): return False
    if not hasattr(font, '_missing_bytes'):
        img = Image.new('1', (spec['cell_width'], spec['cell_height']), 0)
        ImageDraw.Draw(img).text((0, spec['y_offset']), chr(0xFFFE), font=font, fill=1)
        font._missing_bytes = img.tobytes()
    img = Image.new('1', (spec['cell_width'], spec['cell_height']), 0)
    ImageDraw.Draw(img).text((0, spec['y_offset']), char, font=font, fill=1)
    return bool(img.tobytes() == font._missing_bytes)

def parse_nftr_pamac(filepath: Path) -> tuple[bytearray, int, int, dict[int, int]]:
    with open(filepath, 'rb') as f: data = bytearray(f.read())
    plgc_off, hdwc_off = data.find(b'PLGC'), data.find(b'HDWC')
    plgc_start, hdwc_start = plgc_off + 16, hdwc_off + 16

    code_to_index = {}
    start_search = 0
    while True:
        pamc_off = data.find(b'PAMC', start_search)
        if pamc_off == -1: break
        chunk_end = pamc_off + read_uint32(data, pamc_off + 4)
        cursor = pamc_off + 8
        while cursor < chunk_end - 12:
            start_code, end_code, map_type = read_uint16(data, cursor), read_uint16(data, cursor + 2), read_uint16(data, cursor + 4)
            next_offset, body_start = read_uint32(data, cursor + 8), cursor + 12
            if map_type == 0:
                first_index = read_uint16(data, body_start)
                for c in range(start_code, end_code + 1): code_to_index[c] = c - start_code + first_index
            elif map_type == 1:
                for i in range(end_code - start_code + 1):
                    idx = read_uint16(data, body_start + i * 2)
                    if idx != 0xFFFF: code_to_index[start_code + i] = idx
            elif map_type == 2:
                for i in range(read_uint16(data, body_start)):
                    code_to_index[read_uint16(data, body_start + 2 + i * 4)] = read_uint16(data, body_start + 2 + i * 4 + 2)
            if next_offset == 0: break
            cursor += next_offset
        start_search = pamc_off + 4
    return data, plgc_start, hdwc_start, code_to_index

def _scan_xlsx_chars(unique_chars: set[str]) -> None:
    """从 xlsx 翻译表收集字符（向后兼容）"""
    for filepath in [EXCEL_SCN, EXCEL_TBL, EXCEL_ARM9]:
        if not filepath.exists():
            continue
        try:
            for sheet_name, df in pd.read_excel(filepath, sheet_name=None).items():
                col = 'Translated_Text' if 'Translated_Text' in df.columns else '译文'
                if col in df.columns:
                    unique_chars.update(list("".join(df[col].dropna().astype(str))))
        except Exception:
            pass


def _scan_csv_chars(unique_chars: set[str]) -> None:
    """从 CSV 翻译表收集字符（P1-4 faraplay 兼容窄列格式）"""
    import glob
    # SCN/TBL 目录下的所有 .csv（含 _ML.csv 邮件文件）
    for csv_dir in (CSV_SCN_DIR, CSV_TBL_DIR):
        if not os.path.isdir(csv_dir):
            continue
        for csv_path in sorted(glob.glob(os.path.join(csv_dir, "*.csv"))):
            try:
                for row in read_text_csv(csv_path):
                    txt = row.get("Translated_Text", "")
                    if txt:
                        unique_chars.update(list(txt))
            except Exception:
                pass
    # ARM9 单文件
    if CSV_ARM9_FILE.exists():
        try:
            for row in read_arm9_csv(CSV_ARM9_FILE):
                txt = row.get("Translated_Text", "")
                if txt:
                    unique_chars.update(list(txt))
        except Exception:
            pass


def build_font_mapping(fmt: str = "xlsx", incremental: bool = True) -> dict[str, int]:
    """构建字库映射表。

    Args:
        fmt: "xlsx" 扫描 Excel 翻译表；"csv" 扫描 faraplay 兼容 CSV 目录/文件。
        incremental: True=增量模式（默认，锁定 old_mapping 已有分配，防止编码漂移）；
                     False=全量模式（重新优化分配，可能改变已有 code）。

    P3-1 兼容性锁定：incremental 模式下，old_mapping 中所有条目（含翻译表已移除的字符）
    都会被原样保留，避免字符临时移除导致 code 丢失/漂移。
    """
    print(f"\n🔍 正在扫描翻译表提取字符 (格式: {fmt}, 模式: {'增量' if incremental else '全量'})...")
    unique_chars = set([' ', '\u3000'])

    if fmt == "csv":
        _scan_csv_chars(unique_chars)
    else:
        _scan_xlsx_chars(unique_chars)

    unique_chars.difference_update(['\n', '\r', '\t', ''])

    old_mapping = {}
    if MAPPING_FILE.exists():
        # P3-2: 构建前自动备份旧 mapping（便于误操作后回滚）
        bak = backup_mapping(reason="auto")
        if bak:
            print(f"  💾 已备份旧 mapping 至: {bak.name}")
        with open(MAPPING_FILE, 'r', encoding='utf-8') as f:
            old_mapping = json.load(f)

    print("📖 分析原版字库，提取原生白嫖映射表...")
    _, _, _, code_map = parse_nftr_pamac(ORIGINAL_LC12)
    
    char_to_code_raw = {}
    for code in code_map.keys():
        if is_protected(code): continue
        try:
            bytes_seq = struct.pack('>H', code) if code > 0xFF else struct.pack('B', code)
            char_jis = bytes_seq.decode('cp932')
            char_simp = convert_to_simp(char_jis)
            char_to_code_raw[char_simp] = code
        except: pass

    final_mapping = {
        ' ': 0x20,         
        '\u3000': 0x8140,  
        '\xa0': 0x20,       
        '\u2002': 0x20,
        '\u2003': 0x8140
    }

    to_be_added =[]
    taken_slots = set([0x20, 0x8140])
    reused_count = 0
    native_protected_count = 0
    preserved_count = 0  # incremental 模式保留的 old_mapping 条目数

    # 修正 old_mapping 中 protected 字符的 code（原版注入器一致性）
    for char, old_code in list(old_mapping.items()):
        try:
            code_cp932 = char.encode('cp932')
            code_int = struct.unpack('>H', code_cp932)[0] if len(code_cp932) == 2 else code_cp932[0]
            if is_protected(code_int) and old_code != code_int:
                old_mapping[char] = code_int 
        except: pass

    # === P3-1: 增量模式 — 先锁定 old_mapping 所有已有分配，防止编码漂移 ===
    FIXED_CHARS = (' ', '\u3000', '\xa0', '\u2002', '\u2003')
    if incremental:
        for char, old_code in old_mapping.items():
            if char in FIXED_CHARS or char in final_mapping:
                continue
            final_mapping[char] = old_code
            taken_slots.add(old_code)
            preserved_count += 1

    for char in unique_chars:
        if char in FIXED_CHARS: continue 

        # incremental 模式下，已被 old_mapping 锁定的字符跳过（code 不变）
        if incremental and char in final_mapping:
            continue

        try:
            code_cp932 = char.encode('cp932')
            code_int = struct.unpack('>H', code_cp932)[0] if len(code_cp932) == 2 else code_cp932[0]
            if is_protected(code_int):
                final_mapping[char] = code_int
                taken_slots.add(code_int)
                native_protected_count += 1
                continue 
        except: pass

        if char in char_to_code_raw:
            code = char_to_code_raw[char]
            final_mapping[char] = code
            taken_slots.add(code)
            reused_count += 1
        elif char in old_mapping:
            code = old_mapping[char]
            final_mapping[char] = code
            taken_slots.add(code)
        else:
            to_be_added.append(char)

    available_slots =[]
    for code in sorted(code_map.keys(), reverse=True):
        if is_protected(code) or code in taken_slots or code in (0x20, 0x8140): continue
        try:
            orig_char_jis = (struct.pack('>H', code) if code > 0xFF else struct.pack('B', code)).decode('cp932', errors='ignore')
            orig_char_simp = convert_to_simp(orig_char_jis)
            if orig_char_simp in unique_chars: continue 
        except: pass
        available_slots.append(code)

    if len(to_be_added) > len(available_slots):
        raise MemoryError(f"字库空间不足！缺 {len(to_be_added) - len(available_slots)} 个位置。")

    for i, char in enumerate(to_be_added):
        final_mapping[char] = available_slots[i]

    # === P3-1: 漂移检测 — 对比 old_mapping 与 final_mapping ===
    if incremental and old_mapping:
        drift_changes = []
        for char, old_code in old_mapping.items():
            new_code = final_mapping.get(char, old_code)
            if new_code != old_code:
                drift_changes.append((char, old_code, new_code))
        if drift_changes:
            print(f"  ⚠️[漂移警告] {len(drift_changes)} 个字符的 code 发生变化：")
            for char, oc, nc in drift_changes[:10]:
                print(f"     '{char}': 0x{oc:X} → 0x{nc:X}")
            if len(drift_changes) > 10:
                print(f"     ... 共 {len(drift_changes)} 个")
        else:
            print(f"  ✓ [漂移检测] old_mapping 中 {len(old_mapping)} 个字符的 code 全部稳定（无漂移）")

    with open(MAPPING_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_mapping, f, ensure_ascii=False, indent=2)

    # ===================================================================
    # 【新增模块】生成 CT2 专用的 TXT 码表
    # ===================================================================
    print("\n📝 正在生成 CT2 (CrystalTile2) 专用全景 txt 码表...")
    ct2_mapping = {}
    
    # 1. 遍历原版所有槽位，模拟洗地
    for code in code_map.keys():
        if is_protected(code):
            try:
                bytes_seq = struct.pack('>H', code) if code > 0xFF else struct.pack('B', code)
                ct2_mapping[code] = bytes_seq.decode('cp932')
            except: ct2_mapping[code] = ""
        else:
            try:
                bytes_seq = struct.pack('>H', code) if code > 0xFF else struct.pack('B', code)
                orig_jis = bytes_seq.decode('cp932')
                ct2_mapping[code] = convert_to_simp(orig_jis)
            except: ct2_mapping[code] = ""
            
    # 2. 将我们翻译用到/新分配的汉字强制覆盖上去
    for char, code in final_mapping.items():
        ct2_mapping[code] = char
        
    # 3. 按照十六进制格式输出
    ct2_txt_path = WORKSPACE_DIR / "CT2_Font_Mapping.txt"
    with open(ct2_txt_path, 'w', encoding='utf-8') as f:
        # 按码位大小正序排列，方便 CT2 读取
        for code in sorted(ct2_mapping.keys()):
            char = ct2_mapping[code]
            if char:  # 过滤掉空字符，保持文件纯净
                f.write(f"{code:X}={char}\n")
                
    print(f"  -> ✅ CT2 码表已同步导出至: {ct2_txt_path.name}")
    # ===================================================================

    print(f"✅ 映射分配更新完毕！成功复用原生汉字槽 {reused_count} 个。新增汉字 {len(to_be_added)} 个，剩余空位 {len(available_slots) - len(to_be_added)} 个。"
          + (f" 增量锁定 {preserved_count} 个已有分配（防漂移）。" if incremental and preserved_count else ""))
    return final_mapping

def get_pixel_width(img: Image.Image) -> int:
    width, height = img.size
    pixels = img.load()
    for x in range(width - 1, -1, -1):
        for y in range(height):
            if pixels[x, y] > 0: return x + 1
    return 0 

def render_glyph_1bpp(char: str, font: Any, code: int, spec: dict[str, Any]) -> tuple[bytearray, int, int]:
    img = Image.new('1', (spec['cell_width'], spec['cell_height']), 0)
    is_space = char in (' ', '\u3000', '\xa0', '\u2002', '\u2003', '\u200b')
    
    if char and not is_space: 
        ImageDraw.Draw(img).text((0, spec['y_offset']), char, font=font, fill=1)

    real_width = get_pixel_width(img)

    if code < 0x100:
        glyph_w = real_width if real_width > 0 else spec['space_w']
        advance = glyph_w + 1
        if is_space: glyph_w, advance = spec['space_w'], spec['space_advance']
    else:
        glyph_w = spec['cjk_glyph_w']
        advance = spec['cjk_advance']
        if is_space: glyph_w, advance = spec['space_w'], spec['cjk_advance']

    pixels, bytes_data, buffer, bit_count = img.load(), bytearray(), 0, 0
    for y in range(spec['cell_height']):
        for x in range(spec['cell_width']):
            buffer |= ((1 if pixels[x, y] > 0 else 0) << (7 - bit_count))
            bit_count += 1
            if bit_count == 8:
                bytes_data.append(buffer)
                buffer, bit_count = 0, 0
    if bit_count > 0: bytes_data.append(buffer)
    while len(bytes_data) < spec['glyph_bytes']: bytes_data.append(0)

    return bytes_data, glyph_w, advance

def inject_nftr(spec_name: str, spec: dict[str, Any], char_map: dict[str, int]) -> None:
    print(f"\n🔨 正在无损注入 {spec_name} (解开封印：全面洗地 + VWF 排版) ...")
    rom_data, plgc_start, hdwc_start, code_map = parse_nftr_pamac(spec['original'])
    
    primary_font = ImageFont.truetype(str(spec['font_file']), spec['font_size'])
    fallback_font = get_fallback_font(spec['font_size'])
    
    glyph_size = spec['glyph_bytes']
    original_file_size = len(rom_data)
    
    code_to_char = {v: k for k, v in char_map.items()}
    count_translated, count_unified, count_skipped = 0, 0, 0

    for code in code_map.keys():
        idx = code_map[code]
        font_to_use = primary_font
        
        if code in code_to_char:
            char_to_render = code_to_char[code]
            if is_char_missing(primary_font, char_to_render, spec):
                if fallback_font: font_to_use = fallback_font
            count_translated += 1
        else:
            try:
                bytes_seq = struct.pack('>H', code) if code > 0xFF else struct.pack('B', code)
                orig_jis = bytes_seq.decode('cp932')
                char_to_render = convert_to_simp(orig_jis)
                
                if is_char_missing(primary_font, char_to_render, spec):
                    count_skipped += 1
                    continue
                count_unified += 1
            except:
                continue

        glyph_bytes, char_w, advance = render_glyph_1bpp(char_to_render, font_to_use, code, spec)

        addr_plgc = plgc_start + idx * glyph_size
        if addr_plgc + glyph_size <= len(rom_data):
            rom_data[addr_plgc:addr_plgc + glyph_size] = glyph_bytes

        addr_hdwc = hdwc_start + idx * 3
        if addr_hdwc + 3 <= len(rom_data):
            rom_data[addr_hdwc] = 0           
            rom_data[addr_hdwc + 1] = char_w  
            rom_data[addr_hdwc + 2] = advance 

    if len(rom_data) != original_file_size:
        raise ValueError(f"严重错误！{spec_name} 体积被改变！")

    spec['output'].parent.mkdir(parents=True, exist_ok=True)
    with open(spec['output'], 'wb') as f:
        f.write(rom_data)

    print(f"  ✅ {spec_name} 注入完成！\n     - 强覆盖字典汉字 {count_translated} 个\n     - 全局清洗(含英文/标点/假名) {count_unified} 个\n     - 特殊符号透明跳过 {count_skipped} 个。")

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="智能字库生成引擎")
    parser.add_argument("--format", choices=["xlsx", "csv"], default="xlsx",
                        help="翻译表格式：xlsx（默认）或 csv（faraplay 兼容窄列）")
    # P3-1: 增量/全量模式互斥组（默认增量，保护 font_mapping.json 已有进度）
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--incremental", action="store_true", default=True,
                            help="增量模式（默认）：锁定 old_mapping 已有分配，防止编码漂移")
    mode_group.add_argument("--full", dest="incremental", action="store_false",
                            help="全量模式：重新优化分配（可能改变已有 code，慎用）")
    args = parser.parse_args()

    print("=" * 50)
    print(f" 智能字库生成引擎 (防弹容错版 + 空格强制隐身) [格式: {args.format}, 模式: {'增量' if args.incremental else '全量'}]")
    print("=" * 50)

    try:
        char_map = build_font_mapping(fmt=args.format, incremental=args.incremental)
        for name, spec in NFTR_SPECS.items():
            inject_nftr(name, spec, char_map)
        print("\n🎉 字库构建完美落幕！幽灵 L 标志已被彻底抹除。")
    except Exception as e:
        print(f"\n❌ 字库构建失败: {e}")

if __name__ == "__main__":
    main()