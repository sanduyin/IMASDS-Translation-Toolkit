# src/stage2_export_images.py
"""
GLD → PNG sprite 级别导出工具。

【工作流程】
  ① 解析 .GLD 文件（header + pixel_data + palette_data + footer entries）
  ② 输出模式按文件夹区分：
     - AGL（PSD 模式，类似 OBJ）：
         {gld_stem}.psd             # 可编辑，多图层，每 sprite 一个图层（名 sprite_{index}）
         {gld_stem}_preview.jpg     # 带线框预览（单 256x192 满屏图跳过）
       用户工作流：PS 编辑 .psd → 从 PS 导出 {stem}.png → 程序按 PSD 图层位置
       从 PNG crop 出 sprite 注入（可自由移动图层位置，只要图层名不变）。
     - TEX/TBL/BG（sprite 模式）：
         {gld_stem}_{index}.png     # 每个 sprite 一个 RGBA PNG
         {gld_stem}_sheet.png       # 概览图（带线框，TEX 跳过）
  ③ 输出目录：game_data/1_Extracted_Images/<folder>/

【设计说明】
  · 采用 RGBA PNG（而非索引色 PNG），原因：
      - 完整保留 format 1/6 的多级 alpha（索引色 PNG 的 tRNS 只支持 1 级透明/色）
      - 用户可用任意工具（Photoshop/GIMP）自由编辑，不受调色板约束
      - 注入时由 gld_format.inject_sprite_rgba() 做调色板匹配重建索引
  · Deleted sprite（bit15=1）跳过不导出
  · AGL sheet 模式按 sprite 编号 0~N 顺序排列（从左到右、从上到下）

参考实现：dearlystars_tool (Rust) gld.rs
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR
from src.utils.gld_format import parse_gld, SPRITE_DELETED, GldFile

# 概览图边框颜色（品红，高对比反差色）
BORDER_COLOR = (255, 0, 255, 255)
# 概览图背景（棋盘格浅灰，便于观察透明区域）
SHEET_BG = (200, 200, 200, 255)

# AGL 可编辑 sheet 的最大宽度
AGL_SHEET_MAX_WIDTH = 256


def export_sprite_png(gld: GldFile, index: int, output_path: Path) -> bool:
    """
    导出单个 sprite 为 RGBA PNG。

    Returns:
        True 表示成功导出；False 表示 sprite 已删除被跳过。
    """
    entry = gld.footer_entries[index]
    if entry.is_deleted:
        return False

    width, height, rgba = gld.extract_sprite_rgba(index)
    img = Image.frombytes('RGBA', (width, height), rgba)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, 'PNG')
    return True


# ----------------------------------------------------------------------
# AGL sheet 模式：可编辑 sheet + 预览 + manifest
# ----------------------------------------------------------------------

def _shelf_pack_by_index(
    sprites: list[tuple[int, int, int]],
    max_width: int = AGL_SHEET_MAX_WIDTH,
) -> tuple[list[tuple[int, int, int, int, int]], int, int]:
    """按 sprite 编号顺序 shelf packing（从左到右、从上到下，无空隙）。

    sprites 必须已按 index 升序排列。
    Returns:
        (positions, sheet_w, sheet_h)
        positions = [(index, x, y, w, h), ...]
    """
    positions: list[tuple[int, int, int, int, int]] = []
    x = 0
    y = 0
    row_h = 0
    for idx, w, h in sprites:
        # 当前行放不下则换行
        if x + w > max_width and x > 0:
            x = 0
            y += row_h
            row_h = 0
        positions.append((idx, x, y, w, h))
        x += w
        if h > row_h:
            row_h = h
    sheet_h = y + row_h
    return positions, max_width, max(sheet_h, 1)


def _is_fullscreen_single_sprite(gld: GldFile) -> bool:
    """判断是否为单个 256x192 满屏图（此类 GLD 不生成 _preview.jpg）。"""
    active = [e for e in gld.footer_entries if not e.is_deleted]
    return (len(active) == 1
            and active[0].crop_width == 256
            and active[0].crop_height == 192)


def export_editable_psd(
    gld: GldFile,
    output_path: Path,
    max_width: int = AGL_SHEET_MAX_WIDTH,
) -> int | None:
    """生成 AGL 可编辑 PSD：每个 sprite 一个图层，透明背景，按编号顺序排列。

    图层名 = sprite_{index}，用户在 PS 中可自由编辑/移动图层，导回时按图层名识别。
    图层位置 = sprite 在 sheet 中的 shelf packing 位置（仅作初始布局，用户可移动）。

    Returns:
        sprite 数量，无活跃 sprite 时返回 None。
    """
    from psd_tools import PSDImage

    # 收集所有未删除 sprite，按 index 升序
    sprites: list[tuple[int, int, int]] = []
    for i, entry in enumerate(gld.footer_entries):
        if entry.is_deleted:
            continue
        sprites.append((i, entry.crop_width, entry.crop_height))

    if not sprites:
        return None

    positions, sheet_w, sheet_h = _shelf_pack_by_index(sprites, max_width)

    # 创建 PSD（透明背景）
    psd = PSDImage.new(mode='RGBA', size=(sheet_w, sheet_h), color=(0, 0, 0, 0))

    # 每个 sprite 一个图层（按 index 升序，第一个在底层）
    for idx, x, y, w, h in positions:
        sw, sh, rgba = gld.extract_sprite_rgba(idx)
        pil_img = Image.frombytes('RGBA', (sw, sh), rgba)
        layer = psd.create_pixel_layer(pil_img, name=f'sprite_{idx}', top=y, left=x)
        psd.append(layer)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    psd.save(output_path)
    return len(positions)


def export_preview_jpg(gld: GldFile, output_path: Path) -> None:
    """生成预览图：和 sheet 一样的尺寸/布局，每个 sprite 朝内描 1px 品红边框。

    与 export_editable_sheet_png 共用 _shelf_pack_by_index 布局（无空隙、256px 宽、
    按编号顺序），但用浅灰背景便于观察透明区域，并为每个 sprite 朝内画边框
    （边框覆盖 sprite 最外圈像素，不会和相邻 sprite 的边框重叠）。

    保存为 JPEG（不支持透明，先转 RGB 合成到浅灰背景上）。
    """
    # 收集所有未删除 sprite，按 index 升序
    sprites: list[tuple[int, int, int]] = []
    for i, entry in enumerate(gld.footer_entries):
        if entry.is_deleted:
            continue
        sprites.append((i, entry.crop_width, entry.crop_height))

    if not sprites:
        return

    positions, sheet_w, sheet_h = _shelf_pack_by_index(sprites)

    canvas = Image.new('RGBA', (sheet_w, sheet_h), SHEET_BG)
    for idx, x, y, w, h in positions:
        sw, sh, rgba = gld.extract_sprite_rgba(idx)
        sprite_img = Image.frombytes('RGBA', (sw, sh), rgba)
        canvas.paste(sprite_img, (x, y), sprite_img)

    # 朝内描边：边框画在 sprite 区域的最外圈像素上
    draw = ImageDraw.Draw(canvas)
    for idx, x, y, w, h in positions:
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=BORDER_COLOR, width=1)

    # JPEG 不支持透明，转 RGB（保留浅灰背景）
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert('RGB').save(output_path, 'JPEG', quality=95)


# ----------------------------------------------------------------------
# 单 GLD 导出入口
# ----------------------------------------------------------------------

def export_gld_to_pngs(gld_path: Path, output_dir: Path,
                       folder_name: str = '') -> int:
    """将一个 GLD 文件导出为 PNG。

    Args:
        folder_name: 文件夹名（大写），决定导出模式：
            'AGL' → sheet 模式（_sheet.png + _preview.png + _manifest.json）
            其他 → sprite 模式（{stem}_{i}.png + 可选 _sheet.png）

    Returns:
        导出的 sprite 数量
    """
    try:
        gld = parse_gld(gld_path)
    except ValueError as e:
        print(f"   ⚠️  跳过 {gld_path.name}: {e}")
        return 0

    stem = gld.file_stem
    output_dir.mkdir(parents=True, exist_ok=True)

    if folder_name.upper() == 'AGL':
        # AGL PSD 模式：可编辑 PSD（多图层）+ 预览 jpg（满屏单图跳过预览）
        psd_path = output_dir / f'{stem}.psd'
        n = export_editable_psd(gld, psd_path)
        if n is not None:
            # 单个 256x192 满屏图不生成 _preview.jpg
            if not _is_fullscreen_single_sprite(gld):
                preview_path = output_dir / f'{stem}_preview.jpg'
                export_preview_jpg(gld, preview_path)
        return n if n else 0
    else:
        # sprite 模式：每个 sprite 一个 PNG
        exported = 0
        for i in range(len(gld.footer_entries)):
            out_png = output_dir / f'{stem}_{i}.png'
            if export_sprite_png(gld, i, out_png):
                exported += 1
        # TEX 不生成 sheet，其他生成
        if folder_name.upper() != 'TEX':
            sheet_path = output_dir / f'{stem}_sheet.jpg'
            export_preview_jpg(gld, sheet_path)
        return exported


# 需处理的图像文件夹
IMAGE_FOLDERS = ["TEX", "TBL", "AGL", "BG"]


def batch_process_images(folders: list[str] | None = None) -> None:
    """
    批量处理所有 GLD 文件，导出 sprite PNG / sheet。

    目录约定：
      输入 GLD : game_data/1_Extracted/<folder>/
      输出 PNG : game_data/1_Extracted_Images/<folder>/
    """
    if folders is None:
        folders = IMAGE_FOLDERS

    for folder_name in folders:
        input_dir = EXTRACT_DIR / folder_name
        if not input_dir.exists():
            print(f"⏭️  跳过未找到的图像目录: {input_dir}")
            continue

        output_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)

        files = sorted(f for f in os.listdir(input_dir) if f.upper().endswith('.GLD'))
        if not files:
            print(f"⏭️  {folder_name} 下未找到 GLD 文件，跳过。")
            continue

        mode = "PSD 模式（多图层 .psd + _preview.jpg）" if folder_name.upper() == 'AGL' else "sprite 模式"
        print(f"🎨 正在处理 {folder_name} 下的 {len(files)} 个 GLD 文件（{mode}）...")

        total_sprites = 0
        for fname in files:
            gld_path = input_dir / fname
            count = export_gld_to_pngs(gld_path, output_dir, folder_name)
            total_sprites += count

        print(f"   ✅ {folder_name}: {len(files)} 个 GLD → {total_sprites} 个 sprite")
        print(f"      输出至: {output_dir}")


def main() -> None:
    batch_process_images()


if __name__ == "__main__":
    main()
