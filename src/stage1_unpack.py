# src/stage1_unpack.py
from __future__ import annotations

import os
import sys
import struct
import shutil
import re
from pathlib import Path
from typing import Any

import ndspy.rom
import ndspy.codeCompression as comp

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ORIGINAL_DIR, EXTRACT_DIR, FILE_PACKS, ORIGINAL_ROM
from src.packez.ezp_pack import extract_pack

_INDEXED_FILENAME = re.compile(r"^(\d{4})(_.+)$")


def _preserve_legacy_pack_numbering(output_dir: Path) -> bool:
    """Keep the file numbers previously exposed by Stage 1.

    Game archives commonly wrap all real files in an outer ``*_BEGIN`` /
    ``*_END`` pair.  The historical extractor skipped that first marker and
    therefore exposed file number ``raw_entry - 1``.  Translation sheets and
    image work already use those numbers, so the canonical parser must retain
    that user-facing convention even though it reads the correct 16-byte EZT
    header internally.
    """

    index_path = output_dir / ".index"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        return False
    first_name = lines[1].strip()
    if first_name.endswith("_BEGIN") or first_name.endswith("_START"):
        wrapper_name = first_name[:-6]
    else:
        return False

    indexed_paths = [
        path for path in sorted(output_dir.rglob("*"))
        if path.is_file() and _INDEXED_FILENAME.match(path.name)
    ]
    for path in indexed_paths:
        match = _INDEXED_FILENAME.match(path.name)
        assert match is not None
        raw_index = int(match.group(1))
        if raw_index == 0:
            raise ValueError(f"包装标记之后出现无法下移的 entry 0000：{path}")
        legacy_name = f"{raw_index - 1:04d}{match.group(2)}"
        target = path.with_name(legacy_name)
        if target.exists():
            raise FileExistsError(f"PackEZ 兼容编号发生冲突：{target}")
        path.replace(target)

    # The old extractor also exposed the contents of the outer wrapper at the
    # archive root.  Unwrap only that exact directory; nested real directories
    # remain intact.
    wrapper_dir = output_dir / wrapper_name
    if wrapper_dir.is_dir():
        for child in sorted(wrapper_dir.iterdir()):
            target = output_dir / child.name
            if target.exists():
                raise FileExistsError(f"PackEZ 外层目录展开发生冲突：{target}")
            child.replace(target)
        wrapper_dir.rmdir()
    return True

def _dump_folder(folder_obj: Any, current_path: str, rom: Any, base_dir: Path) -> None:
    """递归爬树提取器：遍历 ndspy 的 FNT(文件目录表) 树"""
    for filename in folder_obj.files:
        rel_path = current_path + filename
        out_path = base_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(rom.getFileByName(rel_path))
        
    for sub_name, sub_folder in folder_obj.folders:
        _dump_folder(sub_folder, current_path + sub_name + "/", rom, base_dir)

def unpack_nds_rom() -> bool:
    if not ORIGINAL_ROM.exists():
        print(f"❌ 找不到原版游戏 ROM: {ORIGINAL_ROM.name}")
        return False
        
    base_data_dir = ORIGINAL_DIR / "Data"
    arm9_extract_dir = EXTRACT_DIR / "ARM9"
    
    if base_data_dir.exists() and arm9_extract_dir.exists():
        print("📦 检测到底层文件已存在，跳过基底解包...")
        return True
        
    print(f"\n💿 正在使用 ndspy 纯 Python 引擎解析 ROM: {ORIGINAL_ROM.name} ...")
    
    rom = ndspy.rom.NintendoDSRom.fromFile(str(ORIGINAL_ROM))
    
    print("  -> 正在遍历导出文件系统 (FNT)...")
    base_data_dir.mkdir(parents=True, exist_ok=True)
    _dump_folder(rom.filenames, "", rom, base_data_dir)

    # 导出映射表和核心文件 (供后续 Stage 2 提取 ARM9 文本计算 RAM 地址使用)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 【核心修复】直接从原始文件切下前 512 字节作为 header.bin，绕过 ndspy 对象的限制！
    with open(ORIGINAL_ROM, 'rb') as f:
        (EXTRACT_DIR / "header.bin").write_bytes(f.read(0x200))
        
    (EXTRACT_DIR / "y9.bin").write_bytes(rom.arm9OverlayTable)
    (EXTRACT_DIR / "arm7.bin").write_bytes(rom.arm7)
    (EXTRACT_DIR / "y7.bin").write_bytes(rom.arm7OverlayTable)

    print("🔓 正在执行 BLZ 逆向解压算法 (处理 ARM9 & Overlays)...")
    arm9_extract_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        (arm9_extract_dir / "arm9.bin").write_bytes(comp.decompress(rom.arm9))
        print("  -> ✅ arm9.bin 解压成功")
    except Exception:
        (arm9_extract_dir / "arm9.bin").write_bytes(rom.arm9)
        print("  -> ⚠️ arm9.bin 保持原样 (未检测到压缩)")
        
    # =======================================================
    # 通过 y9.bin 直接狙击底层物理文件 (FAT ID)
    # =======================================================
    ovl_count = 0
    y9_data = rom.arm9OverlayTable
    for i in range(len(y9_data) // 32):
        ovl_id = struct.unpack_from('<I', y9_data, i * 32)[0]
        file_id = struct.unpack_from('<I', y9_data, i * 32 + 24)[0] & 0x00FFFFFF
        
        if file_id < len(rom.files):
            ovl_data = rom.files[file_id]
            try:
                (arm9_extract_dir / f"overlay_{ovl_id:04d}.bin").write_bytes(comp.decompress(ovl_data))
            except Exception:
                (arm9_extract_dir / f"overlay_{ovl_id:04d}.bin").write_bytes(ovl_data)
            ovl_count += 1
            
    print(f"  -> ✅ 成功处理并解压了 {ovl_count} 个 Overlay 文件")
    return True

def decompress_ring_lz(f_in: bytes, decompressed_size: int) -> bytearray:
    it = iter(f_in)
    out_data, ring_buffer, r_cursor = bytearray(), bytearray(0x1000), 0
    def get_byte() -> int | None:
        try: return next(it)
        except StopIteration: return None

    while len(out_data) < decompressed_size:
        flag_byte = get_byte()
        if flag_byte is None: break
        for i in range(7, -1, -1):
            if len(out_data) >= decompressed_size: break
            if (flag_byte >> i) & 1:
                b1, b2 = get_byte(), get_byte()
                if b1 is None or b2 is None: break
                val = (b1 << 8) | b2
                length = ((val >> 12) & 0xF) + 3
                read_pos = (r_cursor - (val & 0xFFF) - 1) % 0x1000
                for _ in range(length):
                    byte_val = ring_buffer[read_pos]
                    out_data.append(byte_val)
                    ring_buffer[r_cursor] = byte_val
                    r_cursor, read_pos = (r_cursor + 1) % 0x1000, (read_pos + 1) % 0x1000
            else:
                next_byte = get_byte()
                if next_byte is None: break
                byte_val = next_byte
                out_data.append(byte_val)
                ring_buffer[r_cursor] = byte_val
                r_cursor = (r_cursor + 1) % 0x1000
    return out_data

def extract_archive(ezt_path: Path, ezp_path: Path, output_dir: Path) -> None:
    print(f"📦 提取内部数据包: {os.path.basename(ezt_path)}")
    # Extract through the canonical 16-byte EZT parser.  Use a sibling
    # temporary directory so an interrupted extraction never leaves a mix of
    # old and new entry numbering behind.
    temp_dir = output_dir.with_name(f"{output_dir.name}.extracting")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    try:
        extract_pack(ezt_path, ezp_path, temp_dir)
        if _preserve_legacy_pack_numbering(temp_dir):
            print("  🔒 保留既有翻译素材使用的 entry 编号")
        if output_dir.exists():
            shutil.rmtree(output_dir)
        temp_dir.replace(output_dir)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

def main() -> None:
    print("=" * 50)
    print(" 纯 Python NDS 底层解包引擎 (ndspy)")
    print("=" * 50)
    
    if not unpack_nds_rom(): return

    input_data_dir = ORIGINAL_DIR / "Data"
    for pack in FILE_PACKS:
        ezt_path, ezp_path, out_path = input_data_dir / pack["ezt"], input_data_dir / pack["ezp"], EXTRACT_DIR / pack["output"]
        if ezt_path.exists() and ezp_path.exists():
            extract_archive(ezt_path, ezp_path, out_path)
            
    print("\n🎉 Stage 1 提取全部完成！")

if __name__ == "__main__":
    main()
