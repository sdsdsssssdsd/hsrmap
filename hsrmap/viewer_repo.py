from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hsrmap.paths import ASSETS
from hsrmap.viewer_bind import ViewerContext
from hsrmap.viewer_crs import get_max_bounds, get_raster_bounds

SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.I)


def _map_parents(ctx: ViewerContext) -> dict[str, Any]:
    return {
        row["source_id"]: row
        for row in ctx.core.conn.execute("SELECT source_id, parent_source_id, name FROM map_nodes")
    }


def _map_path(parents: dict[str, Any], source_id: str) -> str:
    parts: list[str] = []
    current: str | None = source_id
    seen: set[str] = set()
    while current and current in parents and current not in seen:
        seen.add(current)
        parts.append(parents[current]["name"] or current)
        current = parents[current]["parent_source_id"]
    return " / ".join(reversed(parts))


def _json_list(value: Any) -> list[Any] | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, list) else None


def asset_path(sha256: str) -> Path | None:
    if not SHA_RE.fullmatch(sha256 or ""):
        return None
    folder = ASSETS / sha256[:2]
    if not folder.exists():
        return None
    matches = sorted(folder.glob(f"{sha256}.*"))
    return matches[0] if matches else None


def map_payload(ctx: ViewerContext, map_id: str) -> dict[str, Any] | None:
    row = ctx.core.map_by_source(str(map_id))
    if row is None:
        return None
    padding = _json_list(row["padding_json"])
    fragments = ctx.core.fragments_for_map(row["id"])
    raster_asset = fragments[0]["asset_sha256"] if fragments else None
    bounds = get_raster_bounds(row["origin_x"], row["origin_y"], row["canvas_width"], row["canvas_height"])
    max_bounds = get_max_bounds(row["origin_x"], row["origin_y"], row["canvas_width"], row["canvas_height"], padding)
    return {
        "id": row["source_id"],
        "name": row["name"],
        "width": int(row["canvas_width"]),
        "height": int(row["canvas_height"]),
        "origin": [float(row["origin_x"]), float(row["origin_y"])],
        "padding": padding,
        "raster": {"asset": raster_asset},
        "bounds": bounds,
        "max_bounds": max_bounds,
        "crs": {
            "projection": "LonLat",
            "transformation": [1, float(row["origin_x"]), 1, float(row["origin_y"])],
            "marker": ["y_pos", "x_pos"],
        },
    }


def points_for_map(ctx: ViewerContext, map_id: str, semantic_key: str | None = None, label_id: str | None = None) -> list[dict[str, Any]]:
    row = ctx.core.map_by_source(str(map_id))
    if row is None:
        return []
    sql = """
        SELECT p.id, p.source_id, p.x_pos, p.y_pos, p.raster_x, p.raster_y
        FROM points p
        WHERE p.map_id = ?
    """
    args: list[Any] = [row["id"]]
    if semantic_key:
        sql = """
            SELECT DISTINCT p.id, p.source_id, p.x_pos, p.y_pos, p.raster_x, p.raster_y
            FROM points p
            JOIN point_labels pl ON pl.point_id = p.id
            JOIN label_nodes l ON l.id = pl.label_id
            JOIN semantic_label_bindings b ON b.source_label_id = l.source_id
            WHERE p.map_id = ? AND b.semantic_key = ?
        """
        args.append(semantic_key)
    elif label_id:
        sql = """
            SELECT DISTINCT p.id, p.source_id, p.x_pos, p.y_pos, p.raster_x, p.raster_y
            FROM points p
            JOIN point_labels pl ON pl.point_id = p.id
            JOIN label_nodes l ON l.id = pl.label_id
            WHERE p.map_id = ? AND l.source_id = ?
        """
        args.append(str(label_id))
    points = []
    for point in ctx.core.conn.execute(sql, args):
        labels = []
        for label in ctx.core.conn.execute(
            """
            SELECT l.source_id, l.name, l.icon_asset_sha256
            FROM point_labels pl
            JOIN label_nodes l ON l.id = pl.label_id
            WHERE pl.point_id = ?
            """,
            (point["id"],),
        ):
            icon = None
            if label["icon_asset_sha256"]:
                icon = f"/assets/{label['icon_asset_sha256']}"
            labels.append({"id": label["source_id"], "name": label["name"], "icon": icon})
        points.append(
            {
                "id": int(point["id"]),
                "source_id": point["source_id"],
                "x": float(point["x_pos"]),
                "y": float(point["y_pos"]),
                "raster_x": float(point["raster_x"]),
                "raster_y": float(point["raster_y"]),
                "labels": labels,
            }
        )
    return points


def point_detail(ctx: ViewerContext, core_point_id: int) -> dict[str, Any] | None:
    row = ctx.core.conn.execute("SELECT * FROM points WHERE id = ?", (int(core_point_id),)).fetchone()
    if row is None:
        return None
    map_row = ctx.core.conn.execute("SELECT source_id FROM maps WHERE id = ?", (row["map_id"],)).fetchone()
    labels = []
    for label in ctx.core.conn.execute(
        """
        SELECT l.source_id, l.name FROM point_labels pl
        JOIN label_nodes l ON l.id = pl.label_id
        WHERE pl.point_id = ?
        """,
        (row["id"],),
    ):
        labels.append({"id": label["source_id"], "name": label["name"]})
    detail = {"state": "UNAVAILABLE", "text": None, "images": []}
    if ctx.detail is None:
        detail = {"state": "UNAVAILABLE", "text": "详细资料尚未同步", "images": []}
    else:
        binding = ctx.detail.conn.execute(
            "SELECT * FROM point_detail_bindings WHERE core_point_id = ?",
            (row["id"],),
        ).fetchone()
        if binding is None:
            detail = {"state": "UNAVAILABLE", "text": "详细资料尚未同步", "images": []}
        else:
            rec = ctx.detail.conn.execute("SELECT * FROM point_details WHERE id = ?", (binding["detail_id"],)).fetchone()
            if rec is None:
                detail = {"state": "UNAVAILABLE", "text": "详细资料尚未同步", "images": []}
            elif rec["detail_state"] == "EMPTY" or rec["is_empty"]:
                detail = {"state": "EMPTY", "text": None, "images": []}
            else:
                images = []
                for image in ctx.detail.conn.execute(
                    "SELECT * FROM point_detail_assets WHERE detail_id = ? AND asset_sha256 IS NOT NULL ORDER BY sort_order",
                    (rec["id"],),
                ):
                    images.append({"url": f"/assets/{image['asset_sha256']}", "role": image["role"]})
                detail = {
                    "state": rec["detail_state"] or "NONEMPTY",
                    "text": rec["plain_text"] or None,
                    "images": images,
                }
    return {
        "core": {
            "point_id": str(row["id"]),
            "source_id": row["source_id"],
            "map_id": map_row["source_id"] if map_row else None,
            "x": float(row["x_pos"]),
            "y": float(row["y_pos"]),
        },
        "labels": labels,
        "detail": detail,
    }


def map_tree_payload(ctx: ViewerContext) -> list[dict[str, Any]]:
    rows = list(
        ctx.core.conn.execute(
            """
            SELECT source_id, parent_source_id, name, is_renderable, sort_order, id
            FROM map_nodes
            ORDER BY sort_order, id
            """
        )
    )
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_id[row["source_id"]] = {
            "id": row["source_id"],
            "name": row["name"] or row["source_id"],
            "type": "map" if row["is_renderable"] else "folder",
            "renderable": bool(row["is_renderable"]),
            "children": [],
        }
    roots: list[dict[str, Any]] = []
    for row in rows:
        node = by_id[row["source_id"]]
        parent = row["parent_source_id"]
        if parent and parent in by_id:
            by_id[parent]["children"].append(node)
        else:
            roots.append(node)
    return roots


def map_labels_payload(ctx: ViewerContext, map_id: str) -> list[dict[str, Any]] | None:
    row = ctx.core.map_by_source(str(map_id))
    if row is None:
        return None
    labels = list(
        ctx.core.conn.execute(
            """
            SELECT
                l.source_id AS label_id,
                l.name AS label_name,
                l.icon_asset_sha256 AS icon,
                l.parent_source_id AS parent_id,
                COUNT(*) AS count
            FROM points p
            JOIN point_labels pl ON pl.point_id = p.id
            JOIN label_nodes l ON l.id = pl.label_id
            WHERE p.map_id = ?
            GROUP BY l.source_id
            ORDER BY l.sort_order, l.id
            """,
            (row["id"],),
        )
    )
    if not labels:
        return []
    parents = {
        item["source_id"]: item
        for item in ctx.core.conn.execute("SELECT source_id, name, parent_source_id, is_category, sort_order FROM label_nodes")
    }
    bindings = {
        item["source_label_id"]: item["semantic_key"]
        for item in ctx.core.conn.execute("SELECT source_label_id, semantic_key FROM semantic_label_bindings")
    }

    def category_of(label_parent: str | None) -> tuple[str, str, int]:
        current = label_parent
        seen: set[str] = set()
        while current and current in parents and current not in seen:
            seen.add(current)
            node = parents[current]
            if node["is_category"]:
                return node["source_id"], node["name"] or current, int(node["sort_order"] or 0)
            current = node["parent_source_id"]
        return "0", "其他", 999

    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for label in labels:
        cat_id, cat_name, cat_sort = category_of(label["parent_id"])
        if cat_id not in grouped:
            grouped[cat_id] = {
                "category": {"id": cat_id, "name": cat_name, "sort": cat_sort},
                "labels": [],
            }
            order.append(cat_id)
        icon = f"/assets/{label['icon']}" if label["icon"] else None
        grouped[cat_id]["labels"].append(
            {
                "id": label["label_id"],
                "name": label["label_name"],
                "icon": icon,
                "count": int(label["count"]),
                "semantic_key": bindings.get(label["label_id"]),
            }
        )
    order.sort(key=lambda key: grouped[key]["category"]["sort"])
    result = []
    for key in order:
        group = grouped[key]
        group["category"].pop("sort", None)
        result.append(group)
    return result


def meta_payload(ctx: ViewerContext) -> dict[str, Any]:
    counts = ctx.core.counts()
    selectable = int(ctx.core.conn.execute("SELECT COUNT(*) n FROM label_nodes WHERE is_selectable = 1").fetchone()["n"])
    grease = ctx.core.semantic_source_id("floating_grease_origin_retrace")
    grease_n = 0
    if grease:
        grease_n = int(
            ctx.core.conn.execute(
                """
                SELECT COUNT(DISTINCT p.id) n FROM points p
                JOIN point_labels pl ON pl.point_id = p.id
                JOIN label_nodes l ON l.id = pl.label_id
                WHERE l.source_id = ?
                """,
                (grease,),
            ).fetchone()["n"]
        )
    nonempty = empty = 0
    if ctx.detail is not None:
        nonempty = int(ctx.detail.conn.execute("SELECT COUNT(*) n FROM point_details WHERE is_empty = 0").fetchone()["n"])
        empty = int(ctx.detail.conn.execute("SELECT COUNT(*) n FROM point_details WHERE is_empty = 1").fetchone()["n"])
    return {
        "api_version": 1,
        "core_snapshot": ctx.snapshot_id,
        "detail_state": ctx.detail_state,
        "maps": counts["renderable_maps"],
        "points": counts["points"],
        "labels": selectable,
        "detail_nonempty": nonempty,
        "detail_empty": empty,
        "floating_grease": {"semantic_key": "floating_grease_origin_retrace", "count": grease_n},
    }


def search_payload(ctx: ViewerContext, q: str, limit: int = 40) -> dict[str, Any]:
    query = (q or "").strip()
    if not query:
        return {"labels": [], "maps": [], "points": []}
    like = f"%{query}%"
    parents = _map_parents(ctx)

    def map_path(source_id: str) -> str:
        return _map_path(parents, source_id)

    labels = []
    for row in ctx.core.conn.execute(
        """
        SELECT l.source_id, l.name, b.semantic_key, COUNT(DISTINCT p.id) AS count
        FROM label_nodes l
        LEFT JOIN semantic_label_bindings b ON b.source_label_id = l.source_id
        LEFT JOIN point_labels pl ON pl.label_id = l.id
        LEFT JOIN points p ON p.id = pl.point_id
        WHERE l.is_selectable = 1 AND l.name LIKE ?
        GROUP BY l.source_id
        ORDER BY
            CASE WHEN b.semantic_key IS NOT NULL THEN 0 ELSE 1 END,
            CASE WHEN l.name LIKE ? THEN 0 ELSE 1 END,
            count DESC
        LIMIT ?
        """,
        (like, f"{query}%", limit),
    ):
        labels.append(
            {
                "id": row["source_id"],
                "name": row["name"],
                "count": int(row["count"]),
                "semantic_key": row["semantic_key"],
            }
        )
    maps = []
    for row in ctx.core.conn.execute(
        """
        SELECT source_id, name, is_renderable FROM map_nodes
        WHERE name LIKE ?
        ORDER BY is_renderable DESC, sort_order, id
        LIMIT ?
        """,
        (like, limit),
    ):
        maps.append(
            {
                "id": row["source_id"],
                "name": row["name"],
                "path": map_path(row["source_id"]),
                "renderable": bool(row["is_renderable"]),
            }
        )
    points = []
    sql = """
        SELECT p.id, p.source_id, m.source_id AS map_id, m.name AS map_name, l.name AS label_name
        FROM points p
        JOIN maps m ON m.id = p.map_id
        LEFT JOIN point_labels pl ON pl.point_id = p.id
        LEFT JOIN label_nodes l ON l.id = pl.label_id
        WHERE l.name LIKE ?
        GROUP BY p.id
        LIMIT ?
    """
    args: list[Any] = [like, limit]
    if ctx.detail is not None:
        hits = {
            int(row["core_point_id"])
            for row in ctx.detail.conn.execute(
                """
                SELECT b.core_point_id FROM point_detail_bindings b
                JOIN point_details d ON d.id = b.detail_id
                WHERE d.plain_text LIKE ?
                LIMIT ?
                """,
                (like, limit),
            )
        }
        if hits:
            placeholders = ",".join("?" * len(hits))
            sql = f"""
                SELECT p.id, p.source_id, m.source_id AS map_id, m.name AS map_name, l.name AS label_name
                FROM points p
                JOIN maps m ON m.id = p.map_id
                LEFT JOIN point_labels pl ON pl.point_id = p.id
                LEFT JOIN label_nodes l ON l.id = pl.label_id
                WHERE l.name LIKE ? OR p.id IN ({placeholders})
                GROUP BY p.id
                LIMIT ?
            """
            args = [like, *hits, limit]
    for row in ctx.core.conn.execute(sql, args):
        points.append(
            {
                "id": int(row["id"]),
                "source_id": row["source_id"],
                "map_id": row["map_id"],
                "name": row["label_name"] or row["source_id"],
                "path": map_path(row["map_id"]),
            }
        )
    return {"labels": labels, "maps": maps, "points": points}


def _semantic_topic(ctx: ViewerContext, semantic_key: str) -> dict[str, Any]:
    label_id = ctx.core.semantic_source_id(semantic_key)
    if not label_id:
        return {"semantic_key": semantic_key, "label_id": None, "name": semantic_key, "count": 0, "maps": []}
    label = ctx.core.conn.execute(
        "SELECT source_id, name FROM label_nodes WHERE source_id = ?",
        (label_id,),
    ).fetchone()
    parents = _map_parents(ctx)
    maps: list[dict[str, Any]] = []
    total = 0
    for row in ctx.core.conn.execute(
        """
        SELECT m.source_id AS map_id, m.name AS map_name, COUNT(DISTINCT p.id) AS count
        FROM points p
        JOIN maps m ON m.id = p.map_id
        JOIN point_labels pl ON pl.point_id = p.id
        JOIN label_nodes l ON l.id = pl.label_id
        WHERE l.source_id = ?
        GROUP BY m.source_id
        ORDER BY m.id
        """,
        (label_id,),
    ):
        points = []
        for point in ctx.core.conn.execute(
            """
            SELECT DISTINCT p.id, p.source_id, p.x_pos, p.y_pos
            FROM points p
            JOIN maps m ON m.id = p.map_id
            JOIN point_labels pl ON pl.point_id = p.id
            JOIN label_nodes l ON l.id = pl.label_id
            WHERE m.source_id = ? AND l.source_id = ?
            ORDER BY p.id
            """,
            (row["map_id"], label_id),
        ):
            points.append(
                {
                    "id": int(point["id"]),
                    "source_id": point["source_id"],
                    "x": float(point["x_pos"]),
                    "y": float(point["y_pos"]),
                }
            )
        count = int(row["count"])
        total += count
        maps.append(
            {
                "map_id": row["map_id"],
                "name": row["map_name"],
                "path": _map_path(parents, row["map_id"]),
                "count": count,
                "points": points,
            }
        )
    return {
        "semantic_key": semantic_key,
        "label_id": label_id,
        "name": (label["name"] if label else semantic_key),
        "count": total,
        "maps": maps,
    }


def grease_topic_payload(ctx: ViewerContext) -> dict[str, Any]:
    return {
        "origin": _semantic_topic(ctx, "floating_grease_origin_retrace"),
        "notes": _semantic_topic(ctx, "floating_grease_notes"),
    }


def _sha_bytes(sha: str) -> int:
    path = asset_path(sha)
    return int(path.stat().st_size) if path and path.exists() else 0


def settings_payload(ctx: ViewerContext) -> dict[str, Any]:
    counts = ctx.core.counts()
    core_shas = {
        row["asset_sha256"]
        for row in ctx.core.conn.execute("SELECT asset_sha256 FROM map_fragments WHERE asset_sha256 IS NOT NULL")
    }
    detail_shas: set[str] = set()
    if ctx.detail is not None:
        detail_shas = {
            row["asset_sha256"]
            for row in ctx.detail.conn.execute(
                "SELECT DISTINCT asset_sha256 FROM point_detail_assets WHERE asset_sha256 IS NOT NULL"
            )
        }
    from hsrmap.paths import CACHE_DIR, THUMB_DIR, TILE_DIR

    def dir_bytes(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    return {
        "snapshot_id": ctx.snapshot_id,
        "maps": counts["renderable_maps"],
        "points": counts["points"],
        "detail_state": ctx.detail_state,
        "storage": {
            "core_assets_bytes": sum(_sha_bytes(sha) for sha in core_shas),
            "detail_assets_bytes": sum(_sha_bytes(sha) for sha in detail_shas),
            "thumbnail_bytes": dir_bytes(THUMB_DIR),
            "tile_cache_bytes": dir_bytes(TILE_DIR),
            "cache_bytes": dir_bytes(CACHE_DIR),
        },
    }


def update_check_payload() -> dict[str, Any]:
    return {
        "remote_enabled": False,
        "message": "离线模式，不访问外网",
        "snapshot_command": "python -m hsrmap sync",
        "live": "use /api/v1/data-source",
    }

