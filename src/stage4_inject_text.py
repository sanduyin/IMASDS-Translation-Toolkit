# src/stage4_inject_text.py
import os
import sys
import struct
import shutil
import pandas as pd

# 导入全局配置和工具类
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    EXTRACT_DIR, PATCHED_DIR, 
    EXCEL_SCN, EXCEL_TBL, EXCEL_ARM9, 
    MAPPING_FILE, EMPTY_MARKERS
)
from src.utils.text_encoder import load_mapping, text_to_bytes

# ===================================================================
#  第一部分: BBQ 封包重建 (适用于 SCN 剧情与 TBL 系统文本)
# ===================================================================

def rebuild_bbq_file(src_path, dst_path, translations, char_map):
    shutil.copy2(src_path, dst_path)
    
    if not translations: 
        return

    with open(dst_path, 'rb+') as f:
        f.seek(0, 2)
        if f.tell() < 32: return
        f.seek(0)
        
        if f.read(4) != b'\x2E\x42\x42\x51': 
            return

        f.seek(16)
        header_size = struct.unpack('<I', f.read(4))[0]
        num_sec = struct.unpack('<I', f.read(4))[0]
        
        f.seek(header_size)
        sec7_info = None
        for i in range(num_sec):
            entry_pos = header_size + i * 20
            f.seek(entry_pos)
            sec_id = struct.unpack('<I', f.read(4))[0]
            if sec_id == 7:
                vals = struct.unpack('<4I', f.read(16))
                sec7_info = {'entry_pos': entry_pos, 'values': vals}
                break
        
        if not sec7_info: return 

        entry_pos = sec7_info['entry_pos']
        ptr_tbl_rel = sec7_info['values'][0]
        num_str = sec7_info['values'][1]
        pool_rel = sec7_info['values'][2]
        
        ptr_tbl_abs = entry_pos + ptr_tbl_rel
        pool_abs = entry_pos + pool_rel
        
        f.seek(ptr_tbl_abs)
        old_pointers = list(struct.unpack(f'<{num_str}I', f.read(num_str * 4)))

        new_pool_data = bytearray()
        new_pointers =[]
        
        for i in range(num_str):
            new_pointers.append(len(new_pool_data)) 
            
            if i in translations:
                # text_to_bytes 已经自带 \x00
                new_pool_data.extend(text_to_bytes(translations[i], char_map))
            else:
                f.seek(pool_abs + old_pointers[i])
                raw = bytearray()
                while True:
                    b = f.read(1)
                    raw.extend(b)
                    if b == b'\x00': break
                new_pool_data.extend(raw)
        
        while len(new_pool_data) % 4 != 0: 
            new_pool_data.append(0)

        f.seek(ptr_tbl_abs)
        for p in new_pointers:
            f.write(struct.pack('<I', p))
            
        f.seek(0, 2)
        new_pool_abs_start = f.tell()
        f.write(new_pool_data)
        
        new_pool_rel = new_pool_abs_start - entry_pos
        f.seek(entry_pos + 12) 
        f.write(struct.pack('<I', new_pool_rel))

def process_bbq_directory(excel_path, input_subfolder, output_subfolder, char_map):
    if not excel_path.exists():
        print(f"⏭️  跳过 {input_subfolder}: 找不到翻译表格 {excel_path.name}")
        return

    print(f"\n📂 开始注入 {input_subfolder} 文本...")
    
    trans_db = {}
    xls = pd.read_excel(excel_path, sheet_name=None)
    
    for _, df in xls.items():
        if 'File' not in df.columns: continue
        for _, row in df.iterrows():
            fname = str(row['File']).strip()
            if not fname or fname.lower() == 'nan': continue
            
            raw_trans = row.get('Translated_Text')
            if pd.isna(raw_trans): continue
            
            trans_str = str(raw_trans)
            final_text = "" if trans_str in EMPTY_MARKERS else trans_str
            
            if fname not in trans_db: 
                trans_db[fname] = {}
            trans_db[fname][int(row['Index'])] = final_text

    src_dir = EXTRACT_DIR / input_subfolder
    dst_dir = PATCHED_DIR / output_subfolder
    dst_dir.mkdir(parents=True, exist_ok=True)
    
    success_count = 0
    for root, _, files in os.walk(src_dir):
        for file in files:
            if file.lower().endswith(('.bin', '.bbq')):
                src_path = os.path.join(root, file)
                dst_path = os.path.join(dst_dir, file)
                translations = trans_db.get(file, {})
                
                try:
                    rebuild_bbq_file(src_path, dst_path, translations, char_map)
                    if translations: success_count += 1
                except Exception as e:
                    print(f"  ❌ 错误 {file}: {e}")
                    shutil.copy2(src_path, dst_path)
                    
    print(f"  ✅ 完成！共成功修改 {success_count} 个 {input_subfolder} 文件。")

# ===================================================================
#  第二部分: ARM9 与 Overlay 程序文件原地注入
# ===================================================================

def process_arm9_overlays(excel_path, char_map):
    if not excel_path.exists():
        print(f"⏭️  跳过 ARM9: 找不到翻译表格 {excel_path.name}")
        return

    print(f"\n💻 开始注入 ARM9 & Overlays 程序代码段文本...")
    
    df = pd.read_excel(excel_path)
    df = df.dropna(subset=['Translated_Text', 'Text_Offset'])
    grouped = df.groupby('File')
    
    dst_dir = PATCHED_DIR / "PRG_CHS_PATCHED"
    dst_dir.mkdir(parents=True, exist_ok=True)
    
    src_arm9_dir = EXTRACT_DIR / "ARM9" 
    if not src_arm9_dir.exists():
        print("  ⚠️ 警告: 未找到 Extracted/ARM9 目录。")
        return

    for f in os.listdir(src_arm9_dir):
        name_lower = f.lower()
        if name_lower == 'arm9.bin' or (name_lower.startswith('overlay') and name_lower.endswith('.bin')):
            shutil.copy2(src_arm9_dir / f, dst_dir / f)

    total_success = 0
    total_overflow = 0

    for filename, group in grouped:
        dst_path = dst_dir / filename
        if not dst_path.exists(): continue
        
        with open(dst_path, 'rb+') as f:
            for _, row in group.iterrows():
                try:
                    offset = int(str(row['Text_Offset']), 16)
                    
                    f.seek(offset)
                    original_len = 0
                    while True:
                        b = f.read(1)
                        if b == b'\x00' or b == b'': break
                        original_len += 1
                    
                    # 【核心修复】原本字符串的空间，必须包含最后的 \x00 结束符！
                    limit_bytes = original_len + 1
                    
                    # 编码后的 new_bytes 内部已经自带了 1 个 \x00
                    new_bytes = text_to_bytes(row['Translated_Text'], char_map)
                    
                    if len(new_bytes) > limit_bytes:
                        print(f"  ⚠️ [溢出跳过] {filename} @ {row['Text_Offset']}")
                        print(f"     原文: {row['Original_Text']}")
                        print(f"     译文: {row['Translated_Text']}")
                        print(f"     ❌ 译文所需 {len(new_bytes)} 字节 > 原版安全空间 {limit_bytes} 字节\n")
                        total_overflow += 1
                        continue
                    
                    # 写入 (new_bytes 已经包含 \x00，绝不能再次手写追加 \x00)
                    f.seek(offset)
                    f.write(new_bytes)
                    
                    # 抹除原版日文字符残影
                    remaining = limit_bytes - len(new_bytes)
                    if remaining > 0:
                        f.write(b'\x00' * remaining)
                        
                    total_success += 1
                    
                except Exception as e:
                    print(f"  [错误] 注入偏移 {row.get('Text_Offset')} 时失败: {e}")

    print(f"  ✅ ARM9 注入完成。成功: {total_success} 条。")
    if total_overflow > 0:
        print(f"  🚨 注意: 仍有 {total_overflow} 条文本由于确实过长被跳过。")

# ===================================================================
#  主流程入口
# ===================================================================

def main():
    print("=" * 50)
    print(" 文本注入与数据重建引擎")
    print("=" * 50)
    
    char_map = load_mapping(MAPPING_FILE)
    if not char_map:
        print("❌ 找不到 font_mapping.json，请先执行 Stage 3 字库生成脚本。")
        return

    process_bbq_directory(EXCEL_SCN, "SCN", "SCN_CHS_PATCHED", char_map)
    process_bbq_directory(EXCEL_TBL, "TBL", "TBL_CHS_PATCHED", char_map)
    process_arm9_overlays(EXCEL_ARM9, char_map)

    print("\n🎉 Stage 4 注入阶段全部完成！")

if __name__ == "__main__":
    main()