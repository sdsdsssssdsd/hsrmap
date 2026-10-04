import pytest
from pathlib import Path

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.discover import load_seeds_for_topic
from hsrmap.guides.review.service import approve_item, create_item
from hsrmap.guides.topics.official import official_payload_for_topic


BANNED = (
    'if key == "floating_grease"',
    "if key == 'floating_grease'",
    'if key == "dream_ticker"',
    "if key == 'dream_ticker'",
    'if topic == "dream_ticker"',
    "if topic == 'dream_ticker'",
    'if topic == "floating_grease"',
    "if topic == 'floating_grease'",
)

MAIN_FLOW = (
    Path("hsrmap/guides/discover.py"),
    Path("hsrmap/guides/review/service.py"),
    Path("hsrmap/viewer_app.py"),
    Path("hsrmap/guides/topics/official.py"),
)


def test_gate_a_main_flow_has_no_topic_key_hardcode():
    for path in MAIN_FLOW:
        text = path.read_text(encoding="utf-8")
        for needle in BANNED:
            assert needle not in text, f"{path} still hardcodes {needle}"


def test_shared_seeds_yaml_joins_only_matching_topic():
    grease = load_seeds_for_topic("floating_grease")
    bucket = load_seeds_for_topic("king_bucket")
    assert any("2092445" in url for url in grease["urls"])
    assert "2092445" not in " ".join(bucket["urls"])
    assert "786205107434815910" not in " ".join(bucket["urls"])


def test_approve_requires_candidates_from_matcher_profile(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/gate", "title": "t"})
    item = create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "101",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "dream_ticker",
                "map_id": "508",
                "semantic_key": "梦境迷钟",
                "candidate_points": [],
            },
        },
    )
    try:
        approve_item(db, item["id"], official_points=[{"source_point_id": "101", "map_id": "508", "label": "梦境迷钟"}])
        raise AssertionError("expected reject")
    except ValueError as exc:
        assert "candidate" in str(exc)


def test_approve_label_uses_official_names_not_hardcoded_token(tmp_path):
    db = GuideDatabase(tmp_path / "g.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/goat", "title": "t"})
    item = create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "9",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "golden_scapegoat",
                "map_id": "1",
                "semantic_key": "黄金替罪羊",
            },
        },
    )
    try:
        approve_item(db, item["id"], official_points=[{"source_point_id": "9", "map_id": "1", "label": "宝箱"}])
        raise AssertionError("expected reject")
    except ValueError as exc:
        assert "label" in str(exc)

@pytest.mark.data

def test_grease_official_payload_keeps_notes_via_viewer_profile():
    body = official_payload_for_topic("floating_grease")
    assert body["origin"]["count"] == 48
    assert body["notes"]["count"] == 3
    assert body["origin"]["semantic_key"] == "floating_grease_origin_retrace"
