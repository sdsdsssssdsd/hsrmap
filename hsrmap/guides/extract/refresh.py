"""Re-derive a stored page's article text with the current parser (a1-6 §33).

The extractor improves over time: a page imported by an older parser keeps the
text that parser produced, and the audit then reads a truncated article as if
the steps were invented. Re-parsing the HTML we already stored is cheap and
touches nothing but `guide_page.raw_text_path` / `parser_version`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.extract.blocks import BLOCK_PARSER_VERSION, html_to_blocks
from hsrmap.guides.store import RawGuideStore


def extract_page_text(html_path: str | Path | None) -> tuple[str, list[dict[str, Any]]]:
    """The article text and blocks the current parser finds in a stored page."""
    path = Path(str(html_path or ""))
    if not path.is_file():
        return "", []
    html = path.read_text(encoding="utf-8", errors="replace")
    blocks = html_to_blocks(html)
    text = "\n".join(block.get("text") or "" for block in blocks if block.get("text"))
    return text, list(blocks)


def _stored_text(path_value: Any) -> str:
    path = Path(str(path_value or ""))
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def refresh_page_text(
    db: GuideDatabase,
    store: RawGuideStore,
    *,
    page_id: int | None = None,
    apply: bool = False,
    topic: str = "floating_grease",
) -> dict[str, Any]:
    """Re-parse stored HTML and report (or write) the pages whose text changed."""
    sql = "SELECT id, canonical_url, raw_html_path, raw_text_path, parser_version FROM guide_page"
    params: tuple[Any, ...] = ()
    if page_id is not None:
        sql += " WHERE id = ?"
        params = (page_id,)
    sql += " ORDER BY id"
    rows = [dict(row) for row in db.conn.execute(sql, params)]

    changed: list[dict[str, Any]] = []
    missing_html: list[int] = []
    run_id: str | None = None
    for page in rows:
        if not page.get("raw_html_path") or not Path(str(page["raw_html_path"])).is_file():
            missing_html.append(int(page["id"]))
            continue
        text, blocks = extract_page_text(page["raw_html_path"])
        old = _stored_text(page.get("raw_text_path"))
        if old == text:
            continue
        record = {
            "page_id": int(page["id"]),
            "url": page.get("canonical_url"),
            "old_chars": len(old),
            "new_chars": len(text),
        }
        changed.append(record)
        if not apply:
            continue
        if run_id is None:
            run_id = store.start_run(topic)
        key = f"p{page['id']}"
        text_path = store.save_text(run_id, key, text)
        store.save_extracted(run_id, key, blocks)
        db.conn.execute(
            "UPDATE guide_page SET raw_text_path = ?, parser_version = ? WHERE id = ?",
            (str(text_path), BLOCK_PARSER_VERSION, page["id"]),
        )
        record["raw_text_path"] = str(text_path)
    if apply and changed:
        db.conn.commit()
    return {
        "pages_checked": len(rows),
        "pages_changed": len(changed),
        "missing_html": missing_html,
        "parser_version": BLOCK_PARSER_VERSION,
        "applied": bool(apply),
        "run_id": run_id,
        "changes": changed,
    }
