from __future__ import annotations

from typing import Any

from hsrmap.guide_db import GuideDatabase


def publish_page(db: GuideDatabase, page: dict[str, Any], bindings: list[dict[str, Any]], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    published = []
    for bind in bindings:
        if bind.get("status") not in {"approved", "APPROVED"}:
            status = "AUTO_SUGGEST" if bind.get("status") in {"auto", "AUTO_ACCEPTED", "AUTO_SUGGEST"} else "NEEDS_REVIEW"
            db.conn.execute(
                "INSERT INTO review_item(page_id, reason, status, created_at, source_point_id) VALUES (?, ?, ?, datetime('now'), ?)",
                (page["id"], f"{bind.get('status')} {bind.get('confidence')}", status, bind.get("source_point_id")),
            )
            db.conn.commit()
            continue
        entry = db.create_entry(
            {
                "source_point_id": bind["source_point_id"],
                "title": page.get("title") or bind.get("heading") or "社区攻略",
                "summary": bind.get("heading"),
                "source_name": page.get("author") or page.get("source_claim"),
                "source_url": page.get("canonical_url"),
                "source_kind": "Community",
                "author": page.get("author"),
                "status": "published",
                "steps": steps or [{"text": bind.get("heading") or page.get("title") or "", "images": []}],
            }
        )
        db.bind_point(page["id"], bind)
        published.append(entry)
    return published
