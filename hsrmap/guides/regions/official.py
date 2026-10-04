from __future__ import annotations

from typing import Any

from hsrmap.guides.regions.resolver import resolve_map


def resolve_official_map(map_name_raw: str | None, maps: list[dict[str, Any]]) -> dict[str, Any]:
    raw = str(map_name_raw or "").strip()
    if not raw or raw.isdigit() or len(raw) < 2:
        return {
            "status": "AMBIGUOUS_REGION",
            "map_id": None,
            "map_name": raw or None,
            "map_path": None,
            "confidence": 0.0,
        }
    hit = resolve_map(raw, maps)
    if hit.get("status") in {"PARENT", "AMBIGUOUS"}:
        return {
            "status": "AMBIGUOUS_REGION",
            "map_id": None,
            "map_name": hit.get("map_name"),
            "map_path": hit.get("map_path"),
            "confidence": hit.get("confidence") or 0.4,
        }
    return hit
