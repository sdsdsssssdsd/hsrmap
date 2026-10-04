"""Canary queue must cover grease, notes, golden 20, and empty fixture 5260."""

import json

from hsrmap.database import CoreDatabase
from hsrmap.detail_queue import canary_source_ids
from hsrmap.paths import GOLDEN_PATH, SNAPSHOTS
import pytest

pytestmark = pytest.mark.data


def test_canary_ids_cover_required_sets():
    core = CoreDatabase(SNAPSHOTS / "20261001T105105Z" / "core.db", readonly=True)
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    ids = canary_source_ids(core, golden)
    assert "5260" in ids
    assert {str(item["source_point_id"]) for item in golden} <= ids
    grease = core.semantic_source_id("floating_grease_origin_retrace")
    notes = core.semantic_source_id("floating_grease_notes")
    grease_n = int(core.conn.execute(
        "SELECT COUNT(DISTINCT p.source_id) n FROM points p JOIN point_labels pl ON pl.point_id = p.id JOIN label_nodes l ON l.id = pl.label_id WHERE l.source_id = ?",
        (grease,),
    ).fetchone()["n"])
    notes_n = int(core.conn.execute(
        "SELECT COUNT(DISTINCT p.source_id) n FROM points p JOIN point_labels pl ON pl.point_id = p.id JOIN label_nodes l ON l.id = pl.label_id WHERE l.source_id = ?",
        (notes,),
    ).fetchone()["n"])
    assert grease_n == 48
    assert notes_n == 3
    assert len(ids) >= 48
    core.close()
