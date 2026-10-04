from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase


FREEZE_ID = "dream-ticker-review-20261002"


def freeze_ticker_review(
    db: GuideDatabase,
    *,
    dest: Path,
    official_targets: int,
    run_id: str = FREEZE_ID,
) -> dict[str, Any]:
    rows = []
    for row in db.conn.execute(
        """
        SELECT r.id, r.page_id, r.status, r.source_point_id, r.draft_json, p.canonical_url, p.title
        FROM review_item r
        LEFT JOIN guide_page p ON p.id = r.page_id
        ORDER BY r.id
        """
    ):
        try:
            draft = json.loads(row["draft_json"] or "{}")
        except json.JSONDecodeError:
            draft = {}
        topic = str(draft.get("topic_key") or draft.get("topic") or "").replace("-", "_")
        if topic != "dream_ticker":
            continue
        if not draft.get("corpus_run_id"):
            draft["corpus_run_id"] = run_id
            db.conn.execute(
                "UPDATE review_item SET draft_json=? WHERE id=?",
                (json.dumps(draft, ensure_ascii=False), int(row["id"])),
            )
        rows.append(
            {
                "item_id": int(row["id"]),
                "page_id": int(row["page_id"]),
                "status": row["status"],
                "source_point_id": row["source_point_id"] or "",
                "url": row["canonical_url"],
                "title": row["title"],
                "map_name": draft.get("resolved_map_name") or draft.get("map_name"),
                "map_id": draft.get("map_id"),
            }
        )
    db.conn.commit()
    payload = {
        "run_id": run_id,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "review_items": len(rows),
        "official_targets": official_targets,
        "publish": "skipped",
        "items": rows,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
