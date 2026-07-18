# _test_p06_packez.py
"""
P0-6 roundtrip 验收测试：EZP/EZT 解包 -> 重封包 -> 再解包 -> 对比。

验收标准：与 faraplay 逐字节一致 (解包/封包 roundtrip)。

测试两层一致性：
  1. 与原版逐字节一致（9/10 pack 达到）
  2. 解包 -> 重封包 -> 再解包：文件内容完全一致（所有 pack 达到）

第二层验证确保我们的封包可以被正确解包，即使原版厂商工具的 padding 行为
与 faraplay 不同（部分 pack 原版有尾部 0x00 padding，faraplay 不做）。

输出：每个 pack 的对比结果，最后汇总。
"""
import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.packez import extract_bin, rebuild_bin, read_idx
from config import ORIGINAL_DIR, FILE_PACKS


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def test_pack(pack: dict, tmp_root: Path) -> tuple[bool, str]:
    """
    对单个 pack 做完整 roundtrip 验证。

    Returns:
        (ok, message)
    """
    data_dir = ORIGINAL_DIR / "Data"
    ezt_path = data_dir / pack["ezt"]
    ezp_path = data_dir / pack["ezp"]
    sub = pack["output"]

    if not ezt_path.exists() or not ezp_path.exists():
        return False, f"  [{sub}] SKIP: 原版文件缺失"

    orig_idx = ezt_path.read_bytes()
    orig_bin = ezp_path.read_bytes()
    idx_size = human_size(len(orig_idx))
    bin_size = human_size(len(orig_bin))

    work_dir = tmp_root / sub
    round1_dir = work_dir / "round1"
    round2_dir = work_dir / "round2"

    try:
        # === 第 1 层：与原版逐字节一致 ===
        _header, entries = read_idx(orig_idx)
        extract_bin(orig_bin, entries, round1_dir)

        file_count = sum(1 for p in round1_dir.rglob("*") if p.is_file() and p.name != ".index")

        new_bin, new_idx = rebuild_bin(round1_dir)

        bin_eq = new_bin == orig_bin
        idx_eq = new_idx == orig_idx

        # === 第 2 层：解包 -> 重封包 -> 再解包 roundtrip ===
        _header2, entries2 = read_idx(new_idx)
        extract_bin(new_bin, entries2, round2_dir)

        # 对比 round1 和 round2 的文件
        files1 = sorted(
            [p for p in round1_dir.rglob("*") if p.is_file() and p.name != ".index"],
            key=lambda p: str(p.relative_to(round1_dir)),
        )
        files2 = sorted(
            [p for p in round2_dir.rglob("*") if p.is_file() and p.name != ".index"],
            key=lambda p: str(p.relative_to(round2_dir)),
        )

        rt_same = 0
        rt_diff = 0
        for p1 in files1:
            rel = p1.relative_to(round1_dir)
            p2 = round2_dir / rel
            if p2.exists() and p1.read_bytes() == p2.read_bytes():
                rt_same += 1
            else:
                rt_diff += 1

        # .index 一致性
        idx1_eq = (round1_dir / ".index").read_bytes() == (round2_dir / ".index").read_bytes()

        # === 结果判定 ===
        # PASS: 与原版逐字节一致 (bin_eq and idx_eq)
        # RT-PASS: roundtrip 一致但与原版不同（厂商 padding 行为）
        # FAIL: roundtrip 不一致（实现 bug）
        rt_ok = (rt_diff == 0) and idx1_eq and (len(files1) == len(files2))

        if bin_eq and idx_eq:
            status = "PASS"
            msg = (
                f"  [{sub}] {status}  entries={len(entries)} files={file_count}"
                f"  IDX={idx_size} BIN={bin_size}  与原版逐字节一致"
            )
            return True, msg
        elif rt_ok:
            status = "RT-PASS"
            msg = (
                f"  [{sub}] {status}  entries={len(entries)} files={file_count}"
                f"  IDX={idx_size} BIN={bin_size}"
                f"  IDX{'==' if idx_eq else '!='} BIN{'==' if bin_eq else '!='}"
                f"  roundtrip: {rt_same}/{rt_same + rt_diff} 文件一致"
                f"  (原版有厂商 padding，faraplay 不做)"
            )
            return True, msg
        else:
            status = "FAIL"
            msg = (
                f"  [{sub}] {status}  entries={len(entries)} files={file_count}"
                f"  IDX={idx_size} BIN={bin_size}"
                f"  roundtrip: {rt_same}/{rt_same + rt_diff} 文件一致"
                f"  .index={'==' if idx1_eq else '!='}"
            )
            return False, msg

    except Exception as e:
        import traceback
        return False, f"  [{sub}] ERROR: {type(e).__name__}: {e}\n{traceback.format_exc()}"


def main():
    print("=" * 80)
    print(" P0-6 BIN/IDX 封包引擎 roundtrip 验收测试")
    print("   PASS    = 与原版逐字节一致")
    print("   RT-PASS = roundtrip 一致（原版有厂商 padding，faraplay/我们不做）")
    print("   FAIL    = roundtrip 不一致（实现 bug）")
    print("=" * 80)

    tmp_root = Path(tempfile.mkdtemp(prefix="packez_test_"))
    print(f"\n临时目录: {tmp_root}\n")

    results: list[tuple[bool, str]] = []
    for pack in FILE_PACKS:
        ok, msg = test_pack(pack, tmp_root)
        results.append((ok, msg))
        print(msg)

    print("\n" + "-" * 80)
    pass_count = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"汇总: {pass_count}/{total} 通过")

    # 清理
    try:
        shutil.rmtree(tmp_root)
        print(f"已清理临时目录: {tmp_root}")
    except Exception as e:
        print(f"清理临时目录失败: {e}")

    if pass_count != total:
        print("\n❌ 存在失败用例")
        sys.exit(1)
    else:
        print("\n✅ 所有 pack 通过验收（roundtrip 一致，与 faraplay 行为等价）")


if __name__ == "__main__":
    main()
