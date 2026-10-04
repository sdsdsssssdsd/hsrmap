"""Closure scoreboard: structure, deferrals and an honest verdict."""

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.closure import (
    ARCHITECTURE_DECISIONS,
    closure_check,
    corpus_health,
    golden_status,
    render_closure,
)


def _entry(db, pid, title="攻略"):
    db.create_entry(
        {
            "source_point_id": pid,
            "title": title,
            "status": "published",
            "steps": [{"text": "第一步"}],
        }
    )


def test_golden_registration_covers_enabled_topics():
    status = golden_status()
    assert status["enabled_topics"] >= 10
    assert status["ok"] is True, status
    assert status["registered"] == status["enabled_topics"]


def test_architecture_decisions_are_declared():
    assert set(ARCHITECTURE_DECISIONS) == {
        "published targets",
        "content_block",
        "article resolver",
        "job resume",
        "ai cache",
    }
    assert "IMPLEMENTED" in ARCHITECTURE_DECISIONS["published targets"]
    assert "DEFERRED" in ARCHITECTURE_DECISIONS["content_block"]


def test_closure_check_reports_every_hard_check(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1")
    _entry(published, "1")
    report = closure_check(
        working, published, assets_root=tmp_path / "assets", e2e=False, completeness=False
    )
    assert report["completeness"] == {"skipped": True}
    names = {item["name"] for item in report["checks"]}
    assert {
        "Engine Gate A",
        "Engine Gate B",
        "Engine Gate C",
        "Broken bindings",
        "Broken assets",
        "Missing core targets",
        "Hallucinated steps",
        "Golden regression",
        "Offline E2E",
        "Snapshot regression",
    } <= names
    assert report["closure"] in {"PASS", "BLOCKED"}
    assert "corpus" in report and "quality_debt" in report
    text = render_closure(report)
    assert "Guide Atlas V3 Closure" in text
    assert "Architecture decisions" in text and "Corpus health" in text
    assert "CLOSURE RESULT" in text
    working.close()
    published.close()


def test_corpus_health_is_reported_not_gated(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    _entry(working, "1")
    _entry(published, "1")
    health = corpus_health(working, published)
    assert set(health) >= {"published", "official", "no_source_yet", "needs_review", "no_public_source"}
    working.close()
    published.close()
