from __future__ import annotations

from typing import Any

from hsrmap.guides.matching.image_similarity import score_official_images


def official_candidates_for_map(
    map_id: str | None,
    official_points: list[dict[str, Any]],
    *,
    label_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not map_id:
        return []
    names = [item for item in (label_names or ["梦境迷钟"]) if item]
    hits = []
    for point in official_points:
        if str(point.get("map_id") or "") != str(map_id):
            continue
        label = str(point.get("label") or point.get("name") or point.get("semantic_key") or "")
        if names and not any(token in label for token in names):
            continue
        pid = str(point.get("source_point_id") or "")
        if not pid or pid == "pending":
            continue
        hits.append({**point, "source_point_id": pid})
    return hits


def score_ticker_unit(unit: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    empty = {
        "source_point_id": "",
        "target_type": "POINT",
        "heading": unit.get("map_name"),
        "confidence": 0.0,
        "status": "review",
        "evidence": {"article_ordinal": 0.0, "official_image_similarity": 0.0},
        "candidates": [],
    }
    if not candidates:
        return empty
    guide_bytes = unit.get("guide_image_bytes") or b""
    official = [
        {
            "source_point_id": str(row.get("source_point_id") or ""),
            "bytes": row.get("official_image_bytes") or b"",
        }
        for row in candidates
        if row.get("source_point_id")
    ]
    similar = {str(row["source_point_id"]): float(row.get("score") or 0) for row in score_official_images(guide_bytes, official)} if guide_bytes else {}
    ordinal = unit.get("article_ordinal")
    scored = []
    for index, point in enumerate(candidates, start=1):
        pid = str(point.get("source_point_id") or "")
        image_score = similar.get(pid, 0.0)
        scene = float((unit.get("vision_scene_scores") or {}).get(pid) or 0.0)
        hud = 1.0 if unit.get("floor_label") and str(unit.get("floor_label")) in str(point.get("map_name") or point.get("map_path") or "") else 0.0
        if unit.get("hud_text") and str(unit.get("hud_text")) in str(point.get("map_path") or point.get("map_name") or ""):
            hud = max(hud, 0.6)
        ordinal_score = 0.1 if ordinal and int(ordinal) == index else 0.0
        total = image_score * 1.0 + scene * 0.7 + hud * 0.4 + ordinal_score
        scored.append(
            {
                **point,
                "source_point_id": pid,
                "score": total,
                "evidence": {
                    "official_image_similarity": image_score,
                    "vision_scene": scene,
                    "hud_landmark_floor": hud,
                    "article_ordinal": ordinal_score,
                },
            }
        )
    scored.sort(key=lambda row: row["score"], reverse=True)
    top = scored[0]
    second = scored[1]["score"] if len(scored) > 1 else 0.0
    margin = top["score"] - second
    image_lead = top["evidence"]["official_image_similarity"] >= 0.85 and (
        len(scored) == 1 or top["evidence"]["official_image_similarity"] - scored[1]["evidence"]["official_image_similarity"] >= 0.2
    )
    scene_lead = top["evidence"]["vision_scene"] >= 0.85 and margin >= 0.3
    mixed_region = (not unit.get("map_id")) and " / " in str(unit.get("map_name") or "")
    suggest = (image_lead or scene_lead) and not mixed_region
    return {
        "source_point_id": top["source_point_id"] if suggest else "",
        "target_type": "POINT",
        "heading": unit.get("map_name"),
        "confidence": top["score"],
        "status": "auto" if suggest else "review",
        "evidence": {**top["evidence"], "margin": margin, "article_ordinal_only": not image_lead and not scene_lead},
        "candidates": [{"source_point_id": row["source_point_id"], "score": row["score"]} for row in scored],
    }
