from __future__ import annotations

from typing import Any

_FORBIDDEN = {"source_point_id", "point_id", "map_id", "official_point_id"}


def sanitize_region(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(raw or {})
    for key in _FORBIDDEN:
        payload.pop(key, None)
    payload["map_id"] = None
    return payload
