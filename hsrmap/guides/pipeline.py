from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.crawler.base import _between, _strip_tags, adapter_for
from hsrmap.guides.dedup import classify_duplicate
from hsrmap.guides.extract.blocks import html_to_blocks, is_ad_image
from hsrmap.guides.store import RawGuideStore


def import_page(
    html: str,
    url: str,
    db: GuideDatabase,
    store: RawGuideStore,
    *,
    topic: str = "floating_grease",
    fetch_asset=None,
) -> dict[str, Any]:
    adapter = adapter_for(url)
    meta = adapter.parse_page(html, url)
    if not meta.get("author"):
        meta["author"] = "unknown"
    if not meta.get("published_at"):
        meta["published_at"] = _strip_tags(_between(html, 'class="time">', "</") or "") or None
    meta["content_sha256"] = sha256(html.encode("utf-8")).hexdigest()
    source = db.upsert_source(adapter.source_record())
    page = db.add_page(source["id"], meta)
    run_id = store.start_run(topic)
    key = f"p{page['id']}"
    html_path = store.save_html(run_id, key, html)
    image_map: dict[str, str] = {}
    for src in adapter.extract_assets(html):
        if is_ad_image(src):
            continue
        if fetch_asset is None:
            continue
        body = fetch_asset(src)
        if not body or body.lstrip().startswith(b"<"):
            continue
        asset = store.save_asset(body, src)
        image_map[src] = asset.sha256
    blocks = html_to_blocks(html, image_map)
    text = "\n".join(b.get("text") or "" for b in blocks if b.get("text"))
    text_path = store.save_text(run_id, key, text)
    extracted = store.save_extracted(run_id, key, blocks)
    db.conn.execute(
        "UPDATE guide_page SET raw_html_path=?, raw_text_path=?, crawl_status=?, content_sha256=?, published_at=? WHERE id=?",
        (str(html_path), str(text_path), "FETCHED", meta.get("content_sha256"), meta.get("published_at"), page["id"]),
    )
    db.conn.commit()
    page = dict(db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page["id"],)).fetchone())
    _cluster_reprints(db, page, text)
    page = dict(db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page["id"],)).fetchone())
    return {"page": page, "blocks": blocks, "extracted_path": str(extracted), "source": source}


def _cluster_reprints(db: GuideDatabase, page: dict[str, Any], text: str = "") -> None:
    author = page.get("author")
    if not author or str(author).lower() == "unknown":
        return
    left = {
        "author": author,
        "title": page.get("title") or "",
        "text": text,
        "content_sha256": page.get("content_sha256"),
    }
    for row in db.conn.execute("SELECT * FROM guide_page WHERE id != ?", (page["id"],)):
        other = dict(row)
        other_text = ""
        raw_text = other.get("raw_text_path")
        if raw_text and Path(raw_text).exists():
            other_text = Path(raw_text).read_text(encoding="utf-8", errors="replace")
        kind = classify_duplicate(
            left,
            {
                "author": other.get("author"),
                "title": other.get("title") or "",
                "text": other_text,
                "content_sha256": other.get("content_sha256"),
            },
        )
        if kind not in {"DUPLICATE_EXACT", "DUPLICATE_PROBABLE"}:
            continue
        if other.get("cluster_id"):
            db.attach_page_to_cluster(page["id"], other["cluster_id"])
            return
        cluster = db.upsert_cluster({"title": other.get("title") or page.get("title"), "canonical_page_id": other["id"]})
        db.attach_page_to_cluster(other["id"], cluster["id"])
        db.attach_page_to_cluster(page["id"], cluster["id"])
        return
