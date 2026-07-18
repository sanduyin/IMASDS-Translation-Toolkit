# tests/utils/test_mapping_backup.py
"""mapping_backup 模块单元测试。

通过 monkeypatch 替换模块级常量 MAPPING_FILE / BACKUP_DIR / MAX_BACKUPS，
避免污染真实的 workspace/font_mapping.json。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.utils import mapping_backup


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离测试环境：替换 MAPPING_FILE / BACKUP_DIR 到 tmp_path。"""
    mapping_file = tmp_path / "font_mapping.json"
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(mapping_backup, "MAPPING_FILE", mapping_file)
    monkeypatch.setattr(mapping_backup, "BACKUP_DIR", backup_dir)
    # 同时 patch 模块内引用的 config.MAPPING_FILE（backup_mapping 读取的是模块顶层 import）
    monkeypatch.setattr(mapping_backup, "BACKUP_DIR", backup_dir)

    return tmp_path


def _write_mapping(path: Path, data: dict[str, int]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


# ----------------------------------------------------------------------
# backup_mapping
# ----------------------------------------------------------------------

class TestBackupMapping:
    def test_backup_returns_none_if_no_source(self, isolated_env: Path) -> None:
        # MAPPING_FILE 不存在时应返回 None
        assert mapping_backup.backup_mapping() is None

    def test_backup_creates_backup_file(self, isolated_env: Path) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"A": 0x41, "B": 0x42})

        result = mapping_backup.backup_mapping(reason="test")
        assert result is not None
        assert result.exists()
        # 备份内容应与原文一致
        assert json.loads(result.read_text(encoding="utf-8")) == {"A": 0x41, "B": 0x42}

    def test_backup_creates_backup_dir(self, isolated_env: Path) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"X": 1})
        mapping_backup.backup_mapping()
        assert mapping_backup.BACKUP_DIR.exists()

    def test_backup_same_second_appends_sequence(self, isolated_env: Path) -> None:
        # 同秒内多次备份应追加序号避免覆盖
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"A": 1})

        # 第一次备份
        b1 = mapping_backup.backup_mapping()
        # 立即修改内容并再次备份（同一秒）
        _write_mapping(mapping_file, {"A": 2})
        b2 = mapping_backup.backup_mapping()

        assert b1 is not None and b2 is not None
        assert b1 != b2  # 文件名不同
        # 两份备份内容应不同
        assert json.loads(b1.read_text(encoding="utf-8")) == {"A": 1}
        assert json.loads(b2.read_text(encoding="utf-8")) == {"A": 2}


# ----------------------------------------------------------------------
# _prune_old_backups
# ----------------------------------------------------------------------

class TestPruneOldBackups:
    def test_prune_no_dir_returns_zero(self, isolated_env: Path) -> None:
        # 目录不存在时返回 0
        assert mapping_backup._prune_old_backups() == 0

    def test_prune_keeps_max_backups(self, isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # 设置 MAX_BACKUPS=3，创建 5 个备份
        monkeypatch.setattr(mapping_backup, "MAX_BACKUPS", 3)
        backup_dir = mapping_backup.BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        mapping_file = mapping_backup.MAPPING_FILE

        for i in range(5):
            _write_mapping(mapping_file, {"i": i})
            mapping_backup.backup_mapping()
            # 确保时间戳不同（避免同秒序号机制）
            import time
            time.sleep(0.01)

        # 应保留 3 个
        backups = list(backup_dir.iterdir())
        backup_count = len([p for p in backups if p.name.startswith("font_mapping_")])
        assert backup_count == 3


# ----------------------------------------------------------------------
# list_backups
# ----------------------------------------------------------------------

class TestListBackups:
    def test_list_empty_returns_empty(self, isolated_env: Path) -> None:
        # 没有任何备份时返回空列表
        assert mapping_backup.list_backups() == []

    def test_list_returns_sorted_descending(self, isolated_env: Path) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        import time
        for i in range(3):
            _write_mapping(mapping_file, {"i": i})
            mapping_backup.backup_mapping()
            time.sleep(1.1)  # 确保时间戳秒数不同

        backups = mapping_backup.list_backups()
        assert len(backups) == 3
        # 应按时间倒序（最新在前）
        assert backups[0]["timestamp"] >= backups[1]["timestamp"]
        assert backups[1]["timestamp"] >= backups[2]["timestamp"]

    def test_list_includes_metadata(self, isolated_env: Path) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"A": 1, "B": 2, "C": 3})
        mapping_backup.backup_mapping()

        backups = mapping_backup.list_backups()
        assert len(backups) == 1
        b = backups[0]
        assert "path" in b
        assert "name" in b
        assert "timestamp" in b
        assert "size" in b
        assert "entries" in b
        assert b["entries"] == 3


# ----------------------------------------------------------------------
# rollback
# ----------------------------------------------------------------------

class TestRollback:
    def test_rollback_without_backups_raises(self, isolated_env: Path) -> None:
        with pytest.raises(FileNotFoundError, match="没有任何备份"):
            mapping_backup.rollback()

    def test_rollback_restores_previous_version(self, isolated_env: Path) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        # v1
        _write_mapping(mapping_file, {"v": 1})
        mapping_backup.backup_mapping()
        import time
        time.sleep(1.1)
        # v2
        _write_mapping(mapping_file, {"v": 2})
        mapping_backup.backup_mapping()
        time.sleep(1.1)
        # v3（当前）
        _write_mapping(mapping_file, {"v": 3})

        # 回滚到 v2（最新备份）
        mapping_backup.rollback(steps=1)
        restored = json.loads(mapping_file.read_text(encoding="utf-8"))
        assert restored == {"v": 2}

    def test_rollback_creates_prerollback_backup(self, isolated_env: Path) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"v": 1})
        mapping_backup.backup_mapping()
        _write_mapping(mapping_file, {"v": 2})

        mapping_backup.rollback(steps=1)

        # 应存在 _prerollback 后缀的文件
        backups = list(mapping_backup.BACKUP_DIR.iterdir())
        prerollback_files = [p for p in backups if "prerollback" in p.name]
        assert len(prerollback_files) >= 1

    def test_rollback_invalid_steps_raises(self, isolated_env: Path) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"v": 1})
        mapping_backup.backup_mapping()

        with pytest.raises(FileNotFoundError, match="无效"):
            mapping_backup.rollback(steps=0)

        with pytest.raises(FileNotFoundError, match="无效"):
            mapping_backup.rollback(steps=99)


# ----------------------------------------------------------------------
# list_backups 边缘情况：非匹配文件 / 时间戳解析回退
# ----------------------------------------------------------------------

class TestListBackupsEdgeCases:
    def test_list_skips_non_matching_files(self, isolated_env: Path) -> None:
        """BACKUP_DIR 中存在非 font_mapping_*.json 文件时应被跳过。"""
        backup_dir = mapping_backup.BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        # 写入不匹配的文件
        (backup_dir / "readme.txt").write_text("not a backup")
        (backup_dir / "other.json").write_text("{}")
        (backup_dir / "font_mapping.txt").write_text("wrong ext")
        # 写入一个合法备份
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"a": 1})
        mapping_backup.backup_mapping()

        backups = mapping_backup.list_backups()
        # 应只返回 1 个合法备份
        assert len(backups) == 1

    def test_list_fallback_timestamp_for_unparseable_name(self, isolated_env: Path) -> None:
        """文件名时间戳无法解析时回退到文件 mtime。"""
        backup_dir = mapping_backup.BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        # 手动创建一个名字无法解析的备份文件
        # 名字格式 font_mapping_GARBAGE.json → ts_str="GARBAGE"
        # split("_")=["GARBAGE"]，len < 2 → ts_core="GARBAGE"
        # strptime 失败 → 回退到 mtime
        bad_name = backup_dir / "font_mapping_GARBAGE.json"
        _write_mapping(bad_name, {"x": 1})

        backups = mapping_backup.list_backups()
        assert len(backups) == 1
        # 时间戳应来自 mtime（不为 None）
        assert backups[0]["timestamp"] is not None

    def test_list_fallback_timestamp_for_bad_core(self, isolated_env: Path) -> None:
        """文件名有下划线但核心时间戳无法解析 → 回退到 mtime。"""
        backup_dir = mapping_backup.BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        # 名字 font_mapping_BAD_DATE_FOO.json → ts_str="BAD_DATE_FOO"
        # split → ["BAD", "DATE", "FOO"]，ts_core = "BAD_DATE"
        # strptime("BAD_DATE", "%Y%m%d_%H%M%S") 失败 → 回退 mtime
        bad_name = backup_dir / "font_mapping_BAD_DATE_FOO.json"
        _write_mapping(bad_name, {"x": 1})

        backups = mapping_backup.list_backups()
        assert len(backups) == 1
        assert backups[0]["timestamp"] is not None


# ----------------------------------------------------------------------
# _count_entries：异常路径
# ----------------------------------------------------------------------

class TestCountEntries:
    def test_count_entries_corrupt_json_returns_neg1(self, isolated_env: Path) -> None:
        """损坏的 JSON 文件应返回 -1。"""
        backup_dir = mapping_backup.BACKUP_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        bad_path = backup_dir / "font_mapping_20260101_000000.json"
        bad_path.write_text("{invalid json", encoding="utf-8")

        count = mapping_backup._count_entries(bad_path)
        assert count == -1

    def test_count_entries_missing_file_returns_neg1(self, isolated_env: Path) -> None:
        """文件不存在应返回 -1。"""
        backup_dir = mapping_backup.BACKUP_DIR
        missing = backup_dir / "nonexistent.json"
        count = mapping_backup._count_entries(missing)
        assert count == -1


# ----------------------------------------------------------------------
# rollback prerollback 同秒序号
# ----------------------------------------------------------------------

class TestRollbackPrerollbackSequence:
    def test_prerollback_same_second_appends_seq(self, isolated_env: Path) -> None:
        """同秒内多次回滚应追加序号避免覆盖 prerollback 文件。"""
        mapping_file = mapping_backup.MAPPING_FILE
        # 创建 3 个备份（用 sleep 确保时间戳不同）
        import time
        for i in range(3):
            _write_mapping(mapping_file, {"v": i})
            mapping_backup.backup_mapping()
            time.sleep(1.1)

        # 当前 v=2，连续回滚 2 次（同秒内）
        mapping_backup.rollback(steps=1)  # 回滚到 v=2 最新备份
        mapping_backup.rollback(steps=1)  # 再次回滚

        # 应至少有 2 个 prerollback 文件
        backups = list(mapping_backup.BACKUP_DIR.iterdir())
        prerollback_files = [p for p in backups if "prerollback" in p.name]
        assert len(prerollback_files) >= 2


# ----------------------------------------------------------------------
# print_backup_history
# ----------------------------------------------------------------------

class TestPrintBackupHistory:
    def test_print_no_backups(self, isolated_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mapping_backup.print_backup_history()
        out = capsys.readouterr().out
        assert "无备份历史" in out

    def test_print_with_backups(self, isolated_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"a": 1, "b": 2})
        mapping_backup.backup_mapping()

        mapping_backup.print_backup_history(limit=5)
        out = capsys.readouterr().out
        assert "备份目录" in out
        assert "font_mapping_" in out


# ----------------------------------------------------------------------
# main (CLI)
# ----------------------------------------------------------------------

class TestMain:
    def test_main_no_args_prints_usage(self, capsys: pytest.CaptureFixture[str],
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["mapping_backup"])
        mapping_backup.main()
        out = capsys.readouterr().out
        assert "用法" in out

    def test_main_list(self, isolated_env: Path, capsys: pytest.CaptureFixture[str],
                       monkeypatch: pytest.MonkeyPatch) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"a": 1})
        mapping_backup.backup_mapping()

        monkeypatch.setattr("sys.argv", ["mapping_backup", "list"])
        mapping_backup.main()
        out = capsys.readouterr().out
        assert "备份历史" in out

    def test_main_backup(self, isolated_env: Path, capsys: pytest.CaptureFixture[str],
                         monkeypatch: pytest.MonkeyPatch) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"a": 1})

        monkeypatch.setattr("sys.argv", ["mapping_backup", "backup"])
        mapping_backup.main()
        out = capsys.readouterr().out
        assert "已手动备份" in out

    def test_main_backup_no_source(self, isolated_env: Path, capsys: pytest.CaptureFixture[str],
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        # MAPPING_FILE 不存在
        monkeypatch.setattr("sys.argv", ["mapping_backup", "backup"])
        mapping_backup.main()
        out = capsys.readouterr().out
        assert "不存在" in out

    def test_main_rollback_success(self, isolated_env: Path, capsys: pytest.CaptureFixture[str],
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        mapping_file = mapping_backup.MAPPING_FILE
        _write_mapping(mapping_file, {"v": 1})
        mapping_backup.backup_mapping()
        _write_mapping(mapping_file, {"v": 2})

        monkeypatch.setattr("sys.argv", ["mapping_backup", "rollback"])
        mapping_backup.main()
        out = capsys.readouterr().out
        assert "已回滚" in out

    def test_main_rollback_with_steps(self, isolated_env: Path, capsys: pytest.CaptureFixture[str],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
        import time
        mapping_file = mapping_backup.MAPPING_FILE
        for i in range(3):
            _write_mapping(mapping_file, {"v": i})
            mapping_backup.backup_mapping()
            time.sleep(1.1)

        monkeypatch.setattr("sys.argv", ["mapping_backup", "rollback", "2"])
        mapping_backup.main()
        out = capsys.readouterr().out
        assert "已回滚 2 版" in out

    def test_main_rollback_no_backups(self, isolated_env: Path, capsys: pytest.CaptureFixture[str],
                                       monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["mapping_backup", "rollback"])
        mapping_backup.main()
        out = capsys.readouterr().out
        assert "没有任何备份" in out

    def test_main_unknown_command(self, capsys: pytest.CaptureFixture[str],
                                   monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["mapping_backup", "frobnicate"])
        mapping_backup.main()
        out = capsys.readouterr().out
        assert "未知命令" in out
