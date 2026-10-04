from __future__ import annotations

import re
from typing import Any

from hsrmap.guides.matching.spatial import parse_floor_label, parse_spatial_anchor


def section_floor_label(section: Any) -> Any:
    """从「区域名 + 原始 heading」里解析楼层。

    区域解析命中官方 region 名时，heading 里的楼层会被切掉
    （`匹诺康尼 / 「白日梦」酒店-梦境 / 1层` → `map_name="「白日梦」酒店-梦境"`），
    只从 `map_name` 解析就永远拿不到楼层；而 heading 原文还留在 `section["texts"]` 里。
    楼层是点位匹配的硬条件（1 层的稿子不能挂到 3 层的点位上），所以两处一起看。
    """
    name = str((section or {}).get("map_name") or "")
    direct = parse_floor_label(name)
    if direct is not None:
        return direct
    texts = (section or {}).get("texts") or []
    if isinstance(texts, str):
        texts = [texts]
    return parse_floor_label(" ".join(str(item) for item in texts))

_TEXT_ORDINAL = re.compile(r"第\s*(\d+)\s*个|点位\s*(\d+)")


def build_guide_units(sections: list[dict[str, Any]], observations: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for section in sections:
        images = list(section.get("observations") or [])
        if observations and not images:
            images = [item for item in observations if (item.get("resolved_map") or {}).get("map_name") == section.get("map_name") or item.get("map_name_raw") == section.get("map_name")]
        if not images:
            text_units = _units_from_text_ordinals(section)
            if text_units:
                units.extend(text_units)
                continue
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for image in images:
            role = image.get("role")
            ordinal = image.get("article_ordinal")
            if role == "location_map" and current:
                chunks.append(current)
                current = [image]
                continue
            if ordinal and current and any(item.get("article_ordinal") not in (None, ordinal) for item in current):
                chunks.append(current)
                current = [image]
                continue
            current.append(image)
        if current:
            chunks.append(current)
        if not chunks:
            units.append(
                {
                    "map_name": section.get("map_name"),
                    "map_id": (section.get("resolved_map") or {}).get("map_id"),
                    "status": "UNRESOLVED_REGION_BATCH",
                    "article_ordinal": _section_ordinal(section),
                    "spatial_anchor": None,
                    "floor_label": section_floor_label(section),
                    "images": [],
                    "steps": _steps(section),
                    "source_block_ids": section.get("block_ids") or [],
                }
            )
            continue
        if len(images) >= 3 and not any(image.get("role") == "location_map" for image in images) and not any(image.get("article_ordinal") for image in images):
            units.append(
                {
                    "map_name": section.get("map_name"),
                    "map_id": None,
                    "status": "UNRESOLVED_REGION_BATCH",
                    "article_ordinal": None,
                    "spatial_anchor": _chunk_anchor(images),
                    "floor_label": _chunk_floor(images),
                    "images": images,
                    "steps": _steps(section),
                    "source_block_ids": [img.get("block_id") for img in images],
                }
            )
            continue
        for index, chunk in enumerate(chunks, start=1):
            ordinal = next((item.get("article_ordinal") for item in chunk if item.get("article_ordinal")), index)
            units.append(
                {
                    "map_name": section.get("map_name"),
                    "map_id": (chunk[0].get("resolved_map") or {}).get("map_id"),
                    "status": "ok",
                    "article_ordinal": ordinal,
                    "spatial_anchor": _chunk_anchor(chunk),
                    "floor_label": _chunk_floor(chunk),
                    "images": chunk,
                    "steps": _unit_steps(chunk, section),
                    "source_block_ids": [item.get("block_id") for item in chunk],
                    "unit_confidence": 0.9,
                }
            )
    return units


def _section_ordinal(section: dict[str, Any]) -> int | None:
    hit = _TEXT_ORDINAL.search(str(section.get("map_name") or ""))
    if hit:
        return int(next(part for part in hit.groups() if part))
    return None


def _units_from_text_ordinals(section: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[tuple[int | None, list[str]]] = []
    current_ord: int | None = None
    current_texts: list[str] = []
    started = False
    for text in section.get("texts") or []:
        if not text:
            continue
        hit = _TEXT_ORDINAL.search(text)
        if hit:
            if started:
                groups.append((current_ord, current_texts))
            current_ord = int(next(part for part in hit.groups() if part))
            current_texts = [text]
            started = True
            continue
        if started:
            current_texts.append(text)
    if started:
        groups.append((current_ord, current_texts))
    if len(groups) < 2:
        return []
    out = []
    for ordinal, texts in groups:
        out.append(
            {
                "map_name": section.get("map_name"),
                "map_id": (section.get("resolved_map") or {}).get("map_id"),
                "status": "ok",
                "article_ordinal": ordinal,
                "spatial_anchor": None,
                "floor_label": parse_floor_label(" ".join(texts)),
                "images": [],
                "steps": [{"text": item, "images": []} for item in texts if item],
                "source_block_ids": section.get("block_ids") or [],
                "unit_confidence": 0.6,
            }
        )
    return out


def _chunk_floor(chunk: list[dict[str, Any]]) -> int | None:
    for item in chunk:
        for blob in (item.get("spatial_anchor"), *(item.get("visible_text") or [])):
            hit = parse_floor_label(blob)
            if hit:
                return hit
    return None


def _chunk_anchor(chunk: list[dict[str, Any]]) -> str | None:
    for item in chunk:
        hit = parse_spatial_anchor(item.get("spatial_anchor"))
        if hit:
            return hit
        for text in item.get("visible_text") or []:
            hit = parse_spatial_anchor(text)
            if hit:
                return hit
    return None


def _steps(section: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"text": text, "images": []} for text in section.get("texts") or [] if text]


def _unit_steps(chunk: list[dict[str, Any]], section: dict[str, Any]) -> list[dict[str, Any]]:
    texts = []
    for item in chunk:
        texts.extend(item.get("instruction_text") or [])
    if not texts:
        return _steps(section)
    return [{"text": text, "images": [item.get("sha256") for item in chunk if item.get("sha256")]} for text in texts]
