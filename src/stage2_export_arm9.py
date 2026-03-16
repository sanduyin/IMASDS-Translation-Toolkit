# src/stage2_export_arm9.py
import os
import sys
import struct
import re
import pandas as pd

# 导入全局配置
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, EXCEL_ARM9

# ARM9 默认内存基址
FALLBACK_ARM9_BASE = 0x02000000

def read_arm9_base():
    """从 header.bin 读取 ARM9 在内存中的加载基址"""
    header_path = EXTRACT_DIR / 'header.bin'
    if header_path.exists():
        with open(header_path, 'rb') as f:
            header_data = f.read(0x30)
            addr = struct.unpack_from('<I', header_data, 0x28)[0]
            return addr
    return FALLBACK_ARM9_BASE

def read_overlay_bases():
    """从 y9.bin 读取所有 Overlay 在内存中的加载基址"""
    y9_path = EXTRACT_DIR / 'y9.bin'
    overlay_bases = {}
    if y9_path.exists():
        with open(y9_path, 'rb') as f:
            y9_data = f.read()
        num_entries = len(y9_data) // 32
        for i in range(num_entries):
            off = i * 32
            ovl_id = struct.unpack_from('<I', y9_data, off)[0]
            ram_addr = struct.unpack_from('<I', y9_data, off + 4)[0]
            overlay_bases[ovl_id] = ram_addr
    return overlay_bases

def get_base_address(filename, arm9_base, overlay_bases):
    """根据文件名匹配其内存基址"""
    name_lower = filename.lower()
    if "arm9" in name_lower: return arm9_base
    if "overlay" in name_lower:
        match = re.search(r'(\d+)', filename)
        if match: return overlay_bases.get(int(match.group(1)))
    return None

def analyze_chars(text):
    """V7 智能字符分析器：统计各类字符数量，用于识别并过滤乱码"""
    clean = text.replace('\n', '').replace('\r', '')
    stats = {
        'length': len(clean), 'hiragana': 0, 'katakana': 0, 'kanji': 0, 
        'fullwidth': 0, 'hw_kana': 0, 'ascii_letter': 0, 'ascii_digit': 0, 
        'ascii_symbol': 0, 'space': 0
    }
    for c in clean:
        cp = ord(c)
        if '\u3040' <= c <= '\u309f': stats['hiragana'] += 1
        elif '\u30a0' <= c <= '\u30ff': stats['katakana'] += 1
        elif '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf': stats['kanji'] += 1
        elif '\uff01' <= c <= '\uff5e': stats['fullwidth'] += 1
        elif '\uff61' <= c <= '\uff9f': stats['hw_kana'] += 1
        elif c in (' ', '\u3000'): stats['space'] += 1
        elif cp < 128:
            if c.isalpha(): stats['ascii_letter'] += 1
            elif c.isdigit(): stats['ascii_digit'] += 1
            elif 0x20 <= cp <= 0x7e: stats['ascii_symbol'] += 1

    stats['kana'] = stats['hiragana'] + stats['katakana']
    stats['jp_total'] = stats['kana'] + stats['kanji'] + stats['fullwidth']
    stats['ascii_total'] = stats['ascii_letter'] + stats['ascii_digit'] + stats['ascii_symbol']
    return stats

def strict_filter(text):
    """智能过滤器：过滤编译器路径、乱码碎片和非人类文本"""
    s = analyze_chars(text)
    length = s['length']
    if length == 0 or s['hw_kana'] > 0: return False
    
    # 过滤 SDK 标识和代码路径
    if text.lstrip().startswith(('[SDK', 'SDK+')): return False
    if ":\\" in text or "/home/" in text or text.endswith(('.c', '.h', '.cpp', '.o')): return False
    
    # 纯 ASCII 短文本过滤
    if s['jp_total'] == 0:
        if length < 4: return False
        if s['ascii_total'] > 0 and s['ascii_symbol'] / s['ascii_total'] > 0.3: return False
        
    return True

def scan_prg_file(file_path, filename, base_addr):
    """扫描单个程序文件并提取 Shift-JIS 文本"""
    with open(file_path, 'rb') as f:
        data = f.read()

    entries =[]
    # 正则匹配：至少 2 个连续的 Shift-JIS 字符，以 0x00 结尾
    pattern = rb'(?:[\x20-\x7e\xa1-\xdf\x0a\x0d]|[\x81-\x9f\xe0-\xef][\x40-\xfc]){2,}\x00'
    
    for match in re.finditer(pattern, data):
        raw_bytes = match.group(0)[:-1]
        start_offset = match.start()
        
        try: text = raw_bytes.decode('cp932')
        except: continue
        
        if not text.strip(): continue
        
        if strict_filter(text):
            entries.append({
                'Original_Text': text,
                'Translated_Text': "",
                'File': filename,
                'Text_Offset': f"0x{start_offset:X}",
                'RAM_Address': f"0x{(base_addr + start_offset):08X}",
                'Pointer_Locs': "", # 程序段文本通常靠基址计算，这里留空以适应格式
                'Max_Bytes': len(raw_bytes),
                'Index': len(entries),
                'Type': "程序硬编码"
            })
    return entries

def main():
    arm9_dir = EXTRACT_DIR / "ARM9"
    if not arm9_dir.exists():
        print("❌ 未找到 ARM9 提取目录。请先执行 Unpack 解包！")
        return

    print("🔍 读取内存映射表...")
    arm9_base = read_arm9_base()
    overlay_bases = read_overlay_bases()
    
    all_entries =[]
    for f in os.listdir(arm9_dir):
        if not f.endswith('.bin'): continue
        base = get_base_address(f, arm9_base, overlay_bases)
        if base is None: continue
        
        print(f"  -> 扫描: {f} (Base: 0x{base:08X})")
        entries = scan_prg_file(arm9_dir / f, f, base)
        all_entries.extend(entries)

    if not all_entries:
        print("⚠️ 未在 ARM9 中提取到任何有效文本。")
        return

    print(f"✅ 提取完毕！共导出 {len(all_entries)} 条有效文本。正在生成 Excel...")
    
    df = pd.DataFrame(all_entries)
    # 强制排序列顺序
    cols =['Original_Text', 'Speaker', 'Translated_Text', 'File', 'Text_Offset', 'Pointer_Locs', 'Max_Bytes', 'Index', 'Type']
    # 因为 ARM9 没有 Speaker，填充一下避免格式乱
    if 'Speaker' not in df.columns: df['Speaker'] = "System/PRG"
    df = df[cols]
    
    with pd.ExcelWriter(EXCEL_ARM9, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='ARM9_Text', index=False)
        ws = writer.sheets['ARM9_Text']
        fmt_text = writer.book.add_format({'text_wrap': True, 'valign': 'top'})
        ws.set_column('A:A', 50, fmt_text)
        ws.set_column('C:C', 50, fmt_text)
        ws.set_column('D:G', 15)
        ws.freeze_panes(1, 1)

    print(f"🎉 ARM9 文本已成功导出至: {EXCEL_ARM9.name}")

if __name__ == "__main__":
    main()