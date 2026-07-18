#!/usr/bin/env python3
"""
P1-3 调色板匹配预览功能测试。

验证 batch_import_images(generate_preview=True) 的端到端流程：
  1. 导出 GLD → PNG（模拟用户修改）
  2. 回写 PNG → GLD（含预览生成）
  3. 检查 preview 目录是否生成 PNG
  4. 比较原 PNG 与 preview PNG（应视觉一致 = 调色板匹配无损）
"""
import sys
import os
import shutil
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from src.utils.gld_format import parse_gld, write_gld
from src.stage2_export_images import export_gld_to_pngs, export_sprite_png
from src.stage4_import_images import batch_import_images
from config import EXTRACT_DIR, PATCHED_DIR

# 临时测试目录
TMP_ROOT = EXTRACT_DIR.parent / "_test_p1_3"
TMP_EXTRACT = TMP_ROOT / "1_Extracted"
TMP_IMAGES = TMP_ROOT / "1_Extracted_Images"
TMP_PATCHED = TMP_ROOT / "2_Patched"


def cleanup():
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


def test_preview_e2e():
    """端到端测试：导出 → 回写(含预览) → 验证预览图"""
    cleanup()

    # 选一个 Format 2 的 GLD（字节级 roundtrip 无损）
    src_gld = EXTRACT_DIR / "AGL" / "0035_ERRORMSGWND.GLD"
    if not src_gld.exists():
        print(f"跳过：源文件不存在 {src_gld}")
        return False

    # 1. 搭建临时目录结构
    tmp_agl_dir = TMP_EXTRACT / "AGL"
    tmp_agl_img_dir = TMP_IMAGES / "AGL"
    tmp_agl_dir.mkdir(parents=True, exist_ok=True)
    tmp_agl_img_dir.mkdir(parents=True, exist_ok=True)

    # 复制 GLD 到临时目录
    tmp_gld = tmp_agl_dir / src_gld.name
    shutil.copy2(src_gld, tmp_gld)

    # 2. 导出 GLD → PNG（模拟用户修改前的导出）
    n = export_gld_to_pngs(tmp_gld, tmp_agl_img_dir)
    print(f"导出: {n} sprites")

    # 3. 用 mock 路径调用 batch_import_images(generate_preview=True)
    with patch("src.stage4_import_images.EXTRACT_DIR", TMP_EXTRACT), \
         patch("src.stage4_import_images.PATCHED_DIR", TMP_PATCHED):
        batch_import_images(import_folders=["AGL"], generate_preview=True)

    # 4. 检查 preview 目录
    preview_dir = TMP_IMAGES / "AGL_PREVIEW"
    preview_pngs = sorted(preview_dir.glob("*.png")) if preview_dir.exists() else []
    print(f"预览图数量: {len(preview_pngs)}")

    if len(preview_pngs) == 0:
        print("FAIL ✗: 未生成预览图")
        cleanup()
        return False

    # 5. 比较原 PNG 与 preview PNG（应视觉一致）
    orig_gld = parse_gld(src_gld)
    visual_ok = True
    for i in range(len(orig_gld.footer_entries)):
        entry = orig_gld.footer_entries[i]
        if entry.is_deleted:
            continue
        orig_png = tmp_agl_img_dir / f"{orig_gld.file_stem}_{i}.png"
        prev_png = preview_dir / f"{orig_gld.file_stem}_{i}.png"
        if not orig_png.exists() or not prev_png.exists():
            continue
        with Image.open(orig_png) as img1, Image.open(prev_png) as img2:
            d1 = img1.convert('RGBA').tobytes()
            d2 = img2.convert('RGBA').tobytes()
            if d1 != d2:
                visual_ok = False
                px_diffs = sum(1 for j in range(0, min(len(d1), len(d2)), 4)
                              if d1[j:j+4] != d2[j:j+4])
                print(f"  PNG[{i}] 视觉差异: {px_diffs} 像素")

    print(f"视觉级（原PNG vs 预览PNG）: {'PASS ✓' if visual_ok else 'DIFF'}")

    # 6. 检查回写的 GLD 也存在
    patched_gld = TMP_PATCHED / "AGL_IMG_PATCHED" / src_gld.name
    patched_exists = patched_gld.exists()
    print(f"回写 GLD 存在: {'✓' if patched_exists else '✗'}")

    cleanup()
    return visual_ok and patched_exists


def main():
    print("=" * 60)
    print("P1-3 调色板匹配预览功能测试")
    print("=" * 60)

    ok = test_preview_e2e()

    print(f"\n验收: {'PASS ✓' if ok else 'FAIL ✗'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
