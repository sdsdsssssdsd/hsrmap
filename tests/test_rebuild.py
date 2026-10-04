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
    published = CoreDatabase(DATA / current["core_db"])
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
