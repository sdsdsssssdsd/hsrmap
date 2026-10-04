from __future__ import annotations

import json
from typing import Any

from hsrmap_phase1.labels import SEMANTIC_NAME_RULES
from hsrmap_phase1.raster import raster_spec_from_detail
from hsrmap_phase1.tree import _parse_detail, has_raster_detail
from hsrmap.transform import TRANSFORM_VERSION, source_to_raster, transform_invariant_ok


def flatten_map_nodes(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]] | None, inherited_parent: Any = None) -> None:
        if not nodes:
            return
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            source_id = node.get("id")
            parent = node.get("parent_id", inherited_parent)
            children = node.get("children") or []
            node_type = node.get("node_type")
            is_renderable = node_type == 2 and not children
            raw = {k: v for k, v in node.items() if k != "children"}
            out.append(
                {
                    "source_id": str(source_id),
                    "parent_source_id": None if parent in (None, 0, "0") else str(parent),
                    "node_type": node_type,
                    "name": node.get("name"),
                    "depth": node.get("depth"),
                    "sort_order": index,
                    "is_renderable": bool(is_renderable),
                    "raw_json": raw,
                }
            )
            walk(children, source_id)

    walk(tree)
    return out


def flatten_label_nodes(tree: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []

    def walk(items: list[dict[str, Any]] | None, inherited_parent: Any = None) -> None:
        if not items:
            return
        for index, node in enumerate(items):
            if not isinstance(node, dict):
                continue
            children = node.get("children") or []
            parent = node.get("parent_id", inherited_parent)
            raw = {k: v for k, v in node.items() if k != "children"}
            nodes.append(
                {
                    "source_id": str(node.get("id")),
                    "parent_source_id": None if parent in (None, 0, "0") else str(parent),
                    "name": node.get("name"),
                    "is_category": bool(children) or node.get("depth") == 1,
                    "is_selectable": not children,
                    "sort_order": index,
                    "icon_remote_url": node.get("icon") or None,
                    "raw_json": raw,
                }
            )
            walk(children, node.get("id"))

    walk(tree)
    by_name = {str(n["name"]): n for n in nodes}
    bindings = []
    for rule in SEMANTIC_NAME_RULES:
        hit = by_name.get(rule["match"])
        if not hit:
            continue
        bindings.append(
            {
                "semantic_key": rule["semantic_key"],
                "canonical_name": rule["match"],
                "source_label_id": hit["source_id"],
                "confidence": 1.0,
                "resolver": "exact_normalized_name",
            }
        )
    return nodes, bindings


def normalize_map_info(payload: dict[str, Any]) -> dict[str, Any]:
    info = ((payload.get("data") or {}).get("info") or {})
    detail = _parse_detail(info.get("detail")) or {}
    if not has_raster_detail(detail):
        raise ValueError(f"map {info.get('id')} has no raster detail")
    spec = raster_spec_from_detail(info.get("id"), detail)
    return {
        "source_id": str(info.get("id")),
        "name": info.get("name"),
        "canvas_width": spec["canvas"]["width"],
        "canvas_height": spec["canvas"]["height"],
        "origin_x": float((spec["origin"] or [0, 0])[0]),
        "origin_y": float((spec["origin"] or [0, 0])[1]),
        "padding_json": spec.get("padding"),
        "fragment_count": len(spec["fragments"]),
        "coordinate_transform": TRANSFORM_VERSION,
        "fragments": spec["fragments"],
        "detail": detail,
        "raw_info": info,
    }


def normalize_point(point: dict[str, Any], map_source_id: str, origin_x: float, origin_y: float) -> dict[str, Any]:
    x = float(point["x_pos"])
    y = float(point["y_pos"])
    rx, ry = source_to_raster(x, y, origin_x, origin_y)
    if not transform_invariant_ok(x, y, rx, ry, origin_x, origin_y):
        raise ValueError(f"transform invariant failed for point {point.get('id')}")
    return {
        "source_id": str(point["id"]),
        "map_source_id": str(map_source_id),
        "label_id": None if point.get("label_id") is None else str(point.get("label_id")),
        "x_pos": x,
        "y_pos": y,
        "z_pos": point.get("z_pos"),
        "raster_x": rx,
        "raster_y": ry,
        "raw_json": point,
    }
