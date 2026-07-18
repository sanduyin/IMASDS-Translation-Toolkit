"""对比原版 ROM 和汉化 ROM 的关键字段，诊断白屏原因"""
import struct, sys
sys.path.append('.')
from pathlib import Path

orig_path = Path('game_data/0_Original/THE iDOLM@STER Dearly Stars.nds')
patched_path = Path('game_data/3_Build/THE iDOLM@STER Dearly Stars_CHS.nds')

orig = orig_path.read_bytes()
patched = patched_path.read_bytes()

print(f'原版大小: {len(orig)}')
print(f'汉化大小: {len(patched)}')
print()

# 对比 header 前 0x200 字节
print('=== Header 字段对比 ===')
fields = [
    (0x00, 'Game Title', 12, 's'),
    (0x0C, 'Game Code', 4, 's'),
    (0x10, 'Maker Code', 2, 's'),
    (0x12, 'Unit Code', 1, 'B'),
    (0x15, 'Flags', 1, 'B'),
    (0x20, 'ARM9 ROM offset', 4, 'I'),
    (0x24, 'ARM9 Entry addr', 4, 'I'),
    (0x28, 'ARM9 RAM addr', 4, 'I'),
    (0x2C, 'ARM9 Size', 4, 'I'),
    (0x30, 'ARM7 ROM offset', 4, 'I'),
    (0x34, 'ARM7 Entry addr', 4, 'I'),
    (0x38, 'ARM7 RAM addr', 4, 'I'),
    (0x3C, 'ARM7 Size', 4, 'I'),
    (0x40, 'FNT offset', 4, 'I'),
    (0x44, 'FNT size', 4, 'I'),
    (0x48, 'FAT offset', 4, 'I'),
    (0x4C, 'FAT size', 4, 'I'),
    (0x50, 'ARM9 Overlay offset', 4, 'I'),
    (0x54, 'ARM9 Overlay size', 4, 'I'),
    (0x55, 'ARM7 Overlay offset', 4, 'I'),
    (0x60, 'ROM Header Size', 4, 'I'),
    (0x68, 'ARM9 Auto Load List RAM addr', 4, 'I'),
    (0x6C, 'Secure Area CRC', 2, 'H'),
    (0x70, 'ROM Timeout', 4, 'I'),
    (0x80, 'TWL ROM size', 4, 'I'),
    (0x15C, 'Logo CRC', 2, 'H'),
    (0x15E, 'Header CRC', 2, 'H'),
]

diff_count = 0
for offset, name, size, fmt in fields:
    orig_val = struct.unpack_from(f'<{fmt}', orig, offset)[0]
    patched_val = struct.unpack_from(f'<{fmt}', patched, offset)[0]
    if orig_val != patched_val:
        diff_count += 1
        if fmt == 's':
            orig_s = orig[offset:offset+size]
            patched_s = patched[offset:offset+size]
            print(f'  ❌ {name} (0x{offset:03X}): orig={orig_s!r} patched={patched_s!r}')
        else:
            print(f'  ❌ {name} (0x{offset:03X}): orig=0x{orig_val:08X} patched=0x{patched_val:08X}')
    else:
        if fmt == 's':
            print(f'  ✅ {name} (0x{offset:03X}): {orig[offset:offset+size]!r}')
        else:
            print(f'  ✅ {name} (0x{offset:03X}): 0x{orig_val:08X}')

print(f'\nHeader 差异: {diff_count} 个字段')

# 检查 ARM9 secure area magic
print('\n=== ARM9 Secure Area ===')
arm9_off_orig = struct.unpack_from('<I', orig, 0x20)[0]
arm9_off_patched = struct.unpack_from('<I', patched, 0x20)[0]
print(f'ARM9 offset: orig=0x{arm9_off_orig:X} patched=0x{arm9_off_patched:X}')
if arm9_off_orig == 0x4000:
    sa_orig = orig[0x4000:0x4008]
    sa_patched = patched[0x4000:0x4008]
    print(f'Secure area magic: orig={sa_orig!r} patched={sa_patched!r}')
    # 检查 secure area 前 0x100 字节是否相同
    sa_orig_full = orig[0x4000:0x4100]
    sa_patched_full = patched[0x4000:0x4100]
    if sa_orig_full == sa_patched_full:
        print('Secure area 前 0x100 字节: 相同')
    else:
        diffs = sum(1 for a, b in zip(sa_orig_full, sa_patched_full) if a != b)
        print(f'Secure area 前 0x100 字节: {diffs} 字节不同')

# 检查 overlay 表
print('\n=== Overlay Table ===')
ovt_off_orig = struct.unpack_from('<I', orig, 0x50)[0]
ovt_size_orig = struct.unpack_from('<I', orig, 0x54)[0]
ovt_off_patched = struct.unpack_from('<I', patched, 0x50)[0]
ovt_size_patched = struct.unpack_from('<I', patched, 0x54)[0]
print(f'Overlay table: orig offset=0x{ovt_off_orig:X} size=0x{ovt_size_orig:X}')
print(f'Overlay table: patched offset=0x{ovt_off_patched:X} size=0x{ovt_size_patched:X}')

ovt_count = ovt_size_orig // 32
print(f'Overlay count: {ovt_count}')
for i in range(ovt_count):
    o_orig = struct.unpack_from('<I', orig, ovt_off_orig + i*32 + 24)[0]
    o_patched = struct.unpack_from('<I', patched, ovt_off_patched + i*32 + 24)[0]
    cs_orig = struct.unpack_from('<I', orig, ovt_off_orig + i*32 + 28)[0]
    cs_patched = struct.unpack_from('<I', patched, ovt_off_patched + i*32 + 28)[0]
    rs_orig = struct.unpack_from('<I', orig, ovt_off_orig + i*32 + 8)[0]
    rs_patched = struct.unpack_from('<I', patched, ovt_off_patched + i*32 + 8)[0]
    if o_orig != o_patched or cs_orig != cs_patched or rs_orig != rs_patched:
        print(f'  Overlay {i}: file_id orig=0x{o_orig:08X} patched=0x{o_patched:08X}, '
              f'ram_size orig=0x{rs_orig:08X} patched=0x{rs_patched:08X}, '
              f'cs_flag orig=0x{cs_orig:08X} patched=0x{cs_patched:08X}')

# 检查 0x1C0-0x200 DSi header
print('\n=== DSi Header (0x1C0-0x200) ===')
dsi_orig = orig[0x1C0:0x200]
dsi_patched = patched[0x1C0:0x200]
if dsi_orig == dsi_patched:
    print('DSi header: 相同 ✅')
else:
    diffs = sum(1 for a, b in zip(dsi_orig, dsi_patched) if a != b)
    print(f'DSi header: {diffs} 字节不同 ❌')
