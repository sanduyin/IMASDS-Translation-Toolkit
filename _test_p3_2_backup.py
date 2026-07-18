#!/usr/bin/env python3
"""
P3-2 font_mapping.json 备份与回滚机制测试。

验收标准（对齐 RUST_TO_PYTHON_PLAN.md P3-2）：
  1. backup_mapping：创建带时间戳的备份文件
  2. list_backups：按时间倒序列出所有备份
  3. rollback：回滚到上一版，当前 mapping 被备份为 prerollback
  4. _prune_old_backups：超出 MAX_BACKUPS 时自动清理最旧
  5. 集成测试：build_font_mapping 调用前自动备份
"""
import os
import sys
import json
import shutil
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import MAPPING_FILE, WORKSPACE_DIR
from src.utils.mapping_backup import (
    backup_mapping, list_backups, rollback, _prune_old_backups,
    BACKUP_DIR, MAX_BACKUPS, print_backup_history
)


def _clear_backups():
    """清空备份目录（测试前置）"""
    if BACKUP_DIR.exists():
        for p in BACKUP_DIR.iterdir():
            if p.is_file():
                p.unlink()


def _write_mapping(entries: dict):
    """写入测试 mapping"""
    with open(MAPPING_FILE, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _read_mapping() -> dict:
    with open(MAPPING_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def test_backup_creates_file():
    """1. backup_mapping 创建带时间戳的备份文件"""
    print("\n--- 1. backup_mapping 创建备份 ---")
    _clear_backups()
    _write_mapping({"a": 1, "b": 2})
    bak = backup_mapping(reason="test")
    assert bak is not None, "backup_mapping 返回 None"
    assert bak.exists(), f"备份文件不存在: {bak}"
    assert bak.name.startswith("font_mapping_"), f"备份命名错误: {bak.name}"
    assert bak.name.endswith(".json"), f"备份扩展名错误: {bak.name}"
    # 验证备份内容与原文件一致
    with open(bak, 'r', encoding='utf-8') as f:
        bak_content = json.load(f)
    assert bak_content == {"a": 1, "b": 2}, "备份内容与原文件不一致"
    print(f"  ✓ 备份创建成功: {bak.name}")
    return True


def test_list_backups_sorted():
    """2. list_backups 按时间倒序列出"""
    print("\n--- 2. list_backups 排序 ---")
    _clear_backups()
    _write_mapping({"v1": 1})
    backup_mapping()
    time.sleep(1.1)  # 确保时间戳不同
    _write_mapping({"v2": 2})
    backup_mapping()
    time.sleep(1.1)
    _write_mapping({"v3": 3})
    backup_mapping()

    backups = list_backups()
    assert len(backups) == 3, f"应有 3 个备份，实际 {len(backups)}"
    # 验证按时间倒序（最新在前）
    assert backups[0]["timestamp"] >= backups[1]["timestamp"] >= backups[2]["timestamp"], \
        "备份未按时间倒序"
    # 验证条目数正确
    assert backups[0]["entries"] == 1, f"最新备份条目数错误: {backups[0]['entries']}"
    print(f"  ✓ 3 个备份按时间倒序排列正确")
    return True


def test_rollback_restores_previous():
    """3. rollback 回滚到上一版，当前 mapping 被备份为 prerollback"""
    print("\n--- 3. rollback 回滚 ---")
    _clear_backups()
    # v1 → 备份 → v2 → 备份 → v3（当前）
    _write_mapping({"version": 1})
    backup_mapping()
    time.sleep(1.1)
    _write_mapping({"version": 2})
    backup_mapping()
    time.sleep(1.1)
    _write_mapping({"version": 3})  # 当前版本
    assert _read_mapping() == {"version": 3}

    # 回滚 1 步：应恢复到 v2（最新备份）
    restored = rollback(1)
    assert restored is not None, "rollback 返回 None"
    assert _read_mapping() == {"version": 2}, \
        f"回滚后应为 v2，实际: {_read_mapping()}"

    # 验证回滚前当前版本（v3）被备份为 prerollback
    backups = list_backups()
    prerollback = [b for b in backups if "_prerollback" in b["name"]]
    assert len(prerollback) == 1, f"应有 1 个 prerollback 备份，实际 {len(prerollback)}"
    print(f"  ✓ 回滚 1 步：v3 → v2，v3 已备份为 prerollback")
    return True


def test_rollback_multiple_steps():
    """4. rollback 多步回滚"""
    print("\n--- 4. rollback 多步 ---")
    _clear_backups()
    _write_mapping({"v": 1})
    backup_mapping()
    time.sleep(1.1)
    _write_mapping({"v": 2})
    backup_mapping()
    time.sleep(1.1)
    _write_mapping({"v": 3})
    backup_mapping()
    time.sleep(1.1)
    _write_mapping({"v": 4})  # 当前

    # 回滚 3 步：应恢复到 v1
    rollback(3)
    assert _read_mapping() == {"v": 1}, f"回滚 3 步应为 v1，实际: {_read_mapping()}"
    print(f"  ✓ 回滚 3 步：v4 → v1")
    return True


def test_prune_old_backups():
    """5. _prune_old_backups 超出 MAX_BACKUPS 时清理最旧"""
    print("\n--- 5. _prune_old_backups 清理 ---")
    _clear_backups()
    # 创建 MAX_BACKUPS + 5 个备份
    for i in range(MAX_BACKUPS + 5):
        _write_mapping({"i": i})
        backup_mapping()
        time.sleep(0.2)  # 确保时间戳不同

    backups = list_backups()
    assert len(backups) <= MAX_BACKUPS, \
        f"备份数量应 <= {MAX_BACKUPS}，实际 {len(backups)}"
    print(f"  ✓ 创建 {MAX_BACKUPS + 5} 个备份后自动清理至 {len(backups)} 个")
    return True


def test_backup_nonexistent_mapping():
    """6. backup_mapping 对不存在的 mapping 返回 None"""
    print("\n--- 6. 不存在的 mapping ---")
    _clear_backups()
    # 临时移走 MAPPING_FILE
    bak_path = None
    if MAPPING_FILE.exists():
        bak_path = MAPPING_FILE.with_suffix('.json.tmpbak')
        shutil.move(str(MAPPING_FILE), str(bak_path))
    try:
        result = backup_mapping()
        assert result is None, "mapping 不存在时应返回 None"
        print(f"  ✓ mapping 不存在时 backup_mapping 返回 None")
        return True
    finally:
        if bak_path and bak_path.exists():
            shutil.move(str(bak_path), str(MAPPING_FILE))


def test_rollback_no_backups():
    """7. rollback 无备份时报错"""
    print("\n--- 7. 无备份时 rollback ---")
    _clear_backups()
    _write_mapping({"x": 1})
    try:
        rollback(1)
        assert False, "无备份时应抛出 FileNotFoundError"
    except FileNotFoundError as e:
        print(f"  ✓ 无备份时 rollback 抛出: {e}")
        return True


def main():
    print("=" * 60)
    print("P3-2 font_mapping.json 备份与回滚机制测试")
    print("=" * 60)

    # 保存原始 mapping，测试后还原
    orig_bak = None
    if MAPPING_FILE.exists():
        orig_bak = MAPPING_FILE.with_suffix('.json.origbak')
        shutil.copy2(MAPPING_FILE, orig_bak)

    try:
        tests = [
            ("backup_mapping 创建备份", test_backup_creates_file),
            ("list_backups 排序", test_list_backups_sorted),
            ("rollback 回滚", test_rollback_restores_previous),
            ("rollback 多步", test_rollback_multiple_steps),
            ("_prune_old_backups 清理", test_prune_old_backups),
            ("不存在的 mapping", test_backup_nonexistent_mapping),
            ("无备份时 rollback", test_rollback_no_backups),
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
    finally:
        # 还原原始 mapping
        if orig_bak and orig_bak.exists():
            shutil.copy2(orig_bak, MAPPING_FILE)
            orig_bak.unlink()
        _clear_backups()


if __name__ == "__main__":
    sys.exit(main())
