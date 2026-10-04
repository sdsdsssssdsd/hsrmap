import json

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.topics.backfill import backfill_page_topics


def test_backfill_binds_page_topic_from_review_draft(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/ticker", "title": "黄金的时刻迷钟"})
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (page["id"], '{"topic_key":"dream_ticker","map_name":"黄金的时刻"}'),
    )
    db.conn.commit()
    assert db.topics_for_page(page["id"]) == []
    out = backfill_page_topics(db)
    assert out["bound"] >= 1
    keys = {row["topic_key"] for row in db.topics_for_page(page["id"])}
    assert "dream_ticker" in keys
    db.close()


def test_backfill_writes_topic_key_into_drafts_missing_it(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/bird", "title": "折纸小鸟"})
    db.bind_page_topic(page["id"], "origami_bird", 1.0)
    db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json)
        VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'), '', ?, '{}')
        """,
        (page["id"], '{"map_name":"黄金的时刻","steps":[]}'),
    )
    db.conn.commit()
    out = backfill_page_topics(db)
    assert out["drafts"] >= 1
    draft = json.loads(db.conn.execute("SELECT draft_json FROM review_item").fetchone()[0])
    assert draft["topic_key"] == "origami_bird"
    db.close()
