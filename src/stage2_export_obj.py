# src/stage2_export_obj.py
"""OBJ UI 文字汉化导出工具（基于 NCER cell 渲染）。

将 OBJ 目录下每套 4 件套的 NCGR + NCLR + NCER 组合渲染为可编辑的 RGB PNG：

    1_Extracted_Images/OBJ/
        ├── {stem}_sheet.png       # 所有 cell shelf packing 拼图（用户 PS 编辑此文件）
        └── {stem}_manifest.json   # cell 几何 + OAM + tile 引用 + flip

    其中 {stem} 为 NCGR 文件名前缀（如 "0012_MENUITEM"），所有套平铺在同一目录，
    不再细分子文件夹。

设计要点：
    - 用 NCER 的 cell/OAM 信息正确渲染（char_name → tile_index 按 mapping_mode 转换）
    - 每套只输出一张 sheet.png + 一个 manifest.json（用户只编辑 sheet.png）
    - RGB 模式（非索引模式），方便 PS 编辑
    - index 0 = 绿幕 (0, 255, 0)（透明像素）
    - 其他 index → NCLR 调色板 RGB
    - 4bpp: tile pixel + palette * 16 = NCLR 调色板索引
    - 8bpp: tile pixel = NCLR 调色板索引
    - flip 处理：导出时应用 flip（PNG 显示实际 UI 效果），导入时反转 flip
    - NANR 完全只读不写

参考实现：
    - NitroPaint ncer.c CellRender / CellRenderOBJ
    - NCGR_CHNAME 宏：BYTE_BOUNDARY * char_name / (bpp_bits * 8)
    - GBATEK: https://problemkaputt.de/gbatek.htm
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR
from src.stage2_export_bg import parse_ncgr, parse_nclr
from src.utils.ncer_format import (
    Cell,
    OamEntry,
    compute_shared_tiles,
    find_obj_quartets,
    parse_ncer,
)


# 绿幕颜色（index 0 = 透明）
GREEN_SCREEN = (0, 255, 0)


def render_oam_to_rgb(
    oam: OamEntry,
    tiles: list[list[int]],
    ncgr_tile_count: int,
    mapping_mode: int,
    palette: list[tuple[int, int, int]],
    bpp: int,
) -> list[list[tuple[int, int, int]]]:
    """渲染单个 OAM 为 RGB 像素矩阵（未应用 flip，未应用绿幕）。

    返回 height × width 的 RGB 像素矩阵。
    透明像素（tile pixel = 0）返回 (0, 255, 0) 绿幕。
    """
    w = oam.width
    h = oam.height
    # 初始化为绿幕
    pixels: list[list[tuple[int, int, int]]] = [
        [GREEN_SCREEN] * w for _ in range(h)
    ]

    tile_idxs = oam.tile_indices(ncgr_tile_count, mapping_mode)
    tiles_per_row = oam.tiles_per_row

    for row in range(oam.tiles_per_col):
        for col in range(tiles_per_row):
            idx = row * tiles_per_row + col
            if idx >= len(tile_idxs):
                continue
            tile_idx = tile_idxs[idx]
            if tile_idx >= len(tiles):
                continue
            tile = tiles[tile_idx]

            for ty in range(8):
                for tx in range(8):
                    pixel_val = tile[ty * 8 + tx]
                    px = col * 8 + tx
                    py = row * 8 + ty

                    if pixel_val == 0:
                        # index 0 = 透明 = 绿幕
                        pixels[py][px] = GREEN_SCREEN
                    else:
                        # 4bpp: pixel_val + palette * 16 = NCLR 索引
                        # 8bpp: pixel_val = NCLR 索引
                        if bpp == 4:
                            pal_idx = pixel_val + oam.palette * 16
                        else:
                            pal_idx = pixel_val
                        if pal_idx < len(palette):
                            pixels[py][px] = palette[pal_idx]
                        else:
                            pixels[py][px] = (0, 0, 0)

    return pixels


def apply_flip(
    pixels: list[list[tuple[int, int, int]]],
    flip_x: bool,
    flip_y: bool,
) -> list[list[tuple[int, int, int]]]:
    """对 OAM 像素矩阵应用 flip（整个 OAM 范围翻转）。"""
    if not flip_x and not flip_y:
        return pixels

    h = len(pixels)
    w = len(pixels[0]) if h > 0 else 0
    result = [row[:] for row in pixels]

    if flip_x:
        for row in result:
            row.reverse()
    if flip_y:
        result.reverse()

    return result


def render_cell_to_rgb(
    cell: Cell,
    tiles: list[list[int]],
    ncgr_tile_count: int,
    mapping_mode: int,
    palette: list[tuple[int, int, int]],
    bpp: int,
) -> tuple[Image.Image, int, int]:
    """渲染整个 cell 为 RGB PNG。

    OAM 从后往前渲染（索引小的覆盖索引大的，与 NitroPaint 一致）。
    返回 (PIL Image, width, height)。
    """
    bbox = cell.compute_bbox()
    min_x, min_y, max_x, max_y = bbox
    width = max_x - min_x
    height = max_y - min_y

    if width <= 0 or height <= 0:
        # 空 cell，返回 1x1 绿幕
        img = Image.new('RGB', (1, 1), GREEN_SCREEN)
        return img, 1, 1

    # 创建 RGB 画布，初始填充绿幕
    img = Image.new('RGB', (width, height), GREEN_SCREEN)
    canvas = img.load()
    assert canvas is not None

    # OAM 从后往前渲染（索引小的最后渲染，覆盖上层）
    for oam in reversed(cell.oam):
        if oam.disabled:
            continue

        # 渲染 OAM 到临时像素矩阵
        oam_pixels = render_oam_to_rgb(
            oam, tiles, ncgr_tile_count, mapping_mode, palette, bpp,
        )
        # 应用 flip
        oam_pixels = apply_flip(oam_pixels, oam.flip_x, oam.flip_y)

        # 绘制到画布（覆盖绿幕和非绿幕像素）
        for py in range(oam.height):
            for px in range(oam.width):
                canvas_x = oam.x + px - min_x
                canvas_y = oam.y + py - min_y
                if 0 <= canvas_x < width and 0 <= canvas_y < height:
                    pixel = oam_pixels[py][px]
                    # 只覆盖非绿幕像素（透明像素不覆盖下层）
                    if pixel != GREEN_SCREEN:
                        canvas[canvas_x, canvas_y] = pixel

    return img, width, height


def shelf_pack_cells(
    cell_sizes: list[tuple[int, int, int]],  # (cell_idx, width, height)
) -> tuple[list[tuple[int, int, int, int, int]], int, int]:
    """Shelf packing 算法排列 cell。

    Returns:
        (positions, sheet_w, sheet_h)
        positions: [(cell_idx, x, y, w, h), ...]
    """
    if not cell_sizes:
        return [], 64, 64

    # 按高度降序排列
    sorted_cells = sorted(cell_sizes, key=lambda c: -c[2])

    # 标准尺寸
    standard_sizes = [64, 128, 256, 512, 1024]
    max_w = max(c[1] for c in cell_sizes)
    for ss in standard_sizes:
        if ss >= max_w:
            sheet_w = ss
            break
    else:
        sheet_w = standard_sizes[-1]

    positions: list[tuple[int, int, int, int, int]] = []
    x = 0
    y = 0
    shelf_height = 0

    for cell_idx, w, h in sorted_cells:
        if x + w > sheet_w:
            # 换行
            x = 0
            y += shelf_height + 1  # 1px 间隔
            shelf_height = 0

        positions.append((cell_idx, x, y, w, h))
        x += w + 1  # 1px 间隔
        if h > shelf_height:
            shelf_height = h

    sheet_h = y + shelf_height
    # 标准化高度
    for ss in standard_sizes:
        if ss >= sheet_h:
            sheet_h = ss
            break
    else:
        sheet_h = max(sheet_h, standard_sizes[-1])

    return positions, sheet_w, sheet_h


def build_manifest(
    stem: str,
    ncgr_tile_count: int,
    bpp: int,
    mapping_mode: int,
    palette: list[tuple[int, int, int]],
    cells: list[Cell],
    cell_geometry: list[dict[str, Any]],
    shared_tiles: dict[int, list[int]],
) -> dict[str, Any]:
    """构建 manifest.json 内容。"""
    manifest: dict[str, Any] = {
        'stem': stem,
        'bpp': bpp,
        'mapping_mode': mapping_mode,
        'n_tiles': ncgr_tile_count,
        'n_cells': len(cells),
        'green_screen': list(GREEN_SCREEN),
        'cells': [],
        'shared_tiles': {str(k): v for k, v in shared_tiles.items()},
    }

    for i, cell in enumerate(cells):
        geom = cell_geometry[i]
        # 计算 bbox 原点（OAM 坐标相对于 bbox min）
        bbox = cell.compute_bbox()
        cell_info: dict[str, Any] = {
            'cell_idx': cell.cell_idx,
            'width': geom['width'],
            'height': geom['height'],
            'min_x': bbox[0],
            'min_y': bbox[1],
            'sheet_rect': list(geom['sheet_rect']),
            'oam': [],
        }
        for oam in cell.oam:
            tile_idxs = oam.tile_indices(ncgr_tile_count, mapping_mode)
            oam_info: dict[str, Any] = {
                'x': oam.x,
                'y': oam.y,
                'width': oam.width,
                'height': oam.height,
                'flip_x': oam.flip_x,
                'flip_y': oam.flip_y,
                'color_mode': oam.color_mode,
                'char_name': oam.char_name,
                'palette': oam.palette,
                'disabled': oam.disabled,
                'affine': oam.affine,
                'tiles': tile_idxs,
            }
            cell_info['oam'].append(oam_info)
        manifest['cells'].append(cell_info)

    return manifest


def export_obj_quartet(
    ncgr_path: Path,
    nclr_path: Path,
    ncer_path: Path,
    out_dir: Path,
    stem: str,
) -> dict[str, Any] | None:
    """导出一套 OBJ 4 件套为 sheet.png + manifest.json（平铺在 out_dir）。

    输出文件：
        {out_dir}/{stem}_sheet.png       - 所有 cell shelf packing 拼图（用户 PS 编辑此文件）
        {out_dir}/{stem}_manifest.json   - cell 几何 + OAM + tile 引用

    stem 为 NCGR 文件名前缀（如 "0012_MENUITEM"），用作输出文件编号。

    Returns:
        manifest dict（成功）或 None（失败）
    """
    try:
        ncgr_data = ncgr_path.read_bytes()
        nclr_data = nclr_path.read_bytes()
        ncer_data = ncer_path.read_bytes()

        tiles, bpp, _, _ = parse_ncgr(ncgr_data)
        # 处理空 NCLR（如 LISTITEM.NCLR 是 0 字节）
        if len(nclr_data) == 0:
            palette = [(i, i, i) for i in range(256)]
        else:
            palette = parse_nclr(nclr_data)
        ncer_info = parse_ncer(ncer_data)
        cells: list[Cell] = ncer_info['cells']
        mapping_mode = ncer_info['mapping_mode']
        ncgr_tile_count = len(tiles)

        affine_refs = [
            f"cell {cell.cell_idx} OAM {index}"
            for cell in cells
            for index, oam in enumerate(cell.oam)
            if oam.affine and not oam.disabled
        ]
        if affine_refs:
            raise NotImplementedError(
                "OBJ 含 affine OAM，当前导出器无法无损处理旋转/缩放："
                + ", ".join(affine_refs[:10])
            )

        out_dir.mkdir(parents=True, exist_ok=True)

        # 渲染每个 cell 为 RGB Image（不保存为单独 PNG，仅暂存用于拼 sheet）
        cell_geometry: list[dict[str, Any]] = []
        cell_images: list[tuple[int, Image.Image, int, int]] = []
        for cell in cells:
            img, w, h = render_cell_to_rgb(
                cell, tiles, ncgr_tile_count, mapping_mode, palette, bpp,
            )
            cell_images.append((cell.cell_idx, img, w, h))
            cell_geometry.append({
                'width': w,
                'height': h,
                'sheet_rect': (0, 0, w, h),  # 待 shelf packing 后更新
            })

        # Shelf packing 拼接 sheet（用 paste 高效复制）
        cell_sizes = [(idx, w, h) for idx, _, w, h in cell_images]
        positions, sheet_w, sheet_h = shelf_pack_cells(cell_sizes)

        sheet = Image.new('RGB', (sheet_w, sheet_h), GREEN_SCREEN)
        img_by_idx = {idx: (img, w, h) for idx, img, w, h in cell_images}
        for cell_idx, sx, sy, sw, sh in positions:
            img, w, h = img_by_idx[cell_idx]
            sheet.paste(img, (sx, sy))
            # 更新 cell_geometry 的 sheet_rect（按 cell_idx 匹配）
            for i, cell in enumerate(cells):
                if cell.cell_idx == cell_idx:
                    cell_geometry[i]['sheet_rect'] = (sx, sy, w, h)
                    break

        sheet.save(out_dir / f'{stem}_sheet.png')

        # 检测共享 tile
        shared_tiles = compute_shared_tiles(cells, ncgr_tile_count, mapping_mode)

        # 构建 manifest
        manifest = build_manifest(
            stem, ncgr_tile_count, bpp, mapping_mode, palette,
            cells, cell_geometry, shared_tiles,
        )
        with open(out_dir / f'{stem}_manifest.json', 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        return manifest
    except Exception as e:
        print(f'   ❌ {stem}: {e}')
        import traceback
        traceback.print_exc()
        return None


def main() -> None:
    input_dir = EXTRACT_DIR / 'OBJ'
    output_dir = EXTRACT_DIR.parent / '1_Extracted_Images' / 'OBJ'
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print('OBJ 目录不存在，跳过')
        return

    quartets = find_obj_quartets(input_dir)
    print(f'🎨 正在处理 OBJ 下的 {len(quartets)} 套 4 件套...')

    success = 0
    failures: list[str] = []
    for ncgr, nclr, _nanr, ncer, base, stem in quartets:
        manifest = export_obj_quartet(ncgr, nclr, ncer, output_dir, stem)
        if manifest is not None:
            success += 1
            n_shared = len(manifest['shared_tiles'])
            print(f'   ✅ {stem}: {manifest["n_cells"]} cells, '
                  f'bpp={manifest["bpp"]}, mapping={manifest["mapping_mode"]}, '
                  f'tiles={manifest["n_tiles"]}, shared={n_shared}')
        else:
            failures.append(stem)

    print(f'\n🎉 成功导出 {success}/{len(quartets)} 套 OBJ UI')
    if failures:
        raise RuntimeError(
            f"OBJ 导出失败 {len(failures)} 套：" + ", ".join(failures)
        )


if __name__ == '__main__':
    main()
