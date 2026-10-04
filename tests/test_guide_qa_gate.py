"""QA is the admission gate: a failing source never reaches Review."""

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides import ingest
from hsrmap.guides.qa import (
    ADMISSION_STATUS,
    admits_review,
    classify_failure,
    evaluate_import,
    record_qa,
    set_override,
)
from hsrmap.guides.store import RawGuideStore


def report(**overrides):
    base = {
        "pass": True,
        "title": "标题",
        "author": "作者",
        "blocks": [{"type": "heading", "text": "x"}, {"type": "image", "asset": "a" * 64}],
        "raw_html_exists": True,
        "images_in_guide_assets": True,
        "image_blocks": 1,
        "order_ok": True,
    }
    base.update(overrides)
    return base


def test_passing_report_is_admitted():
    assert evaluate_import(report()) == (ADMISSION_STATUS, None)
    assert evaluate_import(report()) == ("QA_PASS", None)


def test_typed_reasons():
    assert classify_failure(report(pass_=False) | {"pass": False, "blocks": []}) == "JS_RENDER_REQUIRED"
    assert classify_failure(report(**{"pass": False, "image_blocks": 0})) == "NO_IMAGES"
    assert classify_failure(report(**{"pass": False, "images_in_guide_assets": False})) == "ASSET_FETCH_FAILED"
    assert classify_failure(report(**{"pass": False, "author": "UNKNOWN"})) == "LOW_INFORMATION"
    assert classify_failure(report(**{"pass": False, "raw_html_exists": False})) == "PARSER_ERROR"
    assert classify_failure(report(**{"pass": False, "order_ok": False})) == "PARSER_ERROR"


def test_evaluate_import_returns_fail_with_reason():
    status, reason = evaluate_import(report(**{"pass": False, "image_blocks": 0}))
    assert status == "QA_FAIL" and reason == "NO_IMAGES"


def test_admission_needs_pass_or_an_explicit_override(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/a", "title": "标题"})
    row = dict(db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page["id"],)).fetchone())
    assert admits_review(row) is False
    record_qa(db, page["id"], "QA_PASS", None)
    row = dict(db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page["id"],)).fetchone())
    assert admits_review(row) is True
    record_qa(db, page["id"], "QA_FAIL", "NO_IMAGES")
    row = dict(db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page["id"],)).fetchone())
    assert admits_review(row) is False
    set_override(db, page["id"], operator="human", reason="checked manually")
    row = dict(db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page["id"],)).fetchone())
    assert admits_review(row) is True
    db.close()


def test_override_requires_operator_and_reason(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/b", "title": "标题"})
    try:
        set_override(db, page["id"], operator="", reason="why")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    db.close()


class _Provider:
    def extract_sections(self, blocks, page_id=None, topic=""):
        return {"sections": []}

    def classify_article(self, blocks):
        return {"topics": []}


def test_ingest_quarantines_a_failing_page(tmp_path, monkeypatch):
    db = GuideDatabase(tmp_path / "guide.db")
    store = RawGuideStore(tmp_path / "raw", tmp_path / "assets")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(
        source["id"],
        {"canonical_url": "https://t.test/a", "title": "标题", "author": "作者"},
    )
    html_path = tmp_path / "a.html"
    html_path.write_text("<html></html>", encoding="utf-8")
    db.conn.execute("UPDATE guide_page SET raw_html_path = ? WHERE id = ?", (str(html_path), page["id"]))
    db.conn.commit()

    def fake_import(html, url, database, raw_store, **kwargs):
        return {
            "page": dict(database.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page["id"],)).fetchone()),
            "blocks": [],
        }

    monkeypatch.setattr(ingest, "import_page", fake_import)
    result = ingest.ingest_page("<html></html>", "https://t.test/a", db, store, _Provider(), topic="jump")
    assert result["qa_status"] == "QA_FAIL"
    assert result["qa_reason"] == "JS_RENDER_REQUIRED"
    assert result["quarantined"] is True
    assert result["admitted"] is False
    assert result["review"] == []
    assert db.conn.execute("SELECT COUNT(*) AS c FROM review_item").fetchone()["c"] == 0
    row = db.conn.execute("SELECT qa_status, qa_reason, qa_checked_at FROM guide_page WHERE id = ?", (page["id"],)).fetchone()
    assert row["qa_status"] == "QA_FAIL" and row["qa_reason"] == "JS_RENDER_REQUIRED" and row["qa_checked_at"]
    db.close()

class _FakeStore:
    def __init__(self, root):
        self.assets_root = root


def _store_with(tmp_path, shas):
    root = tmp_path / "assets"
    for sha in shas:
        folder = root / sha[:2]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (sha + ".png")).write_bytes(b"x")
    return _FakeStore(root)


def _result(tmp_path, blocks):
    html = tmp_path / "p.html"
    html.write_text("<html></html>", encoding="utf-8")
    return {
        "page": {"title": "标题", "author": "作者", "raw_html_path": str(html), "published_at": None},
        "blocks": blocks,
    }


def test_asset_coverage_is_a_ratio_not_all_or_nothing(tmp_path):
    from hsrmap.guides.qa import inspect_import

    sha = "a" * 64
    store = _store_with(tmp_path, [sha])
    blocks = [{"type": "heading", "text": "h"}]
    blocks += [{"type": "image", "asset": sha} for _ in range(9)]
    blocks += [{"type": "image"}]
    report = inspect_import(_result(tmp_path, blocks), store)
    assert report["images_stored"] == 9
    assert report["asset_coverage"] == 0.9
    assert report["images_in_guide_assets"] is True
    assert report["pass"] is True


def test_majority_missing_assets_still_fails(tmp_path):
    from hsrmap.guides.qa import inspect_import

    sha = "a" * 64
    store = _store_with(tmp_path, [sha])
    blocks = [{"type": "heading", "text": "h"}]
    blocks += [{"type": "image", "asset": sha} for _ in range(5)]
    blocks += [{"type": "image"} for _ in range(5)]
    report = inspect_import(_result(tmp_path, blocks), store)
    assert report["asset_coverage"] == 0.5
    assert report["images_in_guide_assets"] is False
    assert evaluate_import(report) == ("QA_FAIL", "ASSET_FETCH_FAILED")


def test_empty_img_tags_do_not_create_blocks():
    from hsrmap.guides.extract.blocks import html_to_blocks

    blocks = html_to_blocks('<img alt="no src"><img src="https://x.test/a.png">')
    assert [block["type"] for block in blocks] == ["image"]
    assert blocks.dropped_empty_images == 1
