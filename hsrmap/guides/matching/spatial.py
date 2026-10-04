from __future__ import annotations

import re
from statistics import median
from typing import Any

_FLOOR_RE = re.compile(r"(\d)\s*层")

CANONICAL = (
    "左下角",
    "右下角",
    "左上角",
    "右上角",
    "左侧",
    "右侧",
    "上方",
    "下方",
    "中间",
)

_ALIASES = {
    "左下角": "左下角",
    "左下方": "左下角",
    "左下": "左下角",
    "右下角": "右下角",
    "右下方": "右下角",
    "右下": "右下角",
    "左上角": "左上角",
    "左上方": "左上角",
    "左上": "左上角",
    "右上角": "右上角",
    "右上方": "右上角",
    "右上": "右上角",
    "左侧": "左侧",
    "左边": "左侧",
    "右侧": "右侧",
    "右边": "右侧",
    "上方": "上方",
    "上面": "上方",
    "下方": "下方",
    "下面": "下方",
    "中间": "中间",
    "中部": "中间",
    "中央": "中间",
}

_AXIS = {
    "左下角": ("left", "down"),
    "右下角": ("right", "down"),
    "左上角": ("left", "up"),
    "右上角": ("right", "up"),
    "左侧": ("left", None),
    "右侧": ("right", None),
    "上方": (None, "up"),
    "下方": (None, "down"),
    "中间": ("mid", "mid"),
}

_TOKENS = tuple(sorted(_ALIASES, key=len, reverse=True))


def parse_spatial_anchor(text: str | None) -> str | None:
    if not text:
        return None
    blob = str(text)
    for token in _TOKENS:
        if token in blob:
            return _ALIASES[token]
    return None


def parse_floor_label(text: str | None) -> int | None:
    if not text:
        return None
    match = _FLOOR_RE.search(str(text))
    return int(match.group(1)) if match else None


def filter_by_floor(points: list[dict[str, Any]], floor: int | None) -> list[dict[str, Any]]:
    if not floor:
        return list(points)
    token = f"{floor}层"
    hits = [
        row
        for row in points
        if token in str(row.get("map_name") or "") or token in str(row.get("map_path") or "") or token in str(row.get("path") or "")
    ]
    return hits or list(points)


def assign_map_units(units: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[int, str]:
    remaining_units = list(enumerate(units))
    remaining = list(candidates)
    assigned: dict[int, str] = {}
    progressed = True
    while progressed and remaining_units and remaining:
        progressed = False
        claims: list[tuple[int, str]] = []
        for index, unit in remaining_units:
            pool = filter_by_floor(remaining, unit.get("floor_label") or parse_floor_label(unit.get("spatial_anchor")))
            if unit.get("spatial_anchor"):
                pick = pick_by_anchor(unit.get("spatial_anchor"), pool)
            elif len(pool) == 1:
                pick = str(pool[0]["source_point_id"])
            else:
                pick = None
            if pick:
                claims.append((index, pick))
        counts: dict[str, int] = {}
        for _, pick in claims:
            counts[pick] = counts.get(pick, 0) + 1
        kept = []
        for index, unit in remaining_units:
            hit = next((pick for idx, pick in claims if idx == index and counts.get(pick) == 1), None)
            if hit:
                assigned[index] = hit
                remaining = [row for row in remaining if str(row["source_point_id"]) != hit]
                progressed = True
            else:
                kept.append((index, unit))
        remaining_units = kept
    leftover_ids = [str(row["source_point_id"]) for row in remaining]
    for index, unit in remaining_units:
        current = str(unit.get("source_point_id") or "")
        if current and current in leftover_ids:
            assigned[index] = current
            leftover_ids.remove(current)
    for index, _unit in remaining_units:
        if index not in assigned and leftover_ids:
            assigned[index] = leftover_ids.pop(0)
    return assigned


def pick_by_anchor(anchor: str | None, points: list[dict[str, Any]]) -> str | None:
    label = parse_spatial_anchor(anchor) or (anchor if anchor in _AXIS else None)
    axis = _AXIS.get(label or "")
    usable = [row for row in points if _has_xy(row)]
    if not axis or len(usable) < 2:
        return None
    xs = [float(row["x"]) for row in usable]
    ys = [float(row["y"]) for row in usable]
    mid_x = median(xs)
    mid_y = median(ys)
    x_dir, y_dir = axis
    if x_dir == "mid" and y_dir == "mid":
        return _closest_to(usable, mid_x, mid_y)
    if x_dir and y_dir:
        hits = [row for row in usable if _on_side(float(row["x"]), x_dir, mid_x) and _on_side(float(row["y"]), y_dir, mid_y)]
        return _unique_id(hits)
    if x_dir:
        return _extreme(usable, "x", x_dir)
    return _extreme(usable, "y", y_dir)


def _has_xy(row: dict[str, Any]) -> bool:
    return row.get("x") is not None and row.get("y") is not None


def _on_side(value: float, direction: str, mid: float) -> bool:
    # Official x/y are image-space: x east, y down. 上 is smaller y.
    if direction == "left" or direction == "up":
        return value < mid
    if direction == "right" or direction == "down":
        return value > mid
    return False


def _extreme(points: list[dict[str, Any]], axis: str, direction: str) -> str | None:
    reverse = direction in {"right", "down"}
    ranked = sorted(points, key=lambda row: float(row[axis]), reverse=reverse)
    if len(ranked) >= 2 and float(ranked[0][axis]) == float(ranked[1][axis]):
        return None
    return str(ranked[0]["source_point_id"])


def _closest_to(points: list[dict[str, Any]], mid_x: float, mid_y: float) -> str | None:
    ranked = sorted(points, key=lambda row: (float(row["x"]) - mid_x) ** 2 + (float(row["y"]) - mid_y) ** 2)
    if len(ranked) >= 2:
        first = (float(ranked[0]["x"]) - mid_x) ** 2 + (float(ranked[0]["y"]) - mid_y) ** 2
        second = (float(ranked[1]["x"]) - mid_x) ** 2 + (float(ranked[1]["y"]) - mid_y) ** 2
        if first == second:
            return None
    return str(ranked[0]["source_point_id"])


def _unique_id(hits: list[dict[str, Any]]) -> str | None:
    ids = {str(row["source_point_id"]) for row in hits}
    if len(ids) != 1:
        return None
    return next(iter(ids))
