from __future__ import annotations

from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.publish.publisher import publish_page


def list_pending(db: GuideDatabase) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.conn.execute(
            "SELECT * FROM review_item WHERE status IN ('pending','NEW','NEEDS_REVIEW','AUTO_ACCEPTED','AUTO_SUGGEST') ORDER BY id"
        )
    ]


def approve(db: GuideDatabase, item_id: int, source_point_id: str, page: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    db.conn.execute("UPDATE review_item SET status = 'APPROVED', source_point_id = ? WHERE id = ?", (source_point_id, item_id))
    db.conn.commit()
    published = publish_page(
        db,
        page,
        [{"source_point_id": source_point_id, "heading": page.get("title"), "confidence": 1.0, "status": "approved"}],
        steps,
    )
    return published[0]
