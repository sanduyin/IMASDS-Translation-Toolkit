#!/usr/bin/env python3
"""
P1-1 PNG 导出/导入 roundtrip 测试。

测试内容：
  1. GLD → PNG 导出
  2. PNG → GLD 导入
  3. 比较原始 GLD 与 roundtrip GLD（字节级 + 视觉级）
  4. 重新导出 roundtrip GLD → PNG2，比较 PNG vs PNG2（视觉级）
"""
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from src.utils.gld_format import parse_gld, write_gld, BIT_DEPTH
from src.stage2_export_images import export_gld_to_pngs
from config import EXTRACT_DIR, PATCHED_DIR

TEST_DIR = EXTRACT_DIR.parent / "1_Extracted_Images" / "_test_p1"
ROUNDTRIP_DIR = PATCHED_DIR / "_test_p1_roundtrip"
REEXPORT_DIR = EXTRACT_DIR.parent / "1_Extracted_Images" / "_test_p1_reexport"


def cleanup():
    for d in [TEST_DIR, ROUNDTRIP_DIR, REEXPORT_DIR]:
        shutil.rmtree(d, ignore_errors=True)


def test_roundtrip(gld_path: Path, label: str) -> dict:
    """测试单个 GLD 的 roundtrip。"""
    print(f"\n{'='*60}")
    print(f"测试: {label} ({gld_path.name})")
    print(f"{'='*60}")

    cleanup()
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    ROUNDTRIP_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 导出
    orig_gld = parse_gld(gld_path)
    n = export_gld_to_pngs(gld_path, TEST_DIR)
    print(f"导出: {n} sprites")

    # 2. 导入
    rt_gld_path = ROUNDTRIP_DIR / gld_path.name
    gld = parse_gld(gld_path)
    for i in range(len(gld.footer_entries)):
        entry = gld.footer_entries[i]
        if entry.is_deleted:
            continue
        png_path = TEST_DIR / f"{gld.file_stem}_{i}.png"
        if not png_path.exists():
            continue
        with Image.open(png_path) as img:
            img = img.convert('RGBA')
            w, h = img.size
            rgba = img.tobytes()
        gld.inject_sprite_rgba(i, rgba, w, h)
    write_gld(gld, rt_gld_path)

    # 3. 字节比较
    orig_bytes = open(gld_path, 'rb').read()
    rt_bytes = open(rt_gld_path, 'rb').read()
    byte_equal = orig_bytes == rt_bytes
    print(f"字节级: {'PASS ✓' if byte_equal else 'DIFF'} (orig={len(orig_bytes)}, rt={len(rt_bytes)})")

    if not byte_equal:
        # 分析差异
        total_diffs = sum(1 for i in range(min(len(orig_bytes), len(rt_bytes)))
                         if orig_bytes[i] != rt_bytes[i])
        print(f"  差异字节数: {total_diffs}")

        # 按 sprite 分析
        pixel_start = 32
        pixel_end = pixel_start + orig_gld.header.pixel_data_size
        for i, entry in enumerate(orig_gld.footer_entries):
            if entry.is_deleted:
                continue
            fmt = entry.format_id
            bd = BIT_DEPTH.get(fmt, 8)
            rw = orig_gld.render_width(entry)
            max_y = entry.crop_y + entry.crop_height
            rwb = rw * bd // 8
            s = entry.pixels_offset
            e = s + rwb * max_y
            od = orig_gld.pixel_data[s:e]
            rd = gld.pixel_data[s:e]
            diffs = sum(1 for j in range(min(len(od), len(rd))) if od[j] != rd[j])
            if diffs > 0:
                # 检查是否只是调色板索引变化（视觉相同）
                pal = orig_gld.get_palette(entry)
                visual_same = 0
                visual_diff = 0
                alpha_diff = 0
                for j in range(min(len(od), len(rd))):
                    if od[j] != rd[j]:
                        if fmt == 1:
                            a_o = od[j] >> 5
                            a_r = rd[j] >> 5
                            c_o = od[j] & 0x1F
                            c_r = rd[j] & 0x1F
                        elif fmt == 6:
                            a_o = od[j] >> 3
                            a_r = rd[j] >> 3
                            c_o = od[j] & 0x07
                            c_r = rd[j] & 0x07
                        else:
                            a_o = a_r = 0
                            c_o = od[j]
                            c_r = rd[j]

                        if a_o != a_r:
                            alpha_diff += 1
                        elif c_o < len(pal) and c_r < len(pal):
                            if (pal[c_o] & 0x7FFF) == (pal[c_r] & 0x7FFF):
                                visual_same += 1
                            else:
                                visual_diff += 1
                        else:
                            visual_diff += 1

                print(f"  [{i}] fmt={fmt} crop={entry.crop_width}x{entry.crop_height}: "
                      f"{diffs} diffs (alpha_diff={alpha_diff}, "
                      f"visual_same={visual_same}, visual_diff={visual_diff})")

    # 4. 重新导出 roundtrip GLD → PNG2，比较视觉
    REEXPORT_DIR.mkdir(parents=True, exist_ok=True)
    export_gld_to_pngs(rt_gld_path, REEXPORT_DIR)

    visual_ok = True
    for i in range(len(orig_gld.footer_entries)):
        entry = orig_gld.footer_entries[i]
        if entry.is_deleted:
            continue
        png1 = TEST_DIR / f"{orig_gld.file_stem}_{i}.png"
        png2 = REEXPORT_DIR / f"{orig_gld.file_stem}_{i}.png"
        if not png1.exists() or not png2.exists():
            continue
        with Image.open(png1) as img1, Image.open(png2) as img2:
            d1 = img1.convert('RGBA').tobytes()
            d2 = img2.convert('RGBA').tobytes()
            if d1 != d2:
                visual_ok = False
                # 找出差异像素数
                px_diffs = sum(1 for j in range(0, min(len(d1), len(d2)), 4)
                              if d1[j:j+4] != d2[j:j+4])
                print(f"  PNG[{i}] 视觉差异: {px_diffs} 像素")

    print(f"视觉级: {'PASS ✓' if visual_ok else 'DIFF'}")

    cleanup()
    return {'byte_equal': byte_equal, 'visual_ok': visual_ok}


def main():
    tests = [
        (EXTRACT_DIR / 'TBL' / '0349_RYO.GLD', 'TBL 角色 (fmt 1/3/4/6)'),
        (EXTRACT_DIR / 'TEX' / '0000_B2D_765PRO_ENTRANCE_LV3_DAYTIME.GLD', 'TEX 背景 (fmt 4)'),
        (EXTRACT_DIR / 'TBL' / '0359_MP_AIH_COS01.GLD', 'TBL 小文件 (2 entries)'),
        # P1-2: 补充 Format 2 (2bpp, 4色) 的 roundtrip 测试
        (EXTRACT_DIR / 'AGL' / '0035_ERRORMSGWND.GLD', 'AGL Format 2 (2bpp, 4色)'),
    ]

    results = []
    for gld_path, label in tests:
        if not gld_path.exists():
            print(f"跳过（文件不存在）: {gld_path}")
            continue
        results.append(test_roundtrip(gld_path, label))

    print(f"\n{'='*60}")
    print("总结:")
    print(f"{'='*60}")
    for (gld_path, label), r in zip(tests, results):
        status = []
        status.append("字节" + ("✓" if r['byte_equal'] else "✗"))
        status.append("视觉" + ("✓" if r['visual_ok'] else "✗"))
        print(f"  {label}: {', '.join(status)}")

    # 验收标准：视觉级必须 PASS（游戏内显示正常）
    all_visual_ok = all(r['visual_ok'] for r in results)
    print(f"\n验收（视觉级全部 PASS）: {'PASS ✓' if all_visual_ok else 'FAIL ✗'}")

    cleanup()
    return 0 if all_visual_ok else 1


if __name__ == "__main__":
    sys.exit(main())
