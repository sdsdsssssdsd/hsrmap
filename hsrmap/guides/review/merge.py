"""Evidence merge: one review object per unit signature (a1-6 §13).

Three articles, their paginated continuations and their mirrors describing the
same unit produce the same signature, so the Review Console should show **one
object with N evidences**, not N objects. `merge_duplicates` folds every
duplicate group into a keeper:

- the keeper is the strongest status (AUTO_SUGGEST before NEEDS_REVIEW), then
  the oldest row, so merging never downgrades a suggestion;
- the keeper's evidence gains a `merged_sources` list naming every folded item;
- folded items become `MERGED` with `reason = merged_into:<keeper id>` — kept
  for audit, never deleted.
"""

from __future__ import annotations

import json
from typing import Any

from hsrmap.guides.signature import signature_for_draft

#: Only these statuses take part in a merge; APPROVED/PUBLISHED/REJECTED never do.
MERGEABLE_STATUSES = ("NEEDS_REVIEW", "AUTO_SUGGEST", "MATCHED", "pending", "NEW")

#: Lower wins. A suggestion must never be folded into a plain review item.
_KEEP_PRIORITY = {"AUTO_SUGGEST": 0, "MATCHED": 1, "NEEDS_REVIEW": 2, "pending": 3, "NEW": 4}


def _draft(item: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(item.get("draft_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _evidence(item: dict[str, Any]) -> dict[str, Any]:
    try:
        body = json.loads(item.get("evidence_json") or "{}")
    except json.JSONDecodeError:
        body = {}
    return body if isinstance(body, dict) else {}


def signature_of(item: dict[str, Any], page_url: str = "") -> str:
    stored = str(item.get("signature") or "").strip()
    if stored:
        return stored
    return signature_for_draft(_draft(item), page_url=page_url)


def backfill_signatures(db, *, apply: bool = False) -> dict[str, Any]:
    """Compute signatures for items that predate the column."""
    pages = {
        int(row["id"]): str(row["canonical_url"] or "")
        for row in db.conn.execute("SELECT id, canonical_url FROM guide_page")
    }
    missing = [
        dict(row)
        for row in db.conn.execute(
            "SELECT id, page_id, draft_json, signature FROM review_item WHERE IFNULL(signature, '') = ''"
        )
    ]
    for item in missing:
        item["signature"] = signature_for_draft(_draft(item), page_url=pages.get(int(item["page_id"]), ""))
    if apply:
        for item in missing:
            db.conn.execute("UPDATE review_item SET signature = ? WHERE id = ?", (item["signature"], item["id"]))
        db.conn.commit()
    return {"missing": len(missing), "applied": bool(apply)}


def pending_items(db) -> list[dict[str, Any]]:
    placeholders = ",".join("?" * len(MERGEABLE_STATUSES))
    return [
        dict(row)
        for row in db.conn.execute(
            f"""
            SELECT ri.*, p.canonical_url AS page_url, p.title AS page_title
            FROM review_item ri LEFT JOIN guide_page p ON p.id = ri.page_id
            WHERE ri.status IN ({placeholders})
            ORDER BY ri.id
            """,
            MERGEABLE_STATUSES,
        )
    ]


def duplicate_groups(db) -> dict[str, list[dict[str, Any]]]:
    """Signature -> items, for signatures claimed by more than one item."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in pending_items(db):
        signature = signature_of(item, str(item.get("page_url") or ""))
        groups.setdefault(signature, []).append(item)
    return {signature: items for signature, items in groups.items() if len(items) > 1}


def choose_keeper(items: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        items,
        key=lambda item: (_KEEP_PRIORITY.get(str(item.get("status")), 9), int(item["id"])),
    )[0]


def _merge_group(db, signature: str, items: list[dict[str, Any]], *, apply: bool) -> dict[str, Any]:
    keeper = choose_keeper(items)
    folded = [item for item in items if int(item["id"]) != int(keeper["id"])]
    sources = [
        {
            "item_id": int(item["id"]),
            "page_id": int(item["page_id"]),
            "page_url": item.get("page_url"),
            "status": item.get("status"),
            "signature": signature,
        }
        for item in [keeper] + folded
    ]
    if apply:
        evidence = _evidence(keeper)
        merged = list(evidence.get("merged_sources") or [])
        known = {int(entry.get("item_id") or 0) for entry in merged}
        for entry in sources:
            if entry["item_id"] not in known:
                merged.append(entry)
        evidence["merged_sources"] = merged
        evidence["merged_count"] = len(merged)
        db.conn.execute(
            "UPDATE review_item SET evidence_json = ?, signature = ? WHERE id = ?",
            (json.dumps(evidence, ensure_ascii=False), signature, int(keeper["id"])),
        )
        for item in folded:
            db.conn.execute(
                "UPDATE review_item SET status = 'MERGED', reason = ? WHERE id = ?",
                (f"merged_into:{int(keeper['id'])}", int(item["id"])),
            )
        db.conn.commit()
    return {
        "signature": signature,
        "keeper": int(keeper["id"]),
        "keeper_status": keeper.get("status"),
        "folded": [int(item["id"]) for item in folded],
        "sources": len(sources),
    }


def merge_duplicates(db, *, apply: bool = False) -> dict[str, Any]:
    """Fold duplicate review items into one object with N evidences."""
    backfill_signatures(db, apply=apply)
    before = pending_items(db)
    groups = duplicate_groups(db)
    merges = [_merge_group(db, signature, items, apply=apply) for signature, items in sorted(groups.items())]
    folded = sum(len(merge["folded"]) for merge in merges)
    after = pending_items(db) if apply else before
    total = len(before) or 1
    return {
        "applied": bool(apply),
        "review_items_before": len(before),
        "review_items_after": len(after),
        "merged_items": folded,
        "unique_signatures": len({signature_of(item, str(item.get("page_url") or "")) for item in before}),
        "unique_target_candidates": len(
            {
                str(item.get("source_point_id") or "")
                for item in before
                if str(item.get("source_point_id") or "")
            }
        ),
        "duplicate_groups": len(groups),
        "duplicate_ratio": round(1 - len({signature_of(item, str(item.get("page_url") or "")) for item in before}) / total, 4),
        "merge_ratio": round(folded / total, 4),
        "groups": merges[:20],
    }
