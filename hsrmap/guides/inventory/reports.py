from __future__ import annotations

from collections import Counter
from typing import Any

from hsrmap.guides.inventory.classifier import classify_labels
from hsrmap.reports import write_json


def load_selectable_labels(db) -> list[dict[str, Any]]:
    parents = {
        row["source_id"]: row["name"]
        for row in db.conn.execute("SELECT source_id, name FROM label_nodes")
    }
    rows = []
    for row in db.conn.execute(
        """
        SELECT l.source_id, l.name, l.parent_source_id, l.is_selectable,
               (SELECT COUNT(*) FROM point_labels pl WHERE pl.label_id = l.id) AS point_count
        FROM label_nodes l
        WHERE l.is_selectable = 1
        """
    ):
        rows.append(
            {
                "source_id": row["source_id"],
                "name": row["name"],
                "category": parents.get(row["parent_source_id"]) or "",
                "point_count": int(row["point_count"] or 0),
            }
        )
    return rows


def build_inventory(labels: list[dict[str, Any]]) -> dict[str, Any]:
    classified = classify_labels(labels)
    counts = Counter(item["status"] for item in classified)
    return {
        "labels_total": len(classified),
        "guide_topic": counts.get("GUIDE_TOPIC", 0),
        "location_only": counts.get("LOCATION_ONLY", 0),
        "no_guide": counts.get("NO_GUIDE_REQUIRED", 0),
        "needs_review": counts.get("NEEDS_REVIEW", 0),
        "points_by_category": _points(classified),
        "maps_affected": None,
        "labels": classified,
    }


def write_inventory_reports(root, inventory: dict[str, Any]) -> dict[str, Any]:
    json_path = root / "guide-inventory.json"
    md_path = root / "guide-inventory.md"
    write_json(json_path, inventory)
    lines = [
        "# Guide Inventory",
        "",
        f"- labels total: {inventory['labels_total']}",
        f"- GUIDE_TOPIC: {inventory['guide_topic']}",
        f"- LOCATION_ONLY: {inventory['location_only']}",
        f"- NO_GUIDE_REQUIRED: {inventory['no_guide']}",
        f"- NEEDS_REVIEW: {inventory['needs_review']}",
        "",
        "| status | name | kind | points |",
        "| --- | --- | --- | --- |",
    ]
    for item in inventory["labels"]:
        if item["status"] == "GUIDE_TOPIC":
            lines.append(f"| {item['status']} | {item['name']} | {item['suggested_kind']} | {item['point_count']} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def _points(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in rows:
        out[item["status"]] = out.get(item["status"], 0) + int(item["point_count"] or 0)
    return out
