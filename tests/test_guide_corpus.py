"""Corpus crawl is incremental: a known URL is never fetched twice."""

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides import corpus
from hsrmap.guides.store import RawGuideStore

URL = "https://news.17173.com/content/01012026/000000001.shtml"
NEW_URL = "https://news.17173.com/content/01012026/000000002.shtml"


def _store(tmp_path):
    return RawGuideStore(tmp_path / "raw", tmp_path / "assets")


def test_known_url_is_skipped_without_a_request(tmp_path, monkeypatch):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "17173", "domain": "17173.com"})
    db.add_page(source["id"], {"canonical_url": URL, "title": "已入库"})
    fetched: list[str] = []
    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(
        corpus,
        "fetch_page",
        lambda target, client, robots_txt=None: fetched.append(target) or {"status": "ok", "html": "<html></html>"},
    )
    reports = corpus.import_real_urls(
        [URL],
        topic="jump",
        inbox=tmp_path / "inbox",
        db=db,
        store=_store(tmp_path),
        official_points=[],
        official_maps=[],
        derived_root=tmp_path / "derived",
    )
    assert reports == [{"url": URL, "status": "SKIPPED_ALREADY_IMPORTED"}]
    assert fetched == []
    db.close()


def test_refresh_refetches_known_url(tmp_path, monkeypatch):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "17173", "domain": "17173.com"})
    db.add_page(source["id"], {"canonical_url": URL, "title": "已入库"})
    fetched: list[str] = []
    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(
        corpus,
        "fetch_page",
        lambda target, client, robots_txt=None: fetched.append(target) or {"status": "ok", "html": "<html></html>"},
    )
    monkeypatch.setattr(
        corpus,
        "ingest_page",
        lambda *args, **kwargs: {"page": {"id": 1}, "qa": {"pass": True}, "review": []},
    )
    reports = corpus.import_real_urls(
        [URL],
        topic="jump",
        inbox=tmp_path / "inbox",
        db=db,
        store=_store(tmp_path),
        official_points=[],
        official_maps=[],
        skip_known=False,
        derived_root=tmp_path / "derived",
    )
    assert fetched == [URL]
    assert reports[0]["status"] == "IMPORTED"
    db.close()


def test_new_url_is_fetched_and_ingested(tmp_path, monkeypatch):
    db = GuideDatabase(tmp_path / "guide.db")
    fetched: list[str] = []
    captured: dict = {}
    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(
        corpus,
        "fetch_page",
        lambda target, client, robots_txt=None: fetched.append(target)
        or {"status": "ok", "html": "<html><h1>标题</h1></html>"},
    )

    def fake_ingest(html, target, database, store, extractor, **kwargs):
        captured["topic"] = kwargs.get("topic")
        return {
            "page": {"id": 7, "title": "标题", "author": "作者"},
            "qa": {"pass": True},
            "review": [{"status": "NEEDS_REVIEW"}],
        }

    monkeypatch.setattr(corpus, "ingest_page", fake_ingest)
    reports = corpus.import_real_urls(
        [NEW_URL],
        topic="jump",
        inbox=tmp_path / "inbox",
        db=db,
        store=_store(tmp_path),
        official_points=[],
        official_maps=[],
        derived_root=tmp_path / "derived",
    )
    assert fetched == [NEW_URL]
    assert reports[0]["status"] == "IMPORTED"
    assert reports[0]["review"] == ["NEEDS_REVIEW"]
    assert captured["topic"] == "jump"
    db.close()

def test_corpus_run_leaves_search_evidence(tmp_path, monkeypatch):
    """A crawl answers "why is this target still empty?" from the database."""
    from hsrmap.guides.evidence import summary

    db = GuideDatabase(tmp_path / "guide.db")
    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(
        corpus,
        "fetch_page",
        lambda target, client, robots_txt=None: {"status": "ok", "html": "<html><h1>标题</h1></html>"},
    )

    def fake_ingest(html, target, database, store, extractor, **kwargs):
        source = database.upsert_source({"name": "t", "domain": "t.test"})
        page = database.add_page(source["id"], {"canonical_url": target, "title": "标题"})
        return {"page": page, "qa": {"pass": True}, "review": []}

    monkeypatch.setattr(corpus, "ingest_page", fake_ingest)
    arguments = dict(
        topic="jump",
        inbox=tmp_path / "inbox",
        db=db,
        store=_store(tmp_path),
        official_points=[],
        official_maps=[],
        derived_root=tmp_path / "derived",
    )
    first = corpus.import_real_urls([NEW_URL], **arguments)
    assert first[0]["status"] == "IMPORTED"
    report = summary(db, topic="jump")
    assert report["searches"] == 1
    assert report["decisions"] == {"ACCEPTED": 1}

    second = corpus.import_real_urls([NEW_URL, "https://m.17173.com/content/01012026/000000002.shtml"], **arguments)
    assert second[0]["status"] == "SKIPPED_ALREADY_IMPORTED"
    assert second[1]["status"] == "SKIPPED_MIRROR"
    assert summary(db, topic="jump")["decisions"] == {
        "ACCEPTED": 1,
        "ALREADY_IMPORTED": 1,
        "MIRROR": 1,
    }
    db.close()

def test_a_page_that_needs_a_browser_is_recorded_as_js_only(tmp_path, monkeypatch):
    """QA says JS_RENDER_REQUIRED; the ledger has to say JS_ONLY, not IRRELEVANT."""
    from hsrmap.guides.evidence import decision_counts

    db = GuideDatabase(tmp_path / "guide.db")
    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(
        corpus,
        "fetch_page",
        lambda target, client, robots_txt=None: {"status": "ok", "html": "<html></html>"},
    )
    monkeypatch.setattr(
        corpus,
        "ingest_page",
        lambda *args, **kwargs: {
            "page": {"id": 7, "qa_reason": "JS_RENDER_REQUIRED"},
            "qa": {"pass": False, "reason": None},
            "qa_status": "QA_FAIL",
            "qa_reason": "JS_RENDER_REQUIRED",
            "admitted": False,
            "review": [],
        },
    )
    reports = corpus.import_real_urls(
        [NEW_URL],
        topic="jump",
        inbox=tmp_path / "inbox",
        db=db,
        store=_store(tmp_path),
        official_points=[],
        official_maps=[],
        derived_root=tmp_path / "derived",
    )
    assert reports[0]["status"] == "QA_FAIL"
    assert reports[0]["qa_pass"] is False
    assert decision_counts(db, topic="jump").get("JS_ONLY") == 1
    db.close()


def test_a_qa_reject_keeps_its_reason_in_the_ledger(tmp_path, monkeypatch):
    from hsrmap.guides.evidence import decision_counts

    db = GuideDatabase(tmp_path / "guide.db")
    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(
        corpus,
        "fetch_page",
        lambda target, client, robots_txt=None: {"status": "ok", "html": "<html></html>"},
    )
    monkeypatch.setattr(
        corpus,
        "ingest_page",
        lambda *args, **kwargs: {
            "page": {"id": 8},
            "qa": {"pass": False},
            "qa_status": "QA_FAIL",
            "qa_reason": "NO_TEXT_BLOCKS",
            "admitted": False,
            "review": [],
        },
    )
    corpus.import_real_urls(
        [NEW_URL],
        topic="jump",
        inbox=tmp_path / "inbox",
        db=db,
        store=_store(tmp_path),
        official_points=[],
        official_maps=[],
        derived_root=tmp_path / "derived",
    )
    row = db.conn.execute(
        "SELECT reason FROM source_search_result WHERE url = ? ORDER BY id DESC LIMIT 1", (NEW_URL,)
    ).fetchone()
    assert "NO_TEXT_BLOCKS" in str(row["reason"])
    assert decision_counts(db, topic="jump").get("IRRELEVANT") == 1
    db.close()

def test_a_page_that_needs_javascript_is_rendered_and_imported_again(tmp_path, monkeypatch):
    """First pass says JS_RENDER_REQUIRED; the rendered DOM is imported instead."""
    from hsrmap.guides.evidence import decision_counts

    db = GuideDatabase(tmp_path / "guide.db")
    calls = {"ingest": 0}

    def fake_ingest(html, url, *args, **kwargs):
        calls["ingest"] += 1
        if calls["ingest"] == 1:
            return {
                "page": {"id": 9},
                "qa": {"pass": False},
                "qa_status": "QA_FAIL",
                "qa_reason": "JS_RENDER_REQUIRED",
                "admitted": False,
                "review": [],
            }
        return {
            "page": {"id": 9},
            "qa": {"pass": True},
            "qa_status": "QA_PASS",
            "qa_reason": None,
            "admitted": True,
            "review": [{"status": "NEEDS_REVIEW"}],
        }

    monkeypatch.setattr(corpus, "ingest_page", fake_ingest)
    monkeypatch.setattr(corpus, "browser_path", lambda env=None: "chrome.exe")
    monkeypatch.setattr(
        corpus,
        "render_page",
        lambda url, **kwargs: {"status": "ok", "url": url, "html": "<html>" + "针" * 300 + "</html>"},
    )
    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(
        corpus,
        "fetch_page",
        lambda target, client, robots_txt=None: {"status": "ok", "html": "<html></html>"},
    )
    reports = corpus.import_real_urls(
        [NEW_URL],
        topic="jump",
        inbox=tmp_path / "inbox",
        db=db,
        store=_store(tmp_path),
        official_points=[],
        official_maps=[],
        derived_root=tmp_path / "derived",
    )
    assert calls["ingest"] == 2
    assert reports[0]["status"] == "IMPORTED"
    assert reports[0]["rendered"] is True
    row = db.conn.execute(
        "SELECT decision, reason FROM source_search_result WHERE url = ? ORDER BY id DESC LIMIT 1", (NEW_URL,)
    ).fetchone()
    assert row["decision"] == "ACCEPTED" and "render" in str(row["reason"])
    assert decision_counts(db, topic="jump").get("ACCEPTED") == 1
    db.close()


def test_rendering_can_be_turned_off(tmp_path, monkeypatch):
    db = GuideDatabase(tmp_path / "guide.db")
    rendered = {"called": False}

    def fake_render(url, **kwargs):
        rendered["called"] = True
        return {"status": "ok", "url": url, "html": "<html>" + "针" * 300 + "</html>"}

    monkeypatch.setattr(corpus, "render_page", fake_render)
    monkeypatch.setattr(corpus, "_robots", lambda client, target: None)
    monkeypatch.setattr(
        corpus,
        "fetch_page",
        lambda target, client, robots_txt=None: {"status": "ok", "html": "<html></html>"},
    )
    monkeypatch.setattr(
        corpus,
        "ingest_page",
        lambda *args, **kwargs: {
            "page": {"id": 10},
            "qa": {"pass": False},
            "qa_status": "QA_FAIL",
            "qa_reason": "JS_RENDER_REQUIRED",
            "admitted": False,
            "review": [],
        },
    )
    corpus.import_real_urls(
        [NEW_URL],
        topic="jump",
        inbox=tmp_path / "inbox",
        db=db,
        store=_store(tmp_path),
        official_points=[],
        official_maps=[],
        render=False,
        derived_root=tmp_path / "derived",
    )
    assert rendered["called"] is False
    db.close()
