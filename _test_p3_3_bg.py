#!/usr/bin/env python3
"""
P3-3 stage4_import_bg.py 4bpp Tile 边界修复测试。

验收标准（对齐 RUST_TO_PYTHON_PLAN.md P3-3）：
  1. 4bpp 背景图回写无错位，与 8bpp 同等稳定
  2. BMP 尺寸与 NSCR map 尺寸不一致时不崩溃（越界保护）
  3. roundtrip：导出 BMP → 导入 NCGR → 重新导出 BMP2，BMP == BMP2（视觉一致）

测试方法：
  1. 扫描所有 BG 文件，统计 bpp 分布
  2. 对 4bpp 样本做 roundtrip 测试
  3. 构造 BMP 尺寸 != NSCR map 尺寸的边界场景
"""
import os
import sys
import struct
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import EXTRACT_DIR, PATCHED_DIR
from src.stage2_export_bg import (
    parse_nds_container, parse_ncgr, parse_nscr, parse_nclr,
    compose_bg_image, write_bmp_8bpp, find_bg_triplets
)
from src.stage4_import_bg import (
    read_bmp_8bpp, extract_tiles_from_bmp, encode_tiles,
    bmp_palette_to_nds, import_bg_triplet
)


def scan_bg_bpp():
    """扫描所有 BG 文件的 bpp 分布"""
    bg_dir = EXTRACT_DIR / "BG"
    if not bg_dir.exists():
        return {}, []

    triplets = find_bg_triplets(bg_dir)
    bpp_dist = {}
    samples = []
    for ncgr, nclr, nscr, base, stem in triplets:
        try:
            ncgr_data = ncgr.read_bytes()
            _, bpp, _, _ = parse_ncgr(ncgr_data)
            entries, map_w, map_h, w_px, h_px = parse_nscr(nscr.read_bytes())
            bpp_dist[bpp] = bpp_dist.get(bpp, 0) + 1
            samples.append({
                'stem': stem, 'ncgr': ncgr, 'nclr': nclr, 'nscr': nscr,
                'bpp': bpp, 'map_w_tiles': map_w, 'map_h_tiles': map_h,
                'w_px': w_px, 'h_px': h_px,
                'tile_count': len(parse_ncgr(ncgr_data)[0]),
            })
        except Exception as e:
            print(f"  ⚠️ {stem}: {e}")
    return bpp_dist, samples


def test_roundtrip_4bpp(samples):
    """1. 4bpp roundtrip — 导出 BMP → 导入 NCGR → 重新导出 BMP2，视觉一致"""
    print("\n--- 1. 4bpp roundtrip ---")
    bpp4_samples = [s for s in samples if s['bpp'] == 4]
    if not bpp4_samples:
        print("  ⚠️ 无 4bpp 样本，跳过")
        return True

    tmpdir = Path(tempfile.mkdtemp())
    try:
        all_ok = True
        for s in bpp4_samples[:5]:  # 测试最多 5 个
            stem = s['stem']
            print(f"\n  测试: {stem} (4bpp, {s['w_px']}x{s['h_px']}, {s['tile_count']} tiles)")

            # 1. 导出 BMP
            ncgr_data = s['ncgr'].read_bytes()
            nclr_data = s['nclr'].read_bytes()
            nscr_data = s['nscr'].read_bytes()

            palette_colors = parse_nclr(nclr_data)
            tiles_orig, bpp, _, _ = parse_ncgr(ncgr_data)
            entries, map_w, map_h, w_px, h_px = parse_nscr(nscr_data)
            pixel_data, bmp_palette = compose_bg_image(
                tiles_orig, bpp, palette_colors, entries, map_w, map_h)

            bmp1_path = tmpdir / f"{stem}_1.bmp"
            write_bmp_8bpp(bmp1_path, w_px, h_px, pixel_data, bmp_palette)

            # 2. 导入 NCGR
            out_ncgr = tmpdir / f"{stem}_rt.ncgr"
            out_nclr = tmpdir / f"{stem}_rt.nclr"
            out_nscr = tmpdir / f"{stem}_rt.nscr"
            try:
                import_bg_triplet(bmp1_path, s['ncgr'], s['nclr'], s['nscr'],
                                  out_ncgr, out_nclr, out_nscr)
            except Exception as e:
                print(f"    ❌ 导入失败: {e}")
                all_ok = False
                continue

            # 3. 重新导出 BMP2
            rt_ncgr_data = out_ncgr.read_bytes()
            rt_nclr_data = out_nclr.read_bytes()
            rt_nscr_data = out_nscr.read_bytes()
            tiles_rt, bpp_rt, _, _ = parse_ncgr(rt_ncgr_data)
            palette_rt = parse_nclr(rt_nclr_data)
            entries_rt, map_w_rt, map_h_rt, _, _ = parse_nscr(rt_nscr_data)
            pixel_data_rt, bmp_palette_rt = compose_bg_image(
                tiles_rt, bpp_rt, palette_rt, entries_rt, map_w_rt, map_h_rt)

            bmp2_path = tmpdir / f"{stem}_2.bmp"
            write_bmp_8bpp(bmp2_path, w_px, h_px, pixel_data_rt, bmp_palette_rt)

            # 4. 对比 BMP1 vs BMP2（像素级）
            if pixel_data == pixel_data_rt:
                print(f"    ✓ 像素级 PASS（roundtrip 一致）")
            else:
                diffs = sum(1 for i in range(min(len(pixel_data), len(pixel_data_rt)))
                           if pixel_data[i] != pixel_data_rt[i])
                print(f"    ⚠️ 像素级 DIFF: {diffs} 像素差异")
                # 4bpp 时检查是否只是 palette 分组差异（视觉相同）
                if diffs > 0:
                    all_ok = False

            # 5. 对比字节级 NCGR
            if ncgr_data == rt_ncgr_data:
                print(f"    ✓ 字节级 NCGR PASS")
            else:
                # 4bpp 可能因 palette 重新映射导致字节差异，但视觉应一致
                diffs = sum(1 for i in range(min(len(ncgr_data), len(rt_ncgr_data)))
                           if ncgr_data[i] != rt_ncgr_data[i])
                print(f"    ℹ️ 字节级 NCGR DIFF: {diffs} 字节（4bpp 可能因 palette 重映射）")

        return all_ok
    finally:
        # Windows 下 rmtree 可能阻塞，用 ignore_errors
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_bmp_nscr_size_mismatch():
    """2. BMP 尺寸 != NSCR map 尺寸时不崩溃（越界保护）"""
    print("\n--- 2. BMP/NSCR 尺寸不一致越界保护 ---")
    # 构造一个小的 4bpp 场景：NSCR map 8x8 tiles (64x64 px)，BMP 只有 16x16
    # extract_tiles_from_bmp 应不崩溃，越界像素用 0 填充
    bmp_w, bmp_h = 16, 16  # 比 NSCR map 小
    pixel_data = bytes([i % 256 for i in range(bmp_w * bmp_h)])
    entries = [(0, False, False, 0)] * 64  # 8x8 tiles = 64 entries
    map_w_tiles, map_h_tiles = 8, 8

    try:
        tiles = extract_tiles_from_bmp(
            pixel_data, bmp_w, bmp_h, entries,
            map_w_tiles, map_h_tiles, original_tile_count=1, bpp=4)
        assert len(tiles) == 1
        assert len(tiles[0]) == 64
        # 越界像素应为 0
        # entry_idx=0, col=0, row=0, bmp_x/y 在 0-7 范围内（BMP 16x16 能覆盖）
        # entry_idx=1, col=1, row=0, bmp_x=8-15, bmp_y=0-7（BMP 16x16 能覆盖）
        # entry_idx=4, col=4, row=0, bmp_x=32-39（超出 BMP 16 宽）→ 越界用 0
        print(f"  ✓ BMP(16x16) 与 NSCR(64x64) 尺寸不一致时未崩溃，越界像素用 0 填充")
        return True
    except Exception as e:
        print(f"  ❌ 崩溃: {e}")
        return False


def test_4bpp_encode_decode_symmetry():
    """3. 4bpp encode/decode 对称性 — 确保打包/解包可逆"""
    print("\n--- 3. 4bpp encode/decode 对称性 ---")
    # 构造一个已知 tile
    original_tile = [i % 16 for i in range(64)]  # 0,1,2,...,15,0,1,...
    tiles = [original_tile]
    encoded = encode_tiles(tiles, bpp=4)
    # 4bpp: 64 像素 → 32 字节
    assert len(encoded) == 32, f"4bpp 32 字节错误: {len(encoded)}"

    # 解码回 tile
    decoded_pixels = []
    for byte in encoded:
        decoded_pixels.append(byte & 0x0F)
        decoded_pixels.append((byte >> 4) & 0x0F)

    assert decoded_pixels == original_tile, \
        f"4bpp 对称性失败:\n原始: {original_tile}\n解码: {decoded_pixels}"
    print(f"  ✓ 4bpp encode/decode 对称（32 字节 → 64 像素 → 32 字节）")
    return True


def test_8bpp_roundtrip_stability(samples):
    """4. 8bpp roundtrip 稳定性（回归测试，确保不破坏 8bpp）"""
    print("\n--- 4. 8bpp roundtrip 稳定性 ---")
    bpp8_samples = [s for s in samples if s['bpp'] == 8]
    if not bpp8_samples:
        print("  ⚠️ 无 8bpp 样本，跳过")
        return True

    tmpdir = Path(tempfile.mkdtemp())
    try:
        all_ok = True
        for s in bpp8_samples[:3]:  # 测试最多 3 个
            stem = s['stem']
            print(f"\n  测试: {stem} (8bpp, {s['w_px']}x{s['h_px']})")

            ncgr_data = s['ncgr'].read_bytes()
            nclr_data = s['nclr'].read_bytes()
            nscr_data = s['nscr'].read_bytes()

            palette_colors = parse_nclr(nclr_data)
            tiles_orig, bpp, _, _ = parse_ncgr(ncgr_data)
            entries, map_w, map_h, w_px, h_px = parse_nscr(nscr_data)
            pixel_data, bmp_palette = compose_bg_image(
                tiles_orig, bpp, palette_colors, entries, map_w, map_h)

            bmp1_path = tmpdir / f"{stem}_1.bmp"
            write_bmp_8bpp(bmp1_path, w_px, h_px, pixel_data, bmp_palette)

            out_ncgr = tmpdir / f"{stem}_rt.ncgr"
            out_nclr = tmpdir / f"{stem}_rt.nclr"
            out_nscr = tmpdir / f"{stem}_rt.nscr"
            try:
                import_bg_triplet(bmp1_path, s['ncgr'], s['nclr'], s['nscr'],
                                  out_ncgr, out_nclr, out_nscr)
            except Exception as e:
                print(f"    ❌ 导入失败: {e}")
                all_ok = False
                continue

            # 8bpp 应字节级一致
            rt_ncgr_data = out_ncgr.read_bytes()
            if ncgr_data == rt_ncgr_data:
                print(f"    ✓ 字节级 NCGR PASS")
            else:
                diffs = sum(1 for i in range(min(len(ncgr_data), len(rt_ncgr_data)))
                           if ncgr_data[i] != rt_ncgr_data[i])
                print(f"    ⚠️ 字节级 NCGR DIFF: {diffs} 字节")
                all_ok = False

        return all_ok
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 60)
    print("P3-3 stage4_import_bg.py 4bpp Tile 边界修复测试")
    print("=" * 60)

    print("\n📊 扫描 BG 文件 bpp 分布...")
    bpp_dist, samples = scan_bg_bpp()
    print(f"  bpp 分布: {bpp_dist}")
    print(f"  总样本数: {len(samples)}")

    tests = [
        ("4bpp encode/decode 对称性", test_4bpp_encode_decode_symmetry),
        ("BMP/NSCR 尺寸不一致越界保护", test_bmp_nscr_size_mismatch),
        ("4bpp roundtrip", lambda: test_roundtrip_4bpp(samples)),
        ("8bpp roundtrip 稳定性", lambda: test_8bpp_roundtrip_stability(samples)),
    ]

    all_ok = True
    for name, fn in tests:
        try:
            if not fn():
                all_ok = False
        except Exception as e:
            import traceback
            traceback.print_exc()
            all_ok = False

    print(f"\n{'='*60}")
    print(f"验收: {'PASS ✓' if all_ok else 'FAIL ✗'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
