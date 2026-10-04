"""Pages that need JavaScript are rendered with a browser that is already there."""

import subprocess
import sys
from pathlib import Path

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides import corpus
from hsrmap.guides.crawler import render
from hsrmap.guides.store import RawGuideStore

URL = "https://www.miyoushe.com/zzz/article/77783449"


def test_render_command_uses_the_flags_that_actually_finish(tmp_path):
    """--headless plus --timeout; the newer headless modes hang in this environment."""
    argv = render.render_command(URL, "chrome.exe", profile=str(tmp_path), budget_ms=9000)
    assert argv[0] == "chrome.exe"
    assert "--headless" in argv and "--headless=new" not in argv
    assert "--timeout=9000" in argv
    assert "--virtual-time-budget=9000" not in argv
    assert "--dump-dom" in argv and argv[-1] == URL
    assert "--user-data-dir=" + str(tmp_path) in argv


def test_browser_path_prefers_chrome_over_edge(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome.exe"
    edge = tmp_path / "msedge.exe"
    chrome.write_bytes(b"x")
    edge.write_bytes(b"x")
    env = {"PATH": str(tmp_path)}
    assert render.browser_path(env) == str(chrome)
    # an explicit choice wins over both
    assert render.browser_path({**env, "GUIDE_BROWSER": str(edge)}) == str(edge)


def test_a_machine_without_a_browser_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "browser_path", lambda env=None: "")
    result = render.render_page(URL, profile_root=tmp_path)
    assert result["status"] == "NO_BROWSER" and result["html"] == ""


def test_the_dumped_dom_is_read_and_the_temp_files_are_cleaned(tmp_path, monkeypatch):
    dom = "<html><body>" + ("针" * 300) + "</body></html>"

    def fake_run(argv, stdout=None, stderr=None, timeout=None, check=False):
        stdout.write(dom.encode("utf-8"))
        stdout.flush()
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    result = render.render_page(URL, browser=sys.executable, profile_root=tmp_path)
    assert result["status"] == "ok"
    assert "针" in result["html"]
    # nothing is left behind: the render folder and its profile are both removed
    leftovers = [path for path in Path(tmp_path).iterdir()]
    assert leftovers == []


def test_an_empty_dump_is_a_failure_not_an_empty_guide(tmp_path, monkeypatch):
    def fake_run(argv, stdout=None, stderr=None, timeout=None, check=False):
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    result = render.render_page(URL, browser=sys.executable, profile_root=tmp_path)
    assert result["status"] == "RENDER_FAILED" and result["reason"] == "empty dom"

def test_reingest_renders_a_page_whose_stored_html_is_an_app_shell(tmp_path, monkeypatch):
    """The stored HTML says JS_RENDER_REQUIRED; the rendered DOM is parsed instead."""
    from hsrmap.guide_db import GuideDatabase
    from hsrmap.guides import reingest as reingest_module
    from hsrmap.guides.crawler import render as render_module

    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/js", "title": "app shell"})
    html_path = tmp_path / "page.html"
    html_path.write_text("<html><body>shell</body></html>", encoding="utf-8")
    db.conn.execute("UPDATE guide_page SET raw_html_path = ? WHERE id = ?", (str(html_path), page["id"]))
    db.conn.commit()

    seen: list[str] = []

    def fake_ingest(html, url, *args, **kwargs):
        seen.append(html)
        if len(seen) == 1:
            return {"page": {"id": page["id"]}, "qa": {"pass": False}, "qa_status": "QA_FAIL",
                    "qa_reason": "JS_RENDER_REQUIRED", "admitted": False, "review": []}
        return {"page": {"id": page["id"]}, "qa": {"pass": True}, "qa_status": "QA_PASS",
                "qa_reason": None, "admitted": True, "review": [{"status": "NEEDS_REVIEW"}]}

    monkeypatch.setattr(reingest_module, "ingest_page", fake_ingest)
    monkeypatch.setattr(
        render_module, "render_page",
        lambda url, **kwargs: {"status": "ok", "url": url, "html": "<html><body>RENDERED DOM</body></html>"},
    )
    result = reingest_module.reingest_page(db, int(page["id"]), topic="jump", render=True)

    assert len(seen) == 2 and "RENDERED DOM" in seen[1]
    assert result["rendered"] is True and result["qa_status"] == "QA_PASS"
    # the stored html is replaced by what we actually parsed
    assert "RENDERED DOM" in html_path.read_text(encoding="utf-8")
    db.close()
