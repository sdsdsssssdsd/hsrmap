from __future__ import annotations

from typing import Any

from hsrmap.database import CoreDatabase
from hsrmap.request_key import request_key


def point_info_request_params(point_id: Any, app_version: str) -> dict[str, str]:
    return {
        "app_sn": "sr_map",
        "app_version": app_version,
        "lang": "zh-cn",
        "point_id": str(point_id),
    }


def source_ids_for_label_key(core: CoreDatabase, key: str) -> set[str]:
    label_ids: list[str] = []
    bound = core.semantic_source_id(key)
    if bound:
        label_ids.append(str(bound))
    else:
        for row in core.conn.execute("SELECT source_id FROM label_nodes WHERE name = ?", (key,)):
            label_ids.append(str(row["source_id"]))
    if not label_ids:
        return set()
    placeholders = ",".join("?" * len(label_ids))
    rows = core.conn.execute(
        f"""
        SELECT p.source_id FROM points p
        JOIN point_labels pl ON pl.point_id = p.id
        JOIN label_nodes l ON l.id = pl.label_id
        WHERE l.source_id IN ({placeholders})
        """,
        label_ids,
    )
    return {str(row["source_id"]) for row in rows}


def canary_source_ids(core: CoreDatabase, golden: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = {"5260"}
    for item in golden:
        ids.add(str(item["source_point_id"]))
    for key in ("floating_grease_origin_retrace", "floating_grease_notes"):
        label_id = core.semantic_source_id(key)
        if not label_id:
            continue
        for row in core.conn.execute(
            """
            SELECT p.source_id FROM points p
            JOIN point_labels pl ON pl.point_id = p.id
            JOIN label_nodes l ON l.id = pl.label_id
            WHERE l.source_id = ?
            """,
            (label_id,),
        ):
            ids.add(str(row["source_id"]))
    return ids


def point_info_request_key(point_id: Any, app_version: str) -> str:
    params = point_info_request_params(point_id, app_version)
    return request_key("point_info", params, app_version)


def build_detail_queue(
    core: CoreDatabase,
    app_version: str,
    source_point_ids: set[str] | None = None,
) -> dict[str, Any]:
    rows = list(
        core.conn.execute(
            """
            SELECT p.id AS core_point_id, p.source_id AS source_point_id, m.source_id AS map_source_id,
                   COALESCE(n.depth, 0) AS depth, COALESCE(n.sort_order, 0) AS sort_order
            FROM points p
            JOIN maps m ON m.id = p.map_id
            LEFT JOIN map_nodes n ON n.source_id = m.source_id
            ORDER BY depth, sort_order, CAST(m.source_id AS INTEGER), CAST(p.source_id AS INTEGER)
            """
        )
    )
    if source_point_ids is not None:
        wanted = {str(item) for item in source_point_ids}
        rows = [row for row in rows if str(row["source_point_id"]) in wanted]

    requests: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for row in rows:
        source_id = str(row["source_point_id"])
        source_counts[source_id] = source_counts.get(source_id, 0) + 1
        key = point_info_request_key(source_id, app_version)
        params = point_info_request_params(source_id, app_version)
        requests.setdefault(
            key,
            {
                "request_key": key,
                "endpoint_name": "point_info",
                "parameters": params,
                "source_point_id": source_id,
            },
        )
        bindings.append(
            {
                "core_point_id": int(row["core_point_id"]),
                "request_key": key,
                "source_point_id": source_id,
                "map_source_id": str(row["map_source_id"]),
            }
        )
    duplicates = sum(1 for count in source_counts.values() if count > 1)
    return {
        "core_point_count": len(rows),
        "unique_source_point_ids": len(source_counts),
        "unique_request_count": len(requests),
        "duplicate_source_point_ids": duplicates,
        "requests": list(requests.values()),
        "bindings": bindings,
        "duplicate_groups": [
            {"source_point_id": source_id, "count": count}
            for source_id, count in source_counts.items()
            if count > 1
        ],
    }
