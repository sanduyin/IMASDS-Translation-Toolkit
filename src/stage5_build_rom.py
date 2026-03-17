# src/stage5_build_rom.py
import os
import sys
import struct
import shutil
import subprocess

try:
    import ndspy.rom
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ndspy"])
    import ndspy.rom

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    ORIGINAL_DIR, EXTRACT_DIR, PATCHED_DIR, REPACK_STAGING, BUILD_DIR,
    FILE_PACKS, TARGET_PACKS, ORIGINAL_ROM, OUTPUT_ROM
)
from src.utils.binary_io import read_uint32, nlzss_compress

def crc16_nds(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1: crc = (crc >> 1) ^ 0xA001
            else: crc >>= 1
    return crc & 0xFFFF

def repack_data_archives():
    print("📦 开始重构建核心数据包 (BIN/IDX)...")
    orig_data_dir = ORIGINAL_DIR / "Data"
    
    for pack in FILE_PACKS:
        sub_dir = pack["output"]
        if sub_dir not in TARGET_PACKS: continue
            
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
                
                target_file = None
                for suffix in["_CHS_PATCHED", "_IMG_PATCHED"]:
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

def build_nds_and_restore_twl():
    print("\n🛠️  正在使用 ndspy 纯 Python 引擎在内存中构建 ROM...")
    rom = ndspy.rom.NintendoDSRom.fromFile(str(ORIGINAL_ROM))
    
    # 1. 注入重构后的数据包
    for pack in FILE_PACKS:
        if pack["output"] not in TARGET_PACKS: continue
        ezt, ezp = pack["ezt"], pack["ezp"]
        if (REPACK_STAGING / ezt).exists(): rom.setFileByName(ezt, (REPACK_STAGING / ezt).read_bytes())
        if (REPACK_STAGING / ezp).exists(): rom.setFileByName(ezp, (REPACK_STAGING / ezp).read_bytes())
        
    # 2. 注入修改后的程序段 (ARM9)
    patched_prg_dir = PATCHED_DIR / "PRG_CHS_PATCHED"
    if (patched_prg_dir / "arm9.bin").exists():
        rom.arm9 = (patched_prg_dir / "arm9.bin").read_bytes()
        
    # 3. 【核心修复】以底层原生方式操作 Overlay 文件树与内存映射表 (y9.bin)
    y9_data = bytearray(rom.arm9OverlayTable)
    if patched_prg_dir.exists():
        for f in os.listdir(patched_prg_dir):
            if f.startswith('overlay_') and f.endswith('.bin'):
                try: ovl_id = int(f.replace('overlay_', '').replace('.bin', ''))
                except ValueError: continue
                
                new_data = (patched_prg_dir / f).read_bytes()
                
                # 注入到底层文件系统
                rom.setFileByName(f"overlay/{f}", new_data)
                
                # 解除压缩标记并更新大小
                offset = ovl_id * 32
                if offset + 32 <= len(y9_data):
                    file_size = len(new_data)
                    struct.pack_into('<I', y9_data, offset + 8, file_size)  # RAM Size
                    struct.pack_into('<I', y9_data, offset + 28, file_size) # Size & Flag (0=未压缩)
                    print(f"  -> 已注入并解除内存压缩: {f}")
                    
    # 写回修改后的映射表
    rom.arm9OverlayTable = bytes(y9_data)
            
    # 4. 在内存中生成基础 ROM 二进制流
    temp_rom_data = rom.save()
    print("  -> 基础 ROM 内存构建成功！")
    
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

    final_rom = bytearray(temp_rom_data)
    new_ntr_size = len(final_rom)
    
    if has_twl:
        if new_ntr_size > orig_ntr_size:
            print(f"  ⚠️  严重警告：新 ROM 体积 ({new_ntr_size}) 超出原版 NTR 边界 ({orig_ntr_size})！")
        else:
            padding_size = orig_ntr_size - new_ntr_size
            if padding_size > 0:
                print(f"  -> 0xFF 填充对齐：补齐 {padding_size} 字节至原版 NTR 边界...")
                final_rom.extend(b'\xFF' * padding_size)
            print(f"  -> 嫁接 DSi 扩展数据：{len(twl_data)} 字节...")
        final_rom.extend(twl_data)
        
    struct.pack_into('<I', final_rom, 0x80, orig_ntr_size if has_twl else new_ntr_size)
    if has_twl: final_rom[0x1C0:0x200] = orig_header[0x1C0:0x200]
    
    new_crc = crc16_nds(final_rom[0x000:0x15C])
    struct.pack_into('<H', final_rom, 0x15C, new_crc)
    
    OUTPUT_ROM.write_bytes(final_rom)
    print(f"  ✅ DSi 数据嫁接完成！Header CRC16 已重新校验并更新：0x{new_crc:04X}")

def main():
    print("=" * 50)
    print(" THE iDOLM@STER Dearly Stars - 纯 Python 终极构建")
    print("=" * 50)
    
    if REPACK_STAGING.exists(): shutil.rmtree(REPACK_STAGING)
    REPACK_STAGING.mkdir(parents=True, exist_ok=True)
    
    repack_data_archives()
    build_nds_and_restore_twl()
    
    print("\n🎉 全剧终！终极纯净版汉化 ROM 已生成至：")
    print(f"   {OUTPUT_ROM}")

if __name__ == "__main__":
    main()
