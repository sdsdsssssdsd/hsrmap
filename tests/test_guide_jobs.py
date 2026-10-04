"""Jobs: state, resume, budget and the crawl report (a1-6 §十六–§十八, §28)."""

import json

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides import corpus
from hsrmap.guides.jobs import (
    JOB_STATES,
    JobBudget,
    JobTracker,
    build_report,
    create_job,
    get_job,
    job_status,
    list_jobs,
    render_markdown,
    write_report,
)
from hsrmap.guides.store import RawGuideStore

URLS = [
    "https://news.17173.com/content/01012026/000000001.shtml",
    "https://news.17173.com/content/01012026/000000002.shtml",
    "https://news.17173.com/content/01012026/000000003.shtml",
]


def _patch(monkeypatch, *, fail_titles=()):
    """Offline corpus: every URL fetches and imports unless told otherwise."""

    def fetch(target, client, robots_txt=None):
        if any(marker in target for marker in fail_titles):
            return {"status": "HTTP_BLOCKED", "http_status": 403, "reason": "blocked"}
        return {"status": "ok", "http_status": 200, "html": f"<html><h1>{target}</h1></html>"}

    def ingest(html, target, database, store, extractor, **kwargs):
        source = database.upsert_source({"name": "t", "domain": "t.test"})
        page = database.add_page(source["id"], {"canonical_url": target, "title": "标题"})
        return {
            "page": page,
            "qa": {"pass": True},
            "review": [{"status": "NEEDS_REVIEW", "source_point_id": "1"}],
        }

    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(corpus, "fetch_page", fetch)
    monkeypatch.setattr(corpus, "ingest_page", ingest)


def _arguments(tmp_path, **extra):
    return dict(
        topic="jump",
        inbox=tmp_path / "inbox",
        store=RawGuideStore(tmp_path / "raw", tmp_path / "assets"),
        official_points=[],
        official_maps=[],
        derived_root=tmp_path / "derived",
        **extra,
    )


def test_budget_pauses_a_job_and_resume_finishes_it(tmp_path, monkeypatch):
    _patch(monkeypatch)
    db = GuideDatabase(tmp_path / "guide.db")
    first = corpus.import_real_urls(
        URLS,
        db=db,
        budget={"max_pages": 1},
        report_dir=tmp_path / "reports",
        **_arguments(tmp_path),
    )
    assert len(first) == 1
    jobs = list_jobs(db)
    assert len(jobs) == 1
    job_id = jobs[0]["job_id"]
    status = job_status(db, job_id)
    assert status["state"] == "PAUSED"
    assert status["error"] == "budget:max_pages(1)"
    assert status["counters"]["pages_imported"] == 1
    assert status["remaining"] == 2

    report = json.loads((tmp_path / "reports" / "crawl-report.json").read_text(encoding="utf-8"))
    assert report["state"] == "PAUSED"
    assert report["pages"]["imported"] == 1
    assert report["yield"]["source_yield"] == 1.0
    assert (tmp_path / "reports" / "crawl-report.md").exists()

    # resuming raises the budget (what an operator does after a pause)
    second = corpus.import_real_urls(
        URLS,
        db=db,
        job_id=job_id,
        budget={"max_pages": 5},
        report_dir=tmp_path / "reports",
        **_arguments(tmp_path),
    )
    assert len(second) == 2  # only the two URLs the cursor had left
    final = job_status(db, job_id)
    assert final["state"] == "COMPLETED"
    assert final["remaining"] == 0
    assert final["counters"]["pages_imported"] == 3
    pages = db.conn.execute("SELECT COUNT(*) AS c FROM guide_page").fetchone()["c"]
    assert pages == 3  # idempotent: no page was stored twice
    db.close()


def test_budget_max_failures_fails_the_job(tmp_path, monkeypatch):
    _patch(monkeypatch, fail_titles=("000000001",))
    db = GuideDatabase(tmp_path / "guide.db")
    corpus.import_real_urls(
        URLS[:2],
        db=db,
        budget={"max_failures": 1},
        report_dir=tmp_path / "reports",
        **_arguments(tmp_path),
    )
    job = list_jobs(db)[0]
    assert job["state"] == "FAILED"
    assert job["error"].startswith("budget:max_failures")
    assert job["counters"]["failures"] == 1
    db.close()


def test_budget_limits_are_enforced_individually():
    budget = JobBudget(max_pages=2, max_assets=3, max_bytes=10, max_runtime=5, max_failures=9)
    assert budget.exceeded({"pages_imported": 2}, 0) == "budget:max_pages(2)"
    assert budget.exceeded({"assets_fetched": 3}, 0) == "budget:max_assets(3)"
    assert budget.exceeded({"bytes": 10}, 0) == "budget:max_bytes(10)"
    assert budget.exceeded({}, 5) == "budget:max_runtime(5s)"
    assert budget.exceeded({"failures": 9}, 0) == "budget:max_failures(9)"
    assert budget.exceeded({}, 0) is None
    assert JobBudget.from_mapping({"max_pages": 7}).max_pages == 7
    assert JobBudget.from_mapping(None).as_dict()["max_pages"] == 30


def test_job_state_machine_and_status(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    job = create_job(db, job_type="corpus", topic="jump", cursor={"urls": URLS}, budget={"max_pages": 2})
    assert job["state"] == "PENDING" and job["job_id"]
    assert all(state in JOB_STATES for state in ("PENDING", "RUNNING", "PAUSED", "FAILED", "COMPLETED"))
    status = job_status(db, job["job_id"])
    assert status["urls"] == 3 and status["remaining"] == 3
    tracker = JobTracker(db, job)
    tracker.note_page(URLS[0], {"status": "IMPORTED", "review": ["NEEDS_REVIEW"], "assets": {"total": 1, "bytes": 10}})
    tracker.note_assets([
        {"status": "FETCHED", "byte_size": 100, "http_status": 200, "attempts": [1, 2]},
        {"status": "CACHE_HIT", "byte_size": 50, "http_status": 200, "cache_hit": True, "attempts": [1]},
        {"status": "HTTP_BLOCKED", "http_status": 403, "attempts": [1]},
    ])
    tracker.flush()
    counters = get_job(db, job["job_id"])["counters"]
    assert counters["pages_imported"] == 1 and counters["qa_pass"] == 1
    assert counters["assets_fetched"] == 1 and counters["assets_deduped"] == 1
    assert counters["assets_failed"] == 1 and counters["retries"] == 1
    assert counters["bytes"] == 160  # 10 page + 100 + 50
    assert counters["http"] == {"2xx": 2, "4xx": 1}
    assert counters["hosts"] == {"news.17173.com": 1}
    assert counters["review_units"] == 1 and counters["targets_matched"] == 1
    finished = tracker.finish("COMPLETED")
    assert finished["state"] == "COMPLETED" and finished["remaining"] == 2
    db.close()


def test_crawl_report_has_every_field_of_the_spec(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    job = create_job(db, job_type="corpus", topic="jump", cursor={"urls": URLS})
    tracker = JobTracker(db, job)
    tracker.note_page(URLS[0], {"status": "IMPORTED", "review": [], "assets": {"total": 0}})
    tracker.note_page(URLS[1], {"status": "QA_FAIL", "assets": {"total": 0}})
    report = build_report(job, tracker.counters, elapsed=1.5, results=[], state="COMPLETED")
    for key in (
        "requests",
        "bytes",
        "elapsed_seconds",
        "hosts",
        "http",
        "retries",
        "cache_hits",
        "pages",
        "assets",
        "qa",
        "targets_matched",
        "review_units",
        "yield",
    ):
        assert key in report, key
    assert report["pages"] == {"discovered": 2, "skipped": 0, "imported": 1, "failed": 1}
    assert report["qa"] == {"pass": 1, "fail": 1}
    assert report["yield"]["source_yield"] == 0.5
    paths = write_report(report, json_path=tmp_path / "r.json", md_path=tmp_path / "r.md")
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["requests"] == 2
    markdown = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "Crawl report" in markdown and "source yield" in markdown
    assert render_markdown(report) == markdown
    db.close()


def test_cli_job_status_and_list(tmp_path, capsys):
    db_path = tmp_path / "guide.db"
    db = GuideDatabase(db_path)
    job = create_job(db, job_type="corpus", topic="jump", cursor={"urls": URLS}, budget={"max_pages": 5})
    db.close()
    assert main(["guides", "job-status", "--db", str(db_path), "--job", str(job["job_id"])]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["state"] == "PENDING" and body["remaining"] == 3
    assert main(["guides", "job-list", "--db", str(db_path), "--topic", "all"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["jobs"][0]["job_id"] == job["job_id"]
