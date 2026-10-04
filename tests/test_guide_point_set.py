import pytest
import json

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.matching.registry import match_for_topic
from hsrmap.guides.matching.rematch import rematch_topic
from hsrmap.guides.review.service import approve_item, create_item
from hsrmap.guides.topics.loader import get_topic
from hsrmap.guides.topics.seed import seed_official_targets


def test_point_set_does_not_invent_ids_when_multiple():
    spec = get_topic("dream_ticker")
    previous = (spec.get("matcher") or {}).get("profile")
    spec.setdefault("matcher", {})["profile"] = "multi_point_v1"
    try:
        out = match_for_topic(
            "dream_ticker",
            {"map_name": "黄金的时刻", "map_id": "149"},
            [{"source_point_id": "11"}, {"source_point_id": "12"}],
        )
    finally:
        spec["matcher"]["profile"] = previous
    assert out["target_type"] == "POINT_SET"
    assert out["source_point_id"] in {"", "pending"}
    assert {row["source_point_id"] for row in out["candidates"]} == {"11", "12"}
    assert out["status"] == "review"


def test_zagreus_topic_binds_as_point_set_not_single_point():
    spec = get_topic("zagreus_hand")
    assert spec["scope"] == "POINT_SET"
    assert (spec.get("matcher") or {}).get("profile") == "multi_point_v1"
    out = match_for_topic(
        "zagreus_hand",
        {"map_name": "悬锋城", "map_id": "xuanfeng"},
        [{"source_point_id": "4135"}, {"source_point_id": "4136"}],
    )
    assert out["target_type"] == "POINT_SET"
    assert out["source_point_id"] in {"", "pending"}
    assert out["target_key"] == "set:xuanfeng:topic:zagreus_hand"
    assert {row["source_point_id"] for row in out["candidates"]} == {"4135", "4136"}
    assert out["status"] == "review"

@pytest.mark.data

def test_seed_zagreus_creates_one_official_set(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    n = seed_official_targets(db, "zagreus_hand")
    assert n == 1
    row = db.conn.execute(
        """
        SELECT t.target_type, t.target_key, t.source_point_id, t.metadata_json
        FROM guide_target t
        JOIN guide_topic tp ON tp.id = t.topic_id
        WHERE tp.topic_key = ?
        """,
        ("zagreus_hand",),
    ).fetchone()
    assert row["target_type"] == "POINT_SET"
    assert row["source_point_id"] in {None, ""}
    meta = json.loads(row["metadata_json"] or "{}")
    assert set(meta.get("official_point_ids") or []) == {"4135", "4136"}
    db.close()


def test_rematch_jump_mechanic_article_binds_global(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/jump-how2", "title": "JUMP玩法"})
    create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "jump",
                "map_name": "二次元JUMP玩法说明",
                "steps": [{"text": "长按蓄力再跳"}],
            },
        },
    )
    rematch_topic(db, "jump", official_points=[{"source_point_id": "9001", "map_name": "海原电视塔", "region": "海原电视塔", "label": "二次元 JUMP"}])
    row = db.conn.execute("SELECT source_point_id, status, draft_json FROM review_item").fetchone()
    draft = json.loads(row["draft_json"] or "{}")
    assert draft.get("target_type") == "GLOBAL"
    assert draft.get("target_key") == "global:topic:jump"
    assert row["source_point_id"] in {"", "global:topic:jump"}
    assert row["status"] != "APPROVED"
    db.close()

@pytest.mark.data

def test_jump_seeds_global_mechanic_target(tmp_path):
    spec = get_topic("jump")
    assert spec.get("seed_global") is True
    db = GuideDatabase(tmp_path / "guide.db")
    n = seed_official_targets(db, "jump")
    assert n >= 1
    row = db.conn.execute(
        """
        SELECT t.target_type, t.target_key, t.source_point_id
        FROM guide_target t
        JOIN guide_topic tp ON tp.id = t.topic_id
        WHERE tp.topic_key = ? AND t.target_type = 'GLOBAL'
        """,
        ("jump",),
    ).fetchone()
    assert row is not None
    assert row["target_key"] == "global:topic:jump"
    assert row["source_point_id"] in {None, ""}
    db.close()


def test_approve_global_without_inventing_point_id(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/jump-how", "title": "JUMP玩法"})
    item = create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "jump",
                "target_type": "GLOBAL",
                "target_key": "global:topic:jump",
                "map_name": "二次元JUMP玩法说明",
                "steps": [{"text": "按节奏跳", "images": []}],
            },
        },
    )
    approved = approve_item(db, item["id"], official_points=[])
    assert approved["status"] == "APPROVED"
    assert approved["source_point_id"] == "global:topic:jump"
    db.close()


def test_approve_point_set_without_inventing_point_id(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/zagreus", "title": "悬锋城扎格列斯"})
    item = create_item(
        db,
        {
            "page_id": page["id"],
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "zagreus_hand",
                "target_type": "POINT_SET",
                "target_key": "set:xuanfeng:topic:zagreus_hand",
                "map_name": "悬锋城",
                "candidate_points": [{"source_point_id": "4135"}, {"source_point_id": "4136"}],
                "steps": [{"text": "先操作台再伸手", "images": []}],
            },
        },
    )
    official = [
        {"source_point_id": "4135", "map_id": "xuanfeng", "label": "扎格列斯之手-操作台"},
        {"source_point_id": "4136", "map_id": "xuanfeng", "label": "扎格列斯之手"},
    ]
    approved = approve_item(db, item["id"], official_points=official)
    assert approved["status"] == "APPROVED"
    assert approved["source_point_id"] == "set:xuanfeng:topic:zagreus_hand"
    db.close()
