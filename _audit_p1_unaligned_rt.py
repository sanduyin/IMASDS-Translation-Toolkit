#!/usr/bin/env python3
"""
P1 审核验证：对 crop_x 非 ppb 对齐的 sprite 做 roundtrip 测试。

预期：这些 sprite 的 roundtrip 会失败（视觉差异），证明 inject 有半字节错位 bug。
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from src.utils.gld_format import parse_gld, write_gld, PIXELS_PER_BYTE
from src.stage2_export_images import export_sprite_png
from config import EXTRACT_DIR, PATCHED_DIR

# 从审核扫描结果中选取的 crop_x 非对齐案例
TEST_CASES = [
    (EXTRACT_DIR / "AGL" / "0031_D_BLACKSHEET.GLD", 0, "fmt=3 crop_x=1 4bpp 8x8"),
    (EXTRACT_DIR / "AGL" / "0001_MENUITEM.GLD", 10, "fmt=3 crop_x=1 4bpp 16x16"),
    (EXTRACT_DIR / "AGL" / "0509_D_BATSU_MNG.GLD", 0, "fmt=2 crop_x=1 2bpp 18x16"),
    (EXTRACT_DIR / "AGL" / "0501_D_MEASURE_MNG.GLD", 0, "fmt=3 crop_x=1 4bpp 18x18"),
]

OUT_DIR = EXTRACT_DIR.parent / "1_Extracted_Images" / "_audit_unaligned"
RT_DIR = PATCHED_DIR / "_audit_unaligned_rt"
REEXPORT_DIR = EXTRACT_DIR.parent / "1_Extracted_Images" / "_audit_unaligned_re"


def test_unaligned_roundtrip(gld_path: Path, sprite_idx: int, label: str) -> bool:
    """测试单个非对齐 sprite 的 roundtrip"""
    print(f"\n--- {gld_path.name} [{sprite_idx}] {label} ---")

    if not gld_path.exists():
        print(f"  跳过：文件不存在")
        return True  # 不算失败

    gld = parse_gld(gld_path)
    if sprite_idx >= len(gld.footer_entries):
        print(f"  跳过：索引超出范围（{len(gld.footer_entries)} entries）")
        return True

    entry = gld.footer_entries[sprite_idx]
    if entry.is_deleted:
        print(f"  跳过：sprite 已删除")
        return True

    fmt = entry.format_id
    ppb = PIXELS_PER_BYTE[fmt]
    print(f"  fmt={fmt} ppb={ppb} crop_x={entry.crop_x} crop_w={entry.crop_width} "
          f"crop_h={entry.crop_height} crop_x%ppb={entry.crop_x % ppb}")

    # 1. 导出原始 PNG
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    orig_png = OUT_DIR / f"{gld_path.stem}_{sprite_idx}_orig.png"
    if not export_sprite_png(gld, sprite_idx, orig_png):
        print(f"  跳过：导出失败")
        return True
    print(f"  导出: {orig_png.name}")

    # 2. 注入回去
    rt_gld_path = RT_DIR / gld_path.name
    RT_DIR.mkdir(parents=True, exist_ok=True)
    rt_gld = parse_gld(gld_path)
    with Image.open(orig_png) as img:
        img = img.convert('RGBA')
        w, h = img.size
        rgba = img.tobytes()
    rt_gld.inject_sprite_rgba(sprite_idx, rgba, w, h)
    write_gld(rt_gld, rt_gld_path)

    # 3. 字节级比较
    orig_bytes = gld_path.read_bytes()
    rt_bytes = rt_gld_path.read_bytes()
    byte_equal = orig_bytes == rt_bytes
    print(f"  字节级: {'PASS' if byte_equal else 'DIFF'}")

    if not byte_equal:
        # 分析差异范围
        entry_pixels_start = entry.pixels_offset
        from src.utils.gld_format import BIT_DEPTH
        rw = gld.render_width(entry)
        bd = BIT_DEPTH[fmt]
        rwb = rw * bd // 8
        max_y = entry.crop_y + entry.crop_height
        pixel_end = entry_pixels_start + rwb * max_y
        diffs_in_sprite = sum(1 for i in range(entry_pixels_start, min(pixel_end, min(len(orig_bytes), len(rt_bytes))))
                             if orig_bytes[i] != rt_bytes[i])
        print(f"    sprite 像素区差异字节: {diffs_in_sprite}")

    # 4. 重新导出 → 视觉比较
    REEXPORT_DIR.mkdir(parents=True, exist_ok=True)
    reexport_png = REEXPORT_DIR / f"{gld_path.stem}_{sprite_idx}_re.png"
    export_sprite_png(rt_gld, sprite_idx, reexport_png)

    with Image.open(orig_png) as img1, Image.open(reexport_png) as img2:
        d1 = img1.convert('RGBA').tobytes()
        d2 = img2.convert('RGBA').tobytes()

    if d1 == d2:
        print(f"  视觉级: PASS")
        return True
    else:
        px_diffs = sum(1 for j in range(0, min(len(d1), len(d2)), 4) if d1[j:j+4] != d2[j:j+4])
        total_px = len(d1) // 4
        print(f"  视觉级: DIFF ({px_diffs}/{total_px} 像素差异)")
        return False


def main():
    print("=" * 70)
    print("P1 审核：crop_x 非 ppb 对齐 sprite 的 roundtrip 验证")
    print("=" * 70)

    for d in [OUT_DIR, RT_DIR, REEXPORT_DIR]:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    results = []
    for gld_path, idx, label in TEST_CASES:
        ok = test_unaligned_roundtrip(gld_path, idx, label)
        results.append((gld_path.name, idx, label, ok))

    print(f"\n{'='*70}")
    print("总结:")
    print(f"{'='*70}")
    all_ok = True
    for name, idx, label, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {name} [{idx}] {label}: {status}")
        if not ok:
            all_ok = False

    if not all_ok:
        print(f"\n⚠️  确认 bug：crop_x 非 ppb 对齐的 sprite 注入后视觉不一致")
        print(f"   需要修复 inject_sprite_rgba 的半字节偏移处理")
    else:
        print(f"\n✓ 所有非对齐 sprite roundtrip 通过（bug 未触发或影响可忽略）")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
