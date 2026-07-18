"""P0-1/P0-2 验证脚本：用原版 ROM 测试 Header/FNT/FAT 解析。"""
from pathlib import Path
from src.ndstool import parse_header, parse_fat, parse_fnt, build_fnt

rom_path = next(Path("game_data/0_Original").glob("*.nds"))
print(f"ROM: {rom_path.name}")
data = rom_path.read_bytes()

# ---------- Header ----------
h = parse_header(data)
print("\n=== Header ===")
print(f"FNT off/size: 0x{h.fnt_offset:X}/0x{h.fnt_size:X}")
print(f"FAT off/size: 0x{h.fat_offset:X}/0x{h.fat_size:X}")
print(f"ARM9 OVL off/size: 0x{h.arm9_overlay_offset:X}/0x{h.arm9_overlay_size:X}")
print(f"App end (NTR): 0x{h.application_end_offset:X}")
print(f"Logo CRC: 0x{h.logo_crc:X}, Header CRC: 0x{h.header_crc:X}")
print(f"CRC verify (logo, header): {h.verify_crc()}")

# ---------- FAT ----------
fat_data = data[h.fat_offset : h.fat_offset + h.fat_size]
fat = parse_fat(fat_data)
print(f"\n=== FAT: {len(fat)} entries ===")
for i, e in enumerate(fat.entries[:5]):
    print(f"  [{i}] top=0x{e.top:X}, bottom=0x{e.bottom:X}, size=0x{e.size:X}")
print(f"  ... (last) [{len(fat)-1}] top=0x{fat.entries[-1].top:X}, bottom=0x{fat.entries[-1].bottom:X}")

# ---------- FNT ----------
fnt_data = data[h.fnt_offset : h.fnt_offset + h.fnt_size]
fnt = parse_fnt(fnt_data, fat=fat)
print(f"\n=== FNT root: dir_id=0x{fnt.root.dir_id:X}, first_file_id={fnt.root.first_file_id} ===")
print(f"Root files ({len(fnt.root.files)}):")
for f in fnt.root.files[:15]:
    print(f"  - {f.name} (file_id={f.file_id})")
print(f"Root subdirs ({len(fnt.root.subdirs)}):")
for d in fnt.root.subdirs:
    print(f"  - [0x{d.dir_id:04X}] {d.name} (first_file_id={d.first_file_id}, "
          f"{len(d.files)} files, {len(d.subdirs)} subdirs)")

print("\n=== Full walk (first 20) ===")
for fid, path in [(fid, p) for p, fid in fnt.list_all_files()[:20]]:
    print(f"  {fid:4d}: {path}")
print(f"\nTotal files in FNT: {len(fnt.list_all_files())}")
print(f"Total files in FAT: {len(fat)}")
print(f"Match: {len(fnt.list_all_files()) == len(fat)}")

# ---------- FNT 重建往返测试 ----------
rebuilt = build_fnt(fnt.root)
print(f"\n=== FNT rebuild roundtrip ===")
print(f"Original FNT size: 0x{h.fnt_size:X} = {h.fnt_size}")
print(f"Rebuilt FNT size:  0x{len(rebuilt):X} = {len(rebuilt)}")
print(f"Byte-equal: {rebuilt == fnt_data}")

# 如果不字节相等，做语义比较（重建后再解析）
if rebuilt != fnt_data:
    fnt2 = parse_fnt(rebuilt)
    files1 = sorted(fnt.list_all_files())
    files2 = sorted(fnt2.list_all_files())
    print(f"Semantic equal (same file list): {files1 == files2}")
