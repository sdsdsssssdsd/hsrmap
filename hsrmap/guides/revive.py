"""Thin drafts are usually a parse artefact, not a dead end (a1-6 §27/§31).

A page whose pictures were never fetched arrives with one scope line for steps and
is refused as too thin — correctly, at that moment. Re-ingesting it fetches the
pictures and re-parses the page, which is how p82's "共20只" became a 34-step guide
covering 20 nymph points. This pass finds every page in that state and (with
--apply) re-ingests it, then re-checks the binding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.ledger import published_point_ids
from hsrmap.guides.rebuild import article_index, article_text
from hsrmap.guides.reingest import reingest_page
from hsrmap.guides.review.service import (
    MIN_REGION_SET_STEPS,
    region_set_target_for,
    substantive_steps,
)
from hsrmap.guides.topics.loader import list_topics
from hsrmap.guides.topics.official import official_points_for_topic


def _points_by_topic(ctx: Any = None) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for item in list_topics(enabled_only=True):
        key = str(item["topic_key"])
        try:
            out[key] = list(official_points_for_topic(key, ctx=ctx) or [])
        except Exception:  # noqa: BLE001 - a broken topic must not stop the pass
            out[key] = []
    return out


def _state(
    db: GuideDatabase,
    page_id: int,
    topic: str,
    points: list[dict[str, Any]],
    covered: set[str],
    index: dict[str, str],
    urls: dict[int, str],
    cache: dict[int, str],
) -> dict[str, Any] | None:
    """The best binding this page still has for the topic, thin or not."""
    if page_id not in cache:
        cache[page_id] = article_text(index, urls.get(page_id, "")) or ""
    article = cache[page_id]
    if not article:
        return None
    best: dict[str, Any] | None = None
    for row in db.conn.execute(
        "SELECT id, draft_json FROM review_item WHERE page_id = ? AND status IN ('NEEDS_REVIEW','AUTO_SUGGEST')",
        (int(page_id),),
    ):
        draft = json.loads(row["draft_json"] or "{}")
        if str(draft.get("topic_key") or "") != topic:
            continue
        steps = [
            str((step or {}).get("text") or "")
            for step in (draft.get("steps") or [])
            if isinstance(step, dict)
        ]
        item_text = " ".join([str(draft.get("map_name") or ""), *steps])
        target = region_set_target_for(draft, topic, points, article, item_text)
        if not target:
            continue
        members = [m for m in target.split(":")[1].split("-") if m]
        substantive = len(substantive_steps(draft.get("steps") or []))
        entry = {
            "item_id": int(row["id"]),
            "target": target,
            "members": len(members),
            "new_points": len([m for m in members if m not in covered]),
            "steps": substantive,
            "images": len([i for i in (draft.get("images") or []) if i.get("sha256")]),
        }
        if best is None or (entry["steps"], entry["new_points"]) > (best["steps"], best["new_points"]):
            best = entry
    return best


def revive_candidates(
    db: GuideDatabase,
    *,
    ctx: Any = None,
    published: GuideDatabase | None = None,
    limit: int | None = None,
    points_by_topic: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Pages with a known binding, uncovered points, and too few steps to approve."""
    covered = published_point_ids(published) if published is not None else published_point_ids(db)
    points_by_topic = points_by_topic if points_by_topic is not None else _points_by_topic(ctx)
    index = article_index(db)
    urls = {
        int(row["id"]): str(row["canonical_url"])
        for row in db.conn.execute("SELECT id, canonical_url FROM guide_page")
    }
    cache: dict[int, str] = {}
    pages = sorted({int(row["page_id"]) for row in db.conn.execute("SELECT page_id FROM review_item")})
    found: list[dict[str, Any]] = []
    for page_id in pages:
        for topic, points in points_by_topic.items():
            if not points:
                continue
            state = _state(db, page_id, topic, points, covered, index, urls, cache)
            if not state or not state["new_points"] or state["steps"] >= MIN_REGION_SET_STEPS:
                continue
            found.append({"page_id": page_id, "topic": topic, **state})
    found.sort(key=lambda entry: (-entry["new_points"], entry["page_id"]))
    return found[: int(limit)] if limit else found


def revive_thin(
    db: GuideDatabase,
    *,
    apply: bool = False,
    limit: int | None = None,
    store: Any = None,
    cache_root: Path | str | None = None,
    ctx: Any = None,
    published: GuideDatabase | None = None,
    points_by_topic: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Report (and with apply, re-ingest) the pages whose drafts are too thin."""
    candidates = revive_candidates(
        db, ctx=ctx, published=published, limit=limit, points_by_topic=points_by_topic
    )
    if not apply:
        return {
            "applied": False,
            "candidates": len(candidates),
            "new_points": sum(entry["new_points"] for entry in candidates),
            "entries": candidates[:40],
        }
    covered = published_point_ids(published) if published is not None else published_point_ids(db)
    points_by_topic = points_by_topic if points_by_topic is not None else _points_by_topic(ctx)
    index = article_index(db)
    urls = {
        int(row["id"]): str(row["canonical_url"])
        for row in db.conn.execute("SELECT id, canonical_url FROM guide_page")
    }
    cache: dict[int, str] = {}
    revived: list[dict[str, Any]] = []
    still: list[dict[str, Any]] = []
    for candidate in candidates:
        page_id, topic = int(candidate["page_id"]), str(candidate["topic"])
        result = reingest_page(db, page_id, topic=topic, store=store, cache_root=cache_root, ctx=ctx)
        cache.pop(page_id, None)
        after = _state(db, page_id, topic, points_by_topic.get(topic) or [], covered, index, urls, cache)
        record = {
            **candidate,
            "after": after,
            "ingest": {key: result.get(key) for key in ("qa_status", "review_items", "assets", "error")},
        }
        if after and after["steps"] >= MIN_REGION_SET_STEPS and after["new_points"]:
            revived.append(record)
        else:
            still.append(record)
    return {
        "applied": True,
        "candidates": len(candidates),
        "revived": len(revived),
        "still_thin": len(still),
        "revived_points": sum(entry["after"]["new_points"] for entry in revived),
        "entries": revived + still,
    }
