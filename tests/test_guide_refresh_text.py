"""A stored page is re-derivable: an old parser must not freeze its article."""

from pathlib import Path

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.extract.refresh import extract_page_text, refresh_page_text
from hsrmap.guides.store import RawGuideStore


def _page(db, tmp_path, url, html, text):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": "标题", "author": "作者"})
    html_path = tmp_path / f"page-{page['id']}.html"
    html_path.write_text(html, encoding="utf-8")
    text_path = tmp_path / f"page-{page['id']}.txt"
    text_path.write_text(text, encoding="utf-8")
    db.conn.execute(
        "UPDATE guide_page SET raw_html_path = ?, raw_text_path = ? WHERE id = ?",
        (str(html_path), str(text_path), page["id"]),
    )
    db.conn.commit()
    return page


def test_extract_page_text_reads_the_stored_html(tmp_path):
    html_path = tmp_path / "p.html"
    html_path.write_text("<p>第一段</p><p>第二段</p>", encoding="utf-8")
    text, blocks = extract_page_text(html_path)
    assert text == "第一段\n第二段"
    assert len(blocks) == 2
    assert extract_page_text(tmp_path / "missing.html") == ("", [])


def test_refresh_rewrites_only_pages_whose_text_changed(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    stale = _page(db, tmp_path, "https://t.test/a", "<p>正文第一段</p><p>正文第二段</p>", "正文第一段")
    fresh = _page(db, tmp_path, "https://t.test/b", "<p>同样的正文</p>", "同样的正文")
    store = RawGuideStore(tmp_path / "raw", tmp_path / "assets")

    scoped = refresh_page_text(db, store, page_id=fresh["id"], apply=False)
    assert scoped["pages_checked"] == 1 and scoped["pages_changed"] == 0

    dry = refresh_page_text(db, store, apply=False)
    assert dry["applied"] is False
    assert dry["pages_changed"] == 1
    assert dry["changes"][0]["page_id"] == stale["id"]
    assert Path(dict(db.conn.execute("SELECT raw_text_path FROM guide_page WHERE id = ?", (stale["id"],)).fetchone())["raw_text_path"]).read_text(encoding="utf-8") == "正文第一段"

    applied = refresh_page_text(db, store, apply=True)
    assert applied["pages_changed"] == 1 and applied["applied"] is True and applied["run_id"]
    row = dict(db.conn.execute("SELECT raw_text_path, parser_version FROM guide_page WHERE id = ?", (stale["id"],)).fetchone())
    assert Path(row["raw_text_path"]).read_text(encoding="utf-8") == "正文第一段\n正文第二段"
    assert row["parser_version"]
    assert refresh_page_text(db, store, apply=False)["pages_changed"] == 0
    db.close()
