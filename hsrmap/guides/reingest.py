"""Re-parse a stored page with its assets fetched (a1-6 §27/§31).

A page imported while its images were unfetched can come back with only a scope
line for steps: the article's walkthrough lives in the pictures, so the extractor
has nothing to work with. Re-ingesting the stored HTML with an asset fetcher both
downloads those pictures and re-parses the page, which is what turns "共20只" from
a stub into a 34-step guide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.assets import AssetCache, AssetFetcher
from hsrmap.guides.ingest import ingest_page
from hsrmap.guides.llm.provider import build_provider
from hsrmap.guides.topics.official import official_maps_for_topic, official_points_for_topic
from hsrmap.paths import GUIDE_CACHE


def reingest_page(
    db: GuideDatabase,
    page_id: int,
    *,
    topic: str = "",
    store: Any = None,
    cache_root: Path | str | None = None,
    ctx: Any = None,
    render: bool | None = None,
) -> dict[str, Any]:
    """Re-run the pipeline for one stored page; returns a small report."""
    row = db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (int(page_id),)).fetchone()
    if row is None:
        return {"error": "page not found", "page_id": int(page_id)}
    page = dict(row)
    html = (
        Path(page["raw_html_path"]).read_text(encoding="utf-8", errors="replace")
        if page.get("raw_html_path")
        else ""
    )
    if not html:
        return {"error": "page has no stored html", "page_id": int(page_id)}
    key = str(topic or "").replace("-", "_")
    try:
        points = list(official_points_for_topic(key, ctx=ctx) or []) if key else []
    except Exception:  # noqa: BLE001 - a missing topic must not stop the re-parse
        points = []
    try:
        maps = list(official_maps_for_topic(key, ctx=ctx) or []) if key else []
    except Exception:  # noqa: BLE001
        maps = []
    cache = AssetCache(Path(cache_root) if cache_root else GUIDE_CACHE, db=db)
    fetcher = AssetFetcher(cache=cache)
    asset_log: list[dict] = []

    def fetch_asset(src: str, _page: str = str(page["canonical_url"])) -> bytes:
        try:
            outcome = fetcher.fetch(src, _page)
        except Exception as exc:  # noqa: BLE001 - typed failure
            asset_log.append({"status": "NETWORK_ERROR", "source_url": src, "reason": str(exc)[:120]})
            return b""
        asset_log.append(outcome.to_report())
        return outcome.body if outcome.ok else b""

    def _run(source_html: str) -> dict[str, Any]:
        return ingest_page(
            source_html,
            str(page["canonical_url"]),
            db,
            store,
            build_provider("deterministic"),
            topic=key,
            fetch_asset=fetch_asset,
            official_points=points,
            official_maps=maps,
        )

    result = _run(html)
    rendered = False
    if str(result.get("qa_reason") or "") == "JS_RENDER_REQUIRED" and render is not False:
        #: a stored app shell re-parses to nothing; the same URL rendered gives the
        #: extractor the page it needs. Mirrors the retry that "guides corpus" does
        #: on a live crawl, so re-importing an old JS page actually recovers it.
        from hsrmap.guides.crawler.render import browser_path, render_page

        if render or browser_path():
            outcome = render_page(str(page["canonical_url"]))
            if str(outcome.get("status")) == "ok":
                dom = str(outcome.get("html") or "")
                if page.get("raw_html_path"):
                    Path(page["raw_html_path"]).write_text(dom, encoding="utf-8")
                result = _run(dom)
                rendered = True
    return {
        "rendered": rendered,
        "page_id": int(page_id),
        "topic": key,
        "qa_status": result["qa_status"],
        "qa_reason": result["qa_reason"],
        "admitted": result["admitted"],
        "review_items": len(result.get("review") or []),
        "assets": len(asset_log),
        "url": str(page["canonical_url"]),
    }
