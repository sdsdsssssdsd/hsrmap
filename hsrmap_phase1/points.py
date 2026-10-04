from __future__ import annotations

from collections import defaultdict
from typing import Any

from hsrmap_phase1.labels import semantic_key_for_source_id


PREFERRED_KEYS = (
    "floating_grease_origin_retrace",
    "floating_grease_notes",
)


def select_phase1_points(
    point_list: list[dict[str, Any]],
    resolved_labels: list[dict[str, Any]],
    count: int = 20,
) -> list[dict[str, Any]]:
    by_semantic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unlabeled: list[dict[str, Any]] = []
    for point in point_list:
        key = semantic_key_for_source_id(resolved_labels, point.get("label_id"))
        if key:
            by_semantic[key].append(point)
        else:
            unlabeled.append(point)

    selected: list[dict[str, Any]] = []
    seen: set[Any] = set()

    def take(items: list[dict[str, Any]], n: int) -> None:
        for item in items:
            if len(selected) >= count:
                return
            pid = item.get("id")
            if pid in seen:
                continue
            seen.add(pid)
            selected.append(item)
            n -= 1
            if n <= 0:
                return

    take(by_semantic.get("floating_grease_origin_retrace", []), 3)
    take(by_semantic.get("floating_grease_notes", []), 1)

    # Mix remaining labels instead of dumping one type.
    leftovers = unlabeled + [
        p
        for key, items in by_semantic.items()
        if key not in PREFERRED_KEYS
        for p in items
    ]
    leftovers += by_semantic.get("floating_grease_origin_retrace", [])[3:]
    leftovers += by_semantic.get("floating_grease_notes", [])[1:]

    by_label: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for point in leftovers:
        by_label[point.get("label_id")].append(point)
    rotating = list(by_label.values())
    while len(selected) < count and any(rotating):
        next_round = []
        for bucket in rotating:
            if not bucket:
                continue
            take([bucket[0]], 1)
            if len(bucket) > 1:
                next_round.append(bucket[1:])
        rotating = next_round

    return selected[:count]
