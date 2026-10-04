"""detail.db rebuilds from Phase 1 raw point/info fixtures without the network."""

import json
from pathlib import Path

from hsrmap.detail_normalize import normalize_point_info
from hsrmap.detail_rebuild import rebuild_detail_from_raw

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "phase1" / "samples" / "point_info"


def test_rebuild_phase1_fixtures_keeps_empty_and_nonempty(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for name in ("5260.json", "5171.json", "5146.json"):
        payload = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
        parsed = normalize_point_info(payload)
        dest = raw_dir / f"{parsed['source_point_id']}.json"
        dest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    db = rebuild_detail_from_raw(raw_dir, tmp_path / "rebuilt.db", app_version="test")
    assert db.count_details() == 3
    empty = db.detail_by_source("5260")
    assert empty["is_empty"] == 1
    both = db.detail_by_source("5171")
    assert both["is_empty"] == 0
    assert "浮脂溯源" in (both["plain_text"] or "")
    assert db.asset_count_for_source("5171") == 1
    assert db.asset_count_for_source("5260") == 0
    db.close()
