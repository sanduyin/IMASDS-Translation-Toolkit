# src/stage3_build_font.py
import os
import sys
import json
import struct
import subprocess
import pandas as pd
from PIL import Image, ImageFont, ImageDraw

try:
    import opencc
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "opencc"])
    import opencc

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    EXCEL_SCN, EXCEL_TBL, EXCEL_ARM9, MAPPING_FILE,
    FONT_12PX, FONT_10PX, ORIGINAL_LC12, ORIGINAL_LC10,
    PATCHED_LC12, PATCHED_LC10
)
from src.utils.text_encoder import PROTECTED_RANGES, is_protected
from src.utils.binary_io import read_uint16, read_uint32

# ===================================================================
# 黑科技：OpenCC 柔性容错加载 (Graceful Degradation)
# ===================================================================
converter_t2s = opencc.OpenCC('t2s')
try:
    converter_jp2t = opencc.OpenCC('jp2t')
except Exception:
    converter_jp2t = None
    print("  ⚠️ [环境提示] 当前 OpenCC 库缺失 jp2t 字典。已自动降级为单层映射 (不影响程序运行，仅少白嫖几个特殊日文槽位)。")

def convert_to_simp(jis_char):
    """智能转换链：如果有 jp2t 就双重转换，没有就直接繁转简"""
    try:
        char_trad = converter_jp2t.convert(jis_char) if converter_jp2t else jis_char
        return converter_t2s.convert(char_trad)
    except:
        return jis_char

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
fallback_cache = {}

def get_fallback_font(size):
    if size in fallback_cache: return fallback_cache[size]
    for font_name in FALLBACK_FONTS:
        try:
            font = ImageFont.truetype(font_name, size)
            fallback_cache[size] = font
            return font
        except: continue
    return None

def is_char_missing(font, char, spec):
    if char in (' ', '\u3000', '\n', '\r', '\t', '\xa0'): return False
    if not hasattr(font, '_missing_bytes'):
        img = Image.new('1', (spec['cell_width'], spec['cell_height']), 0)
        ImageDraw.Draw(img).text((0, spec['y_offset']), chr(0xFFFE), font=font, fill=1)
        font._missing_bytes = img.tobytes()
    img = Image.new('1', (spec['cell_width'], spec['cell_height']), 0)
    ImageDraw.Draw(img).text((0, spec['y_offset']), char, font=font, fill=1)
    return img.tobytes() == font._missing_bytes

def parse_nftr_pamac(filepath):
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

def build_font_mapping():
    print("\n🔍 正在扫描 Excel 提取翻译字符...")
    unique_chars = set([' ', '\u3000']) 
    for filepath in[EXCEL_SCN, EXCEL_TBL, EXCEL_ARM9]:
        if not filepath.exists(): continue
        try:
            for sheet_name, df in pd.read_excel(filepath, sheet_name=None).items():
                col = 'Translated_Text' if 'Translated_Text' in df.columns else '译文'
                if col in df.columns: unique_chars.update(list("".join(df[col].dropna().astype(str))))
        except Exception: pass
    unique_chars.difference_update(['\n', '\r', '\t', ''])

    old_mapping = {}
    if MAPPING_FILE.exists():
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
        '\xa0': 0x20       
    }

    to_be_added =[]
    taken_slots = set([0x20, 0x8140])
    reused_count = 0
    native_protected_count = 0

    for char, old_code in list(old_mapping.items()):
        try:
            code_cp932 = char.encode('cp932')
            code_int = struct.unpack('>H', code_cp932)[0] if len(code_cp932) == 2 else code_cp932[0]
            if is_protected(code_int) and old_code != code_int:
                old_mapping[char] = code_int 
        except: pass

    for char in unique_chars:
        if char in (' ', '\u3000', '\xa0'): continue 

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

    with open(MAPPING_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_mapping, f, ensure_ascii=False, indent=2)

    print(f"✅ 映射分配更新完毕！成功复用原生汉字槽 {reused_count} 个。新增汉字 {len(to_be_added)} 个，剩余空位 {len(available_slots) - len(to_be_added)} 个。")
    return final_mapping

def get_pixel_width(img):
    width, height = img.size
    pixels = img.load()
    for x in range(width - 1, -1, -1):
        for y in range(height):
            if pixels[x, y] > 0: return x + 1
    return 0 

def render_glyph_1bpp(char, font, code, spec):
    img = Image.new('1', (spec['cell_width'], spec['cell_height']), 0)
    if char: ImageDraw.Draw(img).text((0, spec['y_offset']), char, font=font, fill=1)

    real_width = get_pixel_width(img)

    if code < 0x100:
        glyph_w = real_width if real_width > 0 else spec['space_w']
        advance = glyph_w + 1
        if char in (' ', '\u3000', '\xa0'): glyph_w, advance = spec['space_w'], spec['space_advance']
    else:
        glyph_w = spec['cjk_glyph_w']
        advance = spec['cjk_advance']

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

def inject_nftr(spec_name, spec, char_map):
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

def main():
    print("=" * 50)
    print(" 智能字库生成引擎 (防弹容错版 + VWF重排版)")
    print("=" * 50)
    
    try:
        char_map = build_font_mapping()
        for name, spec in NFTR_SPECS.items():
            inject_nftr(name, spec, char_map)
        print("\n🎉 字库构建完美落幕！你的英文和日文也将如丝般顺滑。")
    except Exception as e:
        print(f"\n❌ 字库构建失败: {e}")

if __name__ == "__main__":
    main()