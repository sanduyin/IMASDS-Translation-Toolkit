# src/stage4_import_images.py
"""
PNG → GLD 图像回写工具（sprite 级别）。

【工作流程】
  ① 用 stage2_export_images.py 导出 GLD → PNG sprites
  ② 用 Photoshop / GIMP 等工具修改 PNG（必须保持画布尺寸不变）
  ③ 将修改好的 PNG 放回原导出目录：
       game_data/1_Extracted_Images/<文件夹名>/
     文件名必须保持导出时的格式：
       {gld_stem}_{index}.png    （单个 sprite）
       {gld_stem}_sheet.png      （概览图，回写时自动跳过）
  ④ 运行本工具，回写后的 GLD 输出到：
       game_data/2_Patched/<文件夹名>_IMG_PATCHED/
  ⑤ 在 config.py 的 TARGET_PACKS 中加入对应模块（如 "TEX"），
     stage5_build_rom.py 打包时会自动包含这些修改。

【注意事项】
  · PNG 画布尺寸（宽×高）必须与原 sprite 的 crop_width × crop_height 完全一致。
  · PNG 为 RGBA 模式；RGB PNG 会被自动转换（不透明像素 alpha=255）。
  · 调色板由 gld_format.inject_sprite_rgba() 自动匹配（曼哈顿距离 + 透明惩罚）。
  · 透明像素（alpha < 128）回写时会标记为调色板中的透明色。
  · 未修改的 sprite 保持原样（只回写有对应 PNG 的 sprite）。

参考实现：reference/dearlystars_tool/dearlystars/src/gld.rs (inject_png)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, PATCHED_DIR
from src.utils.gld_format import parse_gld, write_gld
from src.stage2_export_images import export_sprite_png


def import_png_to_gld(png_path: Path, original_gld_path: Path,
                      output_gld_path: Path) -> tuple[int, int]:
    """
    将单个修改后的 PNG sprite 写回 GLD 文件。

    流程：
      1. 解析原始 GLD（保留所有 footer entries 和未修改的 sprite）
      2. 读取 PNG，转换为 RGBA
      3. 校验 PNG 尺寸 == sprite 的 crop_width × crop_height
      4. 调用 inject_sprite_rgba() 注入（调色板匹配 + 像素打包）
      5. 写出新 GLD

    Args:
        png_path:           修改后的 PNG sprite 文件
        original_gld_path:  原始 GLD 文件
        output_gld_path:    输出的新 GLD 文件路径

    Returns:
        (width, height) of the injected sprite
    """
    # 从 PNG 文件名提取 sprite 索引
    # 命名规则：{gld_stem}_{index}.png
    stem = png_path.stem
    parts = stem.rsplit('_', 1)
    if len(parts) != 2 or not parts[1].isdigit():
        raise ValueError(f"文件名格式不符，无法识别 sprite 索引: {png_path.name}")

    index = int(parts[1])

    # 解析原始 GLD
    gld = parse_gld(original_gld_path)

    if index >= len(gld.footer_entries):
        raise ValueError(
            f"sprite 索引 {index} 超出范围（GLD 只有 {len(gld.footer_entries)} 个 entry）"
        )

    entry = gld.footer_entries[index]
    if entry.is_deleted:
        raise ValueError(f"sprite [{index}] 已标记为 deleted，无法注入")

    # 读取 PNG
    with Image.open(png_path) as img:
        img = img.convert('RGBA')
        width, height = img.size
        rgba_data = img.tobytes()

    # 校验尺寸
    if width != entry.crop_width or height != entry.crop_height:
        raise ValueError(
            f"PNG 尺寸不匹配：期望 {entry.crop_width}x{entry.crop_height}，"
            f"实际 {width}x{height}"
        )

    # 注入
    gld.inject_sprite_rgba(index, rgba_data, width, height)

    # 写出
    output_gld_path.parent.mkdir(parents=True, exist_ok=True)
    write_gld(gld, output_gld_path)

    return (width, height)


def get_png_index_map(png_dir: Path) -> dict[str, list[int]]:
    """
    扫描 PNG 目录，按 GLD 文件名分组，返回 {gld_stem: [index, ...]}。

    跳过：
      - _sheet.png 概览图
      - 文件名无法解析出整数索引的文件
    """
    result: dict[str, list[int]] = {}

    for fname in os.listdir(png_dir):
        if not fname.lower().endswith('.png'):
            continue

        stem = Path(fname).stem
        # 跳过概览图
        if stem.endswith('_sheet'):
            continue

        parts = stem.rsplit('_', 1)
        if len(parts) != 2:
            continue
        gld_stem, idx_str = parts
        if not idx_str.isdigit():
            continue

        idx = int(idx_str)
        result.setdefault(gld_stem, []).append(idx)

    # 每组的索引排序
    for k in result:
        result[k].sort()

    return result


# 需处理的图像文件夹（与 stage2_export_images.py 一致）
IMPORT_FOLDERS = ["TEX", "TBL", "AGL", "BG"]


def batch_import_images(import_folders: list[str] | None = None,
                        generate_preview: bool = False) -> None:
    """
    批量扫描导出图像目录，将修改过的 PNG sprite 写回对应的 GLD。

    目录约定（与其他 stage 保持一致）：
      PNG 来源 : game_data/1_Extracted_Images/<folder>/
      原始 GLD : game_data/1_Extracted/<folder>/
      输出 GLD : game_data/2_Patched/<folder>_IMG_PATCHED/
      预览 PNG : game_data/1_Extracted_Images/<folder>_PREVIEW/  (当 generate_preview=True)

    Args:
        import_folders:    要处理的文件夹列表（默认 IMPORT_FOLDERS）
        generate_preview:  若为 True，注入后重新导出 sprite PNG 到 <folder>_PREVIEW/，
                           用于人工确认调色板匹配效果（参考 faraplay -p 参数）
    """
    if import_folders is None:
        import_folders = IMPORT_FOLDERS

    total_ok = 0
    total_skip = 0
    total_error = 0
    total_preview = 0

    for folder_name in import_folders:
        png_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / folder_name
        original_dir = EXTRACT_DIR / folder_name
        output_dir = PATCHED_DIR / f"{folder_name}_IMG_PATCHED"
        preview_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / f"{folder_name}_PREVIEW"

        if not png_dir.exists():
            print(f"⏭️  跳过：PNG 目录不存在: {png_dir}")
            continue
        if not original_dir.exists():
            print(f"⏭️  跳过：原始 GLD 目录不存在: {original_dir}")
            continue

        # 扫描 PNG 文件，按 GLD stem 分组
        index_map = get_png_index_map(png_dir)
        if not index_map:
            print(f"⏭️  {folder_name}: 没有找到可回写的 PNG sprite，跳过。")
            continue

        if generate_preview:
            preview_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n📥 正在处理 {folder_name}，共 {len(index_map)} 个 GLD 需要回写..."
              + ("（含预览）" if generate_preview else ""))

        for gld_stem, indices in index_map.items():
            # 查找原始 GLD 文件
            gld_path = None
            for ext in ['.GLD', '.gld']:
                candidate = original_dir / (gld_stem + ext)
                if candidate.exists():
                    gld_path = candidate
                    break

            if gld_path is None:
                print(f"   ⚠️  跳过（找不到对应的原始 GLD）: {gld_stem}.GLD")
                total_skip += len(indices)
                continue

            # 解析原始 GLD 一次，注入所有 sprite，然后写出
            try:
                gld = parse_gld(gld_path)
            except ValueError as e:
                print(f"   ❌ 解析失败 [{gld_stem}.GLD]: {e}")
                total_error += len(indices)
                continue

            modified = False
            injected_indices = []  # 记录已注入的索引，用于预览
            for index in indices:
                if index >= len(gld.footer_entries):
                    print(f"   ⚠️  跳过 [{gld_stem}_{index}]: 索引超出范围")
                    total_skip += 1
                    continue

                entry = gld.footer_entries[index]
                if entry.is_deleted:
                    print(f"   ⚠️  跳过 [{gld_stem}_{index}]: sprite 已 deleted")
                    total_skip += 1
                    continue

                png_path = png_dir / f"{gld_stem}_{index}.png"
                try:
                    with Image.open(png_path) as img:
                        img = img.convert('RGBA')
                        width, height = img.size
                        rgba_data = img.tobytes()

                    if width != entry.crop_width or height != entry.crop_height:
                        print(
                            f"   ⚠️  跳过 [{gld_stem}_{index}]: "
                            f"尺寸不匹配（期望 {entry.crop_width}x{entry.crop_height}，"
                            f"实际 {width}x{height}）"
                        )
                        total_skip += 1
                        continue

                    gld.inject_sprite_rgba(index, rgba_data, width, height)
                    modified = True
                    injected_indices.append(index)
                    total_ok += 1
                except Exception as e:
                    print(f"   ❌ 错误 [{gld_stem}_{index}]: {e}")
                    total_error += 1

            if modified:
                output_gld_path = output_dir / gld_path.name
                output_gld_path.parent.mkdir(parents=True, exist_ok=True)
                write_gld(gld, output_gld_path)
                print(f"   ✅ 已写回: {gld_path.name}")

                # 生成预览：从注入后的 GLD 重新导出 sprite PNG
                if generate_preview:
                    for index in injected_indices:
                        preview_png = preview_dir / f"{gld_stem}_{index}.png"
                        if export_sprite_png(gld, index, preview_png):
                            total_preview += 1

    print(f"\n🎉 图像回写完成！成功 {total_ok}，跳过 {total_skip}，错误 {total_error}")
    if generate_preview and total_preview > 0:
        print(f"   预览图: {total_preview} 个（对比原 PNG 与预览 PNG 可确认调色板匹配效果）")
        print(f"   预览目录: game_data/1_Extracted_Images/<folder>_PREVIEW/")
    print(f"   输出目录: {PATCHED_DIR}/<文件夹名>_IMG_PATCHED/")
    print(f"   请确认 config.py 的 TARGET_PACKS 已包含对应模块（如 \"TEX\"），")
    print(f"   否则 stage5_build_rom.py 打包时不会包含这些修改。")


def main(preview: bool | None = None) -> None:
    """
    PNG → GLD 图像回写入口。

    Args:
        preview: None=命令行解析 --preview; True/False=直接指定
    """
    if preview is None:
        import argparse
        parser = argparse.ArgumentParser(description="PNG → GLD 图像回写")
        parser.add_argument('--preview', action='store_true',
                            help='注入后重新导出预览 PNG 到 <folder>_PREVIEW/，用于确认调色板匹配效果')
        args = parser.parse_args()
        preview = args.preview
    batch_import_images(generate_preview=preview)


if __name__ == "__main__":
    main()
