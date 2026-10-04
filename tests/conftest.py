"""Test layers for Guide Atlas.

Source-only layers (unit + deterministic integration) must pass anywhere,
including in `submit/` where `data/` is absent. Everything that needs the real
snapshot / detail / guide databases is marked `data` or `e2e` and:

- is skipped by default (`pytest` → unit + integration only);
- runs with `pytest --run-data-e2e`, and then still skips itself with
  `SKIPPED: snapshot fixture unavailable` when the data tree is missing
  instead of failing.

Commands:

```bash
python -m pytest                       # unit + integration
python -m pytest -m "not data"         # identical to the default run
python -m pytest --run-data-e2e        # everything the local data supports
```
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CURRENT = DATA / "current.json"

#: 会话开始时 data/ 是否存在：决定结束时要不要报告「测试建出了数据目录」。
_DATA_EXISTED = DATA.exists()

#: 普通测试的运行时根目录（临时目录）：测试永远不写仓库 data/（a1-8 四.2）。
_TEST_DATA_DIR: Path | None = None


def pytest_sessionfinish(session, exitstatus) -> None:
    """测试不该在 checkout 里建出 `data/`。

    仓库里 `data/` 本来就有，测试往里写等于动用户的数据；`submit/` 副本里没有 `data/`，
    所以污染在那里才看得见（曾出现过 `data/guides/guide.db`、`data/user.db`）。
    会话结束时把新出现的目录树清掉并大声说出来，别让它跟着包发出去。
    """
    global _TEST_DATA_DIR
    if _TEST_DATA_DIR is not None:
        shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
        _TEST_DATA_DIR = None
    if _DATA_EXISTED or not DATA.exists():
        return
    created = sorted(str(path.relative_to(DATA)) for path in DATA.rglob("*"))
    shutil.rmtree(DATA, ignore_errors=True)
    print("\n[conftest] WARNING: 测试在 %s 下新建了 %d 个条目，已清理：%s" % (DATA, len(created), created[:6]))


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--run-data-e2e",
        action="store_true",
        default=False,
        help="run tests marked data/e2e (they need data/current.json plus the snapshot DBs)",
    )
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests marked live (they touch the real hosts; run them by hand)",
    )


def pytest_configure(config) -> None:
    """普通测试把运行时根目录指向临时目录。

    带 --run-data-e2e 的测试需要真实 data/（快照 / detail / guide 库），那种情况保持原样；
    其余情况注入 HSRMAP_DATA_DIR，让 hsrmap.paths 解析到 tmp，而不是仓库 data/——
    「测试居然建出了 data/」从此是真正的异常，而不是每次都要事后清理的常态。
    """
    global _TEST_DATA_DIR
    if not config.getoption("--run-data-e2e"):
        _TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="hsrmap-test-runtime-"))
        os.environ.setdefault("HSRMAP_DATA_DIR", str(_TEST_DATA_DIR))
    config.addinivalue_line(
        "markers", "data: needs the real data/ tree (skipped unless --run-data-e2e)"
    )
    config.addinivalue_line(
        "markers", "e2e: end-to-end path over the real data (skipped unless --run-data-e2e)"
    )
    config.addinivalue_line(
        "markers", "live: touches real hosts over the network (skipped unless --run-live)"
    )


def pytest_collection_modifyitems(config, items) -> None:
    if not config.getoption("--run-data-e2e"):
        skip = pytest.mark.skip(reason="SKIPPED: data/e2e test (pass --run-data-e2e to run it)")
        for item in items:
            if item.get_closest_marker("data") or item.get_closest_marker("e2e"):
                item.add_marker(skip)
    if not config.getoption("--run-live"):
        skip_live = pytest.mark.skip(reason="SKIPPED: live network test (pass --run-live to run it)")
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(skip_live)


def snapshot_available() -> bool:
    """True when the bound snapshot exposes a readable core database."""
    if not CURRENT.exists():
        return False
    try:
        snapshot_id = json.loads(CURRENT.read_text(encoding="utf-8"))["snapshot_id"]
    except Exception:
        return False
    return (DATA / "snapshots" / str(snapshot_id) / "core.db").exists()


@pytest.fixture(autouse=True)
def _require_data_for_marked_tests(request):
    """A data/e2e test skips (never fails) when the snapshot fixture is absent."""
    if request.node.get_closest_marker("data") or request.node.get_closest_marker("e2e"):
        if not snapshot_available():
            pytest.skip("SKIPPED: snapshot fixture unavailable")


@pytest.fixture(scope="session")
def snapshot_data():
    """The real data/ tree, or a SKIP when the snapshot fixture is unavailable."""
    if not snapshot_available():
        pytest.skip("SKIPPED: snapshot fixture unavailable")
    return DATA


@pytest.fixture(scope="session")
def guide_data(snapshot_data):
    """data/ plus the working guide DB; skips when the guide DB is absent."""
    db = snapshot_data / "guides" / "guide.db"
    if not db.exists():
        pytest.skip("SKIPPED: guide fixture unavailable")
    return snapshot_data

def create_guide_db(path: Path) -> Path:
    """显式建出一个空的攻略库，返回它的路径。

    只读 / 读写打开都**拒绝**创建不存在的库（a1-8 四.2），只有 `create()` 允许产生
    文件系统副作用：需要「已经存在的工作库 / 发布库」的测试必须自己先把它建出来，
    而不是指望 viewer / CLI 顺手建一个空的。
    """
    from hsrmap.guide_db import GuideDatabase

    GuideDatabase.create(path).close()
    return path


@pytest.fixture
def make_guide_db(tmp_path: Path):
    """按名字显式建库：`make_guide_db("w.db")` → `tmp_path / "w.db"`。"""

    def _make(name: str) -> Path:
        return create_guide_db(tmp_path / name)

    return _make


@pytest.fixture
def guide_dbs(tmp_path: Path) -> Path:
    """把标准名的两个库（`guide.db` + `published.db`）显式建出来，返回所在目录。

    `create_app()` 的审核台模式用 readwrite 打开工作库、用 readonly 打开发布库，
    两者都不建库（a1-8 四.2）；`published.db` 也要建出来，`POST /api/v1/guides`
    才有一条能同步过去的发布库。
    """
    create_guide_db(tmp_path / "guide.db")
    create_guide_db(tmp_path / "published.db")
    return tmp_path