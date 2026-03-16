# src/stage5_build_rom.py
import os
import sys
import struct
import shutil
import subprocess

# 导入全局配置和工具类
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    ORIGINAL_DIR, EXTRACT_DIR, PATCHED_DIR, REPACK_STAGING, BUILD_DIR,
    FILE_PACKS, TARGET_PACKS, NDSTOOL_EXE, ORIGINAL_ROM, OUTPUT_ROM
)
from src.utils.binary_io import read_uint32, nlzss_compress

def repack_data_archives():
    """将汉化修改后的文件重新以 LZ10 压缩，并更新 BIN/IDX"""
    print("📦 开始重构建核心数据包 (BIN/IDX)...")
    
    orig_data_dir = ORIGINAL_DIR / "Data"
    
    for pack in FILE_PACKS:
        sub_dir = pack["output"]
        if sub_dir not in TARGET_PACKS:
            continue
            
        ezt_name, ezp_name = pack["ezt"], pack["ezp"]
        orig_idx, orig_bin = orig_data_dir / ezt_name, orig_data_dir / ezp_name
        out_idx, out_bin = REPACK_STAGING / ezt_name, REPACK_STAGING / ezp_name
        
        if not orig_idx.exists(): continue
        print(f"  -> 处理 {ezp_name} ({sub_dir})...")

        with open(orig_idx, 'rb') as f_idx, open(orig_bin, 'rb') as f_bin, \
             open(out_idx, 'wb') as w_idx, open(out_bin, 'wb') as w_bin:
            
            h_size = struct.unpack('<H', f_idx.read(12)[10:12])[0]
            f_idx.seek(0x0C)
            file_count = read_uint32(f_idx)
            idx_data_start = h_size + 6
            
            f_idx.seek(0)
            w_idx.write(f_idx.read(idx_data_start))
            
            f_idx.seek(idx_data_start)
            w_bin.write(f_bin.read(read_uint32(f_idx)))
            current_bin_offset = w_bin.tell()
            
            f_bin.seek(0x0C)
            old_name_table_offset = read_uint32(f_bin)

            mod_count = 0
            patched_dir = PATCHED_DIR / f"{sub_dir}_CHS_PATCHED"
            
            for i in range(file_count):
                f_idx.seek(idx_data_start + i * 12)
                curr_off, old_size_raw, name_ptr = read_uint32(f_idx), read_uint32(f_idx), read_uint32(f_idx)
                
                # 寻找匹配的汉化文件
                target_file = None
                if patched_dir.exists():
                    for f in os.listdir(patched_dir):
                        if f.startswith(f"{i:04d}_"):
                            target_file = patched_dir / f
                            break
                
                if target_file and target_file.exists():
                    with open(target_file, 'rb') as tf: new_data = tf.read()
                    final_data = nlzss_compress(new_data)
                    final_size_flag = len(new_data) | 0x10000000 
                    mod_count += 1
                else:
                    phys_len = (read_uint32(f_idx) if i < file_count - 1 else old_name_table_offset) - curr_off
                    f_bin.seek(curr_off)
                    final_data = f_bin.read(phys_len)
                    final_size_flag = old_size_raw

                w_bin.write(final_data)
                w_idx.write(struct.pack('<I', current_bin_offset))
                w_idx.write(struct.pack('<I', final_size_flag))
                w_idx.write(struct.pack('<I', name_ptr))
                current_bin_offset = w_bin.tell()
                
            new_name_tbl_off = w_bin.tell()
            f_bin.seek(old_name_table_offset)
            w_bin.write(f_bin.read())
            
            w_bin.seek(0x0C)
            w_bin.write(struct.pack('<I', new_name_tbl_off))
            
        print(f"     ✅ 写入完毕，压缩替换了 {mod_count} 个汉化文件。")

def prepare_build_environment(temp_dir):
    """将所有零散的文件组装到一个临时目录，准备喂给 ndstool"""
    print("\n💻 正在配置程序文件与内存映射表 (智能禁用覆盖压缩)...")
    
    temp_data = temp_dir / "data"
    temp_overlay = temp_dir / "overlay"
    temp_dir.mkdir(exist_ok=True); temp_data.mkdir(); temp_overlay.mkdir()
    
    # 1. 组装 Data 目录
    shutil.copytree(ORIGINAL_DIR / "Data", temp_data, dirs_exist_ok=True)
    for item in os.listdir(REPACK_STAGING):
        shutil.copy2(REPACK_STAGING / item, temp_data / item)
        
    # 2. 组装并配置 ARM9 / Overlays
    src_arm9_dir = EXTRACT_DIR / "ARM9"
    patched_prg_dir = PATCHED_DIR / "PRG_CHS_PATCHED"
    
    # 优先使用汉化版 ARM9，如果没有就用解压版的
    if (patched_prg_dir / "arm9.bin").exists(): shutil.copy2(patched_prg_dir / "arm9.bin", temp_dir / "arm9.bin")
    else: shutil.copy2(src_arm9_dir / "arm9.bin", temp_dir / "arm9.bin")
        
    # 优先使用汉化版 Overlay，如果没有就用解压版的
    if (src_arm9_dir).exists():
        for f in os.listdir(src_arm9_dir):
            if f.startswith('overlay'): shutil.copy2(src_arm9_dir / f, temp_overlay / f)
    if patched_prg_dir.exists():
        for f in os.listdir(patched_prg_dir):
            if f.startswith('overlay'): shutil.copy2(patched_prg_dir / f, temp_overlay / f)
            
    # 3. 核心修复：修改 y9.bin 禁用压缩并更新容量
    shutil.copy2(EXTRACT_DIR / "y9.bin", temp_dir / "y9.bin")
    with open(temp_dir / "y9.bin", 'rb+') as f:
        y9_data = bytearray(f.read())
        for ovl_name in os.listdir(temp_overlay):
            if not ovl_name.startswith('overlay'): continue
            import re
            m = re.search(r'(\d+)', ovl_name)
            if m:
                ovl_id = int(m.group(1))
                offset = ovl_id * 32
                if offset + 32 <= len(y9_data):
                    file_size = os.path.getsize(temp_overlay / ovl_name)
                    # 修正 RAM Size 和 物理 Size，并将压缩 Flag 置 0
                    struct.pack_into('<I', y9_data, offset + 8, file_size)
                    struct.pack_into('<I', y9_data, offset + 28, file_size)
        f.seek(0); f.write(y9_data)
        
    # 4. 复制其他必要文件
    for f in['arm7.bin', 'y7.bin', 'header.bin', 'banner.bin']:
        shutil.copy2(EXTRACT_DIR / f, temp_dir / f)

def build_nds_and_restore_twl(temp_dir):
    """执行构建并触发 DSi (TWL) 增强数据完美对齐嫁接"""
    print("\n🛠️ 正在调用 ndstool 构建基础 ROM...")
    
    cmd =[
        str(NDSTOOL_EXE), "-c", str(OUTPUT_ROM),
        "-9", str(temp_dir / "arm9.bin"), "-7", str(temp_dir / "arm7.bin"),
        "-y9", str(temp_dir / "y9.bin"), "-y7", str(temp_dir / "y7.bin"),
        "-d", str(temp_dir / "data"), "-y", str(temp_dir / "overlay"),
        "-h", str(temp_dir / "header.bin"), "-t", str(temp_dir / "banner.bin")
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        print("  -> 基础 ROM 构建成功！")
    except Exception as e:
        print(f"❌ ndstool 执行失败: {e}")
        return

    print("\n✨ 执行 DSi (TWL) 增强数据无损边界对齐修复协议...")
    
    with open(ORIGINAL_ROM, 'rb') as f_orig:
        orig_header = f_orig.read(0x200)
        orig_ntr_size = struct.unpack('<I', orig_header[0x80:0x84])[0]
        
        f_orig.seek(0, 2)
        orig_total_size = f_orig.tell()
        
        if orig_total_size <= orig_ntr_size:
            print("  -> 该 ROM 无 DSi 扩展数据，无需修复。")
            return
            
        f_orig.seek(orig_ntr_size)
        twl_data = f_orig.read(orig_total_size - orig_ntr_size)

    with open(OUTPUT_ROM, 'rb+') as f_out:
        f_out.seek(0, 2)
        new_ntr_size = f_out.tell()

        if new_ntr_size <= orig_ntr_size:
            padding_size = orig_ntr_size - new_ntr_size
            print(f"  -> 新 ROM 容量小于原版，正在进行 0xFF 填充对齐 (补齐 {padding_size} 字节)...")
            f_out.write(b'\xFF' * padding_size)
            
            print(f"  -> 正在尾部无损嫁接 {len(twl_data)} 字节的 DSi 扩展数据...")
            f_out.write(twl_data)
            
            # 恢复 Header 的关键安全信息
            f_out.seek(0x80)
            f_out.write(struct.pack('<I', orig_ntr_size)) 
            f_out.seek(0x180)
            f_out.write(orig_header[0x180:0x200]) 
            
        else:
            print(f"  ⚠️ 严重警告：新 ROM 基础体积 ({new_ntr_size}) 溢出了原版 NTR 边界 ({orig_ntr_size})！DSi 数据将被迫偏移，可能导致实机黑屏！")
            f_out.write(twl_data)

    print(f"  ✅ DSi 数据恢复成功！最终 ROM 体积已锁定。")

def main():
    print("=" * 50)
    print(" THE iDOLM@STER Dearly Stars - 终极构建流水线")
    print("=" * 50)
    
    if REPACK_STAGING.exists(): shutil.rmtree(REPACK_STAGING)
    REPACK_STAGING.mkdir(parents=True, exist_ok=True)
    
    temp_dir = BUILD_DIR / "_temp_ndstool"
    if temp_dir.exists(): shutil.rmtree(temp_dir)
    
    # 1. 重新压缩 BIN 并打包
    repack_data_archives()
    
    # 2. 组装临时工作区
    prepare_build_environment(temp_dir)
    
    # 3. 生成 ROM 并修复 DSi 数据
    build_nds_and_restore_twl(temp_dir)
    
    # 4. 清理临时垃圾
    if temp_dir.exists(): shutil.rmtree(temp_dir)
    
    print("\n🎉 全剧终！汉化 ROM 已生成至：")
    print(f"   {OUTPUT_ROM}")

if __name__ == "__main__":
    main()