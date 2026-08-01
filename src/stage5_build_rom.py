# src/stage5_build_rom.py
from __future__ import annotations

import os
import sys
import struct
import shutil
import re
from pathlib import Path
from collections.abc import Sequence

import ndspy.rom
from Crypto.Cipher import AES as _AES_DEPENDENCY_CHECK  # noqa: F401

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    ORIGINAL_DIR, EXTRACT_DIR, PATCHED_DIR, REPACK_STAGING, BUILD_DIR,
    FILE_PACKS, TARGET_PACKS, ORIGINAL_ROM, OUTPUT_ROM, LYRIC_BUILD_DIR,
)
from src.utils.blz import blz_compress_overlay, blz_compress_arm9
from src.utils.imasds_arm9_patch import patch_arm9_bytes, PatchError
from src.utils.lesvoice import validate_deployed_lyric_patches
from src.packez.ezp_pack import extract_pack, build_pack
from src.packez.ezt_parser import read_idx
from src.ndstool.dsi_builder import (
    DsiBuildError,
    rebuild_dsi_rom,
    verify_dsi_integrity,
)

# ARM9 内存加载基址（本作 header.bin 0x28 = 0x02004000）
ARM9_RAM_BASE = 0x02004000
# ARM9 压缩数据结束指针的文件内偏移（位于未压缩前缀中，可在压缩后重写）
ARM9_COMPRESSED_END_PTR_OFFSET = 0x0FC4

_INDEXED_FILENAME = re.compile(r"^(\d{4})_")
_PATCH_DIR_SUFFIXES = ("_CHS_PATCHED", "_IMG_PATCHED")


def _indexed_files(root: Path) -> dict[int, Path]:
    """Return entry-number → file, rejecting ambiguous duplicate numbers."""

    result: dict[int, Path] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        match = _INDEXED_FILENAME.match(path.name)
        if match is None:
            continue
        index = int(match.group(1))
        previous = result.get(index)
        if previous is not None:
            raise RuntimeError(
                f"entry {index:04d} 在 {root} 中重复：{previous} / {path}"
            )
        result[index] = path
    return result


def _entry_basename(path: Path) -> str:
    match = _INDEXED_FILENAME.match(path.name)
    if match is None:
        raise ValueError(f"文件缺少四位 entry 编号：{path}")
    return path.name[match.end():]


def _collect_replacements(
    originals: dict[int, Path],
    patched_dirs: Sequence[Path],
) -> dict[int, Path]:
    """Resolve patches without renumbering existing translation assets.

    Canonical PackEZ indices include an outer ``*_BEGIN`` entry, while older
    Stage 1 output (and existing translation work) numbers the following file
    as zero.  An exact stored basename plus either the canonical number or that
    historical one-entry offset is required.  This prevents a legacy ``0022``
    patch from being written to raw entry 22 when its matching file is entry 23.
    """

    originals_by_name: dict[str, list[int]] = {}
    for index, path in originals.items():
        originals_by_name.setdefault(_entry_basename(path), []).append(index)

    replacements: dict[int, Path] = {}
    for patched_dir in patched_dirs:
        resolved_in_dir: dict[int, Path] = {}
        for patch_index, patch_path in _indexed_files(patched_dir).items():
            basename = _entry_basename(patch_path)
            candidates = originals_by_name.get(basename, [])
            compatible = [
                index for index in candidates
                if patch_index == index or patch_index == index - 1
            ]
            if len(compatible) != 1:
                candidate_text = (
                    ", ".join(f"{index:04d}" for index in candidates)
                    if candidates else "无同名 entry"
                )
                raise RuntimeError(
                    f"无法安全定位补丁 {patch_path.name}：原包候选 {candidate_text}；"
                    "要求文件名一致且编号为原始编号或既有的 -1 编号"
                )
            actual_index = compatible[0]
            previous = resolved_in_dir.get(actual_index)
            if previous is not None:
                raise RuntimeError(
                    f"两个补丁指向同一原始 entry {actual_index:04d}："
                    f"{previous} / {patch_path}"
                )
            resolved_in_dir[actual_index] = patch_path

        # Earlier suffixes have priority, matching the historical pipeline:
        # CHS text/font patches override generic image patches for one entry.
        for index, path in resolved_in_dir.items():
            replacements.setdefault(index, path)
    return replacements


def repack_archive(
    original_ezt: Path,
    original_ezp: Path,
    patched_dirs: Sequence[Path],
    output_ezt: Path,
    output_ezp: Path,
    work_dir: Path,
) -> int:
    """Rebuild one PackEZ archive without changing entry numbers or names.

    The original archive is first extracted with the canonical parser.  Patch
    files are matched exclusively by their existing four-digit entry prefix;
    the original ``.index`` remains authoritative for order and stored names.
    """

    if not original_ezt.is_file() or not original_ezp.is_file():
        raise FileNotFoundError(
            f"PackEZ 原包不完整：{original_ezt} / {original_ezp}"
        )

    if work_dir.exists():
        shutil.rmtree(work_dir)
    verify_dir = work_dir.with_name(f"{work_dir.name}.verify")
    if verify_dir.exists():
        shutil.rmtree(verify_dir)

    try:
        extract_pack(original_ezt, original_ezp, work_dir)
        originals = _indexed_files(work_dir)

        replacements = _collect_replacements(originals, patched_dirs)

        for index, patch_path in replacements.items():
            shutil.copy2(patch_path, originals[index])

        output_ezt.parent.mkdir(parents=True, exist_ok=True)
        output_ezp.parent.mkdir(parents=True, exist_ok=True)
        build_pack(work_dir, output_ezt, output_ezp)

        original_header, original_entries = read_idx(original_ezt.read_bytes())
        rebuilt_header, rebuilt_entries = read_idx(output_ezt.read_bytes())
        if rebuilt_header.something1 != original_header.something1:
            raise RuntimeError("PackEZ something1 在重建后发生变化")
        if len(rebuilt_entries) != len(original_entries):
            raise RuntimeError(
                f"PackEZ entry 数变化：{len(original_entries)} -> {len(rebuilt_entries)}"
            )

        # Read the rebuilt archive back and verify every intended replacement.
        # This catches compression/alignment mistakes before a ROM is written.
        extract_pack(output_ezt, output_ezp, verify_dir)
        verified = _indexed_files(verify_dir)
        if set(verified) != set(originals):
            raise RuntimeError("PackEZ 回读后的 entry 编号集合发生变化")
        for index, patch_path in replacements.items():
            if verified[index].read_bytes() != patch_path.read_bytes():
                raise RuntimeError(f"PackEZ entry {index:04d} 回读与补丁数据不一致")

        return len(replacements)
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir)
        if verify_dir.exists():
            shutil.rmtree(verify_dir)

def repack_data_archives() -> None:
    print("📦 开始重构建核心数据包 (BIN/IDX)...")
    orig_data_dir = ORIGINAL_DIR / "Data"
    
    for pack in FILE_PACKS:
        sub_dir = pack["output"]
        if sub_dir not in TARGET_PACKS: continue
            
        ezt_name, ezp_name = pack["ezt"], pack["ezp"]
        orig_idx, orig_bin = orig_data_dir / ezt_name, orig_data_dir / ezp_name
        out_idx, out_bin = REPACK_STAGING / ezt_name, REPACK_STAGING / ezp_name
        
        if not orig_idx.exists() or not orig_bin.exists():
            raise FileNotFoundError(f"缺少原始 PackEZ：{orig_idx} / {orig_bin}")
        print(f"  -> 处理 {ezp_name} ({sub_dir})...")
        patched_dirs = [
            PATCHED_DIR / f"{sub_dir}{suffix}"
            for suffix in _PATCH_DIR_SUFFIXES
        ]
        mod_count = repack_archive(
            orig_idx,
            orig_bin,
            patched_dirs,
            out_idx,
            out_bin,
            REPACK_STAGING / f".{sub_dir}_source",
        )
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
        arm9_compressed_ba = bytearray(arm9_compressed)
        new_end_ptr = ARM9_RAM_BASE + compressed_size
        struct.pack_into(
            '<I', arm9_compressed_ba, ARM9_COMPRESSED_END_PTR_OFFSET, new_end_ptr
        )
        print(f"  -> ARM9 0x0FC4 = 0x{new_end_ptr:08X} (RAM base + compressed size)")

        rom.arm9 = bytes(arm9_compressed_ba)
        
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
    
    validate_deployed_lyric_patches(LYRIC_BUILD_DIR, PATCHED_DIR)

    if REPACK_STAGING.exists(): shutil.rmtree(REPACK_STAGING)
    REPACK_STAGING.mkdir(parents=True, exist_ok=True)
    
    repack_data_archives()
    build_nds_with_twl_rebuild()
    
    print("\n🎉 全剧终！终极纯净版汉化 ROM 已生成至：")
    print(f"   {OUTPUT_ROM}")

if __name__ == "__main__":
    main()
