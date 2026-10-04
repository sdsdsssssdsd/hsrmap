"""Offline Phase 1 fixtures must rebuild 海原市 core without hitting the network."""

import json
from pathlib import Path

from hsrmap.database import CoreDatabase
from hsrmap.normalize import flatten_label_nodes, flatten_map_nodes, normalize_map_info, normalize_point
from hsrmap.validate import golden_point_errors

ROOT = Path(__file__).resolve().parents[1]
PHASE1 = ROOT / "phase1"


def test_phase1_haiyuanshi_fixture_rebuilds_48_points_and_keeps_golden_zero(tmp_path):
    tree = json.loads((PHASE1 / "samples" / "map_tree.json").read_text(encoding="utf-8"))
    labels = json.loads((PHASE1 / "samples" / "label_tree.json").read_text(encoding="utf-8"))
    info = json.loads((PHASE1 / "samples" / "map_info.json").read_text(encoding="utf-8"))
    points = json.loads((PHASE1 / "samples" / "point_list.json").read_text(encoding="utf-8"))
    golden = json.loads((PHASE1 / "calibration" / "points_normalized.json").read_text(encoding="utf-8"))

    db = CoreDatabase(tmp_path / "core.db")
    db.insert_map_nodes(flatten_map_nodes((tree.get("data") or {}).get("tree") or []))
    label_nodes, bindings = flatten_label_nodes((labels.get("data") or {}).get("tree") or [])
    db.insert_label_nodes(label_nodes)
    db.insert_semantic_bindings(bindings)
    mapped = normalize_map_info(info)
    db.insert_map(mapped)
    normalized_points = [
        normalize_point(p, map_source_id="842", origin_x=mapped["origin_x"], origin_y=mapped["origin_y"])
        for p in ((points.get("data") or {}).get("point_list") or [])
    ]
    db.insert_points(normalized_points)

    assert db.count_points_for_map("842") == 48
    errors = golden_point_errors(db, golden)
    assert errors["count"] == 20
    assert errors["mean"] == 0
    assert errors["max"] == 0
    assert db.semantic_source_id("floating_grease_origin_retrace") is not None
    assert db.semantic_source_id("floating_grease_notes") is not None
