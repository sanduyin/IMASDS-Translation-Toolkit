# src/utils/mapping_backup.py
"""
font_mapping.json 备份与回滚机制（P3-2）。

功能：
  - 每次 build_font 前自动备份旧 mapping
  - 提供 rollback 命令一键恢复上一版
  - 保留最近 N 个备份（默认 20），超出自动清理最旧

备份目录：workspace/font_mapping_backups/
备份命名：font_mapping_YYYYMMDD_HHMMSS.json
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from config import MAPPING_FILE, WORKSPACE_DIR

BACKUP_DIR = WORKSPACE_DIR / "font_mapping_backups"
MAX_BACKUPS = 20  # 保留最近 20 个备份


def _ensure_backup_dir() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def backup_mapping(reason: str = "auto") -> Path | None:
    """备份当前 font_mapping.json。

    Args:
        reason: 备份原因（auto/manual/rollback），用于日志，不影响文件名。

    Returns:
        备份文件路径；若原文件不存在则返回 None。
    """
    if not MAPPING_FILE.exists():
        return None

    _ensure_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = BACKUP_DIR / f"font_mapping_{timestamp}.json"
    # 同秒内多次备份时追加序号，避免覆盖
    if bak_path.exists():
        seq = 1
        while bak_path.exists():
            bak_path = BACKUP_DIR / f"font_mapping_{timestamp}_{seq}.json"
            seq += 1
    shutil.copy2(MAPPING_FILE, bak_path)

    # 清理超出 MAX_BACKUPS 的旧备份
    _prune_old_backups()

    return bak_path


def _prune_old_backups() -> int:
    """删除超出 MAX_BACKUPS 的最旧备份，返回删除数量。"""
    if not BACKUP_DIR.exists():
        return 0
    backups = sorted(
        [p for p in BACKUP_DIR.iterdir()
         if p.is_file() and p.name.startswith("font_mapping_") and p.name.endswith(".json")],
        key=lambda p: p.stat().st_mtime,
    )
    deleted = 0
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        oldest.unlink()
        deleted += 1
    return deleted


def list_backups() -> list[dict[str, Any]]:
    """列出所有备份，按时间倒序（最新在前）。"""
    if not BACKUP_DIR.exists():
        return []
    backups: list[dict[str, Any]] = []
    for p in BACKUP_DIR.iterdir():
        if not (p.is_file() and p.name.startswith("font_mapping_") and p.name.endswith(".json")):
            continue
        stat = p.stat()
        # 从文件名提取时间戳（支持 font_mapping_YYYYMMDD_HHMMSS / _N / _prerollback 后缀）
        stem = p.stem  # 去掉 .json
        ts_str = stem.replace("font_mapping_", "")
        # 去掉可能的 _N 序号或 _prerollback 后缀，只保留 YYYYMMDD_HHMMSS
        parts = ts_str.split("_")
        if len(parts) >= 2:
            ts_core = "_".join(parts[:2])  # YYYYMMDD_HHMMSS
        else:
            ts_core = ts_str
        try:
            ts = datetime.strptime(ts_core, "%Y%m%d_%H%M%S")
        except ValueError:
            ts = datetime.fromtimestamp(stat.st_mtime)
        backups.append({
            "path": p,
            "name": p.name,
            "timestamp": ts,
            "size": stat.st_size,
            "entries": _count_entries(p),
        })
    backups.sort(key=lambda b: b["timestamp"], reverse=True)
    return backups


def _count_entries(path: Path) -> int:
    """统计备份文件中的 mapping 条目数。"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return len(json.load(f))
    except Exception:
        return -1


def rollback(steps: int = 1) -> Path | None:
    """回滚到前 N 版备份。

    Args:
        steps: 回滚步数（1=上一版，2=上上版，...）

    Returns:
        恢复后的 mapping 文件路径；若备份不足则返回 None。

    Raises:
        FileNotFoundError: 没有任何备份可用。
    """
    backups = list_backups()
    if not backups:
        raise FileNotFoundError("没有任何备份可用于回滚")

    if steps < 1 or steps > len(backups):
        raise FileNotFoundError(
            f"回滚步数 {steps} 无效，当前只有 {len(backups)} 个备份"
        )

    # 回滚前先备份当前版本（标记为 prerollback，便于撤销回滚）
    if MAPPING_FILE.exists():
        _ensure_backup_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_rollback_bak = BACKUP_DIR / f"font_mapping_{timestamp}_prerollback.json"
        # 同秒内多次回滚时追加序号，避免覆盖
        if pre_rollback_bak.exists():
            seq = 1
            while pre_rollback_bak.exists():
                pre_rollback_bak = BACKUP_DIR / f"font_mapping_{timestamp}_prerollback_{seq}.json"
                seq += 1
        shutil.copy2(MAPPING_FILE, pre_rollback_bak)

    # 目标备份（按时间倒序，steps=1 对应最新备份）
    target = backups[steps - 1]["path"]
    shutil.copy2(target, MAPPING_FILE)
    return MAPPING_FILE


def print_backup_history(limit: int = 10) -> None:
    """打印备份历史（最近 limit 条）。"""
    backups = list_backups()
    if not backups:
        print("  （无备份历史）")
        return
    print(f"  备份目录: {BACKUP_DIR}")
    print(f"  共 {len(backups)} 个备份，显示最近 {min(limit, len(backups))} 个：")
    for i, b in enumerate(backups[:limit], 1):
        ts_str = b["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        print(f"    [{i}] {b['name']}  ({ts_str}, {b['entries']} 条, {b['size']} 字节)")


def main() -> None:
    """命令行入口：python -m src.utils.mapping_backup [list|rollback|backup]"""
    import sys
    if len(sys.argv) < 2:
        print("用法: python -m src.utils.mapping_backup <list|rollback|backup> [steps]")
        print("  list     - 列出所有备份")
        print("  rollback - 回滚到上一版（可指定步数，默认 1）")
        print("  backup   - 手动创建备份")
        return

    cmd = sys.argv[1]
    if cmd == "list":
        print("=" * 60)
        print("font_mapping.json 备份历史")
        print("=" * 60)
        print_backup_history()
    elif cmd == "rollback":
        steps = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        try:
            restored = rollback(steps)
            print(f"✅ 已回滚 {steps} 版，恢复至: {restored}")
            # 显示恢复后的条目数
            if restored is not None:
                with open(restored, 'r', encoding='utf-8') as f:
                    count = len(json.load(f))
                print(f"   当前 mapping: {count} 条")
        except FileNotFoundError as e:
            print(f"❌ {e}")
    elif cmd == "backup":
        bak = backup_mapping(reason="manual")
        if bak:
            print(f"✅ 已手动备份至: {bak}")
        else:
            print(f"❌ {MAPPING_FILE} 不存在，无法备份")
    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
