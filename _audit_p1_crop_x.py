#!/usr/bin/env python3
"""
P1 审核：全量扫描所有 GLD 文件的 crop_x 对齐情况。

验证 inject_sprite_rgba 的字节对齐 bug 是否会在实际数据中触发：
  - 4bpp (Format 3): crop_x 必须是 2 的倍数
  - 2bpp (Format 2): crop_x 必须是 4 的倍数
  - 8bpp (Format 1/4/6): crop_x 无对齐要求（ppb=1）

同时检查 render_width 是否总是 ppb 的倍数（影响 render_width_bytes 计算）。
"""
import os
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.utils.gld_format import parse_gld, BIT_DEPTH, PIXELS_PER_BYTE, SPRITE_DELETED
from config import EXTRACT_DIR


def scan_gld(gld_path: Path) -> dict:
    """扫描单个 GLD 文件，返回对齐统计"""
    try:
        gld = parse_gld(gld_path)
    except Exception as e:
        return {'error': str(e)}

    stats = {
        'total_sprites': 0,
        'deleted': 0,
        'by_format': {},
        'crop_x_unaligned': [],   # crop_x 不是 ppb 倍数
        'crop_w_unaligned': [],   # crop_width 不是 ppb 倍数（影响尾部填充）
        'render_w_unaligned': [], # render_width 不是 ppb 倍数（影响行字节数）
    }

    for i, entry in enumerate(gld.footer_entries):
        if entry.is_deleted:
            stats['deleted'] += 1
            continue
        stats['total_sprites'] += 1

        fmt = entry.format_id
        ppb = PIXELS_PER_BYTE.get(fmt, 1)
        bd = BIT_DEPTH.get(fmt, 8)

        fmt_stats = stats['by_format'].setdefault(fmt, {'count': 0, 'crop_x_unaligned': 0, 'crop_w_unaligned': 0})
        fmt_stats['count'] += 1

        # 检查 crop_x 对齐
        if ppb > 1 and entry.crop_x % ppb != 0:
            stats['crop_x_unaligned'].append({
                'index': i, 'fmt': fmt, 'crop_x': entry.crop_x,
                'ppb': ppb, 'crop_w': entry.crop_width,
                'crop_h': entry.crop_height,
            })
            fmt_stats['crop_x_unaligned'] += 1

        # 检查 crop_width 对齐（影响尾部 last_byte 处理）
        if ppb > 1 and entry.crop_width % ppb != 0:
            stats['crop_w_unaligned'].append({
                'index': i, 'fmt': fmt, 'crop_w': entry.crop_width,
                'ppb': ppb, 'crop_x': entry.crop_x,
            })
            fmt_stats['crop_w_unaligned'] += 1

        # 检查 render_width 对齐
        rw = gld.render_width(entry)
        if ppb > 1 and rw % ppb != 0:
            stats['render_w_unaligned'].append({
                'index': i, 'fmt': fmt, 'render_w': rw, 'ppb': ppb,
            })

    return stats


def main():
    print("=" * 70)
    print("P1 审核：全量 GLD crop_x / crop_width / render_width 对齐扫描")
    print("=" * 70)

    folders = ["TEX", "TBL", "AGL", "BG"]
    total_stats = {
        'total_files': 0,
        'total_sprites': 0,
        'by_format': Counter(),
        'crop_x_unaligned_files': 0,
        'crop_x_unaligned_total': 0,
        'crop_w_unaligned_total': 0,
        'render_w_unaligned_total': 0,
        'worst_cases': [],
    }

    for folder in folders:
        folder_dir = EXTRACT_DIR / folder
        if not folder_dir.exists():
            continue

        gld_files = sorted(f for f in os.listdir(folder_dir) if f.upper().endswith('.GLD'))
        print(f"\n--- {folder}: {len(gld_files)} GLD 文件 ---")

        for fname in gld_files:
            gld_path = folder_dir / fname
            stats = scan_gld(gld_path)

            if 'error' in stats:
                print(f"  ❌ {fname}: {stats['error']}")
                continue

            total_stats['total_files'] += 1
            total_stats['total_sprites'] += stats['total_sprites']

            for fmt, fs in stats['by_format'].items():
                total_stats['by_format'][fmt] += fs['count']

            if stats['crop_x_unaligned']:
                total_stats['crop_x_unaligned_files'] += 1
                total_stats['crop_x_unaligned_total'] += len(stats['crop_x_unaligned'])
                # 记录最严重的案例
                for case in stats['crop_x_unaligned'][:3]:
                    total_stats['worst_cases'].append({
                        'file': f"{folder}/{fname}", **case
                    })

            total_stats['crop_w_unaligned_total'] += len(stats['crop_w_unaligned'])
            total_stats['render_w_unaligned_total'] += len(stats['render_w_unaligned'])

    print(f"\n{'='*70}")
    print("总结")
    print(f"{'='*70}")
    print(f"扫描文件数: {total_stats['total_files']}")
    print(f"扫描 sprite 数: {total_stats['total_sprites']}")
    print(f"Format 分布: {dict(total_stats['by_format'])}")
    print()
    print(f"crop_x 非 ppb 对齐的文件数: {total_stats['crop_x_unaligned_files']}")
    print(f"crop_x 非 ppb 对齐的 sprite 数: {total_stats['crop_x_unaligned_total']}")
    print(f"crop_width 非 ppb 对齐的 sprite 数: {total_stats['crop_w_unaligned_total']}")
    print(f"render_width 非 ppb 对齐的 sprite 数: {total_stats['render_w_unaligned_total']}")

    if total_stats['crop_x_unaligned_total'] > 0:
        print(f"\n⚠️  严重：发现 crop_x 非 ppb 对齐的 sprite！")
        print(f"   这些 sprite 在注入时会发生半字节错位（Python 和 Rust 参考实现都有此问题）")
        print(f"   前 10 个案例：")
        for case in total_stats['worst_cases'][:10]:
            print(f"   - {case['file']} [{case['index']}] "
                  f"fmt={case['fmt']} crop_x={case['crop_x']} ppb={case['ppb']} "
                  f"crop={case['crop_w']}x{case['crop_h']}")
    else:
        print(f"\n✓ 所有 sprite 的 crop_x 都是 ppb 的倍数（inject 字节对齐安全）")

    if total_stats['crop_w_unaligned_total'] > 0:
        print(f"\n⚠️  注意：{total_stats['crop_w_unaligned_total']} 个 sprite 的 crop_width 非 ppb 对齐")
        print(f"   这些依赖 last_byte 尾部保留逻辑（已实现，需验证正确性）")

    if total_stats['render_w_unaligned_total'] > 0:
        print(f"\n⚠️  严重：{total_stats['render_w_unaligned_total']} 个 sprite 的 render_width 非 ppb 对齐")
        print(f"   render_width_bytes 计算会截断，丢失像素")
    else:
        print(f"✓ 所有 sprite 的 render_width 都是 ppb 的倍数（行字节数计算安全）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
