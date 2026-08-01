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

try:
    from Crypto.Cipher import AES as _AES_DEPENDENCY_CHECK  # noqa: F401
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pycryptodome"])

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    ORIGINAL_DIR, EXTRACT_DIR, PATCHED_DIR, REPACK_STAGING, BUILD_DIR,
    FILE_PACKS, TARGET_PACKS, ORIGINAL_ROM, OUTPUT_ROM
)
from src.utils.binary_io import read_uint32, nlzss_compress
from src.utils.blz import blz_compress_overlay, blz_compress_arm9
from src.utils.imasds_arm9_patch import patch_arm9_bytes, PatchError
from src.ndstool.dsi_builder import (
    DsiBuildError,
    rebuild_dsi_rom,
    verify_dsi_integrity,
)

# ARM9 内存加载基址（本作 header.bin 0x28 = 0x02004000）
ARM9_RAM_BASE = 0x02004000
# ARM9 压缩数据结束指针的文件内偏移（位于未压缩前缀中，可在压缩后重写）
ARM9_COMPRESSED_END_PTR_OFFSET = 0x0FC4

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

def build_nds_with_twl_rebuild() -> None:
    print("\n🛠️  正在使用 ndspy 纯 Python 引擎在内存中构建 ROM...")
    rom = ndspy.rom.NintendoDSRom.fromFile(str(ORIGINAL_ROM))
    
    # 1. 注入重构后的数据包
    for pack in FILE_PACKS:
        if pack["output"] not in TARGET_PACKS: continue
        ezt, ezp = pack["ezt"], pack["ezp"]
        if (REPACK_STAGING / ezt).exists(): rom.setFileByName(ezt, (REPACK_STAGING / ezt).read_bytes())
        if (REPACK_STAGING / ezp).exists(): rom.setFileByName(ezp, (REPACK_STAGING / ezp).read_bytes())
        
    # 2. 注入修改后的程序段 (ARM9)
    # 构建顺序（参见 02_IMPLEMENTATION_SPEC.md P0）：
    #   解压态 ARM9 → 文本注入(Stage 4 已完成) → 四点补丁 → BLZ 压缩
    #   → 按实际压缩长度重写 0x0FC4 → 写入 ROM
    patched_prg_dir = PATCHED_DIR / "PRG_CHS_PATCHED"
    if (patched_prg_dir / "arm9.bin").exists():
        arm9_decompressed = (patched_prg_dir / "arm9.bin").read_bytes()

        # 2a. 应用歌词课四点补丁（必须在压缩前）
        try:
            arm9_decompressed, patch_states = patch_arm9_bytes(arm9_decompressed)
            for site, state in patch_states:
                label = "已补丁" if state == "patched" else "待补丁"
                print(f"  ARM9 补丁 [{label}] 0x{site.offset:08X} {site.description}")
        except PatchError as e:
            print(f"  ❌ ARM9 补丁失败: {e}")
            raise

        # 2b. BLZ 压缩（前 0x4000 字节不压缩，与原版工具一致）
        arm9_compressed = blz_compress_arm9(arm9_decompressed)
        if arm9_compressed is None:
            # 压缩后反而变大：实机不允许未压缩 ARM9，直接报错
            raise RuntimeError(
                "ARM9 BLZ 压缩失败：压缩后体积大于解压态，无法构建"
            )

        compressed_size = len(arm9_compressed)
        print(f"  -> ARM9 BLZ 压缩: {len(arm9_decompressed)}B -> {compressed_size}B "
              f"({compressed_size * 100 // len(arm9_decompressed)}%)")

        # 2c. 重写 0x0FC4 = ARM9_RAM_BASE + compressed_size
        #   该字段位于未压缩前缀（0x0000~0x3FFF），压缩后仍然存在，可安全重写。
        #   实机 BIOS 启动解压器据此值倒序读取压缩流；若沿用原版值或清零，
        #   会从错误地址解压 → 白屏。
        arm9_compressed = bytearray(arm9_compressed)
        new_end_ptr = ARM9_RAM_BASE + compressed_size
        struct.pack_into(
            '<I', arm9_compressed, ARM9_COMPRESSED_END_PTR_OFFSET, new_end_ptr
        )
        print(f"  -> ARM9 0x0FC4 = 0x{new_end_ptr:08X} (RAM base + compressed size)")

        rom.arm9 = bytes(arm9_compressed)
        
    # 3. 【核心黑科技】越过文件树，直接篡改 FAT 底层数据！
    # Overlay 表项字段（每个 32 字节）：
    #   offset+0   overlay_id
    #   offset+4   ram_address
    #   offset+8   ram_size（解压后 RAM 占用大小，BIOS 据此分配 RAM）
    #   offset+12  bss_size
    #   offset+16  static_init_start
    #   offset+20  static_init_end
    #   offset+24  file_id（低 24 位）+ 高字节保留
    #   offset+28  compressed_size_flag：高字节 0x02=未压缩，0x03=BLZ 压缩
    #               低 24 位 = ROM 中文件实际字节数（压缩后大小）
    #
    # 旧实现把汉化 overlay 以未压缩（0x02）写入，导致：
    #   1. ROM 中 overlay 区体积从 ~683KB 暴涨到 ~1.1MB（卡顿）
    #   2. NDS 实机 BIOS overlay loader 严格模式校验失败 → 白屏
    # 修复：对汉化 overlay 做 BLZ 压缩后写入，comp_size_flag=0x03，
    #      ram_size 保持解压后大小，file_size_field=压缩后大小。
    y9_data = bytearray(rom.arm9OverlayTable)
    if patched_prg_dir.exists():
        for f in sorted(os.listdir(patched_prg_dir)):
            if f.startswith('overlay_') and f.endswith('.bin'):
                try: ovl_id = int(f.replace('overlay_', '').replace('.bin', ''))
                except ValueError: continue

                decompressed = (patched_prg_dir / f).read_bytes()
                ram_size = len(decompressed)

                offset = ovl_id * 32
                if offset + 32 <= len(y9_data):
                    # 获取底层物理 ID
                    file_id = struct.unpack_from('<I', y9_data, offset + 24)[0] & 0x00FFFFFF

                    # BLZ 压缩 overlay（min_uncompressed_region_size=0，与原版一致）
                    compressed = blz_compress_overlay(decompressed)
                    if compressed is None:
                        # 压缩后反而更大 → 保持未压缩
                        rom.files[file_id] = decompressed
                        file_size_field = ram_size & 0x00FFFFFF
                        new_flag = 0x02000000 | file_size_field
                        print(f"  -> 已注入（未压缩）: {f} ({ram_size}B)")
                    else:
                        rom.files[file_id] = compressed
                        file_size_field = len(compressed) & 0x00FFFFFF
                        new_flag = 0x03000000 | file_size_field
                        print(f"  -> 已注入（BLZ 压缩）: {f} "
                              f"({ram_size}B -> {len(compressed)}B, "
                              f"{len(compressed)*100//ram_size}%)")

                    # ram_size 始终是解压后大小（BIOS 据此分配 RAM）
                    struct.pack_into('<I', y9_data, offset + 8, ram_size)
                    struct.pack_into('<I', y9_data, offset + 28, new_flag)

    rom.arm9OverlayTable = bytes(y9_data)
            
    # 4. 在内存中生成只含新 NTR 内容的基础 ROM。
    # ndspy 会把 Header 0x80（本作的 application_end_offset）误当作普通 NDS
    # RSA 指针，并把旧 TWL 开头 0x88 字节当成签名追加到文件尾；必须清空。
    rom.rsaSignature = b''
    temp_rom_data = rom.save()
    print(f"  -> 新 NTR 内容构建成功：0x{len(temp_rom_data):X} 字节")

    print("\n✨ 正在完整重建 DSi (TWL) digest / HMAC / modcrypt...")
    try:
        final_rom, report = rebuild_dsi_rom(temp_rom_data, ORIGINAL_ROM)
        crypto_check = verify_dsi_integrity(final_rom)
    except (DsiBuildError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f"DSi/TWL 完整重建失败：{exc}") from exc
    if not crypto_check["all_ok"]:
        raise RuntimeError(f"DSi/TWL 构建后回读门禁失败：{crypto_check}")

    OUTPUT_ROM.write_bytes(final_rom)
    print(f"  -> application_end_offset: 0x{report.application_end_offset:08X}")
    print(f"  -> Sector digest table: 0x{report.sector_hashtable_start:08X} "
          f"+ 0x{report.sector_hashtable_size:X}")
    print(f"  -> Block digest table: 0x{report.block_hashtable_start:08X} "
          f"+ 0x{report.block_hashtable_size:X}")
    print(f"  -> ARM9i: 0x{report.dsi9_rom_offset:08X} + 0x{report.dsi9_size:X}")
    print(f"  -> ARM7i: 0x{report.dsi7_rom_offset:08X} + 0x{report.dsi7_size:X}")
    print(f"  -> modcrypt1: 0x{report.modcrypt1_start:08X} "
          f"+ 0x{report.modcrypt1_size:X}")
    print(f"  -> Secure CRC: 0x{report.secure_area_crc:04X}; "
          f"Header CRC: 0x{report.header_crc:04X}")
    print("  -> DSi digest / HMAC / modcrypt 回读：全部通过")
    print(f"  ✅ DSi/TWL 完整重建完成：0x{report.physical_rom_size:X} 字节")


# 保留旧函数名，避免 main.py 或外部脚本的既有导入失效。
build_nds_and_restore_twl = build_nds_with_twl_rebuild

def main() -> None:
    print("=" * 50)
    print(" THE iDOLM@STER Dearly Stars - 纯 Python 终极构建")
    print("=" * 50)
    
    if REPACK_STAGING.exists(): shutil.rmtree(REPACK_STAGING)
    REPACK_STAGING.mkdir(parents=True, exist_ok=True)
    
    repack_data_archives()
    build_nds_with_twl_rebuild()
    
    print("\n🎉 全剧终！终极纯净版汉化 ROM 已生成至：")
    print(f"   {OUTPUT_ROM}")

if __name__ == "__main__":
    main()
