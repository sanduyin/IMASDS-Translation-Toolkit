# src/stage5_build_rom.py
from __future__ import annotations

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

def crc16_nds(data: bytes | bytearray) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1: crc = (crc >> 1) ^ 0xA001
            else: crc >>= 1
    return crc & 0xFFFF

def repack_data_archives() -> None:
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

def build_nds_and_restore_twl() -> None:
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
        
    # 3. 【核心黑科技】越过文件树，直接篡改 FAT 底层数据！
    y9_data = bytearray(rom.arm9OverlayTable)
    if patched_prg_dir.exists():
        for f in os.listdir(patched_prg_dir):
            if f.startswith('overlay_') and f.endswith('.bin'):
                try: ovl_id = int(f.replace('overlay_', '').replace('.bin', ''))
                except ValueError: continue
                
                new_data = (patched_prg_dir / f).read_bytes()
                
                offset = ovl_id * 32
                if offset + 32 <= len(y9_data):
                    # 获取底层物理 ID
                    file_id = struct.unpack_from('<I', y9_data, offset + 24)[0] & 0x00FFFFFF
                    
                    if file_id < len(rom.files):
                        # 一击致命：直接覆写底层内存块！
                        rom.files[file_id] = new_data

                    # 解除压缩标记并更新大小
                    # overlay 表项字段（每个 32 字节）：
                    #   offset+0   overlay_id
                    #   offset+4   ram_address
                    #   offset+8   ram_size（解压后 RAM 占用大小）
                    #   offset+12  bss_size
                    #   offset+16  static_init_start
                    #   offset+20  static_init_end
                    #   offset+24  file_id（低 24 位）+ 高字节压缩标志
                    #   offset+28  compressed_size_flag：高字节 0x02=未压缩，0x03=BLZ 压缩
                    #               低 24 位 = 文件大小
                    # 旧代码直接写 file_size 到 offset+28，导致高字节=0x00（非法值），
                    # NDS BIOS overlay loader 在严格模式下拒绝加载 → 进入 overlay 场景时白屏。
                    file_size = len(new_data)
                    struct.pack_into('<I', y9_data, offset + 8, file_size)
                    # 高字节 0x02 = standard uncompressed flag（参见 ndstool/overlay.py:30-34）
                    new_flag = 0x02000000 | (file_size & 0x00FFFFFF)
                    struct.pack_into('<I', y9_data, offset + 28, new_flag)
                    print(f"  -> 已底层物理注入并解除压缩: {f}")
                    
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

    # ===================================================================
    # NDS Header / ARM9 关键修复（4 项致命问题的补齐）
    # ===================================================================
    # ndspy 的 rom.save() 不会做以下 4 件事，导致 ROM 在 NDS BIOS 启动
    # 校验阶段失败 → 白屏。参见 src/ndstool/rom_builder.py 与 reference/
    # dearlystars_tool/ndstool/src/write_rom.rs 的对应实现。
    # -------------------------------------------------------------------

    # 修复 1: ARM9 内部 0x0FC4 = "压缩数据结束内存地址"
    #   ndspy save() 不压缩 ARM9（ARM9 数据是解压后的），故 0x0FC4 必须为 0。
    #   旧代码设 0x0FC4 = ARM9_RAM_BASE + arm9_size（非零），BIOS 误以为 ARM9
    #   是 BLZ 压缩的，尝试解压未压缩数据 → 崩溃 → 白屏。
    #   正确行为：0x0FC4 = 0 表示 ARM9 未压缩，BIOS 直接加载不解压。
    ARM9_RAM_BASE = 0x02004000
    ARM9_COMPRESSED_END_PTR_OFFSET = 0x0FC4
    arm9_rom_offset = struct.unpack_from('<I', final_rom, 0x20)[0]
    arm9_size = struct.unpack_from('<I', final_rom, 0x2C)[0]
    if arm9_rom_offset + ARM9_COMPRESSED_END_PTR_OFFSET + 4 <= len(final_rom):
        struct.pack_into('<I', final_rom,
                         arm9_rom_offset + ARM9_COMPRESSED_END_PTR_OFFSET,
                         0)
        print(f"  -> ARM9 0x0FC4 已清零 (ARM9 未压缩, ndspy save 不压缩 ARM9)")

    # 修复 2: Secure Area CRC (Header 0x06C)
    #   Secure Area = ARM9 前 0x4000 字节 = ROM[0x4000:0x8000]
    #   当 arm9_rom_offset == 0x4000 时（标准 NDS ROM 布局），
    #   BIOS 校验这 0x4000 字节的 CRC16，不匹配 → 白屏。
    if arm9_rom_offset == 0x4000 and len(final_rom) >= 0x8000:
        secure_area_crc = crc16_nds(bytes(final_rom[0x4000:0x8000]))
        struct.pack_into('<H', final_rom, 0x6C, secure_area_crc)
        print(f"  -> Secure area CRC 已重算: 0x{secure_area_crc:04X}")

    # 修复 3: Logo CRC (Header 0x15C) = CRC16 of 0x0C0~0x15B (Nintendo Logo)
    logo_crc = crc16_nds(bytes(final_rom[0x0C0:0x15C]))
    struct.pack_into('<H', final_rom, 0x15C, logo_crc)

    # 修复 4: Header CRC (Header 0x15E) = CRC16 of 0x000~0x15D
    #   旧代码错误地把 CRC 写入 0x15C（Logo CRC 位置），且范围用 0x000~0x15B，
    #   既不是 Logo CRC 范围（应为 0x0C0~0x15B），也不是 Header CRC 范围。
    header_crc = crc16_nds(bytes(final_rom[0x000:0x15E]))
    struct.pack_into('<H', final_rom, 0x15E, header_crc)

    OUTPUT_ROM.write_bytes(final_rom)
    print(f"  ✅ DSi 数据嫁接完成！Logo CRC=0x{logo_crc:04X}, Header CRC=0x{header_crc:04X}")

def main() -> None:
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
