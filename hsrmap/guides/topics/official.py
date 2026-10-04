from __future__ import annotations

from typing import Any

from hsrmap.guides.coverage import build_coverage
from hsrmap.guides.review.canary import flatten_topic_points
from hsrmap.guides.topics.loader import get_topic
from hsrmap.viewer_repo import _map_parents, _map_path


def topic_payload_by_label_names(
    ctx,
    names: list[str],
    *,
    display_name: str = "",
) -> dict[str, Any]:
    labels = [name for name in names if name]
    if not labels:
        empty = {"name": display_name, "count": 0, "maps": []}
        return {**empty, "origin": empty}
    parents = _map_parents(ctx)
    placeholders = ",".join("?" * len(labels))
    rows = ctx.core.conn.execute(
        f"""
        SELECT DISTINCT m.source_id AS map_id, m.name AS map_name, m.id AS map_pk,
               p.id, p.source_id, p.x_pos, p.y_pos, l.name AS label_name
        FROM points p
        JOIN maps m ON m.id = p.map_id
        JOIN point_labels pl ON pl.point_id = p.id
        JOIN label_nodes l ON l.id = pl.label_id
        WHERE l.name IN ({placeholders})
        ORDER BY m.id, p.id
        """,
        labels,
    )
    maps_by_id: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        source_id = str(row["source_id"] or "")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        amap = maps_by_id.setdefault(
            str(row["map_id"]),
            {
                "map_id": str(row["map_id"]),
                "name": row["map_name"],
                "path": _map_path(parents, str(row["map_id"])),
                "count": 0,
                "points": [],
            },
        )
        amap["points"].append(
            {
                "id": int(row["id"]),
                "source_id": source_id,
                "x": float(row["x_pos"]),
                "y": float(row["y_pos"]),
                "label": row["label_name"],
            }
        )
        amap["count"] += 1
    maps = list(maps_by_id.values())
    total = sum(int(item["count"]) for item in maps)
    body = {"name": display_name or (labels[0] if labels else ""), "count": total, "maps": maps}
    return {**body, "origin": body}


def official_payload_for_topic(topic_key: str, ctx=None) -> dict[str, Any]:
    spec = get_topic(topic_key)
    names = list((spec.get("official_labels") or {}).get("names") or [])
    own = ctx is None
    if own:
        from hsrmap.viewer_bind import bind_viewer

        ctx = bind_viewer()
    try:
        viewer_cfg = spec.get("viewer") or {}
        origin_key = str(viewer_cfg.get("origin_semantic") or "").strip()
        if origin_key:
            from hsrmap.viewer_repo import _semantic_topic

            origin = _semantic_topic(ctx, origin_key)
            payload = {
                **origin,
                "origin": origin,
                "topic_key": spec["topic_key"],
                "scope": spec.get("scope"),
                "guide_kind": spec.get("guide_kind"),
            }
            notes_key = str(viewer_cfg.get("notes_semantic") or "").strip()
            if notes_key:
                payload["notes"] = _semantic_topic(ctx, notes_key)
            return payload
        payload = topic_payload_by_label_names(ctx, names, display_name=spec.get("display_name") or spec["topic_key"])
        payload["topic_key"] = spec["topic_key"]
        payload["scope"] = spec.get("scope")
        payload["guide_kind"] = spec.get("guide_kind")
        return payload
    finally:
        if own:
            ctx.close()


def official_points_for_topic(topic_key: str, ctx=None) -> list[dict[str, Any]]:
    spec = get_topic(topic_key)
    names = list((spec.get("official_labels") or {}).get("names") or [])
    payload = official_payload_for_topic(topic_key, ctx=ctx)
    label = spec.get("display_name") or (names[0] if names else spec["topic_key"])
    return flatten_topic_points(payload, label=label)


def official_detail_images(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    block = payload or {}
    if isinstance(block.get("detail"), dict):
        block = block["detail"]
    if str(block.get("state") or "") in {"UNAVAILABLE", "EMPTY"}:
        return []
    return [item for item in (block.get("images") or []) if item]


def official_image_url(ctx, source_point_id: str) -> str:
    from hsrmap.viewer_repo import point_detail

    row = ctx.core.conn.execute("SELECT id FROM points WHERE source_id=?", (str(source_point_id),)).fetchone()
    if row is None:
        return ""
    for image in official_detail_images(point_detail(ctx, row["id"])):
        url = str(image.get("url") or "")
        if url:
            return url
    return ""


def official_image_bytes(ctx, source_point_id: str) -> bytes:
    from hsrmap.paths import ASSETS
    from hsrmap.viewer_repo import point_detail

    row = ctx.core.conn.execute("SELECT id FROM points WHERE source_id=?", (str(source_point_id),)).fetchone()
    if row is None:
        return b""
    for image in official_detail_images(point_detail(ctx, row["id"])):
        sha = str(image.get("url") or "").rsplit("/", 1)[-1]
        if not sha:
            continue
        folder = ASSETS / sha[:2]
        for path in folder.glob(f"{sha}.*"):
            return path.read_bytes()
    return b""


def official_maps_for_topic(topic_key: str, ctx=None) -> list[dict[str, Any]]:
    payload = official_payload_for_topic(topic_key, ctx=ctx)
    return list((payload.get("origin") or payload).get("maps") or [])


def atlas_payload(db, ctx=None) -> dict[str, Any]:
    from hsrmap.guides.topics.loader import list_topics

    own = ctx is None
    if own:
        from hsrmap.viewer_bind import bind_viewer

        ctx = bind_viewer()
    try:
        topics = [topic_status(db, item["topic_key"], ctx=ctx) for item in list_topics()]
        return {"topics": topics, "publish": "never_auto", "viewer_network": 0}
    finally:
        if own:
            ctx.close()


def topic_status(db, topic_key: str, ctx=None) -> dict[str, Any]:
    spec = get_topic(topic_key)
    points = official_points_for_topic(topic_key, ctx=ctx)
    official = [row["source_point_id"] for row in points]
    report = build_coverage(db, official, [{"source_point_id": pid} for pid in official])
    seeded = int(
        db.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM guide_target t
            JOIN guide_topic tp ON tp.id = t.topic_id
            WHERE tp.topic_key = ?
            """,
            (spec["topic_key"],),
        ).fetchone()["n"]
    )
    maps = {row["map_id"] for row in points if row.get("map_id")}
    from hsrmap.guides.ledger import _scope_published

    scope_n = _scope_published(db, spec["topic_key"])
    approved = int((report.get("real_guide_coverage") or {}).get("with_approved_guide") or 0)
    if scope_n > approved:
        report["real_guide_coverage"]["with_approved_guide"] = scope_n
        report["real_guide_coverage"]["without_guide"] = max(0, len(official) - scope_n)
    return {
        "topic": spec["topic_key"],
        "display_name": spec.get("display_name"),
        "scope": spec.get("scope"),
        "guide_kind": spec.get("guide_kind"),
        "official_targets": len(official),
        "official_maps": len(maps),
        "official_status": _official_status(ctx, spec, len(official)),
        "enabled": spec.get("enabled", True),
        "seeded_targets": seeded,
        "publish": "never_auto",
        **report,
    }


def _official_status(ctx, spec: dict[str, Any], official_n: int) -> str:
    if official_n:
        return "HAS_OFFICIAL_TARGETS"
    names = [str(name) for name in ((spec.get("official_labels") or {}).get("names") or []) if name]
    if ctx is not None and names:
        placeholders = ",".join("?" * len(names))
        row = ctx.core.conn.execute(
            f"SELECT 1 FROM label_nodes WHERE name IN ({placeholders}) LIMIT 1",
            names,
        ).fetchone()
        if row:
            return "LABEL_EXISTS_NO_POINTS"
    return "NO_OFFICIAL_TARGET"
