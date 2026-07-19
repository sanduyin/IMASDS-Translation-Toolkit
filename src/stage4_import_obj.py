# src/stage4_import_obj.py
"""OBJ UI 文字汉化导入工具（基于 NCER cell 反向提取）。

将编辑后的 {stem}_sheet.png（RGB 模式 + 绿幕背景）按 sheet_rect 切回各 cell，
再反向提取 tile 像素写回 NCGR。

流程：
    1. 读 {stem}_manifest.json + 编辑后的 {stem}_sheet.png + 原始 NCGR/NCLR/NANR/NCER
    2. 按 manifest 的 sheet_rect 从 sheet 切出每个 cell 的图像
    3. 对每个 cell，按 OAM 信息从 cell 图像提取像素
    4. 反转 flip（导出时应用了 flip）
    5. RGB → NCLR 调色板最近色匹配 → tile pixel value
    6. 绿幕 (0,255,0) → index 0（透明）
    7. 按 OAM 的 char_name + mapping_mode 计算 tile_index，写入 NCGR
    8. NCLR/NANR/NCER 原样复制到输出目录

设计要点：
    - 与 stage2_export_obj.py 配对（NCER cell 渲染 + RGB + 绿幕 + 单 sheet 平铺）
    - 用户只需 PS 编辑一张 sheet.png，程序自动按 sheet_rect 切回各 cell
    - 4bpp: 子调色板范围 [palette*16+1, palette*16+15]（index 0 = 透明 = 绿幕）
    - 8bpp: 全调色板范围 [1, 255]（index 0 = 透明 = 绿幕）
    - 共享 tile：从后往前处理 OAM（与导出渲染顺序一致），后面处理的覆盖前面
    - NANR/NCER/NCLR 完全只读不写（原样复制）
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

from PIL import Image

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, PATCHED_DIR
from src.stage2_export_bg import parse_nds_container, parse_nclr
from src.stage2_export_obj import GREEN_SCREEN
from src.utils.ncer_format import find_obj_quartets


def rgb_to_tile_pixel(
    rgb: tuple[int, int, int],
    palette: list[tuple[int, int, int]],
    bpp: int,
    oam_palette: int,
) -> int:
    """RGB → tile pixel value（NCLR 调色板最近色匹配）。

    绿幕 (0,255,0) → 0（透明）
    4bpp: 在 [palette*16+1, palette*16+15] 范围找最近色，返回 1-15
    8bpp: 在 [1, 255] 范围找最近色，返回 1-255
    """
    # 绿幕 → 透明
    if rgb == GREEN_SCREEN:
        return 0

    # 确定搜索范围（跳过 index 0，因为 index 0 = 透明）
    if bpp == 4:
        start = oam_palette * 16 + 1
        end = min(oam_palette * 16 + 16, len(palette))
    else:
        start = 1
        end = len(palette)

    best_idx = 0
    best_dist = 10**9
    r, g, b = rgb
    for i in range(start, end):
        pr, pg, pb = palette[i]
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < best_dist:
            best_dist = d
            best_idx = i

    # 4bpp: 返回子调色板内偏移（1-15）
    if bpp == 4:
        return best_idx - oam_palette * 16
    # 8bpp: 返回 NCLR 索引（1-255）
    return best_idx


def extract_oam_pixels(
    cell_img: Image.Image,
    oam_x: int,
    oam_y: int,
    oam_w: int,
    oam_h: int,
    min_x: int,
    min_y: int,
) -> list[list[tuple[int, int, int]]]:
    """从 cell PNG 提取 OAM 范围内的 RGB 像素矩阵。

    返回 oam_h × oam_w 的 RGB 像素矩阵。
    超出画布范围的像素用绿幕填充。
    """
    canvas_x = oam_x - min_x
    canvas_y = oam_y - min_y
    img_w, img_h = cell_img.size

    pixels: list[list[tuple[int, int, int]]] = []
    for py in range(oam_h):
        row: list[tuple[int, int, int]] = []
        for px in range(oam_w):
            cx = canvas_x + px
            cy = canvas_y + py
            if 0 <= cx < img_w and 0 <= cy < img_h:
                row.append(cell_img.getpixel((cx, cy)))
            else:
                row.append(GREEN_SCREEN)
        pixels.append(row)
    return pixels


def reverse_flip(
    pixels: list[list[tuple[int, int, int]]],
    flip_x: bool,
    flip_y: bool,
) -> list[list[tuple[int, int, int]]]:
    """反转 flip（flip 是自反的，与 apply_flip 相同）。"""
    if not flip_x and not flip_y:
        return pixels
    result = [row[:] for row in pixels]
    if flip_x:
        for row in result:
            row.reverse()
    if flip_y:
        result.reverse()
    return result


def oam_pixels_to_tiles(
    oam_pixels: list[list[tuple[int, int, int]]],
    palette: list[tuple[int, int, int]],
    bpp: int,
    oam_palette: int,
    tiles_per_row: int,
    tiles_per_col: int,
) -> list[list[int]]:
    """把 OAM 像素矩阵转换为 tile 像素列表。

    返回 (tiles_per_row * tiles_per_col) 个 tile，每 tile 64 像素值。
    """
    tiles: list[list[int]] = []
    for tile_row in range(tiles_per_col):
        for tile_col in range(tiles_per_row):
            tile_pixels: list[int] = [0] * 64
            for py in range(8):
                for px in range(8):
                    src_y = tile_row * 8 + py
                    src_x = tile_col * 8 + px
                    rgb = oam_pixels[src_y][src_x]
                    tile_pixels[py * 8 + px] = rgb_to_tile_pixel(
                        rgb, palette, bpp, oam_palette,
                    )
            tiles.append(tile_pixels)
    return tiles


def write_tiles_to_ncgr(
    ncgr_data: bytes,
    tile_indices: list[int],
    new_tiles: list[list[int]],
    bpp: int,
) -> bytes:
    """把新 tile 数据写入 NCGR 对应位置（保留其他 tile 不变）。

    tile_indices 和 new_tiles 一一对应。
    """
    secs = parse_nds_container(ncgr_data)
    rahc_bytes = secs.get('RAHC') or secs.get('CHAR')
    if rahc_bytes is None:
        raise ValueError("未找到图块 Section")
    rahc = bytearray(rahc_bytes)

    # RAHC header 字段（与 stage2_export_bg.parse_ncgr 一致）
    tile_data_size_field = struct.unpack_from('<I', rahc, 0x10)[0]
    header_size_field = struct.unpack_from('<I', rahc, 0x14)[0]
    tile_data_offset = header_size_field if 0 < header_size_field < len(rahc) else 0x18
    if 0 < tile_data_size_field <= len(rahc) - tile_data_offset:
        tile_data_size = tile_data_size_field
    else:
        tile_data_size = len(rahc) - tile_data_offset

    bytes_per_tile = 32 if bpp == 4 else 64
    tile_count = tile_data_size // bytes_per_tile

    # 保留原始 tile data
    merged = bytearray(rahc[tile_data_offset:tile_data_offset + tile_data_size])

    # 写入新 tile 数据
    for tile_idx, tile_pixels in zip(tile_indices, new_tiles):
        if tile_idx >= tile_count:
            continue
        offset = tile_idx * bytes_per_tile
        if bpp == 4:
            for i in range(0, 64, 2):
                merged[offset + i // 2] = (tile_pixels[i] & 0x0F) | ((tile_pixels[i + 1] & 0x0F) << 4)
        else:
            for i in range(64):
                merged[offset + i] = tile_pixels[i] & 0xFF

    rahc[tile_data_offset:tile_data_offset + tile_data_size] = merged

    # 重建 NCGR 容器
    result = bytearray(ncgr_data[:0x10])
    header_size = struct.unpack_from('<H', ncgr_data, 0x0C)[0]
    section_count = struct.unpack_from('<H', ncgr_data, 0x0E)[0]
    offset = header_size
    for _ in range(section_count):
        if offset + 8 > len(ncgr_data):
            break
        magic = ncgr_data[offset:offset + 4].decode('ascii', errors='replace')
        sec_size = struct.unpack_from('<I', ncgr_data, offset + 4)[0]
        if magic in ('RAHC', 'CHAR'):
            result.extend(ncgr_data[offset:offset + 8])
            result.extend(rahc)
        else:
            result.extend(ncgr_data[offset:offset + sec_size])
        offset += sec_size

    return bytes(result)


def import_obj_quartet(
    obj_dir: Path,
    stem: str,
    ncgr_path: Path,
    nclr_path: Path,
    nanr_path: Path,
    ncer_path: Path,
    out_ncgr: Path,
    out_nclr: Path,
    out_nanr: Path,
    out_ncer: Path,
) -> dict[str, Any]:
    """导入一套 OBJ 的编辑结果（从单张 sheet.png 切回 cell 再写回 NCGR）。

    用户只需 PS 编辑 {stem}_sheet.png，程序按 manifest 的 sheet_rect 自动切回各 cell。

    Returns:
        统计信息 dict
    """
    sheet_path = obj_dir / f'{stem}_sheet.png'
    manifest_path = obj_dir / f'{stem}_manifest.json'

    # 读 manifest
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    stem_val = manifest['stem']
    bpp = manifest['bpp']
    mapping_mode = manifest['mapping_mode']
    cells_info = manifest['cells']

    # 读原始 NCGR/NCLR
    ncgr_data = ncgr_path.read_bytes()
    nclr_data = nclr_path.read_bytes()
    if len(nclr_data) == 0:
        # 空 NCLR 用灰度调色板兜底
        palette = [(i, i, i) for i in range(256)]
    else:
        palette = parse_nclr(nclr_data)

    # 读 sheet.png（一张图包含所有 cell，按 sheet_rect 切回）
    sheet_img = Image.open(sheet_path).convert('RGB')

    tiles_written = 0

    for cell_info in cells_info:
        width = cell_info['width']
        height = cell_info['height']
        min_x = cell_info.get('min_x', 0)
        min_y = cell_info.get('min_y', 0)
        sheet_rect = cell_info['sheet_rect']
        sx, sy, sw, sh = sheet_rect

        # 从 sheet 按 sheet_rect crop 出 cell 图像
        cell_img = sheet_img.crop((sx, sy, sx + sw, sy + sh))
        if cell_img.size != (width, height):
            # 尺寸不匹配，跳过
            continue

        # 从后往前处理 OAM（与导出渲染顺序一致）
        # 导出时 reversed(cell.oam) → OAM[N-1] 先画，OAM[0] 最后画
        # 导入时 OAM[0] 先写 tile，OAM[N-1] 最后写（覆盖）
        # 但为了与导出一致，应该从后往前处理（OAM[N-1] 先提取，OAM[0] 最后提取）
        # 后面处理的覆盖前面的 tile 数据
        for oam_info in reversed(cell_info['oam']):
            if oam_info.get('disabled', False):
                continue

            oam_x = oam_info['x']
            oam_y = oam_info['y']
            oam_w = oam_info['width']
            oam_h = oam_info['height']
            flip_x = oam_info['flip_x']
            flip_y = oam_info['flip_y']
            oam_palette = oam_info['palette']
            tile_idxs = oam_info['tiles']

            if not tile_idxs:
                continue

            tiles_per_row = oam_w // 8
            tiles_per_col = oam_h // 8

            # 从 cell 图像提取 OAM 范围内的像素
            oam_pixels = extract_oam_pixels(
                cell_img, oam_x, oam_y, oam_w, oam_h, min_x, min_y,
            )

            # 反转 flip（导出时应用了 flip，导入时要还原 tile 原始排列）
            oam_pixels = reverse_flip(oam_pixels, flip_x, flip_y)

            # RGB → tile 像素
            new_tiles = oam_pixels_to_tiles(
                oam_pixels, palette, bpp, oam_palette,
                tiles_per_row, tiles_per_col,
            )

            # 写入 NCGR
            ncgr_data = write_tiles_to_ncgr(
                ncgr_data, tile_idxs, new_tiles, bpp,
            )
            tiles_written += len(new_tiles)

    # 写出文件
    out_ncgr.parent.mkdir(parents=True, exist_ok=True)
    out_ncgr.write_bytes(ncgr_data)
    shutil.copy2(nclr_path, out_nclr)
    shutil.copy2(nanr_path, out_nanr)
    shutil.copy2(ncer_path, out_ncer)

    return {
        'stem': stem_val,
        'tiles_written': tiles_written,
        'n_cells': len(cells_info),
    }


def main() -> None:
    img_dir = EXTRACT_DIR.parent / '1_Extracted_Images' / 'OBJ'
    orig_dir = EXTRACT_DIR / 'OBJ'
    out_dir = PATCHED_DIR / 'OBJ_CHS_PATCHED'

    if not img_dir.exists():
        print('OBJ 图像目录不存在，跳过')
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    quartets = find_obj_quartets(orig_dir)

    print('=' * 50)
    print(' OBJ UI 文字汉化导入工具')
    print('=' * 50)
    print(f'处理 {len(quartets)} 套 OBJ...')

    success = 0
    for ncgr, nclr, nanr, ncer, base, stem in quartets:
        manifest_path = img_dir / f'{stem}_manifest.json'
        if not manifest_path.exists():
            continue
        print(f'📥 正在回写: {stem} ...')
        try:
            stats = import_obj_quartet(
                img_dir, stem, ncgr, nclr, nanr, ncer,
                out_dir / ncgr.name, out_dir / nclr.name,
                out_dir / nanr.name, out_dir / ncer.name,
            )
            success += 1
            print(f'   ✅ cells={stats["n_cells"]}, tiles_written={stats["tiles_written"]}')
        except Exception as e:
            print(f'   ❌ 失败: {e}')
            import traceback
            traceback.print_exc()

    print(f'\n🎉 成功回写 {success} 套 OBJ UI')


if __name__ == '__main__':
    main()
