"""Topic Admin metrics: the next action is derived, never hard-coded (a1-6 §27)."""

import json

import pytest
from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.admin import (
    NEXT_ACTIONS,
    derive_next_action,
    review_admin,
    snapshots_admin,
    sources_admin,
    topic_admin_rows,
)
from hsrmap.viewer_app import create_app


def test_next_action_follows_the_five_documented_cases():
    assert derive_next_action({"coverage_regression": True}) == "INVESTIGATE_REGRESSION"
    assert derive_next_action({"qa_pass": 1, "qa_fail": 3}) == "FIX_SOURCE_PIPELINE"
    assert derive_next_action({"official_targets": 10, "published": 2, "needs_source": 8}) == "DISCOVER_SOURCE"
    assert derive_next_action({"official_targets": 10, "published": 9, "needs_source": 0, "needs_review": 5, "approved": 1}) == "REVIEW"
    assert derive_next_action({"official_targets": 10, "published": 2, "matched": 6, "approved": 6, "needs_source": 0}) == "PUBLISH"
    assert derive_next_action({"official_targets": 10, "published": 10}) == "HOLD"
    assert set(NEXT_ACTIONS) == {
        "INVESTIGATE_REGRESSION",
        "FIX_SOURCE_PIPELINE",
        "DISCOVER_SOURCE",
        "REVIEW",
        "PUBLISH",
        "HOLD",
    }


def test_regression_beats_every_other_condition():
    state = {
        "coverage_regression": True,
        "qa_pass": 1,
        "qa_fail": 9,
        "official_targets": 10,
        "published": 1,
        "needs_source": 9,
    }
    assert derive_next_action(state) == "INVESTIGATE_REGRESSION"


def test_review_and_sources_admin_read_the_database(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/a", "title": "t", "qa_status": "QA_PASS"})
    db.conn.execute("UPDATE guide_page SET qa_status = 'QA_PASS' WHERE id = ?", (page["id"],))
    db.conn.execute(
        "INSERT INTO review_item(page_id, reason, status, created_at) VALUES (?, 'ingest', 'NEEDS_REVIEW', datetime('now'))",
        (page["id"],),
    )
    db.conn.execute(
        "INSERT INTO review_item(page_id, reason, status, created_at) VALUES (?, 'merge', 'MERGED', datetime('now'))",
        (page["id"],),
    )
    db.conn.commit()
    review = review_admin(db)
    assert review["total"] == 2 and review["counts"]["MERGED"] == 1
    assert review["duplicate_ratio"] == 0.5
    assert review["pending_sample"][0]["status"] == "NEEDS_REVIEW"
    sources = sources_admin(db)
    assert sources["hosts"][0]["host"] == "t.test"
    assert sources["hosts"][0]["qa_pass"] == 1
    assert "searches" in sources["evidence"]
    db.close()


def test_topic_rows_carry_a_derived_action_and_coverage(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    rows = topic_admin_rows(db, db, topics=["origami_bird"])
    assert len(rows) == 1
    row = rows[0]
    assert row["topic"] == "origami_bird"
    assert row["next_action"] in NEXT_ACTIONS
    assert "review_duplicate_ratio" in row and "qa_pass_rate" in row
    regression = topic_admin_rows(db, db, topics=["origami_bird"], regressions=["origami_bird"])
    assert regression[0]["next_action"] == "INVESTIGATE_REGRESSION"
    db.close()


def test_snapshots_admin_summarises_existing_reports(tmp_path):
    (tmp_path / "closure.json").write_text(
        json.dumps({"result": "PASS", "generated_at": "2026-10-03T00:00:00Z"}), encoding="utf-8"
    )
    (tmp_path / "publish-diff.json").write_text(
        json.dumps({"gate": {"result": "REVIEW"}, "change_counts": {}}), encoding="utf-8"
    )
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    body = snapshots_admin(tmp_path)
    assert body["reports"]["closure.json"]["result"] == "PASS"
    assert body["reports"]["publish-diff.json"]["result"] == "REVIEW"
    assert "broken.json" not in body["reports"]
    assert snapshots_admin(tmp_path / "missing")["reports"] == {}


@pytest.mark.data
def test_admin_endpoints_answer():
    client = TestClient(create_app())
    topics = client.get("/api/v1/atlas/topics").json()["topics"]
    assert any(row["topic"] == "origami_bird" for row in topics)
    assert all(row["next_action"] for row in topics)
    review = client.get("/api/v1/atlas/review").json()
    assert "counts" in review and "duplicate_ratio" in review
    sources = client.get("/api/v1/atlas/sources").json()
    assert sources["hosts"] and "evidence" in sources
    snapshots = client.get("/api/v1/atlas/snapshots").json()
    assert "closure.json" in snapshots["reports"]
    coverage = client.get("/api/v1/atlas/coverage", params={"topic": "origami_bird"}).json()
    assert coverage["topics"][0]["official_targets"] >= 40
