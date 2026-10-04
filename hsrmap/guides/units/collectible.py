from __future__ import annotations

from typing import Any


def build_collectible_units(
    sections: list[dict[str, Any]],
    observations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    units = []
    for section in sections:
        images = list(section.get("observations") or [])
        if observations and not images:
            images = [
                item
                for item in observations
                if (item.get("resolved_map") or {}).get("map_name") == section.get("map_name")
                or item.get("map_name_raw") == section.get("map_name")
            ]
        items: list[dict[str, Any]] = []
        for image in images:
            for text in image.get("instruction_text") or []:
                sha = image.get("sha256")
                items.append({"text": text, "images": [sha] if sha else []})
        if not items:
            items = [{"text": text, "images": []} for text in section.get("texts") or [] if text]
        if not items and not images:
            continue
        units.append(
            {
                "map_name": section.get("map_name"),
                "map_id": (section.get("resolved_map") or {}).get("map_id"),
                "scope": "MAP_LABEL",
                "status": "ok" if section.get("map_name") else "UNRESOLVED_REGION_BATCH",
                "article_ordinal": None,
                "spatial_anchor": None,
                "floor_label": None,
                "item_count": max(len(items), len(images), 1),
                "images": images,
                "steps": items,
                "source_block_ids": section.get("block_ids") or [item.get("block_id") for item in images],
                "unit_confidence": 0.8,
            }
        )
    return units
