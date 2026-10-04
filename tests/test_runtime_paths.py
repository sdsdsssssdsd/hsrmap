"""运行时目录解析（a1-8 四.2）：只解析、不建目录。

顺序：显式 → HSRMAP_DATA_DIR → 仓库 data/（兼容窗口）→ 操作系统用户数据目录。
另外验证两件 DoD 相关的事：clean checkout 不写仓库；CLI 能报告当前根目录。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from hsrmap.runtime import ENV_DATA_DIR, repo_data_dir, resolve_runtime

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_wins(tmp_path: Path) -> None:
    runtime = resolve_runtime(tmp_path / "explicit", env={ENV_DATA_DIR: str(tmp_path / "env")}, repo_root=tmp_path)
    assert runtime.source == "explicit"
    assert runtime.root == (tmp_path / "explicit").resolve()


def test_env_wins_over_repo(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    runtime = resolve_runtime(env={ENV_DATA_DIR: str(tmp_path / "env")}, repo_root=tmp_path)
    assert runtime.source == "env"
    assert runtime.root == (tmp_path / "env").resolve()


def test_repo_data_is_the_compatibility_window(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    runtime = resolve_runtime(env={}, repo_root=tmp_path)
    assert runtime.source == "repo"
    assert runtime.root == (tmp_path / "data").resolve()


def test_clean_checkout_falls_back_to_user_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("hsrmap.runtime.user_data_dir", lambda app="hsrmap": tmp_path / "userdata")
    runtime = resolve_runtime(env={}, repo_root=tmp_path)
    assert runtime.source == "user"
    assert runtime.root == (tmp_path / "userdata").resolve()


def test_resolution_creates_nothing(tmp_path: Path) -> None:
    before = sorted(item.name for item in tmp_path.iterdir())
    resolve_runtime(env={}, repo_root=tmp_path)
    resolve_runtime(tmp_path / "explicit", env={})
    assert sorted(item.name for item in tmp_path.iterdir()) == before


def _tree(path: Path) -> dict[str, tuple[int, int]]:
    if not path.is_dir():
        return {}
    return {
        str(item.relative_to(path)): (item.stat().st_size, item.stat().st_mtime_ns)
        for item in path.rglob("*")
        if item.is_file()
    }


def test_cli_runtime_command_reports_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "hsrmap", "--data-dir", str(tmp_path), "runtime"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["root"]) == tmp_path.resolve()
    assert payload["source"] == "explicit"


def test_guides_completeness_does_not_touch_repo_data(tmp_path: Path) -> None:
    """运行时根目录指到别处时，仓库 data/ 一个文件都不该变（a1-8 DoD 2/3）。"""
    data_dir = repo_data_dir(ROOT)
    before = _tree(data_dir)
    result = subprocess.run(
        [sys.executable, "-m", "hsrmap", "--data-dir", str(tmp_path / "runtime"), "guides", "completeness"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    assert _tree(data_dir) == before, (result.returncode, result.stdout[:200], result.stderr[:300])
