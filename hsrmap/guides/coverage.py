from __future__ import annotations

from typing import Any

from hsrmap.guide_db import GuideDatabase


def build_coverage(db: GuideDatabase, official_ids: list[str], matches: list[dict[str, Any]]) -> dict[str, Any]:
    official = [str(item) for item in official_ids]
    matched = {str(item["source_point_id"]) for item in matches}
    approved = set()
    text_n = image_only = with_images = 0
    for point_id in official:
        entries = [e for e in db.list_for_point(point_id) if e.get("status") == "published"]
        if not entries:
            continue
        approved.add(point_id)
        steps = entries[0].get("steps") or []
        images = [img for step in steps for img in step.get("images") or []]
        texts = [step.get("text") for step in steps if step.get("text")]
        if images:
            with_images += 1
        if texts:
            text_n += 1
        elif images:
            image_only += 1
    pending = [
        dict(row)
        for row in db.conn.execute(
            "SELECT * FROM review_item WHERE status IN ('NEEDS_REVIEW','AUTO_SUGGEST','AUTO_ACCEPTED','NEW','pending')"
        )
    ]
    return {
        "official_points": len(official),
        "point_match_coverage": {
            "matched": len(matched & set(official)),
            "unresolved": len(set(official) - matched),
        },
        "real_guide_coverage": {
            "with_approved_guide": len(approved),
            "without_guide": len(set(official) - approved),
            "with_text": text_n,
            "image_only": image_only,
            "with_guide_images": with_images,
        },
        "matcher_accuracy": {
            "reviewed": 0,
            "correct": 0,
            "incorrect": 0,
            "uncertain": len(pending),
            "note": "Accuracy stays 0 until a human marks review items at /review",
        },
        "region_detection_coverage": {"useful": 0, "detected": 0},
        "guide_unit_coverage": {"expected": 0, "built": 0},
        "reviewed_point_binding": {"reviewed": 0, "total": len(official)},
    }
