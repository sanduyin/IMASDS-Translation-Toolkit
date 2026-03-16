# src/stage1_unpack.py
import os
import sys
import struct
import shutil
import subprocess
import urllib.request
import platform

# ===================================================================
# 自动化依赖管理：自动安装 ndspy 以处理 ARM9 的 BLZ(逆向LZ10) 压缩
# ===================================================================
try:
    import ndspy.codeCompression as comp
except ImportError:
    print("🔧 首次运行：正在自动安装底层解压引擎 [ndspy]...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "ndspy"])
        import ndspy.codeCompression as comp
        print("✅ [ndspy] 安装成功！")
    except Exception as e:
        print(f"❌ 自动安装 ndspy 失败: {e}\n请手动在终端输入: pip install ndspy")
        sys.exit(1)

# 导入上一级的全局配置
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ORIGINAL_DIR, EXTRACT_DIR, FILE_PACKS, NDSTOOL_EXE, ORIGINAL_ROM
from src.utils.binary_io import read_uint32

def ensure_ndstool():
    """检查并自动下载 ndstool.exe"""
    if NDSTOOL_EXE.exists(): return True
        
    print("🔧 未检测到 ndstool.exe，正在尝试全自动下载...")
    if platform.system() != 'Windows':
        print("❌ 自动下载目前仅支持 Windows 系统。请手动下载 ndstool 放入 0_Original 目录。")
        return False
        
    url = "https://github.com/Relys/ndstool/releases/download/v2.1.2/ndstool.exe"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response, open(NDSTOOL_EXE, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        print("✅ ndstool.exe 自动下载成功！")
        return True
    except Exception as e:
        print(f"❌ 自动下载失败: {e}")
        return False

def decompress_program_files(arm9_extract_dir):
    """
    【核心修复】自动对 ARM9 和 Overlay 进行 BLZ 解压！
    抛弃 CT2，直接在内存中还原程序二进制，供后续指针扫描。
    """
    print("\n🔓 正在执行 BLZ 逆向解压算法 (处理 ARM9 & Overlays)...")
    
    # 1. 解压 ARM9
    arm9_src = ORIGINAL_DIR / "arm9.bin"
    arm9_dst = arm9_extract_dir / "arm9.bin"
    if arm9_src.exists():
        with open(arm9_src, 'rb') as f: data = f.read()
        try:
            # comp.decompress 会自动识别并跳过 Secure Area 进行解压
            decompressed_data = comp.decompress(data)
            with open(arm9_dst, 'wb') as f: f.write(decompressed_data)
            print("  -> ✅ arm9.bin 解压成功")
        except Exception as e:
            print(f"  -> ⚠️ arm9.bin 解压跳过 (可能已解压或无压缩): {e}")
            shutil.copy2(arm9_src, arm9_dst)

    # 2. 解压所有的 Overlay
    overlay_src_dir = ORIGINAL_DIR / "overlay"
    if overlay_src_dir.exists():
        EXTRACT_DIR.joinpath("overlay").mkdir(parents=True, exist_ok=True)
        # 将原始 overlay 完整复制一份给打包用
        shutil.copytree(overlay_src_dir, EXTRACT_DIR / "overlay", dirs_exist_ok=True)
        
        ovl_count = 0
        for ovl in os.listdir(overlay_src_dir):
            ovl_src = overlay_src_dir / ovl
            ovl_dst = arm9_extract_dir / ovl
            
            with open(ovl_src, 'rb') as f: data = f.read()
            try:
                decomp_data = comp.decompress(data)
                with open(ovl_dst, 'wb') as f: f.write(decomp_data)
                ovl_count += 1
            except Exception:
                shutil.copy2(ovl_src, ovl_dst)
        print(f"  -> ✅ 成功解压了 {ovl_count} 个 Overlay 文件")

def unpack_nds_rom():
    """自动使用 ndstool 解包整个 NDS ROM，并分发文件"""
    if not ORIGINAL_ROM.exists():
        print(f"❌ 找不到原版游戏 ROM: {ORIGINAL_ROM.name}")
        return False
        
    base_data_dir = ORIGINAL_DIR / "Data"
    arm9_extract_dir = EXTRACT_DIR / "ARM9"
    
    if base_data_dir.exists() and arm9_extract_dir.exists():
        print("📦 检测到 ROM 已解包，直接进入数据提取环节...")
        return True
        
    print(f"\n💿 正在调用 ndstool 解包原始 ROM: {ORIGINAL_ROM.name} ...")
    cmd =[
        str(NDSTOOL_EXE), "-x", str(ORIGINAL_ROM),
        "-9", str(ORIGINAL_DIR / "arm9.bin"),
        "-7", str(ORIGINAL_DIR / "arm7.bin"),
        "-y9", str(ORIGINAL_DIR / "y9.bin"),
        "-y7", str(ORIGINAL_DIR / "y7.bin"),
        "-d", str(base_data_dir),
        "-y", str(ORIGINAL_DIR / "overlay"),
        "-h", str(ORIGINAL_DIR / "header.bin"),
        "-t", str(ORIGINAL_DIR / "banner.bin")
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        print("✅ ROM 底包解压成功！")
    except Exception as e:
        print(f"❌ ndstool 解包失败: {e}")
        return False

    print("🚚 正在分发基础文件到工作目录...")
    arm9_extract_dir.mkdir(parents=True, exist_ok=True)
    
    # 分发给 Stage 5 (打包脚本) 使用
    for f in['arm7.bin', 'y9.bin', 'y7.bin', 'header.bin', 'banner.bin']:
        shutil.copy2(ORIGINAL_DIR / f, EXTRACT_DIR / f)
    
    # 调用核心：解压程序文件 (供后续 ARM9 文本导出使用)
    decompress_program_files(arm9_extract_dir)
    return True

def decompress_ring_lz(f_in, decompressed_size):
    """V3/V17 同款环形缓冲解压算法"""
    it = iter(f_in)
    out_data = bytearray()
    ring_buffer = bytearray(0x1000) 
    r_cursor = 0
    
    def get_byte():
        try: return next(it)
        except StopIteration: return None

    while len(out_data) < decompressed_size:
        flag_byte = get_byte()
        if flag_byte is None: break

        for i in range(7, -1, -1):
            if len(out_data) >= decompressed_size: break
            is_compressed = (flag_byte >> i) & 1
            if is_compressed:
                b1, b2 = get_byte(), get_byte()
                if b1 is None or b2 is None: break
                val = (b1 << 8) | b2
                disp = val & 0xFFF
                length = ((val >> 12) & 0xF) + 3
                read_pos = r_cursor - disp - 1
                if read_pos < 0: read_pos += 0x1000
                for _ in range(length):
                    byte_val = ring_buffer[read_pos]
                    out_data.append(byte_val)
                    ring_buffer[r_cursor] = byte_val
                    r_cursor = (r_cursor + 1) & 0xFFF
                    read_pos = (read_pos + 1) & 0xFFF
            else:
                byte_val = get_byte()
                if byte_val is None: break
                out_data.append(byte_val)
                ring_buffer[r_cursor] = byte_val
                r_cursor = (r_cursor + 1) & 0xFFF
    return out_data

def extract_archive(ezt_path, ezp_path, output_dir):
    """解压单个 BIN/IDX 数据包"""
    print(f"📦 提取内部数据: {os.path.basename(ezt_path)}")
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(ezt_path, "rb") as f_idx, open(ezp_path, "rb") as f_bin:
        f_idx.seek(0x0A)
        h_size = struct.unpack('<H', f_idx.read(2))[0]
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
    print(" 环境初始化与全自动解包引擎")
    print("=" * 50)
    
    # 核心：环境检查与全自动底层解包 (包含 ARM9 的自动解压)
    if not ensure_ndstool(): return
    if not unpack_nds_rom(): return

    input_data_dir = ORIGINAL_DIR / "Data"
    for pack in FILE_PACKS:
        ezt_path, ezp_path, out_path = input_data_dir / pack["ezt"], input_data_dir / pack["ezp"], EXTRACT_DIR / pack["output"]
        if ezt_path.exists() and ezp_path.exists():
            extract_archive(ezt_path, ezp_path, out_path)
            
    print("\n🎉 Stage 1 提取全部完成！现在可以直接去执行 Stage 2 导出文本了！")

if __name__ == "__main__":
    main()