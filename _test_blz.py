"""P0-3 BLZ 验证：解压测试 + 小数据压缩往返测试。

只对解压做大规模测试（O(n)），压缩测试用小样本以避免 O(n²) 长耗时。
"""
import struct
import sys
from pathlib import Path

try:
    import ndspy.codeCompression as comp
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "ndspy"])
    import ndspy.codeCompression as comp

from src.ndstool import parse_header, parse_overlay_table
from src.utils.blz import blz_decompress, blz_compress

rom_path = next(Path("game_data/0_Original").glob("*.nds"))
data = rom_path.read_bytes()
h = parse_header(data)

# ---------- 1) 解压对照：ARM9 + 所有 Overlay ----------
print("=== 解压对照（与 ndspy 逐字节比对）===")
arm9_compressed = data[h.arm9_rom_offset : h.arm9_rom_offset + h.arm9_size]
arm9_de_ndspy = comp.decompress(arm9_compressed)
arm9_de_ours = blz_decompress(arm9_compressed)
print(f"ARM9  ndspy=0x{len(arm9_de_ndspy):X} ours=0x{len(arm9_de_ours):X} equal={arm9_de_ndspy == arm9_de_ours}")

ovl_table_data = data[h.arm9_overlay_offset : h.arm9_overlay_offset + h.arm9_overlay_size]
ovl_entries = parse_overlay_table(ovl_table_data)
all_ok = True
for e in ovl_entries:
    fat_top = struct.unpack_from("<I", data, h.fat_offset + e.file_id * 8)[0]
    fat_bot = struct.unpack_from("<I", data, h.fat_offset + e.file_id * 8 + 4)[0]
    ovl_compressed = data[fat_top:fat_bot]
    if not e.is_compressed:
        print(f"  overlay_{e.id:04d}: not compressed, skip")
        continue
    de_ndspy = comp.decompress(ovl_compressed)
    de_ours = blz_decompress(ovl_compressed)
    ok = de_ndspy == de_ours
    all_ok = all_ok and ok
    print(f"  overlay_{e.id:04d}: ndspy=0x{len(de_ndspy):X} ours=0x{len(de_ours):X} equal={ok}")

print(f"\n解压全部字节级一致: {all_ok and arm9_de_ndspy == arm9_de_ours}")

# ---------- 2) 小数据压缩往返测试 ----------
print("\n=== 压缩往返测试（小样本）===")

# 测试 1：少量重复数据（应该压缩率很好）
test1 = b"A" * 100 + b"B" * 100 + b"C" * 50 + b"A" * 100
c1 = blz_compress(test1, 0)
print(f"Test1 (350B): compressed={len(c1) if c1 else 'None'}, "
      f"ratio={len(c1)/len(test1) if c1 else 'N/A'}")
if c1:
    d1 = blz_decompress(c1)
    print(f"  Roundtrip equal: {d1 == test1}")

# 测试 2：ARM9 解压后数据的前 4KB（真实场景）
test2 = arm9_de_ours[:0x4000]
c2 = blz_compress(test2, 0)
print(f"Test2 (ARM9 head 4KB): compressed={len(c2) if c2 else 'None'}")
if c2:
    d2 = blz_decompress(c2)
    print(f"  Roundtrip equal: {d2 == test2}")

# 测试 3：随机数据（应该无法压缩）
import os
test3 = os.urandom(1024)
c3 = blz_compress(test3, 0)
print(f"Test3 (random 1KB): compressed={len(c3) if c3 else 'None'} (None = incompressible, expected)")

# 测试 4：overlay 0 解压后数据（最小 Overlay，~5KB）
ovl0_compressed = data[
    struct.unpack_from("<I", data, h.fat_offset + ovl_entries[0].file_id * 8)[0]:
    struct.unpack_from("<I", data, h.fat_offset + ovl_entries[0].file_id * 8 + 4)[0]
]
ovl0_de = blz_decompress(ovl0_compressed)
print(f"\nTest4: overlay_0000 decompressed size=0x{len(ovl0_de):X}")
from src.utils.blz import blz_compress_overlay
c4 = blz_compress_overlay(ovl0_de)
print(f"  Recompressed size: 0x{len(c4):X} (original 0x{len(ovl0_compressed):X})")
if c4:
    d4 = blz_decompress(c4)
    print(f"  Roundtrip equal: {d4 == ovl0_de}")

# 测试 5：overlay 1（最小，~568B 解压后）
ovl1_compressed = data[
    struct.unpack_from("<I", data, h.fat_offset + ovl_entries[1].file_id * 8)[0]:
    struct.unpack_from("<I", data, h.fat_offset + ovl_entries[1].file_id * 8 + 4)[0]
]
ovl1_de = blz_decompress(ovl1_compressed)
print(f"\nTest5: overlay_0001 decompressed size=0x{len(ovl1_de):X}")
c5 = blz_compress_overlay(ovl1_de)
print(f"  Recompressed size: 0x{len(c5):X} (original 0x{len(ovl1_compressed):X})")
if c5:
    d5 = blz_decompress(c5)
    print(f"  Roundtrip equal: {d5 == ovl1_de}")
