"""运行时目录解析（a1-8 四.2）：只解析、不建目录。

顺序：显式 → HSRMAP_DATA_DIR → 仓库 data/（兼容窗口）→ 操作系统用户数据目录。
另外验证两件 DoD 相关的事：clean checkout 不写仓库；CLI 能报告当前根目录。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

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


#: 这些文件是**共享运行时库**：本机开着的地图/审核台服务、另一个 `--run-data-e2e` 进程
#: 都会合法地打开并提交它们（哪怕只是补一次 schema 版本）。它们只比大小，不比内容 ——
#: 「另一个进程写了 user.db」不是「被测命令写了仓库 data/」。
#: `-wal` / `-journal` 是 SQLite 的瞬时文件，连存在与否都不该参与比较。
_CONTENT_EXEMPT = {"user.db", "guide.db", "published.db", "detail.db", "core.db"}
_TRANSIENT_SUFFIXES = ("-wal", "-shm", "-journal")


def _tree(path: Path, *, hash_limit: int = 4 * 1024 * 1024) -> dict[str, tuple[int, str]]:
    """仓库 data/ 的清单：**文件集合 + 大小 +（非共享库的）内容指纹**，不看 mtime。

    这里以前比 `st_mtime_ns`，于是「另一个进程恰好打开了同一个 user.db」就会假失败 ——
    那是别人的写。现在：新文件、新目录、大小变化、以及绝大多数文件的内容变化都抓得到；
    只有几个共享运行时库（别人也在写）只比大小。被测命令真去写仓库 data/（建库、写
    report、落 raw）依然会立刻暴露。
    """
    if not path.is_dir():
        return {}
    out: dict[str, tuple[int, str]] = {}
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        if item.name.endswith(_TRANSIENT_SUFFIXES):
            continue
        size = item.stat().st_size
        digest = ""
        if size <= hash_limit and item.name not in _CONTENT_EXEMPT:
            try:
                digest = hashlib.sha256(item.read_bytes()).hexdigest()[:16]
            except OSError:  # noqa: PERF203 - 读不到就只比大小，不因为一个文件放弃整棵树
                digest = ""
        out[str(item.relative_to(path))] = (size, digest)
    return out


def test_status_fails_loudly_when_the_pointer_is_bogus(tmp_path: Path) -> None:
    """指针写坏时必须给一条人话（rc=1），而不是一路炸出 traceback。

    这条来自一次真实事故：同步进行中 `data/current.json` 被写成了 `{"snapshot_id": "OLD"}`，
    所有读指针的命令都会去开一个不存在的库。
    """
    data = tmp_path / "data"
    data.mkdir()
    (data / "current.json").write_text(
        json.dumps({"snapshot_id": "OLD", "core_db": "snapshots/OLD/core.db"}), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, "-m", "hsrmap", "--data-dir", str(data), "status"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert "指向的数据库不存在" in output, output
    assert "Traceback" not in output, output


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
    """运行时根目录指到别处时，仓库 data/ 一个文件都不该变（a1-8 DoD 2/3）。

    **前提**是这一刻没有别人在写仓库 data/。后台跑着 `hsrmap sync`（或别的写库进程）时，
    这条断言测的就不再是「被测命令干了什么」——那时如实 skip，而不是报一个假失败。
    sync 的互斥锁就在 `<data>/sync_lock`（sync 自己拿它防并发）。
    """
    from hsrmap.paths import LOCK_PATH

    data_dir = repo_data_dir(ROOT)
    #: 前提检查：这一刻**没有别人**在写仓库 data/。
    #: ① sync 的互斥锁在 → 直接跳过；② 相邻两次采样就不一致 → 也是在被人写。
    #: 这两种情况测的都不是「被测命令干了什么」，如实 skip 好过报一个假失败。
    if Path(LOCK_PATH).exists():
        pytest.skip("SKIPPED: 有 sync 正在跑（仓库 data/ 必然在变，断言前提不成立）")
    probe_before = _tree(data_dir)
    time.sleep(2.0)
    probe_after = _tree(data_dir)
    if probe_before != probe_after:
        changed_now = sorted(
            key for key in set(probe_before) | set(probe_after)
            if probe_before.get(key) != probe_after.get(key)
        )[:3]
        pytest.skip(f"SKIPPED: 仓库 data/ 正在被别的进程写（{changed_now}），断言前提不成立")
    before = _tree(data_dir)
    result = subprocess.run(
        [sys.executable, "-m", "hsrmap", "--data-dir", str(tmp_path / "runtime"), "guides", "completeness"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    after = _tree(data_dir)
    changed = sorted(
        key for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    )
    assert after == before, (
        f"被测命令不该动仓库 data/（a1-8 DoD 2/3）：{changed[:8]}",
        result.returncode, result.stdout[:200], result.stderr[:300],
    )
