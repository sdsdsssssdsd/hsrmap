"""路线规划（a1-9 P6.7）：把「剩余点位」按地图聚类、按坐标就近排序。

这一层刻意只做两件事：**聚类** 和 **排序**。

不做的（因为没数据，做了就是编）：

* 不估「大约几分钟」——没有任何真实移动速度/传送点数据；
* 不说「多少米」——坐标是官方地图的平面坐标，单位不是米。

所以输出的距离一律写成 `distance_units`（地图坐标单位），并且带 `unit` 字段说明它是什么。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from hsrmap.progress.atlas import STATE_COMPLETED, STATE_CONFLICT, STATE_UNCLEAR

#: 一个点位「能照着做」的判定：定位与解法证据都在，才算攻略齐。
READY = "ready"
NEED_SOLVE = "need_solve"
NEED_LOCATE = "need_locate"


def readiness(point: Mapping[str, Any]) -> str:
    if str(point.get("solve_evidence") or "") and str(point.get("locate_evidence") or ""):
        return READY
    if not str(point.get("locate_evidence") or ""):
        return NEED_LOCATE
    return NEED_SOLVE


def _xy(point: Mapping[str, Any]) -> tuple[float, float] | None:
    try:
        return float(point["x"]), float(point["y"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return None


def distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> float | None:
    """两点在地图坐标里的欧氏距离（单位是坐标，不是米）。"""
    first, second = _xy(a), _xy(b)
    if first is None or second is None:
        return None
    return round(math.dist(first, second), 2)


def nearest_order(
    points: Sequence[Mapping[str, Any]],
    *,
    start: Mapping[str, Any] | None = None,
) -> list[Mapping[str, Any]]:
    """贪心最近邻排序（点位数量是几十级，贪心足够且结果可解释）。

    没有坐标的点位不丢：排在最后，保持原来的相对顺序 —— 宁可排得难看，也不能悄悄吞点。
    """
    located = [p for p in points if _xy(p) is not None]
    unlocated = [p for p in points if _xy(p) is None]
    ordered: list[Mapping[str, Any]] = []
    pool = list(located)
    current = start
    if current is None and pool:
        #: 起点：最靠左上（先 x 后 y）的那个点，规则固定、不随机。
        current = min(pool, key=lambda p: (_xy(p) or (0.0, 0.0))[0] + (_xy(p) or (0.0, 0.0))[1])
    while pool:
        if current is None:
            break
        best = min(pool, key=lambda p: (distance(current, p) if distance(current, p) is not None else math.inf))
        ordered.append(best)
        pool.remove(best)
        current = best
    return ordered + unlocated


@dataclass(frozen=True)
class RouteStep:
    index: int
    source_point_id: str
    label: str
    zone: str
    region: str
    map_name: str
    state: str
    readiness: str
    solve_kind: str
    guide_id: int | None
    title: str
    x: float | None
    y: float | None
    distance_units: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "source_point_id": self.source_point_id,
            "label": self.label,
            "zone": self.zone,
            "region": self.region,
            "map_name": self.map_name,
            "state": self.state,
            "readiness": self.readiness,
            "solve_kind": self.solve_kind,
            "guide_id": self.guide_id,
            "title": self.title,
            "x": self.x,
            "y": self.y,
            "distance_units": self.distance_units,
        }


def _remaining_points(atlas: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for region in atlas.get("regions") or []:
        for amap in region.get("maps") or []:
            for point in amap.get("points") or []:
                if str(point.get("state")) == STATE_COMPLETED:
                    continue
                out.append(dict(point))
    return out


def plan_routes(
    atlas: Mapping[str, Any],
    *,
    max_points: int = 12,
    max_routes: int = 6,
    zone: str | None = None,
) -> dict[str, Any]:
    """把剩余点位聚成「一张图一条路线」，图内按坐标就近排序。

    排序依据（都能从数据里读出来，没有一条是猜的）：

    1. 攻略齐（LOCATE + SOLVE 都有）的点位图优先；
    2. 剩余多的图优先；
    3. 大区 / 地图名做稳定兜底。
    """
    points = [p for p in _remaining_points(atlas) if not zone or str(p.get("zone")) == zone]
    clusters: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for point in points:
        key = (str(point.get("zone") or ""), str(point.get("map_name") or ""), str(point.get("map_id") or ""))
        clusters.setdefault(key, []).append(point)

    routes: list[dict[str, Any]] = []
    for (zone_name, map_name, map_id), members in clusters.items():
        ordered = nearest_order(members)
        steps: list[RouteStep] = []
        previous: Mapping[str, Any] | None = None
        for index, point in enumerate(ordered[: max(1, int(max_points))], start=1):
            steps.append(RouteStep(
                index=index,
                source_point_id=str(point.get("source_point_id") or ""),
                label=str(point.get("label") or ""),
                zone=zone_name,
                region=str(point.get("region") or ""),
                map_name=map_name,
                state=str(point.get("state") or ""),
                readiness=readiness(point),
                solve_kind=str(point.get("solve_kind") or ""),
                guide_id=point.get("guide_id") if isinstance(point.get("guide_id"), int) else None,
                title=str(point.get("title") or ""),
                x=_xy(point)[0] if _xy(point) else None,
                y=_xy(point)[1] if _xy(point) else None,
                distance_units=distance(previous, point) if previous is not None else None,
            ))
            previous = point
        ready = sum(1 for step in steps if step.readiness == READY)
        routes.append({
            "route_id": f"{zone_name}/{map_name}",
            "zone": zone_name,
            "region": str(members[0].get("region") or ""),
            "map_name": map_name,
            "map_id": map_id,
            "remaining": len(members),
            "planned": len(steps),
            "ready": ready,
            "conflict": sum(1 for point in members if str(point.get("state")) == STATE_CONFLICT),
            "unclear": sum(1 for point in members if str(point.get("state")) == STATE_UNCLEAR),
            "distance_units": round(sum(s.distance_units or 0.0 for s in steps), 2),
            "unit": "地图坐标单位（不是米）",
            "eta": None,
            "eta_note": "没有真实移动速度数据，不给分钟数",
            "steps": [step.as_dict() for step in steps],
        })
    routes.sort(key=lambda route: (
        -int(route["ready"]),
        -int(route["remaining"]),
        str(route["zone"]),
        str(route["map_name"]),
    ))
    totals = dict(atlas.get("totals") or {})
    return {
        "totals": totals,
        "gate": dict(atlas.get("gate") or {}),
        "routes": routes[: max(0, int(max_routes))],
        "clusters": len(clusters),
        "remaining": len(points),
        "note": "只做区域聚类与坐标就近排序；不估算耗时，也不声称真实距离",
    }


def next_actions(plan: Mapping[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
    """接下来最适合做的 N 个点位（先做「攻略齐」的，再按路线顺序）。"""
    picked: list[dict[str, Any]] = []
    for route in plan.get("routes") or []:
        for step in route.get("steps") or []:
            if step.get("readiness") != READY:
                continue
            picked.append({**step, "route_id": route.get("route_id")})
            if len(picked) >= limit:
                return picked
    for route in plan.get("routes") or []:
        for step in route.get("steps") or []:
            if step.get("readiness") == READY:
                continue
            picked.append({**step, "route_id": route.get("route_id")})
            if len(picked) >= limit:
                return picked
    return picked


def render(plan: Mapping[str, Any], *, limit: int = 4, steps: int = 6) -> str:
    routes = plan.get("routes") or []
    lines = [
        f"路线规划（剩余 {plan.get('remaining', 0)} 个点位 · {plan.get('clusters', 0)} 张图"
        f" · 显示前 {min(limit, len(routes))} 条）",
        "  " + str(plan.get("note") or ""),
        "",
    ]
    for index, route in enumerate(routes[:limit], start=1):
        lines.append(
            f"Route #{index} · {route['zone']} / {route['map_name']}"
            f"  {route['planned']} 个点 · 攻略齐 {route['ready']} · 剩余 {route['remaining']}"
            f" · 坐标距离 {route['distance_units']}"
        )
        if route.get("conflict") or route.get("unclear"):
            lines.append(f"   ⚠ 冲突 {route['conflict']} · 说不清 {route['unclear']}（不做自动处理）")
        for step in (route.get("steps") or [])[:steps]:
            mark = {"ready": "✓", "need_solve": "✗解法", "need_locate": "✗定位"}.get(
                str(step.get("readiness")), "?")
            gap = f" (+{step['distance_units']})" if step.get("distance_units") else ""
            lines.append(f"   {step['index']}. {mark} #{step['source_point_id']} {step['label']}{gap}")
        if len(route.get("steps") or []) > steps:
            lines.append(f"   … 还有 {len(route['steps']) - steps} 个点")
        lines.append("")
    return "\n".join(lines).rstrip()
