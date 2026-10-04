from __future__ import annotations

from typing import Any

from hsrmap.guides.matching.spatial import filter_by_floor, parse_spatial_anchor, pick_by_anchor


def match_sections(
    blocks: list[dict[str, Any]],
    points: list[dict[str, Any]],
    label_tokens: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Score official points against heading/map clues. No LLM."""
    tokens = [item for item in (label_tokens or []) if item]
    headings: list[str] = []
    section_points: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") == "heading":
            if headings:
                section_points.append(current)
                current = []
            headings.append(block.get("text") or "")
        else:
            current.append(block)
    if headings:
        section_points.append(current)
    results = []
    for heading, body in zip(headings, section_points, strict=False):
        text = heading + " " + " ".join(b.get("text") or "" for b in body)
        used: set[str] = set()
        for point in points:
            map_name = str(point.get("map_name") or "")
            path = str(point.get("path") or "")
            map_id = str(point.get("map_id") or "")
            hay = " ".join([map_name, path, map_id, str(point.get("name") or ""), str(point.get("label") or "")])
            score = 0.0
            if "版本" in heading and heading not in {map_name, path}:
                continue
            if heading and heading in {map_name, path, map_id}:
                score = 0.95
            elif heading and path and heading in path:
                score = 0.9
            elif heading and map_name and (map_name in heading or heading in map_name):
                score = 0.85
            elif heading and len(heading) >= 2 and (heading[:2] in map_name or heading[:2] in path):
                score = 0.35
            if tokens and any(token in text for token in tokens) and any(token in hay for token in tokens) and score >= 0.35:
                score += 0.1
            elif (not tokens) and "浮脂" in text and "浮脂" in hay and score >= 0.35:
                score += 0.1
            pid = str(point["source_point_id"] or "")
            if pid in {"", "pending"}:
                continue
            if score >= 0.5 and pid not in used:
                used.add(pid)
                results.append(
                    {
                        "source_point_id": pid,
                        "heading": heading,
                        "confidence": min(score, 0.99),
                        "status": "auto" if score >= 0.8 else "review",
                        "evidence": {
                            "map": 1.0 if heading == map_name else 0.85 if score >= 0.85 else 0.35,
                            "semantic_label": 1.0 if (tokens and any(token in hay for token in tokens)) or (not tokens and "浮脂" in hay) else 0.0,
                            "ordinal": 0.7,
                            "location_text": 0.5,
                            "cv_map_match": 0.0,
                            "official_image_similarity": 0.0,
                            "llm_assist": 0.0,
                        },
                    }
                )
    return results


def match_unit(unit: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    empty = {
        "source_point_id": "",
        "heading": unit.get("map_name"),
        "confidence": 0.0,
        "status": "review",
        "evidence": {"map": 0.0, "article_ordinal": 0.0, "spatial_anchor": 0.0},
        "candidates": [],
    }
    if not candidates:
        return empty
    pool = filter_by_floor(candidates, unit.get("floor_label"))
    if len(pool) == 1 and unit.get("floor_label"):
        picked = str(pool[0]["source_point_id"])
        anchor = _unit_anchor(unit)
    else:
        anchor = _unit_anchor(unit)
        picked = pick_by_anchor(anchor, pool) if anchor else None
    scored = []
    for point in candidates:
        pid = str(point.get("source_point_id") or "")
        if pid in {"", "pending"}:
            continue
        score = 0.95 if picked and pid == picked else 0.55
        scored.append({**point, "source_point_id": pid, "score": score})
    scored.sort(key=lambda row: row["score"], reverse=True)
    if not scored:
        return empty
    top = scored[0]
    margin = top["score"] - (scored[1]["score"] if len(scored) > 1 else 0)
    suggest = bool(picked) and top["score"] >= 0.90 and margin >= 0.15
    return {
        "source_point_id": picked if suggest else "",
        "heading": unit.get("map_name"),
        "confidence": top["score"],
        "status": "auto" if suggest else "review",
        "evidence": {
            "map": 1.0,
            "spatial_anchor": 1.0 if picked else 0.0,
            "article_ordinal": 0.0,
            "margin": margin,
            "anchor": anchor,
        },
        "candidates": [{"source_point_id": row["source_point_id"], "score": row["score"]} for row in scored],
    }


def _unit_anchor(unit: dict[str, Any]) -> str | None:
    direct = parse_spatial_anchor(unit.get("spatial_anchor"))
    if direct:
        return direct
    blobs: list[str] = []
    if unit.get("location_text"):
        blobs.append(str(unit["location_text"]))
    for image in unit.get("images") or []:
        hit = parse_spatial_anchor(image.get("spatial_anchor"))
        if hit:
            return hit
        for item in image.get("visible_text") or []:
            blobs.append(str(item))
    for blob in blobs:
        hit = parse_spatial_anchor(blob)
        if hit:
            return hit
    return None

