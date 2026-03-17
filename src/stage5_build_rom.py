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

# ============================================================
# CRC16 算法核心（专用于 NDS Header 校验，解决 DSi 白屏死机）
# ============================================================
def crc16_nds(data: bytes) -> int:
    """
    计算 NDS Header CRC16。
    覆盖范围：Header[0x000 : 0x15B]，共 348 字节。
    多项式：0x8005，初值：0xFFFF，无反转。
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF

# ============================================================
# 第一部分：重打包 BIN/IDX 数据包
# ============================================================
def repack_data_archives():
    """将汉化修改后的文本或图像文件重新以 LZ10 压缩，并更新 BIN/IDX"""
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
            
            for i in range(file_count):
                f_idx.seek(idx_data_start + i * 12)
                curr_off, old_size_raw, name_ptr = read_uint32(f_idx), read_uint32(f_idx), read_uint32(f_idx)
                
                # 智能寻找修改过的文件（同时兼容文本 _CHS_PATCHED 和图像 _IMG_PATCHED）
                target_file = None
                for suffix in ["_CHS_PATCHED", "_IMG_PATCHED"]:
                    patched_dir = PATCHED_DIR / f"{sub_dir}{suffix}"
                    if patched_dir.exists():
                        for f in os.listdir(patched_dir):
                            if f.startswith(f"{i:04d}_"):
                                target_file = patched_dir / f
                                break
                    if target_file: break
                
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

# ============================================================
# 第二部分：构建环境组装与 ARM9 免压缩破解
# ============================================================
def prepare_build_environment(temp_dir):
    """将所有零散的文件组装到一个临时目录，准备喂给 ndstool"""
    print("\n💻 正在配置程序文件与内存映射表 (智能禁用覆盖压缩)...")
    
    temp_data, temp_overlay = temp_dir / "data", temp_dir / "overlay"
    temp_dir.mkdir(exist_ok=True); temp_data.mkdir(); temp_overlay.mkdir()
    
    shutil.copytree(ORIGINAL_DIR / "Data", temp_data, dirs_exist_ok=True)
    for item in os.listdir(REPACK_STAGING):
        shutil.copy2(REPACK_STAGING / item, temp_data / item)
        
    src_arm9_dir = EXTRACT_DIR / "ARM9"
    patched_prg_dir = PATCHED_DIR / "PRG_CHS_PATCHED"
    
    if (patched_prg_dir / "arm9.bin").exists(): shutil.copy2(patched_prg_dir / "arm9.bin", temp_dir / "arm9.bin")
    else: shutil.copy2(src_arm9_dir / "arm9.bin", temp_dir / "arm9.bin")
        
    if src_arm9_dir.exists():
        for f in os.listdir(src_arm9_dir):
            if f.startswith('overlay'): shutil.copy2(src_arm9_dir / f, temp_overlay / f)
    if patched_prg_dir.exists():
        for f in os.listdir(patched_prg_dir):
            if f.startswith('overlay'): shutil.copy2(patched_prg_dir / f, temp_overlay / f)
            
    shutil.copy2(EXTRACT_DIR / "y9.bin", temp_dir / "y9.bin")
    with open(temp_dir / "y9.bin", 'rb+') as f:
        y9_data = bytearray(f.read())
        for ovl_name in os.listdir(temp_overlay):
            if not ovl_name.startswith('overlay'): continue
            import re
            m = re.search(r'(\d+)', ovl_name)
            if m:
                ovl_id, offset = int(m.group(1)), int(m.group(1)) * 32
                if offset + 32 <= len(y9_data):
                    file_size = os.path.getsize(temp_overlay / ovl_name)
                    struct.pack_into('<I', y9_data, offset + 8, file_size)
                    struct.pack_into('<I', y9_data, offset + 28, file_size)
        f.seek(0); f.write(y9_data)
        
    for f in['arm7.bin', 'y7.bin', 'header.bin', 'banner.bin']:
        shutil.copy2(EXTRACT_DIR / f, temp_dir / f)

# ============================================================
# 第三部分：ROM 构建、TWL 数据精准嫁接与 Header CRC 重算
# ============================================================
def build_nds_and_restore_twl(temp_dir):
    """执行构建，并进行精确的外科手术式 DSi (TWL) 数据缝合及校验和修复"""
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

    print(f"\n✨ 正在执行 DSi (TWL) 扩展数据嫁接与 Header 完整性修复...")
    
    with open(ORIGINAL_ROM, 'rb') as f_orig:
        orig_header = bytearray(f_orig.read(0x200))
        orig_ntr_size = struct.unpack_from('<I', orig_header, 0x80)[0]
        
        f_orig.seek(0, 2)
        orig_total_size = f_orig.tell()
        
        has_twl = orig_total_size > orig_ntr_size
        if has_twl:
            f_orig.seek(orig_ntr_size)
            twl_data = f_orig.read(orig_total_size - orig_ntr_size)
        else:
            twl_data = b''

    if not has_twl:
        print("  -> 该 ROM 无 DSi 扩展数据，无需嫁接。")
    
    with open(OUTPUT_ROM, 'rb+') as f_out:
        f_out.seek(0, 2)
        new_ntr_size = f_out.tell()

        if has_twl:
            if new_ntr_size > orig_ntr_size:
                print(f"  ⚠️  严重警告：新 ROM 体积 ({new_ntr_size} 字节) 超出原版 NTR 边界 ({orig_ntr_size} 字节)！")
                print(f"      DSi 数据将被迫偏移，可能导致实机 DSi 模式黑屏！")
                f_out.write(twl_data)
            else:
                padding_size = orig_ntr_size - new_ntr_size
                if padding_size > 0:
                    print(f"  -> 0xFF 填充对齐：补齐 {padding_size} 字节至原版 NTR 边界...")
                    f_out.write(b'\xFF' * padding_size)
                print(f"  -> 嫁接 DSi 扩展数据：{len(twl_data)} 字节...")
                f_out.write(twl_data)

        # =======================================================
        # 核心修正区：遵循 Header 修改的时序逻辑
        # =======================================================
        # 步骤 1：恢复 NTR 容量大小声明 (0x80)
        f_out.seek(0x80)
        f_out.write(struct.pack('<I', orig_ntr_size if has_twl else new_ntr_size))

        # 步骤 2：仅恢复真正的 TWL 专属扩展区 (0x1C0 ~ 0x1FF)
        if has_twl:
            f_out.seek(0x1C0)
            f_out.write(orig_header[0x1C0:0x200])

        # 步骤 3：在所有修改完成后，重新计算 0x000~0x15B 的校验和
        f_out.seek(0)
        header_for_crc = f_out.read(0x15C)
        new_crc = crc16_nds(header_for_crc)
        
        # 将最新的 CRC16 写入 0x15C
        f_out.seek(0x15C)
        f_out.write(struct.pack('<H', new_crc))

    print(f"  ✅ DSi 数据嫁接完成！Header CRC16 已重新校验并更新：0x{new_crc:04X}")

# ============================================================
# 主流程入口
# ============================================================
def main():
    print("=" * 50)
    print(" THE iDOLM@STER Dearly Stars - 终极构建流水线")
    print("=" * 50)
    
    if REPACK_STAGING.exists(): shutil.rmtree(REPACK_STAGING)
    REPACK_STAGING.mkdir(parents=True, exist_ok=True)
    
    temp_dir = BUILD_DIR / "_temp_ndstool"
    if temp_dir.exists(): shutil.rmtree(temp_dir)
    
    repack_data_archives()
    prepare_build_environment(temp_dir)
    build_nds_and_restore_twl(temp_dir)
    
    if temp_dir.exists(): shutil.rmtree(temp_dir)
    
    print("\n🎉 全剧终！终极汉化 ROM 已生成至：")
    print(f"   {OUTPUT_ROM}")

if __name__ == "__main__":
    main()