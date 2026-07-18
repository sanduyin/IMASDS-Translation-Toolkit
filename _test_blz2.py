"""P0-3 重新验证：BLZ 哈希加速后的正确性和速度。"""
import struct
import sys
import time
from pathlib import Path

try:
    import ndspy.codeCompression as comp
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ndspy"])
    import ndspy.codeCompression as comp

from src.ndstool import parse_header, parse_overlay_table
from src.utils.blz import blz_decompress, blz_compress, blz_compress_overlay

rom_path = next(Path("game_data/0_Original").glob("*.nds"))
data = rom_path.read_bytes()
h = parse_header(data)

# ---------- 1) 解压对照 ----------
print("=== 1) 解压对照（与 ndspy 逐字节比对）===")
arm9_compressed = data[h.arm9_rom_offset : h.arm9_rom_offset + h.arm9_size]
arm9_de_ndspy = comp.decompress(arm9_compressed)
arm9_de_ours = blz_decompress(arm9_compressed)
print(f"ARM9 equal: {arm9_de_ndspy == arm9_de_ours}")

# ---------- 2) 小数据压缩回归 ----------
print("\n=== 2) 小数据压缩回归 ===")
test1 = b"A" * 100 + b"B" * 100 + b"C" * 50 + b"A" * 100
c1 = blz_compress(test1, 0)
d1 = blz_decompress(c1) if c1 else None
print(f"Test1: compressed={len(c1) if c1 else 'None'}, roundtrip={d1 == test1 if c1 else 'N/A'}")

# ---------- 3) overlay 0/1 压缩往返 ----------
print("\n=== 3) Overlay 0/1 压缩往返 ===")
ovl_table_data = data[h.arm9_overlay_offset : h.arm9_overlay_offset + h.arm9_overlay_size]
ovl_entries = parse_overlay_table(ovl_table_data)

for ov_id in [0, 1, 5, 7]:
    e = ovl_entries[ov_id]
    fat_top = struct.unpack_from("<I", data, h.fat_offset + e.file_id * 8)[0]
    fat_bot = struct.unpack_from("<I", data, h.fat_offset + e.file_id * 8 + 4)[0]
    ovl_compressed = data[fat_top:fat_bot]
    de = blz_decompress(ovl_compressed)
    t0 = time.time()
    rc = blz_compress_overlay(de)
    t1 = time.time()
    if rc is None:
        print(f"  overlay_{ov_id:04d}: None (incompressible), time={t1-t0:.2f}s")
    else:
        rd = blz_decompress(rc)
        print(f"  overlay_{ov_id:04d}: orig=0x{len(ovl_compressed):X}, recompressed=0x{len(rc):X}, "
              f"roundtrip={rd == de}, time={t1-t0:.2f}s")

# ---------- 4) ARM9 压缩（关键性能测试） ----------
print("\n=== 4) ARM9 压缩性能测试（哈希加速后）===")
arm9_de = arm9_de_ours
# 去除 footer
footer = b""
if len(arm9_de) >= 12 and arm9_de[-12:-8] == bytes([0x21, 0x06, 0xC0, 0xDE]):
    footer = arm9_de[-12:]
    arm9_payload = arm9_de[:-12]
else:
    arm9_payload = arm9_de
print(f"ARM9 payload size: 0x{len(arm9_payload):X}, footer size: {len(footer)}")

t0 = time.time()
arm9_rc = blz_compress(arm9_payload, min_uncompressed_region_size=0x4000, max_search_positions=32)
t1 = time.time()
print(f"Compress time: {t1-t0:.2f}s")
if arm9_rc is None:
    print("Cannot compress ARM9")
else:
    print(f"Recompressed size: 0x{len(arm9_rc):X} (original 0x{len(arm9_compressed):X})")
    rd = blz_decompress(arm9_rc)
    print(f"Roundtrip decompress equal: {rd == arm9_payload}")
    ratio = len(arm9_rc) / len(arm9_compressed)
    print(f"Compression ratio vs original: {ratio:.4f} ({'better' if ratio < 1 else 'worse'} than original)")
