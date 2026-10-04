"""数据库打开方式（a1-8 四.2）：读/写都不许建库，只有 create 可以。

这一层是「clean checkout 不会建出 data/」「viewer/只读操作不会隐式创建 SQLite」两条 DoD 的直接证据。
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from hsrmap.guide_db import DB_CREATE, DB_READ_ONLY, DB_READ_WRITE, GuideDatabase

ROOT = Path(__file__).resolve().parents[1]


def test_readonly_missing_raises_and_creates_nothing(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "guide.db"
    with pytest.raises(FileNotFoundError):
        GuideDatabase.open_readonly(target)
    assert not target.exists()
    assert not target.parent.exists(), "只读打开不许建目录"


def test_readwrite_missing_raises_and_creates_nothing(tmp_path: Path) -> None:
    target = tmp_path / "guide.db"
    with pytest.raises(FileNotFoundError):
        GuideDatabase.open_readwrite(target)
    assert not target.exists()


def test_create_builds_schema_and_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "guide.db"
    db = GuideDatabase.create(target)
    assert db.mode == DB_CREATE
    tables = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"guide_entry", "guide_steps", "guide_page"} <= tables
    db.close()
    GuideDatabase.create(target).close()


def test_readonly_really_is_readonly(tmp_path: Path) -> None:
    target = tmp_path / "guide.db"
    GuideDatabase.create(target).close()
    db = GuideDatabase.open_readonly(target)
    assert db.mode == DB_READ_ONLY
    with pytest.raises(sqlite3.OperationalError):
        db.conn.execute("INSERT INTO metadata(key, value) VALUES ('x', 'y')")
    db.close()


def test_readwrite_opens_existing(tmp_path: Path) -> None:
    target = tmp_path / "guide.db"
    GuideDatabase.create(target).close()
    db = GuideDatabase.open_readwrite(target)
    assert db.mode == DB_READ_WRITE
    db.close()


def test_unknown_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        GuideDatabase(tmp_path / "guide.db", mode="whatever")


def _run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hsrmap", *argv],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )


def test_readonly_command_refuses_to_create_a_database(tmp_path: Path) -> None:
    runtime = tmp_path / "rt"
    result = _run_cli("--data-dir", str(runtime), "guides", "completeness")
    assert result.returncode == 1, (result.returncode, result.stdout[:200], result.stderr[:200])
    assert "guide database missing" in (result.stdout + result.stderr)
    assert list(runtime.rglob("*.db")) == [], "只读命令不许建库"


def test_write_command_bootstraps_the_database(tmp_path: Path) -> None:
    runtime = tmp_path / "rt"
    result = _run_cli("--data-dir", str(runtime), "guides", "backfill-topics")
    assert result.returncode == 0, (result.returncode, result.stdout[:200], result.stderr[:300])
    assert (runtime / "guides" / "guide.db").is_file(), "写入型命令应当建库"
