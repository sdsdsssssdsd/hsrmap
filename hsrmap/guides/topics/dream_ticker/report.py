from __future__ import annotations

from typing import Any


def ticker_bind_report(
    *,
    review_items: int,
    official_targets: int,
    vision_resolved: int,
    candidate_generated: int,
    point_match: int,
    human_correct: int,
    approved: int,
    wrong_published: int = 0,
    hallucinated: int = 0,
) -> dict[str, Any]:
    return {
        "Dream Ticker review items": review_items,
        "Vision region resolved": f"{vision_resolved} / {review_items}",
        "Official candidate generated": f"{candidate_generated} / {review_items}",
        "Point Match Coverage": f"{point_match} / {review_items}",
        "Human-reviewed correct bindings": f"{human_correct} / {review_items}",
        "Approved Real Guide Coverage": f"{approved} / {official_targets}",
        "Wrong published bindings": wrong_published,
        "Hallucinated published steps": hallucinated,
        "publish": "skipped",
    }
