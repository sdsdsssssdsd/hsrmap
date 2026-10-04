from __future__ import annotations

import json
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.semantic.classifier import classify_topics


def backfill_page_topics(db: GuideDatabase) -> dict[str, Any]:
    bound = 0
    pages = 0
    for page in db.conn.execute("SELECT id, title FROM guide_page"):
        pages += 1
        have = {row["topic_key"] for row in db.topics_for_page(int(page["id"]))}
        keys: set[str] = set()
        for row in db.conn.execute("SELECT draft_json FROM review_item WHERE page_id = ?", (page["id"],)):
            if not row["draft_json"]:
                continue
            try:
                draft = json.loads(row["draft_json"])
            except json.JSONDecodeError:
                continue
            key = draft.get("topic_key") or draft.get("topic")
            if key:
                keys.add(str(key).replace("-", "_"))
        for item in classify_topics([{"type": "heading", "text": page["title"] or ""}]).get("topics") or []:
            keys.add(item["topic_key"])
        for key in keys - have:
            db.bind_page_topic(int(page["id"]), key, 1.0)
            bound += 1
    drafts = 0
    for row in db.conn.execute("SELECT id, page_id, draft_json FROM review_item"):
        try:
            draft = json.loads(row["draft_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if draft.get("topic_key") or draft.get("topic"):
            continue
        topics = db.topics_for_page(int(row["page_id"]))
        if not topics:
            continue
        draft["topic_key"] = topics[0]["topic_key"]
        db.conn.execute(
            "UPDATE review_item SET draft_json=? WHERE id=?",
            (json.dumps(draft, ensure_ascii=False), int(row["id"])),
        )
        drafts += 1
    db.conn.commit()
    return {"pages": pages, "bound": bound, "drafts": drafts, "publish": "skipped"}
