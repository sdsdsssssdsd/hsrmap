"""core.db can be rebuilt from a published raw snapshot without the network."""

import json
from pathlib import Path

from hsrmap.database import CoreDatabase
from hsrmap.paths import DATA, GOLDEN_PATH
from hsrmap.rebuild import rebuild_from_raw
from hsrmap.validate import golden_point_errors
import pytest

pytestmark = pytest.mark.data


def test_rebuild_published_raw_matches_core_counts(tmp_path: Path):
    current = json.loads((DATA / "current.json").read_text(encoding="utf-8"))
    raw_dir = DATA / current["path"] / "raw"
    #: 发布快照是冻结产物：这里只读计数，**必须只读打开**。可写打开会跑 schema 迁移，
    #: 等于让测试改掉 data/（M7.1 的加性迁移让这个老写法立刻现形）。
    published = CoreDatabase(DATA / current["core_db"], readonly=True, immutable=True)
    rebuilt = rebuild_from_raw(raw_dir, tmp_path / "rebuilt.db")
    expected = published.counts()
    actual = rebuilt.counts()
    for key in ("map_nodes", "renderable_maps", "labels", "points", "fragments"):
        assert actual[key] == expected[key], key
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    errors = golden_point_errors(rebuilt, golden)
    assert errors["mean"] == 0
    assert errors["max"] == 0
    published.close()
    rebuilt.close()
