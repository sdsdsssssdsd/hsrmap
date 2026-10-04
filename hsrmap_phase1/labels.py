from __future__ import annotations

from typing import Any, Iterable


SEMANTIC_NAME_RULES = (
    {
        "semantic_key": "floating_grease_origin_retrace",
        "match": "浮脂溯源·二次元ROTATE！",
    },
    {
        "semantic_key": "floating_grease_notes",
        "match": "「浮脂记事」",
    },
)


def _walk_labels(nodes: Iterable[dict[str, Any]] | None):
    if not nodes:
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        yield from _walk_labels(node.get("children") or [])


def resolve_semantic_labels(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    by_name = {str(node.get("name")): node for node in _walk_labels(tree)}
    for rule in SEMANTIC_NAME_RULES:
        node = by_name.get(rule["match"])
        if node is None:
            continue
        resolved.append(
            {
                "semantic_key": rule["semantic_key"],
                "display_name": node.get("name"),
                "observed_source_id": node.get("id"),
            }
        )
    return resolved


def semantic_key_for_source_id(resolved: list[dict[str, Any]], source_id: Any) -> str | None:
    for item in resolved:
        if item.get("observed_source_id") == source_id:
            return item.get("semantic_key")
    return None
