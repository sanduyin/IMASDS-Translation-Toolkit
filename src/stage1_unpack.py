# src/stage1_unpack.py
import os
import sys
import struct
import subprocess

try:
    import ndspy.rom
    import ndspy.codeCompression as comp
except ImportError:
    print("🔧 首次运行：正在自动安装核心 NDS 引擎 [ndspy]...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ndspy"])
    import ndspy.rom
    import ndspy.codeCompression as comp

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ORIGINAL_DIR, EXTRACT_DIR, FILE_PACKS, ORIGINAL_ROM
from src.utils.binary_io import read_uint32

def unpack_nds_rom():
    if not ORIGINAL_ROM.exists():
        print(f"❌ 找不到原版游戏 ROM: {ORIGINAL_ROM.name}")
        return False
        
    base_data_dir = ORIGINAL_DIR / "Data"
    arm9_extract_dir = EXTRACT_DIR / "ARM9"
    
    if base_data_dir.exists() and arm9_extract_dir.exists():
        print("📦 检测到底层文件已存在，跳过基底解包...")
        return True
        
    print(f"\n💿 正在使用 ndspy 纯 Python 引擎解析 ROM: {ORIGINAL_ROM.name} ...")
    
    # 1. 瞬间将整个 ROM 加载到 Python 内存中
    rom = ndspy.rom.NintendoDSRom.fromFile(str(ORIGINAL_ROM))
    
    # 2. 导出内部的 Data 文件系统
    base_data_dir.mkdir(parents=True, exist_ok=True)
    for filename in rom.filenames:
        out_path = base_data_dir / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(rom.getFileByName(filename))
        
    # 3. 导出 Header 和映射表 (供 Stage 2 的 ARM9 提取使用)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    (EXTRACT_DIR / "header.bin").write_bytes(rom.header)
    (EXTRACT_DIR / "y9.bin").write_bytes(rom.arm9OverlayTable)
    
    # 4. 执行智能逆向解压 (ARM9 & Overlays)
    print("🔓 正在执行 BLZ 逆向解压算法 (处理 ARM9 & Overlays)...")
    arm9_extract_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        (arm9_extract_dir / "arm9.bin").write_bytes(comp.decompress(rom.arm9))
        print("  -> ✅ arm9.bin 解压成功")
    except Exception:
        (arm9_extract_dir / "arm9.bin").write_bytes(rom.arm9)
        
    ovl_count = 0
    for ovl_id, ovl in rom.arm9Overlays.items():
        try:
            (arm9_extract_dir / f"overlay_{ovl_id:04d}.bin").write_bytes(comp.decompress(ovl.data))
            ovl_count += 1
        except Exception:
            (arm9_extract_dir / f"overlay_{ovl_id:04d}.bin").write_bytes(ovl.data)
            
    print(f"  -> ✅ 成功解压了 {ovl_count} 个 Overlay 文件")
    return True

def decompress_ring_lz(f_in, decompressed_size):
    it = iter(f_in)
    out_data, ring_buffer, r_cursor = bytearray(), bytearray(0x1000), 0
    def get_byte():
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
                byte_val = get_byte()
                if byte_val is None: break
                out_data.append(byte_val)
                ring_buffer[r_cursor] = byte_val
                r_cursor = (r_cursor + 1) % 0x1000
    return out_data

def extract_archive(ezt_path, ezp_path, output_dir):
    print(f"📦 提取内部数据: {os.path.basename(ezt_path)}")
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(ezt_path, "rb") as f_idx, open(ezp_path, "rb") as f_bin:
        h_size = struct.unpack('<H', f_idx.read(12)[10:12])[0]
        f_idx.seek(0x0C)
        file_count = read_uint32(f_idx)
        idx_data_start = h_size + 6
        f_bin.seek(0x0C)
        name_table_offset = read_uint32(f_bin)

        for i in range(file_count):
            f_idx.seek(idx_data_start + i * 12)
            offset, size_raw, name_rel = read_uint32(f_idx), read_uint32(f_idx), read_uint32(f_idx)
            decomp_size = size_raw & 0xFFFFFFF
            is_compressed = (size_raw & 0x10000000) != 0

            phys_len = (read_uint32(f_idx) if i < file_count - 1 else name_table_offset) - offset
            f_bin.seek(offset)
            raw_data = f_bin.read(phys_len)
            
            if is_compressed and len(raw_data) >= 4 and raw_data[0] == 0x10:
                try: final_data = decompress_ring_lz(raw_data[4:], decomp_size)
                except Exception: final_data = raw_data
            else:
                final_data = raw_data[:decomp_size]

            file_name = f"file_{i}.bin"
            if name_rel > 0:
                saved_pos = f_bin.tell()
                f_bin.seek(name_table_offset + name_rel)
                name_bytes = bytearray()
                while True:
                    char = f_bin.read(1)
                    if char == b'\x00' or not char: break
                    name_bytes += char
                try: file_name = name_bytes.decode('cp932')
                except: file_name = name_bytes.decode('ascii', errors='ignore')
                f_bin.seek(saved_pos)

            file_name = "".join([c for c in file_name if c.isalnum() or c in (' ', '.', '_', '-', '+', '[', ']')])
            if not file_name: file_name = f"file_{i}.bin"

            with open(output_dir / f"{i:04d}_{file_name}", "wb") as f_out:
                f_out.write(final_data)

def main():
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
