from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hsrmap.database import has_table, table_columns
from hsrmap.graph import (
    NAVIGABLE_EDGE_TYPES,
    STRUCTURAL_EDGE_TYPES,
    TREE_CHILD,
    closure,
    edge_type_counts,
    known_map_ids,
    load_edges,
    load_map_nodes,
    load_point_transitions,
    unresolved_targets,
)
from hsrmap.graph_audit import incoming_degree, reachable_from, reciprocal_cycles, self_loops, tree_roots
from hsrmap.graph_nav import navigation_context
from typing import Mapping  # noqa: E402  (仅用于 _map_path 的类型标注)
from hsrmap.paths import ASSETS
from hsrmap.viewer_bind import ViewerContext
from hsrmap.viewer_crs import get_max_bounds, get_raster_bounds

SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.I)


def _map_parents(ctx: ViewerContext) -> dict[str, Any]:
    return {
        row["source_id"]: row
        for row in ctx.core.conn.execute("SELECT source_id, parent_source_id, name FROM map_nodes")
    }


def _map_path(
    parents: dict[str, Any],
    source_id: str,
    *,
    names: Mapping[str, str] | None = None,
) -> str:
    """地图路径「根 / … / 本级」。

    `names` 是**显示名**（派生库 `node_display_names`）：给了就用真名，没给就沿用树名。
    两种口径共用这一段代码，**调用方各自决定用哪种** —— 判定层（Guide 匹配吃的 `map_path`/`region`）
    与显示层（界面上的面包屑）不是一回事，不能偷偷一起换。
    `names` 里没有的节点照旧退回树名 / id（不编）。
    """
    parts: list[str] = []
    current: str | None = source_id
    seen: set[str] = set()
    while current and current in parents and current not in seen:
        seen.add(current)
        label = ""
        if names:
            label = str(names.get(current) or "").strip()
        parts.append(label or parents[current]["name"] or current)
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
    #: §十三 / §十四：点位上的跳转（没有图库时是 None / []，前端据此不显示入口按钮）。
    transition, transition_targets = point_transitions_payload(ctx, int(row["id"]), str(row["source_id"]))
    map_source = map_row["source_id"] if map_row else None
    return {
        "core": {
            "point_id": str(row["id"]),
            "source_id": row["source_id"],
            "map_id": map_source,
            "x": float(row["x_pos"]),
            "y": float(row["y_pos"]),
        },
        "labels": labels,
        "detail": detail,
        "transition": transition,
        "transition_targets": transition_targets,
        #: §十一 / §十九：这张图是怎么走进来的（导航路径 ≠ 树路径），前端「返回入口」要用。
        "navigation": navigation_payload(ctx, map_source) if map_source else None,
    }


def _node_display_names(ctx: ViewerContext) -> dict[str, str]:
    """节点真名（派生表 `node_display_names`）——**两个来源都看**，都没有就空字典。

    为什么要两个：图句柄优先用「core.db 自带 map_edges」的那一份（新快照就是这样），
    而真名是**派生表**、只存在于旁挂库 —— 只查句柄会在新快照上把名字全丢掉。
    顺序：句柄连接 → 旁挂库；两者都没有那张表就返回空（树退回原来的显示，行为不变）。
    """
    try:
        from hsrmap.graph_names import load as load_names
    except Exception:  # noqa: BLE001 - 任何毛病都不该让树长不出来
        return {}

    connections: list[Any] = []
    handle = graph_handle(ctx) if "graph_handle" in globals() else None
    conn = getattr(handle, "conn", None) if handle is not None else None
    if conn is not None:
        connections.append(conn)
    try:
        from hsrmap.graph_nav import sidecar_graph_path
        from hsrmap.paths import DATA

        sidecar = sidecar_graph_path(DATA)
        if sidecar.is_file() and (conn is None or Path(getattr(conn, "path", "") or "") != sidecar):
            import sqlite3 as _sqlite3

            extra = _sqlite3.connect(f"file:{sidecar}?mode=ro", uri=True)
            extra.row_factory = _sqlite3.Row
            connections.append(extra)
    except Exception:  # noqa: BLE001 - 没有运行态路径就只查句柄
        pass

    merged: dict[str, str] = {}
    for connection in connections:
        try:
            merged.update(load_names(connection))
        except Exception:  # noqa: BLE001
            continue
    return merged


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
    #: §6.11 第 1 步：树里 350 个节点是空名或占位符（「特殊房间」），真名在**父容器**的
    #: map/info → children[].name 里，已由 graph backfill 收进**派生库**的 node_display_names。
    #: 这里只改**显示名**（name/name_source）；判定层（_map_path）继续用 map_nodes.name。
    display_names = _node_display_names(ctx)
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        tree_name = str(row["name"] or "")
        display = display_names.get(str(row["source_id"])) or tree_name or str(row["source_id"])
        by_id[row["source_id"]] = {
            "id": row["source_id"],
            "name": display,
            #: 名字从哪来（tree / graph:node_display_names / id）—— 前端要能如实说明。
            "name_source": (
                "graph:node_display_names" if display_names.get(str(row["source_id"]))
                else ("tree" if tree_name else "id")
            ),
            "tree_name": tree_name,
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


# --------------------------------------------------------------------------- #
# Map Graph（M7.4，a1-8-1 §十 / §十一 / §十三 / §十四）
#
# **读取优先级（runbook §6.2）**：
#   1. `core.db` 里有 `map_edges`        → 用它（M7.1 之后的新快照）；
#   2. 否则用旁挂派生库 `<data>/graph/core.db`（M7.2 的离线回填产物）；
#   3. 都没有 → 如实返回「没有跳转信息」，`available: false`。
#
# **只读**：旁挂库一律 `mode=ro` 打开，绝不允许因为「想读图」建出一个新库——文件不存在就是
# 「没有跳转信息」，不是「顺手建一个空的」。core.db 也不写（它是快照 / 冻结文物）。
#
# 深层地图**不进 tree**（§十）：`map_tree_payload` 的父子关系原样保留，跳转只通过
# transitions / point.transition 表达。
# --------------------------------------------------------------------------- #

#: 旁挂派生库的位置，与 `hsrmap.graph_backfill.default_out_path()` 同口径（那边是唯一的写入口）。
GRAPH_DIR_NAME = "graph"
GRAPH_DB_NAME = "core.db"

#: 这次读到的图是从哪一层来的（前端与审计都要能一眼看出降级到了哪一层）。
GRAPH_ORIGIN_CORE = "core.db"
GRAPH_ORIGIN_DERIVED = "graph/core.db"
GRAPH_ORIGIN_NONE = "none"

#: 「没有跳转信息」的如实说法（降级不是错误，但必须说清楚）。
GRAPH_UNAVAILABLE_MESSAGE = "没有跳转信息"

#: 边型 → 中文动作。`point_transitions.action_label` 缺失时用它兜底，前端永远有文案可用。
GRAPH_ACTION_LABELS: dict[str, str] = {
    "TREE_CHILD": "子地图",
    "FLOOR": "同区域楼层",
    "POINT_JUMP": "前往对应地图",
    "RELATED_MAP": "关联地图",
    "MAP_GROUP": "地图组",
    "PORTAL": "传送",
    "RETURN": "返回",
    "UNKNOWN_TRANSITION": "未知跳转",
}

#: 图句柄是**每进程一次**的惰性资源：并发首个请求可能同时进来，加个锁避免开出两条连接。
_GRAPH_LOCK = threading.Lock()


@dataclass
class GraphHandle:
    """一次「图从哪读」的解析结果。

    `conn is None` = 没有跳转信息（不是错误）：`message` 里写清楚为什么。
    `owned=True` 的连接由本层负责关闭（`ViewerContext.close()`）。
    """

    conn: sqlite3.Connection | None
    origin: str
    path: Path | None = None
    owned: bool = False
    message: str = ""

    @property
    def available(self) -> bool:
        return self.conn is not None

    def as_source(self) -> dict[str, Any]:
        """对外只说「哪一层 / 哪个文件」，不暴露绝对路径。"""
        return {
            "origin": self.origin,
            "available": self.available,
            "file": None if self.path is None else Path(self.path).name,
            "message": self.message,
        }


def graph_db_path() -> Path:
    """旁挂图谱库的路径。运行目录按**调用时**解析（a1-8 四.2），不缓存。"""
    from hsrmap import paths

    return Path(paths.DATA) / GRAPH_DIR_NAME / GRAPH_DB_NAME


def _open_graph_readonly(path: Path) -> sqlite3.Connection:
    """只读打开：`mode=ro` 对不存在的文件直接报错，**不会**建库。"""
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_graph(ctx: ViewerContext, graph_path: Path | None) -> GraphHandle:
    if has_table(ctx.core.conn, "map_edges"):
        return GraphHandle(
            ctx.core.conn,
            GRAPH_ORIGIN_CORE,
            Path(getattr(ctx.core, "path", "") or "") or None,
            owned=False,
            message="core.db 自带 map_edges（M7.1 之后的新快照）",
        )
    path = Path(graph_path) if graph_path is not None else graph_db_path()
    if not path.is_file():
        return GraphHandle(
            None,
            GRAPH_ORIGIN_NONE,
            path,
            owned=False,
            message=f"{GRAPH_UNAVAILABLE_MESSAGE}：core.db 里没有 map_edges，{path.name} 也不存在"
            "（要边就先跑 python -m hsrmap graph backfill --write）",
        )
    try:
        conn = _open_graph_readonly(path)
    except sqlite3.Error as exc:  # 打不开也要降级，不是 500
        return GraphHandle(None, GRAPH_ORIGIN_NONE, path, owned=False, message=f"{GRAPH_UNAVAILABLE_MESSAGE}：图谱库打不开（{exc}）")
    if not has_table(conn, "map_edges"):
        conn.close()
        return GraphHandle(None, GRAPH_ORIGIN_NONE, path, owned=False, message=f"{GRAPH_UNAVAILABLE_MESSAGE}：{path.name} 里没有 map_edges 表")
    return GraphHandle(conn, GRAPH_ORIGIN_DERIVED, path, owned=True, message="旁挂图谱库（M7.2 离线回填产物，只读打开）")


def graph_handle(ctx: ViewerContext, *, graph_path: Path | None = None) -> GraphHandle:
    """解析并缓存「这张图的边从哪来」。`graph_path` 显式给出时不走缓存（测试用）。"""
    if graph_path is not None:
        return _resolve_graph(ctx, graph_path)
    cached = getattr(ctx, "graph", None)
    if isinstance(cached, GraphHandle):
        return cached
    with _GRAPH_LOCK:
        cached = getattr(ctx, "graph", None)
        if isinstance(cached, GraphHandle):
            return cached
        handle = _resolve_graph(ctx, None)
        ctx.graph = handle  # type: ignore[attr-defined] - ViewerContext 上的缓存槽（见 viewer_bind）
        return handle


def _map_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """`map_id → {name, renderable, in_tree}`（两条 SQL，不做逐点查询）。

    名字口径与 `hsrmap.graph_nav` 一致：`maps.display_name` → `maps.name` → 树名 → 空串
    （M7.3 才会写 display_name；缺列 / 缺表都回退，绝不编名字）。
    可渲染口径与 M7.1 的 probe 一致：`maps` 落库 + 至少一个带 remote_url 的 fragment + canvas 有效。
    """
    index: dict[str, dict[str, Any]] = {}
    if has_table(conn, "maps"):
        columns = table_columns(conn, "maps")
        display = "display_name" if "display_name" in columns else "NULL AS display_name"
        has_fragments = has_table(conn, "map_fragments")
        join = "LEFT JOIN map_fragments f ON f.map_id = m.id" if has_fragments else ""
        fragments = "COUNT(f.id)" if has_fragments else "0"
        with_url = "SUM(CASE WHEN COALESCE(f.remote_url, '') <> '' THEN 1 ELSE 0 END)" if has_fragments else "0"
        for row in conn.execute(
            f"""
            SELECT m.source_id, m.name, {display}, m.canvas_width, m.canvas_height,
                   {fragments} AS fragments, COALESCE({with_url}, 0) AS with_url
            FROM maps m {join}
            GROUP BY m.source_id
            """
        ):
            name = str(row["display_name"] or "").strip() or str(row["name"] or "").strip()
            index[str(row["source_id"])] = {
                "name": name,
                "renderable": int(row["with_url"] or 0) > 0
                and float(row["canvas_width"] or 0) > 0
                and float(row["canvas_height"] or 0) > 0,
                #: 这一条来自 maps 表（已经同步过的可渲染地图），不是树节点。
                "synced": True,
                "in_tree": False,
            }
    if has_table(conn, "map_nodes"):
        for row in conn.execute("SELECT source_id, name FROM map_nodes"):
            entry = index.setdefault(str(row[0]), {"name": "", "renderable": False, "synced": False, "in_tree": True})
            if not entry["name"]:
                entry["name"] = str(row[1] or "").strip()
            entry["in_tree"] = True
    return index


def _map_name(index: dict[str, dict[str, Any]], map_id: str) -> str:
    return str((index.get(str(map_id)) or {}).get("name") or "")


def _renderable_ids(index: dict[str, dict[str, Any]]) -> set[str]:
    return {map_id for map_id, item in index.items() if item.get("renderable")}


def _synced_ids(index: dict[str, dict[str, Any]]) -> set[str]:
    """已经同步过 map/info 的地图（§二十四 的 synced_renderable_maps 候选）。"""
    return {map_id for map_id, item in index.items() if item.get("synced")}


def _transition_labels(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """`(source_point_id, target_map_id) → 官方动作文案`（point_transitions.action_label）。"""
    labels: dict[tuple[str, str], str] = {}
    if not (has_table(conn, "point_transitions") and has_table(conn, "points")):
        return labels
    for row in conn.execute(
        """
        SELECT p.source_id AS point_source_id, t.target_map_source_id, t.action_label
        FROM point_transitions t
        JOIN points p ON p.id = t.point_id
        """
    ):
        if row["action_label"]:
            labels[(str(row["point_source_id"]), str(row["target_map_source_id"]))] = str(row["action_label"])
    return labels


def _graph_point_agrees(conn: sqlite3.Connection, core_point_id: int, source_id: str) -> bool:
    """旁挂库和当前快照的 `points.id` 必须指同一个点。

    指不上（老图库配新快照）时**宁可不说**，也不报一条错位的跳转。
    """
    if not has_table(conn, "points"):
        return False
    row = conn.execute("SELECT source_id FROM points WHERE id = ?", (int(core_point_id),)).fetchone()
    return row is not None and str(row[0]) == str(source_id)


def _empty_audit() -> dict[str, Any]:
    return {
        "tree_nodes": 0,
        "renderable_maps": 0,
        "deep_maps": 0,
        "edges_total": 0,
        "by_type": {},
        "structural": {"edge_types": sorted(STRUCTURAL_EDGE_TYPES), "edges": 0, "distinct_targets": 0,
                       "targets_renderable": 0, "missing_targets": [], "missing_total": 0},
        "navigable": {"edge_types": sorted(NAVIGABLE_EDGE_TYPES), "edges": 0, "distinct_targets": 0,
                      "targets_renderable": 0, "missing_targets": [], "missing_total": 0},
        "unresolved_targets": {"total": 0, "navigable_total": 0, "sample": []},
        "orphan_renderables": {"total": 0, "sample": []},
        "unreachable_renderables": {"total": 0, "sample": []},
        "closure": {"converged": False, "visited": 0, "frontier": 0, "cycles": 0},
        "cycles": {"expected_reciprocal": 0, "unexpected": 0, "self_loops": 0},
        "gate": {"ok": True, "checks": {}, "reasons": []},
    }


def graph_audit_summary(ctx: ViewerContext, edges: list[Any], *, sample_limit: int = 10) -> dict[str, Any]:
    """§十七 的审计摘要（只读）：孤儿 / unresolved / **结构类与导航类分开**（§6.3 口径）。

    结构类边（TREE_CHILD / RELATED_MAP / MAP_GROUP / FLOOR）的目标本来就是树里的容器节点，
    单独统计、不算「跳不过去」；导航类边（POINT_JUMP / PORTAL / RETURN / UNKNOWN_TRANSITION）
    的目标必须是可渲染地图——缺口留在 `missing_targets` 里。
    """
    nodes = load_map_nodes(ctx.core.conn)
    tree_ids = {str(node["source_id"]) for node in nodes}
    index = _map_index(ctx.core.conn)
    renderable = _renderable_ids(index)
    known = known_map_ids(ctx.core.conn)
    roots = tree_roots(nodes)
    root_set = set(roots)
    reachable = set(reachable_from(edges, roots))
    degree = incoming_degree(edges)
    no_incoming = sorted(map_id for map_id in renderable if degree.get(map_id, 0) == 0 and map_id not in root_set)
    unreachable = sorted(renderable - reachable)
    orphans = sorted(set(no_incoming) | (set(unreachable) - root_set))
    unresolved = unresolved_targets(edges, known)
    unresolved_nav = unresolved_targets(edges, known, navigable_only=True)
    cycles = reciprocal_cycles(edges)
    closure_result = closure(edges, seeds=sorted(known))
    counts = edge_type_counts(edges)
    deep_maps = sorted(renderable - tree_ids)

    def group(types: frozenset[str]) -> dict[str, Any]:
        subset = [edge for edge in edges if edge.edge_type in types]
        targets = {str(edge.target_map_id) for edge in subset}
        missing = sorted(target for target in targets if target not in renderable)
        return {
            "edge_types": sorted(types),
            "edges": len(subset),
            "distinct_targets": len(targets),
            "targets_renderable": len(targets) - len(missing),
            "missing_targets": missing[:sample_limit],
            "missing_total": len(missing),
        }

    structural = group(STRUCTURAL_EDGE_TYPES)
    navigable = group(NAVIGABLE_EDGE_TYPES)
    checks = {
        "unresolved_targets_empty": not unresolved,
        "orphan_renderables_empty": not orphans,
        "render_requiring_targets_renderable": navigable["missing_total"] == 0,
        "closure_converged": bool(closure_result.converged),
    }
    return {
        "tree_nodes": len(nodes),
        "renderable_maps": len(renderable),
        "deep_maps": len(deep_maps),
        "deep_map_ids": deep_maps[:sample_limit],
        "edges_total": len(edges),
        "by_type": {edge_type: value for edge_type, value in counts.items() if value},
        "structural": structural,
        "navigable": navigable,
        "unresolved_targets": {
            "total": len(unresolved),
            "navigable_total": len(unresolved_nav),
            "sample": [
                {
                    "source_map_id": edge.source_map_id,
                    "target_map_id": edge.target_map_id,
                    "edge_type": edge.edge_type,
                    "source_point_id": edge.source_point_id,
                }
                for edge in unresolved[:sample_limit]
            ],
        },
        "orphan_renderables": {"total": len(orphans), "sample": orphans[:sample_limit]},
        "unreachable_renderables": {"total": len(unreachable), "sample": unreachable[:sample_limit]},
        "closure": {
            "converged": bool(closure_result.converged),
            "visited": len(closure_result.visited),
            "frontier": len(closure_result.frontier),
            "cycles": len(closure_result.cycles),
        },
        "cycles": {
            "expected_reciprocal": len(cycles["expected_reciprocal"]),
            "unexpected": len(cycles["unexpected"]),
            "self_loops": len(self_loops(edges)),
        },
        "gate": {"ok": all(checks.values()), "checks": checks, "reasons": [key for key, ok in checks.items() if not ok]},
    }


def graph_payload(ctx: ViewerContext, *, graph_path: Path | None = None, sample_limit: int = 10) -> dict[str, Any]:
    """`GET /api/v1/map/graph`：节点 + 边 + 审计摘要（带 available 降级字段）。

    节点描述的是**这个 Viewer 能打开的地图宇宙**（快照的 map_nodes + 已同步的深层地图），
    边来自图谱层（core.db 或旁挂库）。没有图时如实返回 `available: false`，不抛错。
    """
    handle = graph_handle(ctx, graph_path=graph_path)
    index = _map_index(ctx.core.conn)
    nodes: list[dict[str, Any]] = []
    for item in load_map_nodes(ctx.core.conn):
        map_id = str(item["source_id"])
        entry = index.get(map_id) or {}
        stored = item.get("is_renderable")
        nodes.append(
            {
                "id": map_id,
                "name": item.get("name") or map_id,
                "parent_id": item.get("parent_source_id"),
                "type": "map" if item.get("is_renderable") else "folder",
                "renderable": bool(entry["renderable"]) if map_id in index else (None if stored is None else bool(stored)),
                "in_tree": True,
                "tree_leaf": item.get("tree_leaf"),
                "render_probe_state": item.get("render_probe_state"),
                "discovery_method": item.get("discovery_method"),
            }
        )
    tree_ids = {node["id"] for node in nodes}
    for map_id in sorted(set(index) - tree_ids):
        nodes.append(
            {
                "id": map_id,
                "name": _map_name(index, map_id) or map_id,
                "parent_id": None,
                "type": "map" if index[map_id]["renderable"] else "folder",
                "renderable": bool(index[map_id]["renderable"]),
                "in_tree": False,
                "tree_leaf": None,
                "render_probe_state": None,
                "discovery_method": None,
            }
        )
    base: dict[str, Any] = {
        "api_version": 1,
        "snapshot": ctx.snapshot_id,
        "source": handle.as_source(),
        "available": handle.available,
        "message": None if handle.available else GRAPH_UNAVAILABLE_MESSAGE,
        "counts": {
            "nodes": len(nodes),
            "tree_nodes": len(tree_ids),
            "deep_maps": len(nodes) - len(tree_ids),
            "renderable_maps": len(_renderable_ids(index)),
            "maps": len(_synced_ids(index)),
            "edges": 0,
            "point_transitions": 0,
        },
        "nodes": nodes,
        "edges": [],
        "audit": _empty_audit(),
    }
    if handle.conn is None:
        return base
    edges = load_edges(handle.conn)
    base["edges"] = [
        {
            "source_map_id": str(edge.source_map_id),
            "target_map_id": str(edge.target_map_id),
            "edge_type": edge.edge_type,
            "source_point_id": edge.source_point_id,
            "source_label_id": edge.source_label_id,
            "discovery_source": edge.discovery_source,
            "confidence": edge.confidence,
            "bidirectional": edge.bidirectional,
            "navigable": edge.edge_type in NAVIGABLE_EDGE_TYPES,
        }
        for edge in edges
    ]
    base["counts"]["edges"] = len(edges)
    base["counts"]["point_transitions"] = (
        int(handle.conn.execute("SELECT COUNT(*) FROM point_transitions").fetchone()[0])
        if has_table(handle.conn, "point_transitions")
        else 0
    )
    base["audit"] = graph_audit_summary(ctx, edges, sample_limit=sample_limit)
    return base


def navigation_payload(ctx: ViewerContext, map_id: str, handle: GraphHandle | None = None) -> dict[str, Any]:
    """§十一 / §十九 的导航上下文：这张图**是怎么走进来的**（没有图库时退化为纯树路径）。"""
    resolved = handle if handle is not None else graph_handle(ctx)
    conn = resolved.conn if resolved.conn is not None else ctx.core.conn
    return navigation_context(conn, str(map_id))


def map_transitions_payload(
    ctx: ViewerContext,
    map_id: str,
    *,
    graph_path: Path | None = None,
) -> dict[str, Any]:
    """`GET /api/v1/maps/{map_id}/transitions`：这张图能去哪。

    每条 `{type, target_map_id, target_name, action, source_point_id, renderable}`。
    **不含 TREE_CHILD**：树的父子关系走 `/api/v1/maps/tree`，这里只表达「跳转」（§十）。
    """
    wanted = str(map_id)
    handle = graph_handle(ctx, graph_path=graph_path)
    index = _map_index(ctx.core.conn)
    in_tree = bool((index.get(wanted) or {}).get("in_tree"))
    payload: dict[str, Any] = {
        "map_id": wanted,
        "name": _map_name(index, wanted) or wanted,
        "known": bool(in_tree or wanted in index),
        "available": handle.available,
        "message": None if handle.available else GRAPH_UNAVAILABLE_MESSAGE,
        "reason": handle.message,
        "source": handle.as_source(),
        "transitions": [],
        "counts": {"total": 0, "navigable": 0, "renderable_targets": 0},
        "navigation": navigation_payload(ctx, wanted, handle),
    }
    if handle.conn is None:
        return payload
    labels = _transition_labels(handle.conn)
    renderable = _renderable_ids(index)
    entries: list[dict[str, Any]] = []
    for edge in load_edges(handle.conn, source_map_id=wanted):
        if edge.edge_type == TREE_CHILD:
            continue
        target = str(edge.target_map_id)
        point_id = None if edge.source_point_id is None else str(edge.source_point_id)
        entries.append(
            {
                "type": edge.edge_type,
                "target_map_id": target,
                "target_name": _map_name(index, target) or target,
                "action": labels.get((point_id or "", target)) or GRAPH_ACTION_LABELS.get(edge.edge_type, "跳转"),
                "source_point_id": point_id,
                "renderable": target in renderable,
                "navigable": edge.edge_type in NAVIGABLE_EDGE_TYPES,
                "discovery_source": edge.discovery_source,
                "confidence": edge.confidence,
            }
        )
    entries.sort(key=lambda item: (not item["navigable"], item["type"], item["target_map_id"], item["source_point_id"] or ""))
    payload["transitions"] = entries
    payload["counts"] = {
        "total": len(entries),
        "navigable": sum(1 for item in entries if item["navigable"]),
        "renderable_targets": sum(1 for item in entries if item["renderable"]),
    }
    return payload


def point_transitions_payload(
    ctx: ViewerContext,
    core_point_id: int,
    source_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """§十三 / §十四：点位上的跳转。返回 `(transition, transition_targets)`。

    `transition` 就是 `PointTransition.as_viewer()` 的形状（前端不需要认识 related_jump_id）；
    `transition_targets` 是 §十四 的数组，额外带 `name` / `renderable` 供界面判断能不能进。
    """
    handle = graph_handle(ctx)
    if handle.conn is None:
        return None, []
    if not _graph_point_agrees(handle.conn, core_point_id, source_id):
        return None, []
    found = load_point_transitions(handle.conn, [int(core_point_id)])
    if not found:
        return None, []
    index = _map_index(ctx.core.conn)
    renderable = _renderable_ids(index)
    targets: list[dict[str, Any]] = []
    for item in found:
        target = item.target_map_source_id
        targets.append(
            {
                **item.as_viewer(),
                "map_id": target,
                "name": _map_name(index, target) or target,
                "renderable": target in renderable,
                "source_point_id": str(source_id),
            }
        )
    return found[0].as_viewer(), targets

