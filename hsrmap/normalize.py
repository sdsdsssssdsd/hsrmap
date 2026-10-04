from __future__ import annotations

import json
from typing import Any, Mapping

from hsrmap.render_probe import DISCOVERY_TREE, UNKNOWN, RenderProbe
from hsrmap_phase1.labels import SEMANTIC_NAME_RULES
from hsrmap_phase1.raster import raster_spec_from_detail
from hsrmap_phase1.tree import _parse_detail, has_raster_detail
from hsrmap.transform import TRANSFORM_VERSION, source_to_raster, transform_invariant_ok


def flatten_map_nodes(
    tree: list[dict[str, Any]],
    *,
    probes: Mapping[str, RenderProbe] | None = None,
) -> list[dict[str, Any]]:
    """把官方 map/tree 拍平成 map_nodes 行。

    a1-8-1 §三 取消了 `is_renderable = node_type == 2 and not children`：那两个概念必须拆成
    两个**独立维度**：

    - `tree_leaf`       树事实：这个节点没有 children；
    - `is_renderable`   可渲染探测的结论：True / False / **None（UNKNOWN：还没有证据）**。

    `node_type == 2` 只是官方「这是一张地图」的结构提示，用来决定初始 frontier 去问哪些
    map/info（见 hsrmap/render_probe.py::tree_map_candidates），**不再**参与可渲染判定（§九）。
    没有喂 probes 时 `is_renderable` 就是 None —— 没问过官方就不猜。
    """
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
            raw = {k: v for k, v in node.items() if k != "children"}
            probe = None if probes is None else probes.get(str(source_id))
            out.append(
                {
                    "source_id": str(source_id),
                    "parent_source_id": None if parent in (None, 0, "0") else str(parent),
                    "node_type": node.get("node_type"),
                    "name": node.get("name"),
                    "depth": node.get("depth"),
                    "sort_order": index,
                    "tree_leaf": not children,
                    "is_renderable": None if probe is None else probe.renderable,
                    "render_probe_state": UNKNOWN if probe is None else probe.state,
                    "discovery_method": DISCOVERY_TREE,
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
