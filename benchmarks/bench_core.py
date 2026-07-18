# benchmarks/bench_core.py
"""
性能基准：记录核心工具函数与各 Stage 耗时，追踪性能回归。

用法:
    python -m benchmarks.bench_core            # 运行所有基准
    python -m benchmarks.bench_core --json      # 输出 JSON（用于 CI 对比）
    python -m benchmarks.bench_core --stage     # 同时基准 Stage 函数（需 game_data）

输出:
    控制台表格 +（可选）benchmarks/results/bench_YYYYMMDD_HHMMSS.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import struct
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# 确保项目根在 sys.path
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.utils.lz10 import lz10_compress, lz10_decompress
from src.utils.blz import blz_compress, blz_decompress
from src.utils.gld_format import (
    GLD_MAGIC, FOOTER_TYPE_1, SPRITE_FORMAT_4,
    GldHeader, GldFooterEntry, GldFile, write_gld, parse_gld,
)
from src.utils.bbq_format import parse_bbq_file
from src.ndstool.crc import crc16_header
from src.stage5_build_rom import crc16_nds


# ----------------------------------------------------------------------
# 计时工具
# ----------------------------------------------------------------------

def bench(
    name: str,
    func: Callable[..., Any],
    *args: Any,
    iterations: int = 20,
    warmup: int = 2,
) -> dict[str, Any]:
    """运行 func 多次取平均耗时。

    Args:
        name:       基准名称
        func:       待测函数
        args:       传给 func 的位置参数
        iterations: 正式测量次数
        warmup:     预热次数（不计入统计）

    Returns:
        {name, avg_ms, min_ms, max_ms, p50_ms, iterations}
    """
    # 预热（让 JIT/缓存生效）
    for _ in range(warmup):
        func(*args)

    times: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        func(*args)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)  # ms

    times.sort()
    p50_idx = len(times) // 2
    return {
        "name": name,
        "avg_ms": round(sum(times) / len(times), 3),
        "min_ms": round(times[0], 3),
        "max_ms": round(times[-1], 3),
        "p50_ms": round(times[p50_idx], 3),
        "iterations": iterations,
    }


# ----------------------------------------------------------------------
# 测试数据生成
# ----------------------------------------------------------------------

def make_repetitive_data(size: int) -> bytes:
    """重复模式数据（高压缩率，模拟文本/图形）。"""
    pattern = b"The quick brown fox jumps over the lazy dog. "
    return (pattern * (size // len(pattern) + 1))[:size]


def make_random_data(size: int, seed: int = 42) -> bytes:
    """随机数据（低压缩率，模拟加密/已压缩数据）。"""
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(size))


def make_mixed_data(size: int) -> bytes:
    """混合数据（部分重复 + 部分随机，模拟实际游戏数据）。"""
    half = size // 2
    return make_repetitive_data(half) + make_random_data(size - half, seed=99)


# ----------------------------------------------------------------------
# LZ10 基准
# ----------------------------------------------------------------------

def bench_lz10() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for size, label in [(4096, "4KB"), (65536, "64KB"), (262144, "256KB")]:
        for pattern_name, data_fn in [
            ("repetitive", make_repetitive_data),
            ("random", make_random_data),
            ("mixed", make_mixed_data),
        ]:
            data = data_fn(size)
            results.append(bench(f"LZ10 compress {label} ({pattern_name})",
                                 lz10_compress, data))
            compressed = lz10_compress(data)
            results.append(bench(f"LZ10 decompress {label} ({pattern_name})",
                                 lz10_decompress, compressed))
    return results


# ----------------------------------------------------------------------
# BLZ 基准
# ----------------------------------------------------------------------

def bench_blz() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for size, label in [(4096, "4KB"), (65536, "64KB"), (262144, "256KB")]:
        for pattern_name, data_fn in [
            ("repetitive", make_repetitive_data),
            ("mixed", make_mixed_data),
        ]:
            data = data_fn(size)
            compressed = blz_compress(data)
            if compressed is None:
                continue
            results.append(bench(f"BLZ compress {label} ({pattern_name})",
                                 blz_compress, data, iterations=10))
            results.append(bench(f"BLZ decompress {label} ({pattern_name})",
                                 blz_decompress, compressed, iterations=10))
    return results


# ----------------------------------------------------------------------
# CRC16 基准
# ----------------------------------------------------------------------

def bench_crc() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for size, label in [(1024, "1KB"), (65536, "64KB"), (1048576, "1MB")]:
        data = make_repetitive_data(size)
        results.append(bench(f"CRC16 nds {label}", crc16_nds, data, iterations=50))
        results.append(bench(f"CRC16 ndstool {label}", crc16_header, data, iterations=50))
    return results


# ----------------------------------------------------------------------
# GLD 解析/写入基准
# ----------------------------------------------------------------------

def bench_gld(tmp_path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for num_entries, label in [(100, "100 sprites"), (500, "500 sprites"), (2000, "2000 sprites")]:
        pixel_data = bytearray(b"\x00" * (num_entries * 64))
        palette_data = bytearray(struct.pack("<4H", 0x0000, 0x001F, 0x03E0, 0x7C00) * num_entries)
        entries = [
            GldFooterEntry(
                pixels_offset=i * 64, palette_offset=i * 8,
                sprite_format=SPRITE_FORMAT_4,
                crop_width=8, crop_height=8,
                render_width_id=0, render_height_id=0,
                crop_x=0, crop_y=0,
            )
            for i in range(num_entries)
        ]
        header = GldHeader(
            magic=GLD_MAGIC, data_04=2, data_06=FOOTER_TYPE_1,
            total_size=0, data_0c=len(pixel_data), data_10=0,
            pixel_data_size=len(pixel_data), palette_data_size=len(palette_data),
            footer_entry_count=num_entries,
        )
        gld = GldFile("bench", header, pixel_data, palette_data, entries)

        # write_gld 需要 path
        gld_path = tmp_path / f"bench_{num_entries}.GLD"

        def _write():
            write_gld(gld, gld_path)

        def _parse():
            parse_gld(gld_path)

        # 先写一次确保文件存在
        write_gld(gld, gld_path)

        results.append(bench(f"GLD write ({label})", _write))
        results.append(bench(f"GLD parse ({label})", _parse))

    return results


# ----------------------------------------------------------------------
# BBQ 解析基准
# ----------------------------------------------------------------------

def _build_bbq_file(num_strings: int) -> bytes:
    """构造合法 BBQ 文件（仅 Section 7），用于解析基准。"""
    header_size = 24
    n_sections = 1
    entries_offset = header_size

    strings = [f"String_{i:04d}_test".encode("cp932") for i in range(num_strings)]
    num_str = len(strings)
    ptr_table_size = num_str * 4
    pool_data = b"".join(s + b"\x00" for s in strings)
    while len(pool_data) % 4 != 0:
        pool_data += b"\x00"

    sec7_entry_offset = entries_offset
    ptr_table_rel = 20
    pool_rel = ptr_table_rel + ptr_table_size

    # Header: magic(8) + datetime(4) + magic2(4) + entries_offset(4) + n_sections(4) = 24
    magic_full = b"\x2E\x42\x42\x51" + b"\x31\x2E\x30\x30"  # .BBQ1.00
    header = struct.pack("<8sIIII", magic_full, 0x12345678, 1, entries_offset, n_sections)

    # Section 7 entry: sect_id(4) + 4 values(16) = 20 bytes
    sec7_entry = struct.pack("<IIIII", 7, ptr_table_rel, num_str, pool_rel,
                              ptr_table_size + len(pool_data))

    # Pointer table
    pointers = []
    offset = 0
    for s in strings:
        pointers.append(offset)
        offset += len(s) + 1
    ptr_table = struct.pack(f"<{num_str}I", *pointers)

    return header + sec7_entry + ptr_table + pool_data


def bench_bbq(tmp_path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for num_strings, label in [(100, "100 strings"), (1000, "1000 strings"), (5000, "5000 strings")]:
        bbq_data = _build_bbq_file(num_strings)
        bbq_path = tmp_path / f"bench_{num_strings}.BBQ"
        bbq_path.write_bytes(bbq_data)

        def _parse():
            parse_bbq_file(bbq_path, is_scn=False)

        results.append(bench(f"BBQ parse ({label})", _parse))

    return results


# ----------------------------------------------------------------------
# Stage 基准（需要 game_data）
# ----------------------------------------------------------------------

def bench_stages() -> list[dict[str, Any]]:
    """如果 game_data 存在，基准各 Stage 耗时。"""
    from config import EXTRACT_DIR, ORIGINAL_ROM

    results: list[dict[str, Any]] = []

    if not ORIGINAL_ROM.exists():
        print("  [跳过] game_data 不存在，Stage 基准未运行")
        return results

    # Stage 1: unpack（需要原版 ROM）
    try:
        from src.stage1_unpack import main as stage1_main
        def _stage1():
            stage1_main()
        results.append(bench("Stage1 unpack", _stage1, iterations=1, warmup=0))
    except Exception as e:
        print(f"  [跳过] Stage1: {e}")

    # Stage 5: build_rom（需要解包数据）
    try:
        from src.stage5_build_rom import main as stage5_main
        def _stage5():
            stage5_main()
        results.append(bench("Stage5 build_rom", _stage5, iterations=1, warmup=0))
    except Exception as e:
        print(f"  [跳过] Stage5: {e}")

    return results


# ----------------------------------------------------------------------
# 主函数
# ----------------------------------------------------------------------

def print_table(results: list[dict[str, Any]]) -> None:
    """以表格形式打印结果。"""
    if not results:
        print("  （无基准结果）")
        return

    # 计算列宽
    name_w = max(len("Benchmark"), max(len(r["name"]) for r in results))
    print(f"\n  {'Benchmark':<{name_w}}  {'Avg (ms)':>10}  {'Min (ms)':>10}  {'P50 (ms)':>10}  {'Max (ms)':>10}  {'Iter':>6}")
    print(f"  {'-' * name_w}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 6}")
    for r in results:
        print(f"  {r['name']:<{name_w}}  {r['avg_ms']:>10.3f}  {r['min_ms']:>10.3f}  "
              f"{r['p50_ms']:>10.3f}  {r['max_ms']:>10.3f}  {r['iterations']:>6}")


def main() -> None:
    parser = argparse.ArgumentParser(description="IMASDS 性能基准")
    parser.add_argument("--json", action="store_true", help="输出 JSON 到 benchmarks/results/")
    parser.add_argument("--stage", action="store_true", help="同时基准 Stage 函数（需 game_data）")
    args = parser.parse_args()

    print("=" * 70)
    print(" IMASDS Translation Toolkit - 性能基准")
    print(f" 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" Python: {sys.version.split()[0]}")
    print("=" * 70)

    all_results: list[dict[str, Any]] = []

    # 工具函数基准
    print("\n[1/5] LZ10 压缩/解压...")
    r = bench_lz10()
    all_results.extend(r)
    print_table(r)

    print("\n[2/5] BLZ 压缩/解压...")
    r = bench_blz()
    all_results.extend(r)
    print_table(r)

    print("\n[3/5] CRC16 校验...")
    r = bench_crc()
    all_results.extend(r)
    print_table(r)

    # GLD/BBQ 需要 tmp 目录
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        print("\n[4/5] GLD 解析/写入...")
        r = bench_gld(tmp_path)
        all_results.extend(r)
        print_table(r)

        print("\n[5/5] BBQ 解析...")
        r = bench_bbq(tmp_path)
        all_results.extend(r)
        print_table(r)

    # Stage 基准（可选）
    if args.stage:
        print("\n[Stage] 各 Stage 耗时（需要 game_data）...")
        r = bench_stages()
        all_results.extend(r)
        print_table(r)

    # 汇总
    print("\n" + "=" * 70)
    total_avg = sum(r["avg_ms"] for r in all_results)
    print(f" 总计: {len(all_results)} 项基准，平均耗时合计 {total_avg:.1f} ms")
    print("=" * 70)

    # JSON 输出
    if args.json:
        results_dir = _PROJECT_ROOT / "benchmarks" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = results_dir / f"bench_{timestamp}.json"
        output = {
            "timestamp": timestamp,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "results": all_results,
        }
        json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n JSON 结果已保存至: {json_path}")


if __name__ == "__main__":
    main()
