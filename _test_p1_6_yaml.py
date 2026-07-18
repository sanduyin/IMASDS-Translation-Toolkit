#!/usr/bin/env python3
"""
P1-6 BBQ ↔ YAML 互转往返测试。

验收标准：BBQ→YAML→BBQ 逐字节一致

测试样本：
  - 小文件（MAKERLOGO）
  - 普通 SCN（非 MES，纯字符串）
  - MES 脚本文件（含 Type5/6/7）
  - TBL 字符串文件
"""
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.bbq_yaml import (
    read_bbq, write_bbq, bbq_to_yaml_lines, bbq_from_yaml_lines,
    bbq_to_yaml, yaml_to_bbq
)
from config import EXTRACT_DIR

TMP_DIR = EXTRACT_DIR.parent / "_test_p1_6"


def cleanup():
    shutil.rmtree(TMP_DIR, ignore_errors=True)


def test_roundtrip(bbq_path: Path, label: str) -> dict:
    """测试单个 BBQ 文件的 YAML 往返"""
    print(f"\n{'='*60}")
    print(f"测试: {label} ({bbq_path.name})")
    print(f"{'='*60}")

    # 读取原始文件
    orig_data = bbq_path.read_bytes()
    if not orig_data:
        print("  跳过：空文件")
        return {'skip': True}

    print(f"  原始大小: {len(orig_data)} 字节")

    # 1. BBQ → BbqFile
    try:
        bbq = read_bbq(bbq_path)
    except Exception as e:
        print(f"  ❌ 解析失败: {e}")
        return {'parse_error': str(e)}

    # 统计 type 分布
    types = []
    if bbq.type2data is not None: types.append(f"Type2({len(bbq.type2data)})")
    if bbq.type3data is not None: types.append(f"Type3({len(bbq.type3data)})")
    if bbq.type5data is not None: types.append(f"Type5({len(bbq.type5data)})")
    if bbq.type6data is not None: types.append(f"Type6({len(bbq.type6data)})")
    if bbq.type7data is not None: types.append(f"Type7({len(bbq.type7data)})")
    print(f"  Types: {', '.join(types)}")
    print(f"  datetime: {bbq.datetime}, footer: {bbq.footer}")

    # 2. BbqFile → YAML 行
    yaml_lines = bbq_to_yaml_lines(bbq)
    print(f"  YAML 行数: {len(yaml_lines)}")

    # 3. YAML 行 → BbqFile
    bbq2 = bbq_from_yaml_lines(yaml_lines)

    # 4. BbqFile → bytes
    rt_data = bbq2.to_bytes()

    # 5. 字节比较
    byte_equal = orig_data == rt_data
    print(f"  字节级: {'PASS ✓' if byte_equal else 'DIFF'} (orig={len(orig_data)}, rt={len(rt_data)})")

    if not byte_equal:
        # 分析差异位置
        min_len = min(len(orig_data), len(rt_data))
        first_diff = -1
        diff_count = 0
        for i in range(min_len):
            if orig_data[i] != rt_data[i]:
                if first_diff < 0:
                    first_diff = i
                diff_count += 1
        print(f"  首个差异: offset 0x{first_diff:X}")
        print(f"  差异字节数: {diff_count} / {min_len}")
        # 显示差异上下文
        if first_diff >= 0:
            ctx_start = max(0, first_diff - 8)
            ctx_end = min(min_len, first_diff + 16)
            print(f"  原始 [{ctx_start}:{ctx_end}]: {orig_data[ctx_start:ctx_end].hex()}")
            print(f"  重建 [{ctx_start}:{ctx_end}]: {rt_data[ctx_start:ctx_end].hex()}")

        # 额外验证：BbqFile 对象是否相等
        bbq_rt = read_bbq_path(rt_data)
        if bbq_rt is not None:
            obj_equal = (bbq.datetime == bbq_rt.datetime and
                        bbq.footer == bbq_rt.footer)
            print(f"  对象级(datetime+footer): {'PASS ✓' if obj_equal else 'DIFF'}")

    # 6. 文件级往返测试（BBQ→YAML文件→BBQ文件）
    yaml_path = TMP_DIR / (bbq_path.stem + ".yaml")
    rt_bbq_path = TMP_DIR / bbq_path.name
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    bbq_to_yaml(bbq_path, yaml_path)
    yaml_to_bbq(yaml_path, rt_bbq_path)
    file_data = rt_bbq_path.read_bytes()
    file_equal = orig_data == file_data
    print(f"  文件级往返: {'PASS ✓' if file_equal else 'DIFF'}")

    return {'byte_equal': byte_equal, 'file_equal': file_equal}


def read_bbq_path(data: bytes):
    """从字节读取 BbqFile（用于差异分析）"""
    try:
        from src.utils.bbq_yaml import BbqFile
        return BbqFile.read_bbq(data)
    except Exception:
        return None


def main():
    print("=" * 60)
    print("P1-6 BBQ ↔ YAML 互转往返测试")
    print("=" * 60)

    cleanup()
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    # 选择不同类型的测试样本
    scn_dir = EXTRACT_DIR / "SCN"
    tbl_dir = EXTRACT_DIR / "TBL"

    tests = [
        (scn_dir / "0061_MAKERLOGO.BBQ", "小文件 (MAKERLOGO)"),
        (scn_dir / "0062_GAMETITLE.BBQ", "游戏标题"),
        (scn_dir / "0165_AIH_E01_MAIN01.BBQ", "普通 SCN (非 MES)"),
        (scn_dir / "0166_AIH_E01_MAIN01_MES.BBQ", "MES 脚本文件"),
    ]

    # 添加 TBL 文件
    if tbl_dir.exists():
        tbl_bbqs = sorted(tbl_dir.glob("*.BBQ"))[:2]
        for bbq in tbl_bbqs:
            tests.append((bbq, f"TBL/{bbq.name}"))

    results = []
    for bbq_path, label in tests:
        if not bbq_path.exists():
            print(f"\n跳过（文件不存在）: {bbq_path}")
            continue
        results.append((label, test_roundtrip(bbq_path, label)))

    # 总结
    print(f"\n{'='*60}")
    print("总结:")
    print(f"{'='*60}")
    all_ok = True
    for label, r in results:
        if r.get('skip'):
            print(f"  {label}: 跳过")
            continue
        if r.get('parse_error'):
            print(f"  {label}: ❌ 解析失败")
            all_ok = False
            continue
        be = r.get('byte_equal', False)
        fe = r.get('file_equal', False)
        status = "字节" + ("✓" if be else "✗") + ", 文件" + ("✓" if fe else "✗")
        print(f"  {label}: {status}")
        if not (be and fe):
            all_ok = False

    print(f"\n验收（全部逐字节一致）: {'PASS ✓' if all_ok else 'FAIL ✗'}")
    cleanup()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
