from __future__ import annotations

import json
import re
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.matching.candidates import query_candidates
from hsrmap.guides.matching.registry import match_for_topic
from hsrmap.guides.topics.loader import get_topic


def rematch_topic(
    db: GuideDatabase,
    topic_key: str,
    *,
    official_points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    spec = get_topic(topic_key)
    key = spec["topic_key"]
    names = list((spec.get("official_labels") or {}).get("names") or [])
    points = official_points
    if points is None:
        from hsrmap.guides.topics.official import official_points_for_topic

        points = official_points_for_topic(key)
    updated = 0
    rows = list(db.conn.execute("SELECT id, page_id, source_point_id, status, draft_json FROM review_item"))
    for row in rows:
        try:
            draft = json.loads(row["draft_json"] or "{}")
        except json.JSONDecodeError:
            continue
        item_topic = str(draft.get("topic_key") or draft.get("topic") or "").replace("-", "_")
        if item_topic != key:
            continue
        if str(row["status"] or "") in {"APPROVED", "REJECTED"}:
            continue
        existing_mid = str(draft.get("map_id") or "").strip()
        existing_tk = str(draft.get("target_key") or "").strip()
        if existing_mid and existing_tk == f"map:{existing_mid}:topic:{key}":
            _write_bind(
                db,
                int(row["id"]),
                draft,
                [str(draft.get("map_name") or "")],
                points,
                names,
                key,
                spec,
                resolved_name=draft.get("map_name"),
                map_id=existing_mid,
            )
            updated += 1
            continue
        current_name = str(draft.get("map_name") or "").strip()
        resolved_now = str(draft.get("resolved_map_name") or current_name).strip()
        mixed = " / " in current_name or " / " in resolved_now
        if not mixed and (_already_resolved_leaf(current_name, points) or _already_resolved_leaf(resolved_now, points)):
            leaf = current_name if _already_resolved_leaf(current_name, points) else resolved_now
            updated += _bind_or_split_floors(db, int(row["id"]), int(row["page_id"]), draft, [leaf], points, names, key, spec, leaf)
            continue
        title_leaves = leaf_map_names(current_name, points)
        if not mixed and len(title_leaves) == 1:
            updated += _bind_or_split_floors(
                db, int(row["id"]), int(row["page_id"]), draft, title_leaves, points, names, key, spec, title_leaves[0]
            )
            continue
        content_steps = _content_steps(draft.get("steps") or [])
        hay = " ".join([current_name, resolved_now] + [str(step.get("text") or "") for step in content_steps])
        leaves = leaf_map_names(hay, points)
        official_maps = {str(point.get("map_name") or "").strip() for point in points}
        if len(leaves) >= 2 and (mixed or _is_chrome(current_name) or current_name not in official_maps):
            extra = leaves[1:]
            leaves = leaves[:1]
            from hsrmap.guides.review.service import create_item

            for name in extra:
                extra_draft = dict(draft)
                extra_steps = _steps_for_map(draft.get("steps") or [], name)
                extra_draft["map_name"] = name
                extra_draft["steps"] = extra_steps
                extra_draft["topic_key"] = key
                created = create_item(
                    db,
                    {
                        "page_id": int(row["page_id"]),
                        "source_point_id": "",
                        "status": "NEEDS_REVIEW",
                        "draft": extra_draft,
                    },
                )
                _write_bind(db, int(created["id"]), extra_draft, [name], points, names, key, spec)
                updated += 1
            draft["map_name"] = leaves[0]
            draft["steps"] = _steps_for_map(draft.get("steps") or [], leaves[0])
            hay = " ".join(
                [str(draft.get("map_name") or "")]
                + [str(step.get("text") or "") for step in (draft.get("steps") or []) if isinstance(step, dict)]
            )
        resolved_names = leaves or heading_map_names(hay, points)
        resolved_name = resolved_names[0] if resolved_names else heading_map_name(str(draft.get("map_name") or ""), points)
        updated += _bind_or_split_floors(
            db, int(row["id"]), int(row["page_id"]), draft, resolved_names, points, names, key, spec, resolved_name
        )
    db.conn.commit()
    return {"topic": key, "updated": updated, "official": len(points), "publish": "skipped"}


_HEADING_ORD = re.compile(r"^\s*\d+\s*[\.、．]\s*")
_COUNT_SUFFIX = re.compile(r"[（(]\s*\d+\s*个\s*[）)]\s*$")
_QUOTED_SUFFIX = re.compile(r"「[^」]+」(.+)")
_INNER_QUOTE = re.compile(r"「([^」]+)」")
_PARENT_NAMES = {"翁法罗斯", "匹诺康尼", "星穹列车", "崩坏星穹铁道", "崩坏：星穹铁道", "星穹铁道", "二相乐园"}
_SKIP_NAMES = {"1层", "2层", "3层", "-1层", "1", "2", "3", "5"} | _PARENT_NAMES


def _fold(text: str) -> str:
    return str(text or "").replace("™", "").replace("®", "").replace(" ", "")


def _usable_token(token: str) -> bool:
    text = str(token or "").strip()
    return bool(text) and text not in _SKIP_NAMES and not text.isdigit() and not text.startswith("《")


def _is_chrome(name: str | None) -> bool:
    text = str(name or "").strip()
    return (not text) or text.startswith("《") or text.startswith("崩坏") or text in _PARENT_NAMES


_CHROME_STEP = ("官方中文版下载", "游戏平台", "责任编辑", "相关攻略", "立即下载", "查看更多")


def _content_steps(steps: list[Any]) -> list[dict[str, Any]]:
    out = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        text = str(step.get("text") or "")
        if _is_chrome(text) or any(token in text for token in _CHROME_STEP):
            continue
        out.append(step)
    return out


def _already_resolved_leaf(name: str, official_points: list[dict[str, Any]]) -> bool:
    text = str(name or "").strip()
    if not _usable_token(text) or _is_chrome(text):
        return False
    if " / " in text:
        return False
    return any(_point_mentions(point, text) for point in official_points)


def leaf_map_names(heading: str, official_points: list[dict[str, Any]]) -> list[str]:
    hits = heading_map_names(heading, official_points)
    leaves = []
    for name in hits:
        if not _usable_token(name):
            continue
        if any(_point_mentions(point, name) for point in official_points):
            leaves.append(name)
    ordered = sorted(set(leaves), key=len, reverse=True)
    kept: list[str] = []
    for name in ordered:
        if any(name != other and name in other for other in kept):
            continue
        kept.append(name)
    return kept


def _point_mentions(point: dict[str, Any], token: str) -> bool:
    text = str(token or "").strip()
    if not text:
        return False
    if str(point.get("map_name") or "").strip() == text:
        return True
    if str(point.get("region") or "").strip() == text:
        return True
    return text in str(point.get("map_path") or "")


def _collectible_scope(spec: dict[str, Any]) -> bool:
    profile = str((spec.get("matcher") or {}).get("profile") or "")
    return spec.get("scope") == "MAP_LABEL" or profile == "collectible_route_v1"


def _official_maps_for_tokens(tokens: list[str], points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    usable = [str(token).strip() for token in tokens if _usable_token(str(token or ""))]
    if not usable:
        return out
    token_maps = []
    for token in usable:
        mids = {
            str(point.get("map_id") or "").strip()
            for point in points
            if _point_mentions(point, token) and str(point.get("map_id") or "").strip()
        }
        if mids:
            token_maps.append((token, mids))
    if not token_maps:
        return out
    min_n = min(len(mids) for _, mids in token_maps)
    usable = [token for token, mids in token_maps if len(mids) == min_n]
    for point in points:
        if not any(_point_mentions(point, token) for token in usable):
            continue
        mid = str(point.get("map_id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(
            {
                "map_id": mid,
                "map_name": point.get("map_name") or "",
                "map_path": point.get("map_path") or "",
                "region": point.get("region") or "",
            }
        )
    return out


def _floor_heading(region: str, floor: dict[str, Any]) -> str:
    path = str(floor.get("map_path") or "").strip()
    if path:
        return path
    leaf = str(floor.get("map_name") or "").strip()
    region = str(region or floor.get("region") or "").strip()
    if region and leaf and leaf != region:
        return f"{region} / {leaf}"
    return region or leaf


def _bind_or_split_floors(
    db: GuideDatabase,
    item_id: int,
    page_id: int,
    draft: dict[str, Any],
    resolved_names: list[str],
    points: list[dict[str, Any]],
    label_names: list[str],
    key: str,
    spec: dict[str, Any],
    resolved_name: str | None,
) -> int:
    floors = _official_maps_for_tokens(resolved_names or [resolved_name or ""], points) if _collectible_scope(spec) else []
    if len(floors) < 2:
        _write_bind(db, item_id, draft, resolved_names, points, label_names, key, spec, resolved_name=resolved_name)
        return 1
    from hsrmap.guides.review.service import create_item

    first, *rest = floors
    updated = 0
    for floor in rest:
        extra = dict(draft)
        extra["map_name"] = _floor_heading(resolved_name or "", floor)
        extra["map_id"] = floor["map_id"]
        extra["topic_key"] = key
        created = create_item(
            db,
            {
                "page_id": page_id,
                "source_point_id": "",
                "status": "NEEDS_REVIEW",
                "draft": extra,
            },
        )
        _write_bind(
            db,
            int(created["id"]),
            extra,
            resolved_names,
            points,
            label_names,
            key,
            spec,
            resolved_name=extra["map_name"],
            map_id=floor["map_id"],
        )
        updated += 1
    draft["map_name"] = _floor_heading(resolved_name or "", first)
    draft["map_id"] = first["map_id"]
    _write_bind(
        db,
        item_id,
        draft,
        resolved_names,
        points,
        label_names,
        key,
        spec,
        resolved_name=draft["map_name"],
        map_id=first["map_id"],
    )
    return updated + 1


def _steps_for_map(steps: list[Any], name: str) -> list[Any]:
    hits = [step for step in steps if isinstance(step, dict) and name in str(step.get("text") or "")]
    return hits or [step for step in steps if isinstance(step, dict)]


def _write_bind(
    db: GuideDatabase,
    item_id: int | None,
    draft: dict[str, Any],
    resolved_names: list[str],
    points: list[dict[str, Any]],
    label_names: list[str],
    key: str,
    spec: dict[str, Any],
    *,
    resolved_name: str | None = None,
    map_id: str | None = None,
) -> None:
    name = resolved_name or (resolved_names[0] if resolved_names else draft.get("map_name"))
    wanted_map = str(map_id or draft.get("map_id") or "").strip()
    unit = {
        "map_name": name or draft.get("map_name"),
        "map_id": wanted_map or draft.get("map_id"),
        "spatial_anchor": draft.get("spatial_anchor"),
        "floor_label": draft.get("floor_label"),
        "article_ordinal": draft.get("article_ordinal"),
        "images": draft.get("images") or [],
        "steps": draft.get("steps") or [],
    }
    cands = []
    seen: set[str] = set()
    for token in resolved_names or [unit.get("map_name")]:
        if not _usable_token(str(token or "")):
            continue
        for cand in query_candidates(token, points, semantic=None, label_names=label_names):
            cid = str(cand.get("source_point_id") or "")
            if not cid or cid in seen:
                continue
            if wanted_map and str(cand.get("map_id") or "") != wanted_map:
                continue
            seen.add(cid)
            cands.append(cand)
    bind = match_for_topic(key, unit, cands)
    if spec.get("seed_global") and not cands:
        bind = {
            "source_point_id": "",
            "target_type": "GLOBAL",
            "target_key": f"global:topic:{key}",
            "heading": unit.get("map_name"),
            "confidence": 0.4,
            "status": "review",
            "evidence": {"global": 1.0, "invented": 0.0},
            "candidates": [],
        }
    pid = str(bind.get("source_point_id") or "")
    if pid == "pending":
        pid = ""
    draft["topic_key"] = key
    draft["map_name"] = unit["map_name"]
    if resolved_names:
        draft["resolved_map_name"] = resolved_names[0] if len(resolved_names) == 1 else " / ".join(resolved_names)
    draft["target_type"] = bind.get("target_type") or draft.get("target_type") or spec.get("scope")
    draft["target_key"] = bind.get("target_key") or draft.get("target_key")
    tk = str(draft.get("target_key") or "")
    if tk.startswith("map:"):
        mid = tk.split(":")[1].strip()
        if mid:
            draft["map_id"] = mid
    draft["candidate_points"] = bind.get("candidates") or []
    draft["evidence"] = bind.get("evidence") or draft.get("evidence") or {}
    status = "AUTO_SUGGEST" if bind.get("status") == "auto" else "NEEDS_REVIEW"
    payload = json.dumps(draft, ensure_ascii=False)
    if item_id is None:
        return
    db.conn.execute(
        "UPDATE review_item SET source_point_id=?, draft_json=?, status=? WHERE id=?",
        (pid, payload, status, item_id),
    )


def heading_map_names(heading: str, official_points: list[dict[str, Any]]) -> list[str]:
    cleaned = _COUNT_SUFFIX.sub("", _HEADING_ORD.sub("", heading or "")).strip()
    tokens = set()
    for point in official_points:
        for key in ("region", "map_name"):
            token = str(point.get(key) or "").strip()
            if _usable_token(token):
                tokens.add(token)
        path = str(point.get("map_path") or "")
        for part in path.split("/"):
            part = part.strip()
            if _usable_token(part):
                tokens.add(part)
    extra = set()
    for token in tokens:
        quoted = _QUOTED_SUFFIX.match(token)
        if quoted and _usable_token(quoted.group(1)):
            extra.add(quoted.group(1).strip())
        for inner in _INNER_QUOTE.findall(token):
            if _usable_token(inner.strip()):
                extra.add(inner.strip())
    tokens.update(extra)
    hay = heading or cleaned
    folded_hay = _fold(hay)
    hits = [token for token in tokens if token and (_fold(token) in folded_hay or token in hay)]
    for token in tokens:
        short = token.replace("匹诺康尼", "").replace("学院", "")
        folded_short = _fold(short)
        base = short.split("-")[0]
        if len(folded_short) >= 4 and folded_short in folded_hay:
            hits.append(token)
        elif "-" in token and _fold(token) not in folded_hay:
            continue
        elif len(base) >= 3 and base not in {"匹诺康尼"} and base in hay:
            hits.append(token)
    return sorted(set(hits), key=len, reverse=True)


def heading_map_name(heading: str, official_points: list[dict[str, Any]]) -> str:
    hits = heading_map_names(heading, official_points)
    if hits:
        return hits[0]
    cleaned = _COUNT_SUFFIX.sub("", _HEADING_ORD.sub("", heading or "")).strip()
    return cleaned or heading
