# src/stage2_export_images.py
"""
GLD → PNG sprite 级别导出工具。

【工作流程】
  ① 解析 .GLD 文件（header + pixel_data + palette_data + footer entries）
  ② 每个 sprite 导出为独立 RGBA PNG：
       {gld_stem}_{index}.png
  ③ 每个 GLD 额外生成一张概览图（sheet），在 (crop_x, crop_y) 处合成所有 sprite，
     并用 1px 品红边框标记每个 sprite 的切图范围：
       {gld_stem}_sheet.png
  ④ 输出目录：game_data/1_Extracted_Images/<folder>/

【设计说明】
  · 采用 RGBA PNG（而非索引色 PNG），原因：
      - 完整保留 format 1/6 的多级 alpha（索引色 PNG 的 tRNS 只支持 1 级透明/色）
      - 用户可用任意工具（Photoshop/GIMP）自由编辑，不受调色板约束
      - 注入时由 gld_format.inject_sprite_rgba() 做调色板匹配重建索引
  · 完全替代旧 BMP 8bpp 导出（不再依赖固定宽度/穷举宽度启发式）
  · Deleted sprite（bit15=1）跳过不导出

参考实现：reference/dearlystars_tool/dearlystars/src/gld.rs
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


def export_overview_png(gld: GldFile, output_path: Path) -> None:
    """
    生成 GLD 的概览图（sheet）。

    GLD 文件没有官方的 sheet 排列信息（footer entries 只有 crop_x/crop_y
    纹理坐标，不是 sheet 坐标），直接用 (crop_x, crop_y) 合成会导致共享同一
    纹理区域的 sprite 互相覆盖。因此这里使用 shelf/row packing 算法，按使用
    面积在标准贴图尺寸（64/128/256/512/1024）中排列 sprite，确保不重叠。
    每个 sprite 之间留 1px 间隔，并用 1px 品红边框标记每个 sprite 的范围。
    """
    # 收集所有未删除 sprite 的 (索引, 宽, 高)
    sprites = []
    for i, entry in enumerate(gld.footer_entries):
        if entry.is_deleted:
            continue
        sprites.append((i, entry.crop_width, entry.crop_height))

    if not sprites:
        return

    # 按高度降序排列以提高 shelf packing 空间利用率
    sprites.sort(key=lambda s: s[2], reverse=True)

    # 标准贴图尺寸
    STANDARD_SIZES = [64, 128, 256, 512, 1024]
    SPACING = 1  # sprite 之间 1px 间隔（避免边框重叠）

    # 单个 sprite（含间隔）的最大宽度，用于判断是否需要升级 sheet 尺寸
    max_sprite_w = max(w for _, w, _ in sprites) + SPACING

    def shelf_pack(sheet_w: int) -> tuple[list[tuple[int, int, int, int, int]], int]:
        """
        用 shelf packing 算法在宽度为 sheet_w 的画布上排列 sprite。

        每行从左到右放置，放不下则换行。返回 (positions, total_height)：
            positions = [(index, x, y, w, h), ...]
            total_height = 排列后的总高度
        """
        positions: list[tuple[int, int, int, int, int]] = []
        cur_x = 0
        cur_y = 0
        row_height = 0
        for idx, w, h in sprites:
            cell_w = w + SPACING
            cell_h = h + SPACING
            # 当前行放不下则换行
            if cur_x + cell_w > sheet_w and cur_x > 0:
                cur_y += row_height
                cur_x = 0
                row_height = 0
            positions.append((idx, cur_x, cur_y, w, h))
            cur_x += cell_w
            if cell_h > row_height:
                row_height = cell_h
        total_h = cur_y + row_height
        return positions, total_h

    # 选择最小的能容纳所有 sprite 的标准尺寸
    chosen_w = 1024
    chosen_positions = []
    chosen_h = 0
    for s in STANDARD_SIZES:
        # 单个 sprite 宽度超过当前 sheet 宽度时，升级到更大的尺寸
        if max_sprite_w > s and s < 1024:
            continue
        positions, total_h = shelf_pack(s)
        # 1024 允许高度超过 1024，其余尺寸需在 s×s 内
        if total_h <= s or s == 1024:
            chosen_w = s
            chosen_positions = positions
            chosen_h = s if total_h <= s else total_h
            break

    canvas_w = max(1, chosen_w)
    canvas_h = max(1, chosen_h)
    canvas = Image.new('RGBA', (canvas_w, canvas_h), SHEET_BG)

    # 合成每个 sprite
    for idx, x, y, w, h in chosen_positions:
        sw, sh, rgba = gld.extract_sprite_rgba(idx)
        sprite_img = Image.frombytes('RGBA', (sw, sh), rgba)
        canvas.paste(sprite_img, (x, y), sprite_img)

    # 画 1px 品红边框标记每个 sprite 的范围
    draw = ImageDraw.Draw(canvas)
    for idx, x, y, w, h in chosen_positions:
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=BORDER_COLOR, width=1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, 'PNG')


def export_gld_to_pngs(gld_path: Path, output_dir: Path) -> int:
    """
    将一个 GLD 文件的所有 sprite 导出为 PNG，并生成概览图。

    Returns:
        导出的 sprite 数量（不含概览图）
    """
    try:
        gld = parse_gld(gld_path)
    except ValueError as e:
        print(f"   ⚠️  跳过 {gld_path.name}: {e}")
        return 0

    stem = gld.file_stem
    exported = 0
    for i in range(len(gld.footer_entries)):
        out_png = output_dir / f"{stem}_{i}.png"
        if export_sprite_png(gld, i, out_png):
            exported += 1

    # 概览图
    sheet_path = output_dir / f"{stem}_sheet.png"
    export_overview_png(gld, sheet_path)

    return exported


# 需处理的图像文件夹（不再需要 fixed/bruteforce 策略，统一 sprite 级导出）
IMAGE_FOLDERS = ["TEX", "TBL", "AGL", "BG"]


def batch_process_images(folders: list[str] | None = None) -> None:
    """
    批量处理所有 GLD 文件，导出 sprite PNG + 概览图。

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

        print(f"🎨 正在处理 {folder_name} 下的 {len(files)} 个 GLD 文件（sprite 级 PNG 导出）...")

        total_sprites = 0
        for fname in files:
            gld_path = input_dir / fname
            count = export_gld_to_pngs(gld_path, output_dir)
            total_sprites += count

        print(f"   ✅ {folder_name}: {len(files)} 个 GLD → {total_sprites} 个 sprite PNG + 概览图")
        print(f"      输出至: {output_dir}")


def main() -> None:
    batch_process_images()


if __name__ == "__main__":
    main()
