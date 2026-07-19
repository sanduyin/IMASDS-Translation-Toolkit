import sys
sys.path.insert(0, '.')
from PIL import Image
from pathlib import Path
import json

# 查看 EDT_D sheet.png
sheet = Image.open('game_data/1_Extracted_Images/OBJ/EDT_D/sheet.png')
print('EDT_D sheet.png: {}x{}, mode={}'.format(sheet.width, sheet.height, sheet.mode))

# 查看 cell_000.png
cell0 = Image.open('game_data/1_Extracted_Images/OBJ/EDT_D/cell_000.png')
print('EDT_D cell_000.png: {}x{}, mode={}'.format(cell0.width, cell0.height, cell0.mode))

# 查看 manifest
with open('game_data/1_Extracted_Images/OBJ/EDT_D/manifest.json', 'r', encoding='utf-8') as f:
    manifest = json.load(f)
print('EDT_D manifest: {} cells, mapping_mode={}, bpp={}'.format(
    manifest['n_cells'], manifest['mapping_mode'], manifest['bpp']))
print('Cell 0 info:')
print('  width={}, height={}'.format(manifest['cells'][0]['width'], manifest['cells'][0]['height']))
print('  sheet_rect={}'.format(manifest['cells'][0]['sheet_rect']))
print('  OAM count={}'.format(len(manifest['cells'][0]['oam'])))
for i, oam in enumerate(manifest['cells'][0]['oam']):
    print('    OAM[{}]: x={}, y={}, w={}, h={}, char_name={}, palette={}, tiles={}'.format(
        i, oam['x'], oam['y'], oam['width'], oam['height'],
        oam['char_name'], oam['palette'], oam['tiles']))

# 用 ASCII 预览 cell_000 (16x16 X icon 应在 (8,3) 位置)
print()
print('=== Cell 0 ASCII preview (downsampled 4x) ===')
img = cell0.resize((cell0.width // 4, cell0.height // 4))
px = img.load()
for y in range(img.height):
    line = ''
    for x in range(img.width):
        r, g, b = px[x, y]
        if r < 50 and g > 200 and b < 50:
            line += '.'  # 绿幕
        elif r > 200 and g > 200 and b > 200:
            line += '#'  # 白
        elif r < 50 and g < 50 and b < 50:
            line += ' '  # 黑
        elif r > 200 and g < 100 and b < 100:
            line += 'R'  # 红
        elif r < 100 and g < 100 and b > 200:
            line += 'B'  # 蓝
        else:
            line += '?'
    print(line)

# 也试下 MENUITEM
print()
sheet2 = Image.open('game_data/1_Extracted_Images/OBJ/MENUITEM/sheet.png')
print('MENUITEM sheet.png: {}x{}'.format(sheet2.width, sheet2.height))
