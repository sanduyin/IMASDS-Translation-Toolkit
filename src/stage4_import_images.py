# src/stage4_import_images.py
"""
PNG → GLD 图像回写工具。

【工作流程】
  ① 用 stage2_export_images.py 导出 GLD → PNG
  ② 用 Photoshop / GIMP 等工具修改 PNG
  ③ 将修改好的 PNG 放回原导出目录：
       game_data/1_Extracted_Images/<文件夹名>/
  ④ 运行本工具，回写后的 GLD 输出到：
       game_data/2_Patched/<文件夹名>_IMG_PATCHED/

【按文件夹区分导入模式】
  - AGL（PSD 模式）：读 {stem}.psd + {stem}.png，PSD 提供图层名/位置/尺寸，
    PNG 提供像素。用户编辑 PSD 后从 PS 导出同名 PNG，程序按 PSD 图层 rect
    从 PNG crop 出 sprite 注入。
  - TEX/TBL/BG（sprite 模式）：读 {stem}_{index}.png 单独 sprite 文件注入。

【注意事项】
  · PNG 为 RGBA 模式；RGB PNG 会被自动转换（不透明像素 alpha=255）。
  · 调色板由 gld_format.inject_sprite_rgba() 自动匹配（曼哈顿距离 + 透明惩罚）。
  · 透明像素（alpha < 128）回写时会标记为调色板中的透明色。
  · 未修改的 sprite 保持原样。

参考实现：dearlystars_tool (Rust) gld.rs (inject_png)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EXTRACT_DIR, PATCHED_DIR
from src.utils.gld_format import parse_gld, write_gld, GldFile
from src.stage2_export_images import export_sprite_png


def import_png_to_gld(png_path: Path, original_gld_path: Path,
                      output_gld_path: Path) -> tuple[int, int]:
    """
    将单个修改后的 PNG sprite 写回 GLD 文件（sprite 模式）。

    Args:
        png_path:           修改后的 PNG sprite 文件（{stem}_{index}.png）
        original_gld_path:  原始 GLD 文件
        output_gld_path:    输出的新 GLD 文件路径

    Returns:
        (width, height) of the injected sprite
    """
    stem = png_path.stem
    parts = stem.rsplit('_', 1)
    if len(parts) != 2 or not parts[1].isdigit():
        raise ValueError(f"文件名格式不符，无法识别 sprite 索引: {png_path.name}")

    index = int(parts[1])

    gld = parse_gld(original_gld_path)

    if index >= len(gld.footer_entries):
        raise ValueError(
            f"sprite 索引 {index} 超出范围（GLD 只有 {len(gld.footer_entries)} 个 entry）"
        )

    entry = gld.footer_entries[index]
    if entry.is_deleted:
        raise ValueError(f"sprite [{index}] 已标记为 deleted，无法注入")

    with Image.open(png_path) as img:
        rgba_img: Image.Image = img.convert('RGBA')
        width, height = rgba_img.size
        rgba_data = rgba_img.tobytes()

    if width != entry.crop_width or height != entry.crop_height:
        raise ValueError(
            f"PNG 尺寸不匹配：期望 {entry.crop_width}x{entry.crop_height}，"
            f"实际 {width}x{height}"
        )

    gld.inject_sprite_rgba(index, rgba_data, width, height)

    output_gld_path.parent.mkdir(parents=True, exist_ok=True)
    write_gld(gld, output_gld_path)

    return (width, height)


def import_psd_to_gld(
    png_dir: Path,
    gld_stem: str,
    original_gld_path: Path,
    output_gld_path: Path,
) -> tuple[int, int]:
    """从 {stem}.psd + {stem}.png 导入（AGL PSD 模式）。

    用户工作流：
      1. 程序导出 {stem}.psd（含 sprite_{index} 图层，记录初始位置）
      2. 用户在 PS 编辑 PSD（翻译、移动图层位置等）
      3. 用户从 PS 导出 {stem}.png（合并后的整图）
      4. 程序读 PSD 获取每个 sprite_{index} 的位置/尺寸，从 PNG crop 出像素注入

    PSD 只提供图层名 + 位置 + 尺寸信息，像素来自 PNG。
    若没有 PNG，则回退到从 PSD 图层直接提取像素（用于往返测试）。

    Returns:
        (n_injected, n_skipped)
    """
    from psd_tools import PSDImage

    psd_path = png_dir / f'{gld_stem}.psd'
    png_path = png_dir / f'{gld_stem}.png'
    if not psd_path.exists():
        return (0, 0)

    gld = parse_gld(original_gld_path)
    psd = PSDImage.open(psd_path)

    # 如果有用户导出的 PNG，从 PNG crop 像素；否则从 PSD 图层提取
    png_img: Image.Image | None = None
    if png_path.exists():
        png_img = Image.open(png_path).convert('RGBA')

    n_injected = 0
    n_skipped = 0
    for layer in psd:
        # 解析图层名：sprite_{index}
        name = layer.name
        if not name.startswith('sprite_'):
            n_skipped += 1
            continue
        try:
            index = int(name[len('sprite_'):])
        except ValueError:
            n_skipped += 1
            continue

        if index >= len(gld.footer_entries):
            n_skipped += 1
            continue
        entry = gld.footer_entries[index]
        if entry.is_deleted:
            n_skipped += 1
            continue

        # 获取图层的位置和尺寸
        left, top = layer.offset
        w, h = layer.size

        if png_img is not None:
            # 从用户导出的 PNG 按 PSD 图层位置 crop
            sprite_img = png_img.crop((left, top, left + w, top + h))
        else:
            # 回退：从 PSD 图层直接提取像素（往返测试用）
            layer_img = layer.topil()
            if layer_img is None:
                n_skipped += 1
                continue
            sprite_img = layer_img.convert('RGBA')

        # 校验尺寸（用户不应改变 sprite 尺寸）
        if sprite_img.size != (entry.crop_width, entry.crop_height):
            print(f"   ⚠️  跳过 [{gld_stem}/{name}]: 尺寸不匹配"
                  f"（期望 {entry.crop_width}x{entry.crop_height}，"
                  f"实际 {sprite_img.size[0]}x{sprite_img.size[1]}）")
            n_skipped += 1
            continue

        rgba_data = sprite_img.tobytes()
        gld.inject_sprite_rgba(index, rgba_data, entry.crop_width, entry.crop_height)
        n_injected += 1

    output_gld_path.parent.mkdir(parents=True, exist_ok=True)
    write_gld(gld, output_gld_path)
    return (n_injected, n_skipped)


def get_png_index_map(png_dir: Path) -> dict[str, list[int]]:
    """
    扫描 PNG 目录（sprite 模式），按 GLD 文件名分组，返回 {gld_stem: [index, ...]}。

    跳过：
      - .psd / _preview.jpg / _sheet.jpg / _manifest.json
      - 文件名无法解析出整数索引的文件
    """
    result: dict[str, list[int]] = {}

    for fname in os.listdir(png_dir):
        if not fname.lower().endswith('.png'):
            continue

        stem = Path(fname).stem
        # 跳过 sheet / preview
        if stem.endswith('_sheet') or stem.endswith('_preview'):
            continue
        # 跳过与 PSD 同名的整图 PNG（AGL 用户从 PS 导出的合并图，非 sprite）
        if (png_dir / (stem + '.psd')).exists():
            continue

        parts = stem.rsplit('_', 1)
        if len(parts) != 2:
            continue
        gld_stem, idx_str = parts
        if not idx_str.isdigit():
            continue

        idx = int(idx_str)
        result.setdefault(gld_stem, []).append(idx)

    for k in result:
        result[k].sort()

    return result


def get_psd_stems(png_dir: Path) -> list[str]:
    """扫描目录（PSD 模式），返回有 {stem}.psd 的 stem 列表。"""
    stems: list[str] = []
    for fname in os.listdir(png_dir):
        if fname.lower().endswith('.psd'):
            stems.append(fname[:-len('.psd')])
    stems.sort()
    return stems


# 需处理的图像文件夹（GLD 格式 sprite）
# 注意：BG 使用 NCGR/NCLR/NSCR 格式，由独立的 stage4_import_bg.py 处理，
#       不能放在这里（否则会用 .GLD 扩展名查找，找不到文件）
IMPORT_FOLDERS = ["TEX", "TBL", "AGL"]

# 使用 sheet 模式的文件夹（其他用 sprite 模式）
SHEET_MODE_FOLDERS = {"AGL"}

# 歌词课资产保护名单：Stage 3.5 已将扩容后的 AGL/GLD 部署到
# AGL_CHS_PATCHED/，Stage 6 不能用原始 Extracted/AGL 覆盖它们
# （覆盖会丢失中文字形像素 + frame 表扩容）
LYRIC_PROTECTED_AGL_GLD = {
    "0506_D_MEASURE_MOJI_MNG",  # 左侧候选字 AGL（frame 表已扩容到 489）
    "0507_D_MEASURE_MOJI_MNG",  # 左侧候选字 GLD（已追加 408 个中文字形）
    "0520_D_EPANEL_MOJI_MNG",   # 下侧题目字 AGL（frame 表已扩容到 489）
    "0521_D_EPANEL_MOJI_MNG",   # 下侧题目字 GLD（已追加 408 个中文字形）
}


def batch_import_images(import_folders: list[str] | None = None,
                        generate_preview: bool = False) -> None:
    """
    批量扫描导出图像目录，将修改过的 PNG 写回对应的 GLD。

    目录约定：
      PNG 来源 : game_data/1_Extracted_Images/<folder>/
      原始 GLD : game_data/1_Extracted/<folder>/
      输出 GLD : game_data/2_Patched/<folder>_IMG_PATCHED/
      预览 PNG : game_data/1_Extracted_Images/<folder>_PREVIEW/  (generate_preview=True)
    """
    if import_folders is None:
        import_folders = IMPORT_FOLDERS

    total_ok = 0
    total_skip = 0
    total_error = 0
    total_preview = 0

    for folder_name in import_folders:
        png_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / folder_name
        # AGL 模式：sprite PNG 在 sprites/ 子文件夹（split_psd_to_sprites.py 输出）
        # 其他文件夹（TEX/TBL/BG）：sprite PNG 在根目录
        sprite_png_dir = png_dir / "sprites" if folder_name == "AGL" else png_dir
        original_dir = EXTRACT_DIR / folder_name
        output_dir = PATCHED_DIR / f"{folder_name}_IMG_PATCHED"
        preview_dir = EXTRACT_DIR.parent / "1_Extracted_Images" / f"{folder_name}_PREVIEW"

        if not png_dir.exists():
            print(f"⏭️  跳过：PNG 目录不存在: {png_dir}")
            continue
        if not original_dir.exists():
            print(f"⏭️  跳过：原始 GLD 目录不存在: {original_dir}")
            continue

        is_psd_mode = False  # AGL 已改回 sprite 模式（用 split_psd_to_sprites.py 拆分）

        if is_psd_mode:
            # PSD 模式：扫描 {stem}.psd
            psd_stems = get_psd_stems(png_dir)
            if not psd_stems:
                print(f"⏭️  {folder_name}: 没有找到可回写的 .psd，跳过。")
                continue

            if generate_preview:
                preview_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n📥 正在处理 {folder_name}（PSD 模式），共 {len(psd_stems)} 个 GLD 需要回写..."
                  + ("（含预览）" if generate_preview else ""))

            for gld_stem in psd_stems:
                # 歌词课资产保护：跳过 Stage 3.5 已扩容的 AGL/GLD
                if gld_stem in LYRIC_PROTECTED_AGL_GLD:
                    print(f"   🔒 跳过 Stage 3.5 保护资产: {gld_stem}.GLD")
                    continue

                gld_path = None
                for ext in ['.GLD', '.gld']:
                    candidate = original_dir / (gld_stem + ext)
                    if candidate.exists():
                        gld_path = candidate
                        break

                if gld_path is None:
                    print(f"   ⚠️  跳过（找不到对应的原始 GLD）: {gld_stem}.GLD")
                    total_skip += 1
                    continue

                try:
                    output_gld_path = output_dir / gld_path.name
                    n_inj, n_skp = import_psd_to_gld(
                        png_dir, gld_stem, gld_path, output_gld_path,
                    )
                    total_ok += n_inj
                    total_skip += n_skp
                    print(f"   ✅ 已写回: {gld_path.name} (注入 {n_inj} sprite)")

                    if generate_preview and n_inj > 0:
                        # 从注入后的 GLD 重新导出 PSD 预览
                        gld = parse_gld(output_gld_path)
                        preview_psd = preview_dir / f'{gld_stem}.psd'
                        from src.stage2_export_images import export_editable_psd
                        export_editable_psd(gld, preview_psd)
                        total_preview += 1
                except Exception as e:
                    print(f"   ❌ 错误 [{gld_stem}]: {e}")
                    total_error += 1
        else:
            # sprite 模式
            # AGL 从 sprites/ 子目录读取，TEX/TBL/BG 从根目录读取
            if not sprite_png_dir.exists():
                print(f"⏭️  跳过：sprite PNG 目录不存在: {sprite_png_dir}")
                continue
            index_map = get_png_index_map(sprite_png_dir)
            if not index_map:
                print(f"⏭️  {folder_name}: 没有找到可回写的 PNG sprite，跳过。"
                      + (f"（请确认 sprite PNG 在 {sprite_png_dir.name}/ 子目录）"
                         if folder_name == "AGL" else ""))
                continue

            if generate_preview:
                preview_dir.mkdir(parents=True, exist_ok=True)

            print(f"\n📥 正在处理 {folder_name}（sprite 模式），共 {len(index_map)} 个 GLD 需要回写..."
                  + ("（含预览）" if generate_preview else ""))

            for gld_stem, indices in index_map.items():
                # 歌词课资产保护：跳过 Stage 3.5 已扩容的 AGL/GLD
                if gld_stem in LYRIC_PROTECTED_AGL_GLD:
                    print(f"   🔒 跳过 Stage 3.5 保护资产: {gld_stem} ({len(indices)} sprite)")
                    total_skip += len(indices)
                    continue

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

                try:
                    gld = parse_gld(gld_path)
                except ValueError as e:
                    print(f"   ❌ 解析失败 [{gld_stem}.GLD]: {e}")
                    total_error += len(indices)
                    continue

                modified = False
                injected_indices = []
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

                    png_path = sprite_png_dir / f"{gld_stem}_{index}.png"
                    try:
                        with Image.open(png_path) as opened_img:
                            img: Image.Image = opened_img.convert('RGBA')
                            width, height = img.size

                        if width != entry.crop_width or height != entry.crop_height:
                            # 容错：PS 编辑 PSD 时会自动裁掉图层边缘透明像素，
                            # 导致 sprite PNG 尺寸比期望小 1~2px。
                            # 如果实际尺寸 ≤ 期望尺寸，用透明填充到期望尺寸（左上对齐）。
                            if width <= entry.crop_width and height <= entry.crop_height:
                                new_img = Image.new(
                                    'RGBA',
                                    (entry.crop_width, entry.crop_height),
                                    (0, 0, 0, 0),
                                )
                                new_img.paste(img, (0, 0))
                                img = new_img
                                width, height = img.size
                            else:
                                print(
                                    f"   ⚠️  跳过 [{gld_stem}_{index}]: "
                                    f"尺寸不匹配（期望 {entry.crop_width}x{entry.crop_height}，"
                                    f"实际 {width}x{height}）"
                                )
                                total_skip += 1
                                continue

                        rgba_data = img.tobytes()
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

                    if generate_preview:
                        for index in injected_indices:
                            preview_png = preview_dir / f"{gld_stem}_{index}.png"
                            if export_sprite_png(gld, index, preview_png):
                                total_preview += 1

    print(f"\n🎉 图像回写完成！成功 {total_ok}，跳过 {total_skip}，错误 {total_error}")
    if generate_preview and total_preview > 0:
        print(f"   预览图: {total_preview} 个")
        print(f"   预览目录: game_data/1_Extracted_Images/<folder>_PREVIEW/")
    print(f"   输出目录: {PATCHED_DIR}/<文件夹名>_IMG_PATCHED/")
    print(f"   请确认 config.py 的 TARGET_PACKS 已包含对应模块（如 \"TEX\"），")
    print(f"   否则 stage5_build_rom.py 打包时不会包含这些修改。")


def main(preview: bool | None = None) -> None:
    """PNG → GLD 图像回写入口。"""
    if preview is None:
        import argparse
        parser = argparse.ArgumentParser(description="PNG → GLD 图像回写")
        parser.add_argument('--preview', action='store_true',
                            help='注入后重新导出预览 PNG 到 <folder>_PREVIEW/')
        args = parser.parse_args()
        preview = args.preview
    batch_import_images(generate_preview=preview)


if __name__ == "__main__":
    main()
