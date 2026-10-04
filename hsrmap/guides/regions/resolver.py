from __future__ import annotations

import re
from typing import Any


def _norm(text: str) -> str:
    value = (text or "").strip()
    table = str.maketrans("（）【】《》　", "()[]<> ")
    value = value.translate(table)
    value = re.sub(r"\s+", "", value)
    value = value.replace("·", "")
    return value


def resolve_page_map(title: str, headings: list[str], maps: list[dict[str, Any]]) -> dict[str, Any]:
    """When the page itself names exactly one map, its units belong to that map.

    A per-map guide ("筑梦边境地图折纸小鸟全收集") labels its pictures "1号小鸟",
    which resolves to nothing on its own. The page title is real evidence though:
    if exactly one official map name appears in the title or the headings, every
    unit on that page can be anchored to it. Two or more names mean the page
    covers several maps, and then nothing is decided here.
    """
    empty = {"status": "NO_MATCH", "map_id": None, "map_name": None, "map_path": None, "confidence": 0.0}
    text = _norm(" ".join([str(title or "")] + [str(item or "") for item in headings]))
    if not text:
        return empty
    hits: dict[str, dict[str, Any]] = {}
    for item in maps:
        name = str(item.get("name") or "").strip()
        needle = _norm(name)
        if len(needle) < 2 or needle not in text:
            continue
        hits[str(item.get("map_id") or name)] = item
    if len(hits) != 1:
        return empty
    hit = next(iter(hits.values()))
    return {
        "status": "PAGE_ANCHOR",
        "map_id": hit.get("map_id"),
        "map_name": hit.get("name"),
        "map_path": hit.get("path"),
        "confidence": 0.6,
    }


def apply_page_anchor(
    sections: list[dict[str, Any]],
    *,
    title: str,
    headings: list[str],
    maps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Anchor every section the page itself could not name to the page's map."""
    anchor = resolve_page_map(title, headings, maps)
    if not anchor.get("map_id"):
        return sections
    out: list[dict[str, Any]] = []
    for section in sections or []:
        current = dict(section)
        resolved = resolve_map(current.get("map_name"), maps)
        if resolved.get("status") in {"NO_MATCH", "AMBIGUOUS"}:
            # AMBIGUOUS means the section's own label ("《崩坏…大剧院-20只折纸鸟
            # 全收集") matched several maps fuzzily; the page title is the better
            # evidence, and it resolved to exactly one map.
            current["resolved_map"] = {**anchor, "anchored_by": "page_title"}
            current["observations"] = [
                {**observation, "resolved_map": {**anchor, "anchored_by": "page_title"}}
                for observation in (current.get("observations") or [])
            ]
        else:
            # the section's own name resolved: that answer wins over any anchor
            current["resolved_map"] = resolved
        out.append(current)
    return out


def resolve_map(map_name_raw: str | None, maps: list[dict[str, Any]]) -> dict[str, Any]:
    empty = {"status": "NO_MATCH", "map_id": None, "map_name": None, "map_path": None, "confidence": 0.0}
    if not map_name_raw:
        return empty
    raw = str(map_name_raw).strip()
    exact = [item for item in maps if item.get("name") == raw]
    if len(exact) == 1:
        return _hit(exact[0], 1.0)
    if len(exact) > 1:
        return {"status": "AMBIGUOUS", "map_id": None, "map_name": raw, "map_path": None, "confidence": 0.4}
    needle = _norm(raw)
    normed = [item for item in maps if _norm(str(item.get("name") or "")) == needle]
    if len(normed) == 1:
        return _hit(normed[0], 0.98)
    aliases = [item for item in maps if raw in (item.get("aliases") or []) or needle in {_norm(a) for a in item.get("aliases") or []}]
    if len(aliases) == 1:
        return _hit(aliases[0], 0.95)
    fuzzy = [item for item in maps if needle and (needle in _norm(str(item.get("name") or "")) or _norm(str(item.get("name") or "")) in needle)]
    renderable = [item for item in fuzzy if item.get("renderable", True)]
    parents = [item for item in fuzzy if item.get("renderable") is False]
    if not renderable and parents:
        return {
            "status": "PARENT",
            "map_id": None,
            "map_name": parents[0].get("name"),
            "map_path": parents[0].get("path"),
            "region_level": "parent",
            "confidence": 0.6,
        }
    if len(renderable) == 1:
        return _hit(renderable[0], 0.7)
    if len(renderable) > 1:
        return {"status": "AMBIGUOUS", "map_id": None, "map_name": raw, "map_path": None, "confidence": 0.4}
    return empty


def _hit(item: dict[str, Any], confidence: float) -> dict[str, Any]:
    if item.get("renderable") is False:
        return {
            "status": "PARENT",
            "map_id": None,
            "map_name": item.get("name"),
            "map_path": item.get("path"),
            "region_level": "parent",
            "confidence": 0.6,
        }
    return {
        "status": "MATCH",
        "map_id": item.get("map_id"),
        "map_name": item.get("name"),
        "map_path": item.get("path"),
        "confidence": confidence,
    }
