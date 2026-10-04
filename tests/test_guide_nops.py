"""「查过了、公开来源确实没有」：只承认证据账本里真有的搜索。"""

import pytest

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.evidence import record_search
from hsrmap.guides.ledger import topic_ledger
from hsrmap.guides.nops import mark_no_public_source


def _db(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    db.conn.execute(
        "INSERT INTO guide_target_status(topic_key, target_key, source_point_id, map_id, status, updated_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'))",
        ("nymph", "point:3481", "3481", "398", "NEEDS_SOURCE"),
    )
    db.conn.commit()
    return db


def _points():
    return [{"source_point_id": "3481", "map_id": "398", "map_name": "特殊房间"}]


def _search(db, *, decision="IRRELEVANT", target="point:3481"):
    record_search(
        db,
        topic="nymph",
        target_key=target,
        query="崩坏星穹铁道 若虫 特殊房间 位置",
        results=[{"rank": 1, "url": "https://x.test/achievements", "decision": decision}],
    )


def test_a_recorded_search_without_candidates_becomes_no_public_source(tmp_path):
    db = _db(tmp_path)
    _search(db)
    report = mark_no_public_source(db, topic="nymph", apply=True)
    assert report["eligible"] == 1 and report["blocked"] == 0
    row = db.conn.execute("SELECT * FROM source_search_verdict").fetchone()
    assert row["status"] == "NO_PUBLIC_SOURCE_FOUND" and row["runs"] == 1
    assert "特殊房间" in row["queries"]
    # 账本随即改口：NEEDS_SOURCE -> NO_PUBLIC_SOURCE_FOUND
    ledger = topic_ledger(db, "nymph", official_points=_points())
    assert ledger["counts"].get("NO_PUBLIC_SOURCE_FOUND") == 1
    assert ledger["counts"].get("NEEDS_SOURCE") in (None, 0)
    db.close()


def test_a_search_that_found_a_candidate_blocks_the_verdict(tmp_path):
    db = _db(tmp_path)
    _search(db, decision="ACCEPTED")
    report = mark_no_public_source(db, topic="nymph", apply=True)
    assert report["eligible"] == 0
    assert report["blocked_reasons"] == {"SEARCH_HAD_CANDIDATES": 1}
    assert db.conn.execute("SELECT COUNT(*) FROM source_search_verdict").fetchone()[0] == 0
    db.close()


def test_a_target_without_any_recorded_search_is_not_marked(tmp_path):
    db = _db(tmp_path)
    report = mark_no_public_source(db, topic="nymph", apply=True)
    assert report["eligible"] == 0
    assert report["blocked_reasons"] == {"NO_RECORDED_SEARCH": 1}
    db.close()


def test_a_published_target_is_never_marked_no_public_source(tmp_path):
    db = _db(tmp_path)
    _search(db)
    db.create_entry(
        {
            "source_point_id": "3481",
            "title": "若虫·官方点位",
            "status": "published",
            "steps": [{"text": "位于门旁的凳子上。"}],
        }
    )
    db.conn.commit()
    report = mark_no_public_source(db, topic="nymph", apply=True)
    assert report["eligible"] == 0
    assert report["blocked_reasons"] == {"ALREADY_PUBLISHED": 1}
    db.close()


def test_a_pending_review_keeps_the_target_open(tmp_path):
    db = _db(tmp_path)
    _search(db)
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/a", "title": "t"})
    db.conn.execute(
        "INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json)"
        " VALUES (?, ?, ?, datetime('now'), ?, ?)",
        (page["id"], "ingest", "NEEDS_REVIEW", "3481", '{"topic_key": "nymph"}'),
    )
    db.conn.commit()
    report = mark_no_public_source(db, topic="nymph", apply=True)
    assert report["eligible"] == 0
    assert report["blocked_reasons"] == {"REVIEW_PENDING": 1}
    db.close()


def test_min_runs_requires_more_than_one_search(tmp_path):
    db = _db(tmp_path)
    _search(db)
    report = mark_no_public_source(db, topic="nymph", apply=True, min_runs=2)
    assert report["eligible"] == 0
    assert report["blocked_reasons"] == {"NO_RECORDED_SEARCH": 1}
    db.close()
