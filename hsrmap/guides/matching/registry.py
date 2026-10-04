from __future__ import annotations

from typing import Any

from hsrmap.guides.matching.matcher import match_unit
from hsrmap.guides.matching.ticker import score_ticker_unit
from hsrmap.guides.topics.loader import get_topic


def match_for_topic(topic_key: str, unit: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    topic = get_topic(topic_key)
    profile = (topic.get("matcher") or {}).get("profile") or "puzzle_point_v2"
    if profile == "collectible_route_v1":
        map_id = str(unit.get("map_id") or (candidates[0].get("map_id") if candidates else "") or "")
        target_key = f"map:{map_id}:topic:{topic['topic_key']}" if map_id else ""
        if len(candidates) == 1:
            pid = str(candidates[0].get("source_point_id") or "")
            return {
                "source_point_id": pid,
                "target_type": "MAP_LABEL",
                "target_key": target_key,
                "heading": unit.get("map_name"),
                "confidence": 0.92,
                "status": "auto",
                "evidence": {"unique_candidate": 1.0, "map": 1.0},
                "candidates": [{"source_point_id": pid, "score": 0.92}],
            }
        return {
            "source_point_id": "",
            "target_type": "MAP_LABEL",
            "target_key": target_key,
            "heading": unit.get("map_name"),
            "confidence": 0.4,
            "status": "review",
            "evidence": {"map": 1.0 if unit.get("map_name") else 0.0, "article_ordinal": 0.0},
            "candidates": [{"source_point_id": row.get("source_point_id"), "score": 0.3} for row in candidates[:20]],
        }
    if profile == "multi_point_v1":
        return {
            "source_point_id": "",
            "target_type": "POINT_SET",
            "target_key": f"set:{unit.get('map_id') or ''}:topic:{topic['topic_key']}",
            "heading": unit.get("map_name"),
            "confidence": 0.4 if candidates else 0.0,
            "status": "review",
            "evidence": {"map": 1.0 if unit.get("map_name") else 0.0, "invented": 0.0},
            "candidates": [{"source_point_id": row.get("source_point_id"), "score": 0.3} for row in candidates[:20]],
        }
    if profile == "ticker_point_v1":
        return score_ticker_unit(unit, candidates)
    if profile in {"unique_on_map_v1", "location_only_v1"} and len(candidates) == 1:
        pid = str(candidates[0].get("source_point_id") or "")
        return {
            "source_point_id": pid,
            "heading": unit.get("map_name"),
            "confidence": 0.92,
            "status": "auto",
            "evidence": {"unique_candidate": 1.0, "map": 1.0},
            "candidates": [{"source_point_id": pid, "score": 0.92}],
        }
    return match_unit(unit, candidates)
