from __future__ import annotations

from typing import Any

from hsrmap.guides.regions.units import build_guide_units
from hsrmap.guides.topics.loader import get_topic
from hsrmap.guides.units.collectible import build_collectible_units


def build_units_for_topic(
    topic_key: str,
    sections: list[dict[str, Any]],
    observations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    try:
        spec = get_topic(str(topic_key).replace("-", "_"))
    except KeyError:
        return build_guide_units(sections, observations)
    builder = str((spec.get("unit_builder") or {}).get("type") or spec.get("guide_kind") or "region_ordinal")
    if builder.lower() in {"collectible_route", "collectible"}:
        return build_collectible_units(sections, observations)
    if builder.lower() in {"challenge", "challenge_v1"}:
        return build_guide_units(sections, observations)
    return build_guide_units(sections, observations)
