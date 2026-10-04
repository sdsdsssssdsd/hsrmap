from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _parse_detail(raw: Any) -> dict[str, Any] | None:
    if raw in (None, "", {}):
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    if isinstance(raw, dict):
        return raw
    return None


def has_raster_detail(detail: Any) -> bool:
    parsed = _parse_detail(detail)
    if not parsed:
        return False
    slices = parsed.get("slices")
    if not isinstance(slices, list) or not slices:
        return False
    for row in slices:
        if isinstance(row, list):
            for cell in row:
                if isinstance(cell, dict) and cell.get("url"):
                    return True
        elif isinstance(row, dict) and row.get("url"):
            return True
    return False


@dataclass
class TreeClassification:
    total_nodes: int = 0
    nodes_with_children: int = 0
    leaf_nodes: int = 0
    folder_or_group_nodes: int = 0
    renderable_map_ids: list[int] = field(default_factory=list)
    unsupported_or_empty_leaves: int = 0
    nodes_with_id: int = 0
    node_type_counts: dict[Any, int] = field(default_factory=dict)
    nodes_by_id: dict[int, dict[str, Any]] = field(default_factory=dict)
    map_info_success_ids: list[int] = field(default_factory=list)
    map_info_raster_ids: list[int] = field(default_factory=list)


def classify_tree_nodes(tree: list[dict[str, Any]]) -> TreeClassification:
    report = TreeClassification()

    def walk(nodes: list[dict[str, Any]] | None) -> None:
        if not nodes:
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            report.total_nodes += 1
            node_id = node.get("id")
            if node_id is not None:
                report.nodes_with_id += 1
                report.nodes_by_id[int(node_id)] = node
            node_type = node.get("node_type")
            report.node_type_counts[node_type] = report.node_type_counts.get(node_type, 0) + 1

            children = node.get("children") or []
            has_children = isinstance(children, list) and len(children) > 0
            if has_children:
                report.nodes_with_children += 1
                report.folder_or_group_nodes += 1
                walk(children)
                continue

            report.leaf_nodes += 1
            if has_raster_detail(node.get("detail")):
                if node_id is not None:
                    report.renderable_map_ids.append(int(node_id))
            else:
                report.unsupported_or_empty_leaves += 1
                if node_type != 2:
                    report.folder_or_group_nodes += 1

    walk(tree)
    return report


def apply_map_info(report: TreeClassification, map_id: int, info_payload: dict[str, Any]) -> None:
    if info_payload.get("retcode") != 0:
        return
    if map_id not in report.map_info_success_ids:
        report.map_info_success_ids.append(map_id)
    info = ((info_payload.get("data") or {}).get("info") or {})
    if has_raster_detail(info.get("detail")):
        if map_id not in report.map_info_raster_ids:
            report.map_info_raster_ids.append(map_id)
        if map_id not in report.renderable_map_ids:
            report.renderable_map_ids.append(map_id)
        if map_id in report.nodes_by_id and report.unsupported_or_empty_leaves > 0:
            # A previously empty leaf is now known to be renderable.
            node = report.nodes_by_id[map_id]
            children = node.get("children") or []
            if not children:
                report.unsupported_or_empty_leaves = max(0, report.unsupported_or_empty_leaves - 1)
