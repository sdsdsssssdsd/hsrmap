from __future__ import annotations

from typing import Any


def plan_layout(draft: dict[str, Any] | None) -> dict[str, Any]:
    body = draft or {}
    topic_key = str(body.get("topic_key") or "").replace("-", "_")
    profile = "puzzle_steps_v1"
    if topic_key:
        try:
            from hsrmap.guides.topics.loader import get_topic

            spec = get_topic(topic_key)
            profile = (spec.get("layout") or {}).get("profile") or profile
        except KeyError:
            pass
    block_type = "item" if profile == "collection_route_v1" else "step"
    return {
        "profile": profile,
        "blocks": [
            {"type": block_type, "text": step.get("text") or "", "images": list(step.get("images") or [])}
            for step in (body.get("steps") or body.get("items") or [])
        ],
        "target": {"type": body.get("target_type"), "key": body.get("target_key")},
    }
