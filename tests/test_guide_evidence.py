"""Search evidence: every discovery attempt leaves a record (a1-6 §八)."""

import json

import pytest

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.evidence import (
    classify_candidate,
    decision_counts,
    record_result,
    record_search,
    searched_targets,
    searches_for,
    start_search,
    summary,
    unsearched_targets,
)


def _db(tmp_path):
    return GuideDatabase(tmp_path / "guide.db")


def _page(db, url):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    return db.add_page(source["id"], {"canonical_url": url, "title": "标题", "author": "作者"})


def test_record_search_stores_runs_results_and_summary(tmp_path):
    db = _db(tmp_path)
    outcome = record_search(
        db,
        topic="golden_scapegoat",
        target_key="point:42",
        query="崩铁 黄金替罪羊 龙骸古城",
        results=[
            {"rank": 1, "url": "https://a.test/1.html", "decision": "ACCEPTED", "reason": "new article"},
            {"rank": 2, "url": "https://a.test/1_2.html", "decision": "DUPLICATE", "reason": "same family"},
            {"rank": 3, "url": "https://b.test/x", "decision": "JS_ONLY", "reason": "needs render"},
        ],
        provider="fixture",
    )
    assert outcome["result_count"] == 3
    assert decision_counts(db, topic="golden_scapegoat") == {"ACCEPTED": 1, "DUPLICATE": 1, "JS_ONLY": 1}
    runs = searches_for(db, topic="golden_scapegoat")
    assert len(runs) == 1 and runs[0]["query"] == "崩铁 黄金替罪羊 龙骸古城"
    assert runs[0]["status"] == "OK" and runs[0]["decisions"]["ACCEPTED"] == 1
    report = summary(db, topic="golden_scapegoat")
    assert report["searches"] == 1 and report["results"] == 3
    assert report["targets_searched"] == 1 and report["targets_with_evidence"] == 1
    db.close()


def test_unknown_decision_is_rejected(tmp_path):
    db = _db(tmp_path)
    run_id = start_search(db, topic="t", target_key="point:1", query="q")
    with pytest.raises(ValueError):
        record_result(db, run_id, rank=1, url="https://a.test/1", decision="MAYBE")
    db.close()


def test_classify_candidate_covers_every_decision(tmp_path):
    db = _db(tmp_path)
    _page(db, "https://www.gamersky.com/handbook/202404/1729233.shtml")
    known = {row["canonical_url"] for row in db.conn.execute("SELECT canonical_url FROM guide_page")}

    assert classify_candidate("https://new.test/a.html", db=db)[0] == "ACCEPTED"
    assert classify_candidate(
        "https://m.gamersky.com/handbook/202404/1729233.shtml", db=db
    )[0] == "MIRROR"
    assert classify_candidate(
        "https://www.gamersky.com/handbook/202404/1729233.shtml", db=db
    )[0] == "ALREADY_IMPORTED"
    assert classify_candidate(
        "https://new.test/a.html", accepted_families={"new.test/a.html"}
    )[0] == "DUPLICATE"
    assert classify_candidate("https://new.test/a.html", js_only=True)[0] == "JS_ONLY"
    assert classify_candidate("https://new.test/a.html", blocked=True)[0] == "BLOCKED"
    assert classify_candidate("https://new.test/a.html", relevant=False)[0] == "IRRELEVANT"
    assert classify_candidate("https://www.gamersky.com/x.html")[0] == "ACCEPTED"
    decision, reason = classify_candidate("https://new.test/a.html", db=db, known_families=known)
    assert decision == "ACCEPTED" and reason
    db.close()


def test_searched_and_unsearched_targets(tmp_path):
    db = _db(tmp_path)
    record_search(
        db,
        topic="t",
        target_key="point:1",
        query="q",
        results=[{"rank": 1, "url": "https://a.test/1", "decision": "ACCEPTED"}],
    )
    targets = [{"target_key": "point:1"}, {"target_key": "point:2"}]
    evidence = searched_targets(db, topic="t")
    assert evidence["point:1"]["accepted"] == 1
    assert [item["target_key"] for item in unsearched_targets(db, targets, topic="t")] == ["point:2"]
    db.close()


def test_cli_search_log_reports_and_writes_json(tmp_path, capsys):
    db_path = tmp_path / "guide.db"
    db = GuideDatabase(db_path)
    record_search(
        db,
        topic="floating_grease",
        target_key="point:5538",
        query="崩铁 浮脂 千星城",
        results=[{"rank": 1, "url": "https://a.test/1", "decision": "IRRELEVANT"}],
    )
    db.close()
    out = tmp_path / "search-log.json"
    code = main(["guides", "search-log", "--db", str(db_path), "--topic", "all", "--json", str(out)])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["summary"]["searches"] == 1
    assert body["summary"]["decisions"] == {"IRRELEVANT": 1}
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["searches"] == 1
