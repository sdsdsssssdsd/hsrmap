from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.matching.candidates import query_candidates
from hsrmap.guides.matching.rematch import heading_map_names
from hsrmap.guides.matching.scenes import pick_guide_observation, score_official_scenes
from hsrmap.guides.matching.ticker import official_candidates_for_map, score_ticker_unit
from hsrmap.guides.regions.official import resolve_official_map
from hsrmap.guides.topics.dream_ticker.canary import pick_ticker_canaries
from hsrmap.guides.topics.dream_ticker.freeze import FREEZE_ID, freeze_ticker_review
from hsrmap.guides.topics.dream_ticker.observe import maps_catalog, observe_page_images, page_blocks
from hsrmap.guides.topics.dream_ticker.report import ticker_bind_report
from hsrmap.guides.topics.loader import get_topic
from hsrmap.guides.topics.official import official_points_for_topic
from hsrmap.guides.vision.generic_roles import is_bind_evidence
from hsrmap.paths import GUIDE_ASSETS, GUIDE_DERIVED


def bind_dream_ticker(
    db: GuideDatabase,
    *,
    official_points: list[dict[str, Any]] | None = None,
    provider=None,
    canary_only: bool = True,
    dest: Path | None = None,
    official_image_loader=None,
) -> dict[str, Any]:
    spec = get_topic("dream_ticker")
    points = official_points if official_points is not None else official_points_for_topic("dream_ticker")
    labels = list((spec.get("official_labels") or {}).get("names") or ["梦境迷钟"])
    freeze_path = dest or (GUIDE_DERIVED.parent / "reports" / "dream-ticker-freeze.json")
    freeze = freeze_ticker_review(db, dest=freeze_path, official_targets=len(points))
    items = _ticker_items(db)
    scored_rows = [_score_item(item, points, labels, official_image_loader) for item in items]
    canaries = pick_ticker_canaries(scored_rows, limit=6)
    canary_ids = {int(row["id"]) for row in canaries}
    vision_ids = canary_ids if canary_only else {int(row["id"]) for row in scored_rows}
    if provider is not None:
        seen_pages: set[int] = set()
        for item in items:
            if int(item["id"]) not in vision_ids or int(item["page_id"]) in seen_pages:
                continue
            seen_pages.add(int(item["page_id"]))
            blocks = page_blocks(int(item["page_id"]), item.get("raw_html_path"))
            if blocks:
                observe_page_images(int(item["page_id"]), blocks, provider, official_points=points)
        scored_rows = [
            _score_item(
                item,
                points,
                labels,
                official_image_loader,
                provider=provider if int(item["id"]) in vision_ids else None,
            )
            for item in items
        ]
    applied = []
    for row in scored_rows:
        canary = int(row["id"]) in canary_ids
        _apply_suggest(db, row, allow_point=canary or not canary_only)
        if canary or not canary_only:
            applied.append(row)
    report = ticker_bind_report(
        review_items=freeze["review_items"],
        official_targets=len(points),
        vision_resolved=sum(1 for row in scored_rows if row["vision_resolved"]),
        candidate_generated=sum(1 for row in scored_rows if row["candidate_count"]),
        point_match=sum(1 for row in scored_rows if row["source_point_id"]),
        human_correct=0,
        approved=0,
    )
    report.update(
        {
            "run_id": freeze.get("run_id") or FREEZE_ID,
            "canary_item_ids": sorted(canary_ids),
            "applied": len(applied),
            "canary_only": canary_only,
            "official_targets": len(points),
            "review_vs_official": f"{freeze['review_items']} / {len(points)}",
        }
    )
    out = (dest.parent / "dream-ticker-bind.json") if dest else (GUIDE_DERIVED.parent / "reports" / "dream-ticker-bind.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _score_item(
    item: dict[str, Any],
    points: list[dict[str, Any]],
    labels: list[str],
    official_image_loader,
    provider=None,
) -> dict[str, Any]:
    draft = item["draft"]
    observations = _existing_observations(int(item["page_id"]))
    scoped = _scope_observations(draft, observations)
    useful = [obs for obs in scoped if is_bind_evidence(obs.get("role"))]
    map_hit = best_map_for_unit(draft, observations, points)
    if not useful:
        hay = " ".join(
            [str(draft.get("map_name") or ""), str(draft.get("resolved_map_name") or ""), str(map_hit.get("map_name") or "")]
        )
        useful = [
            obs
            for obs in observations
            if str(obs.get("role") or "") == "LOCATION_MAP"
            and (
                str(obs.get("map_name_raw") or "") in hay
                or str((obs.get("resolved_map") or {}).get("map_name") or "") in hay
                or str((obs.get("resolved_map") or {}).get("map_id") or "") == str(map_hit.get("map_id") or "")
            )
        ]
    cands = official_candidates_for_map(map_hit.get("map_id"), points, label_names=labels)
    if not cands:
        hay = " ".join(
            [str(draft.get("map_name") or ""), str(draft.get("resolved_map_name") or "")]
            + [str(step.get("text") or "") for step in (draft.get("steps") or []) if isinstance(step, dict)]
        )
        seen: set[str] = set()
        for name in heading_map_names(hay, points):
            for cand in query_candidates(name, points, semantic=None, label_names=labels):
                cid = str(cand.get("source_point_id") or "")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                cands.append(cand)
        if cands and not map_hit.get("map_id"):
            map_hit = {**map_hit, "status": "AMBIGUOUS_REGION", "map_name": heading_map_names(hay, points)[0]}
    if official_image_loader:
        for cand in cands:
            cand["official_image_bytes"] = official_image_loader(str(cand["source_point_id"])) or b""
    unit = {
        "map_name": map_hit.get("map_name") or draft.get("resolved_map_name") or draft.get("map_name"),
        "map_id": map_hit.get("map_id"),
        "article_ordinal": draft.get("article_ordinal"),
        "floor_label": draft.get("floor_label"),
        "hud_text": next((obs.get("hud_text") or obs.get("map_name_raw") for obs in useful if obs.get("hud_text") or obs.get("map_name_raw")), None),
        "guide_image_bytes": _first_guide_bytes(useful or scoped or observations),
        "vision_scene_scores": {},
    }
    unit["vision_scene_scores"] = score_official_scenes(unit["guide_image_bytes"], cands, provider)
    bind = score_ticker_unit(unit, cands)
    return {
        "id": item["id"],
        "map_name": unit["map_name"],
        "map_id": unit["map_id"],
        "candidate_count": len(cands),
        "confidence": bind.get("confidence") or 0,
        "status": bind.get("status"),
        "source_point_id": bind.get("source_point_id") or "",
        "vision_resolved": bool(map_hit.get("map_id")),
        "bind": bind,
        "draft": draft,
        "observations": len(observations),
        "useful": len(useful),
    }


def _ticker_items(db: GuideDatabase) -> list[dict[str, Any]]:
    rows = []
    for row in db.conn.execute(
        """
        SELECT r.id, r.page_id, r.status, r.source_point_id, r.draft_json, p.raw_html_path, p.title
        FROM review_item r
        LEFT JOIN guide_page p ON p.id = r.page_id
        ORDER BY r.id
        """
    ):
        try:
            draft = json.loads(row["draft_json"] or "{}")
        except json.JSONDecodeError:
            draft = {}
        if str(draft.get("topic_key") or "").replace("-", "_") != "dream_ticker":
            continue
        rows.append(
            {
                "id": int(row["id"]),
                "page_id": int(row["page_id"]),
                "status": row["status"],
                "source_point_id": row["source_point_id"],
                "draft": draft,
                "raw_html_path": row["raw_html_path"],
                "title": row["title"],
            }
        )
    return rows


def _existing_observations(page_id: int) -> list[dict[str, Any]]:
    path = GUIDE_DERIVED / str(page_id) / "ticker-image-roles.json"
    if not path.exists():
        path = GUIDE_DERIVED / str(page_id) / "image-roles.json"
    if not path.exists():
        return []
    return list((json.loads(path.read_text(encoding="utf-8")) or {}).get("observations") or [])


def _scope_observations(draft: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    block_ids = {str(item) for item in (draft.get("source_block_ids") or []) if item}
    shas: set[str] = set()
    for image in draft.get("images") or []:
        if not isinstance(image, dict):
            continue
        if image.get("block_id"):
            block_ids.add(str(image["block_id"]))
        if image.get("sha256") or image.get("sha"):
            shas.add(str(image.get("sha256") or image.get("sha")))
    if not block_ids and not shas:
        return []
    return [
        obs
        for obs in observations
        if str(obs.get("block_id") or "") in block_ids or str(obs.get("sha256") or "") in shas
    ]


def best_map_for_unit(
    draft: dict[str, Any],
    observations: list[dict[str, Any]],
    official_points: list[dict[str, Any]],
) -> dict[str, Any]:
    maps = maps_catalog(official_points)
    unit_hay = " ".join(
        [str(draft.get("map_name") or "")]
        + [str(step.get("text") or "") for step in (draft.get("steps") or []) if isinstance(step, dict)]
    )
    names = heading_map_names(unit_hay, official_points)
    if not names:
        names = heading_map_names(str(draft.get("resolved_map_name") or "") + " " + unit_hay, official_points)
    for name in names:
        resolved = resolve_official_map(name, maps)
        if resolved.get("status") == "MATCH" and resolved.get("map_id"):
            return resolved
    for obs in _scope_observations(draft, observations):
        raw = obs.get("map_name_raw")
        if not raw:
            continue
        if unit_hay and str(raw) not in unit_hay and not any(part and part in unit_hay for part in str(raw).split("-")):
            continue
        resolved = resolve_official_map(raw, maps)
        if resolved.get("status") == "MATCH" and resolved.get("map_id"):
            return resolved
    for name in (draft.get("map_name"),):
        resolved = resolve_official_map(name, maps)
        if resolved.get("status") == "MATCH" and resolved.get("map_id"):
            return resolved
    return {"status": "NO_MATCH", "map_id": None, "map_name": draft.get("map_name")}


def _first_guide_bytes(observations: list[dict[str, Any]]) -> bytes:
    ordered = []
    picked = pick_guide_observation(observations)
    if picked:
        ordered.append(picked)
    ordered.extend(obs for obs in observations if obs is not picked)
    for obs in ordered:
        sha = obs.get("sha256")
        if not sha:
            continue
        folder = GUIDE_ASSETS / str(sha)[:2]
        if not folder.exists():
            continue
        for path in folder.glob(f"{sha}.*"):
            return path.read_bytes()
    return b""


def _apply_suggest(db: GuideDatabase, row: dict[str, Any], *, allow_point: bool = True) -> None:
    if str(row.get("status") or "") == "APPROVED":
        return
    draft = dict(row.get("draft") or {})
    bind = row.get("bind") or {}
    draft["corpus_run_id"] = draft.get("corpus_run_id") or FREEZE_ID
    draft["topic_key"] = "dream_ticker"
    draft["target_type"] = "POINT"
    if row.get("map_id"):
        draft["map_id"] = row["map_id"]
        if row.get("map_name") and not str(row["map_name"]).startswith("《"):
            draft["resolved_map_name"] = row["map_name"]
    else:
        draft.pop("map_id", None)
    draft["candidate_points"] = bind.get("candidates") or []
    draft["evidence"] = bind.get("evidence") or {}
    pid = str(bind.get("source_point_id") or "") if allow_point else ""
    status = "AUTO_SUGGEST" if allow_point and bind.get("status") == "auto" and pid else "NEEDS_REVIEW"
    db.conn.execute(
        "UPDATE review_item SET source_point_id=?, draft_json=?, status=? WHERE id=?",
        (pid, json.dumps(draft, ensure_ascii=False), status, int(row["id"])),
    )
    db.conn.commit()
