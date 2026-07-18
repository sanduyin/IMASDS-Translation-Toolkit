#!/usr/bin/env python3
"""
P2-6 ARM9/Overlay CSV 注入测试。

验收标准（对齐 faraplay arm9overlay.rs）：
  1. 先清空再写入：注入后 [offset, offset+max_bytes) 区域为 [译文+NUL][零填充]
  2. Max_Bytes 列使用：溢出检查基于 CSV 的 Max_Bytes，不扫描二进制
  3. 重复注入幂等：同一 CSV 注入两次结果一致（先清空确保无上次残留）
  4. 空译文跳过
  5. 恰好填满：译文+NUL == max_bytes 时成功

测试方法：monkey-patch EXTRACT_DIR/PATCHED_DIR，调用真实的 process_arm9_overlays。
"""
import os
import sys
import gc
import shutil
import tempfile
from pathlib import Path


def _safe_rmtree(path):
    """Windows 安全的 rmtree：跳过清理避免文件锁阻塞（OS 最终会回收 tempdir）。"""
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.io.csv_handler import write_arm9_csv


def make_synthetic_arm9():
    """创建合成 arm9.bin，包含 3 个已知字符串。

    布局：
      0x00: "Hello"   (5 bytes) + NUL  → max_bytes=5
      0x10: "World"   (5 bytes) + NUL  → max_bytes=5
      0x20: "Test123" (7 bytes) + NUL  → max_bytes=7
    其余区域为 0。
    """
    data = bytearray(0x100)
    data[0:5] = b"Hello"
    data[0x10:0x15] = b"World"
    data[0x20:0x27] = b"Test123"
    return bytes(data)


def make_ascii_char_map():
    """ASCII 可打印字符自映射（0x20~0x7E → 自身）"""
    return {chr(c): c for c in range(0x20, 0x7F)}


def run_injection(csv_path, char_map, tmpdir):
    """Monkey-patch 配置路径，调用真实的 process_arm9_overlays"""
    extract_dir = tmpdir / "extracted"
    patched_dir = tmpdir / "patched"
    arm9_src = extract_dir / "ARM9"
    arm9_src.mkdir(parents=True, exist_ok=True)
    (arm9_src / "arm9.bin").write_bytes(make_synthetic_arm9())

    import src.stage4_inject_text as stage4
    old_extract, old_patched = stage4.EXTRACT_DIR, stage4.PATCHED_DIR
    stage4.EXTRACT_DIR = extract_dir
    stage4.PATCHED_DIR = patched_dir
    try:
        stage4.process_arm9_overlays(csv_path, char_map)
    finally:
        stage4.EXTRACT_DIR = old_extract
        stage4.PATCHED_DIR = old_patched

    return patched_dir / "PRG_CHS_PATCHED" / "arm9.bin"


def test_clear_then_write():
    """1. 先清空再写入 — 注入后区域为 [译文+NUL][零填充]"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        csv_path = tmpdir / "test.csv"
        write_arm9_csv(csv_path, [
            {"Original_Text": "Hello", "Translated_Text": "Hi", "File": "arm9.bin",
             "Text_Offset": "0x0", "Max_Bytes": 5},
        ])
        patched = run_injection(csv_path, make_ascii_char_map(), tmpdir)
        data = patched.read_bytes()
        # "Hi" → text_to_bytes → b"Hi\x00" (3 bytes), max_bytes=5
        # 期望: [H, i, NUL, 0, 0] = 先清5字节0，再写3字节译文+NUL
        assert data[0:5] == b"Hi\x00\x00\x00", \
            f"先清空再写入失败: {data[0:5].hex()} != 4869000000"
        # 验证 offset 5~15 仍为 0（未被触及）
        assert data[5:16] == b'\x00' * 11
        print("  ✓ [offset, offset+max_bytes) = [译文+NUL][零填充]")
        return True
    finally:
        _safe_rmtree(tmpdir)


def test_reinject_idempotent():
    """2. 重复注入幂等 — 先清空策略确保两次注入结果一致"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        csv_path = tmpdir / "test.csv"
        write_arm9_csv(csv_path, [
            {"Original_Text": "Hello", "Translated_Text": "Hi", "File": "arm9.bin",
             "Text_Offset": "0x0", "Max_Bytes": 5},
            {"Original_Text": "World", "Translated_Text": "Yo", "File": "arm9.bin",
             "Text_Offset": "0x10", "Max_Bytes": 5},
        ])
        char_map = make_ascii_char_map()

        # 第一次注入
        patched1 = run_injection(csv_path, char_map, tmpdir)
        data1 = patched1.read_bytes()

        # 第二次注入：用第一次的输出作为源（需正确设置 EXTRACT_DIR/ARM9 结构）
        extract_dir2 = tmpdir / "extracted2"
        arm9_src2 = extract_dir2 / "ARM9"
        arm9_src2.mkdir(parents=True, exist_ok=True)
        shutil.copy2(patched1, arm9_src2 / "arm9.bin")

        import src.stage4_inject_text as stage4
        old_extract, old_patched = stage4.EXTRACT_DIR, stage4.PATCHED_DIR
        stage4.EXTRACT_DIR = extract_dir2
        stage4.PATCHED_DIR = tmpdir / "patched2"
        try:
            stage4.process_arm9_overlays(csv_path, char_map)
        finally:
            stage4.EXTRACT_DIR = old_extract
            stage4.PATCHED_DIR = old_patched

        patched2 = tmpdir / "patched2" / "PRG_CHS_PATCHED" / "arm9.bin"
        data2 = patched2.read_bytes()

        assert data1 == data2, \
            f"重复注入不幂等!\n第一次: {data1[:32].hex()}\n第二次: {data2[:32].hex()}"
        print("  ✓ 两次注入结果逐字节一致（先清空消除上次残留）")
        return True
    finally:
        _safe_rmtree(tmpdir)


def test_max_bytes_overflow():
    """3. Max_Bytes 溢出 — 译文+NUL > max_bytes 时跳过"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        csv_path = tmpdir / "test.csv"
        # "World!" = 6 bytes + NUL = 7 > max_bytes(5) → 溢出跳过
        write_arm9_csv(csv_path, [
            {"Original_Text": "Hello", "Translated_Text": "World!", "File": "arm9.bin",
             "Text_Offset": "0x0", "Max_Bytes": 5},
        ])
        patched = run_injection(csv_path, make_ascii_char_map(), tmpdir)
        data = patched.read_bytes()
        # 溢出检查在清空之前，所以原数据保留不变
        assert data[0:5] == b"Hello", \
            f"溢出应跳过，原数据应保留: {data[0:5]}"
        print("  ✓ 译文+NUL > max_bytes 时跳过，原数据保留")
        return True
    finally:
        _safe_rmtree(tmpdir)


def test_empty_translation_skip():
    """4. 空译文跳过"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        csv_path = tmpdir / "test.csv"
        write_arm9_csv(csv_path, [
            {"Original_Text": "Hello", "Translated_Text": "", "File": "arm9.bin",
             "Text_Offset": "0x0", "Max_Bytes": 5},
        ])
        # read_translation_table(is_arm9=True) 会过滤空译文行
        # 所以 process_arm9_overlays 根本不会收到这行
        patched = run_injection(csv_path, make_ascii_char_map(), tmpdir)
        data = patched.read_bytes()
        assert data[0:5] == b"Hello", \
            f"空译文应跳过: {data[0:5]}"
        print("  ✓ Translated_Text 为空时跳过注入")
        return True
    finally:
        _safe_rmtree(tmpdir)


def test_exact_fit():
    """5. 恰好填满 — 译文+NUL == max_bytes"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        csv_path = tmpdir / "test.csv"
        # "Test" = 4 bytes + NUL = 5 == max_bytes(5) → 刚好
        write_arm9_csv(csv_path, [
            {"Original_Text": "Hello", "Translated_Text": "Test", "File": "arm9.bin",
             "Text_Offset": "0x0", "Max_Bytes": 5},
        ])
        patched = run_injection(csv_path, make_ascii_char_map(), tmpdir)
        data = patched.read_bytes()
        assert data[0:5] == b"Test\x00", \
            f"恰好填满失败: {data[0:5]}"
        print("  ✓ 译文+NUL == max_bytes 时成功注入")
        return True
    finally:
        _safe_rmtree(tmpdir)


def test_multiple_files_bucketing():
    """6. 按文件分桶 — 多文件 CSV 正确路由到各自文件"""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        extract_dir = tmpdir / "extracted"
        patched_dir = tmpdir / "patched"
        arm9_src = extract_dir / "ARM9"
        arm9_src.mkdir(parents=True, exist_ok=True)

        # 创建两个文件
        arm9_data = bytearray(0x100)
        arm9_data[0:5] = b"Hello"
        (arm9_src / "arm9.bin").write_bytes(bytes(arm9_data))

        ovl_data = bytearray(0x100)
        ovl_data[0:5] = b"Ovl01"
        (arm9_src / "overlay0.bin").write_bytes(bytes(ovl_data))

        csv_path = tmpdir / "test.csv"
        write_arm9_csv(csv_path, [
            {"Original_Text": "Hello", "Translated_Text": "Hi", "File": "arm9.bin",
             "Text_Offset": "0x0", "Max_Bytes": 5},
            {"Original_Text": "Ovl01", "Translated_Text": "Go", "File": "overlay0.bin",
             "Text_Offset": "0x0", "Max_Bytes": 5},
        ])

        import src.stage4_inject_text as stage4
        old_extract, old_patched = stage4.EXTRACT_DIR, stage4.PATCHED_DIR
        stage4.EXTRACT_DIR = extract_dir
        stage4.PATCHED_DIR = patched_dir
        try:
            stage4.process_arm9_overlays(csv_path, make_ascii_char_map())
        finally:
            stage4.EXTRACT_DIR = old_extract
            stage4.PATCHED_DIR = old_patched

        arm9_out = (patched_dir / "PRG_CHS_PATCHED" / "arm9.bin").read_bytes()
        ovl_out = (patched_dir / "PRG_CHS_PATCHED" / "overlay0.bin").read_bytes()
        assert arm9_out[0:5] == b"Hi\x00\x00\x00", f"arm9 注入错误: {arm9_out[0:5]}"
        assert ovl_out[0:5] == b"Go\x00\x00\x00", f"overlay 注入错误: {ovl_out[0:5]}"
        print("  ✓ arm9.bin 和 overlay0.bin 各自正确注入")
        return True
    finally:
        _safe_rmtree(tmpdir)


def main():
    print("=" * 60)
    print("P2-6 ARM9/Overlay CSV 注入测试")
    print("=" * 60)

    tests = [
        ("先清空再写入", test_clear_then_write),
        ("重复注入幂等", test_reinject_idempotent),
        ("Max_Bytes 溢出跳过", test_max_bytes_overflow),
        ("空译文跳过", test_empty_translation_skip),
        ("恰好填满", test_exact_fit),
        ("按文件分桶", test_multiple_files_bucketing),
    ]

    all_ok = True
    for name, fn in tests:
        print(f"\n--- {name} ---")
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
