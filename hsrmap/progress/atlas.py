"""剩余清单 / Remaining Atlas（a1-9 P6.5）：

```text
remaining = 官方可收集集合 − 有效完成集合
```

三件事都不新造真相：

* 「官方可收集集合」= 现有主题层给出的 1006 个官方点位（含坐标、区域、地图）；
* 「点位状态与证据」= `hsrmap.guides.stages.completeness_report` 的行（LOCATE / SOLVE / 攻略）；
* 「有效完成」= 本地手动勾选 ∪ **有推导权**的远端观察（Gate 0 之前只有手动那一半）。

于是这里只回答一个问题：**还差哪些点，以及每个点该怎么拿。**
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable, Mapping, Sequence

from hsrmap.progress.resolver import allowed_semantics

#: 点位在剩余清单里的四种状态。
STATE_COMPLETED = "completed"
STATE_REMAINING = "remaining"
STATE_CONFLICT = "conflict"
STATE_UNCLEAR = "unclear"

#: 冲突：本地说完成、远端明确说没完成 —— 这种点绝不能被静默改掉，只能摆出来让人看。
def _float(value: Any) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _split_path(item: Mapping[str, Any]) -> tuple[str, str, str]:
    """地图路径 →（大区, 子区域, 地图名）。

    官方地图的路径形如 `千星城 / 生研院 / 1014`：第一段是大区，第二段是子区域，
    最后一段通常是地图名，但**有时会退化成 map_id**（那时地图名取第二段）。
    这三个字段在源数据里不总是齐的，所以统一在这里归一化，后面的分组只认归一化结果。
    """
    parts = [seg.strip() for seg in str(item.get("map_path") or "").split("/") if seg.strip()]
    map_id = str(item.get("map_id") or "")
    region = str(item.get("region") or "").strip()
    map_name = str(item.get("map_name") or "").strip()
    if parts:
        zone = parts[0] if len(parts) >= 3 else (parts[0] if not region else region)
        if not region:
            region = parts[1] if len(parts) >= 3 else parts[0]
        if not map_name:
            tail = parts[-1]
            map_name = parts[-2] if tail == map_id and len(parts) >= 2 else tail
    else:
        zone = region
    if not map_name:
        map_name = map_id
    return zone, region, map_name


def point_entry(
    collectible: Mapping[str, Any],
    *,
    completion: Mapping[str, Any] | None = None,
    state: str = STATE_REMAINING,
) -> dict[str, Any]:
    row = dict(completion or {})
    zone, region, map_name = _split_path(collectible)
    return {
        "source_point_id": str(collectible.get("source_point_id") or ""),
        "topic": str(row.get("topic") or collectible.get("topic") or ""),
        "label": str(collectible.get("label") or ""),
        "zone": zone,
        "region": region,
        "map_name": map_name,
        "map_path": str(collectible.get("map_path") or ""),
        "map_id": str(collectible.get("map_id") or ""),
        "x": _float(collectible.get("x")),
        "y": _float(collectible.get("y")),
        "state": state,
        "completed": state == STATE_COMPLETED,
        "status": str(row.get("status") or ""),
        "requirement": str(row.get("requirement") or ""),
        "solve_kind": str(row.get("solve_kind") or ""),
        "locate_evidence": str(row.get("locate_evidence") or ""),
        "solve_evidence": str(row.get("solve_evidence") or ""),
        "guide_id": row.get("guide_id"),
        "title": str(row.get("title") or ""),
        "missing": str(row.get("missing") or ""),
    }


def assemble(
    *,
    collectibles: Iterable[Mapping[str, Any]],
    completion: Mapping[str, Mapping[str, Any]] | None = None,
    effective_completed: Iterable[str] = (),
    unclear: Iterable[str] = (),
    conflict: Iterable[str] = (),
    gate: Mapping[str, Any] | None = None,
    region_order: Sequence[str] | None = None,
) -> dict[str, Any]:
    """纯函数：给三份输入，出剩余清单（不查库、不联网，方便测试与复用）。"""
    done = {str(item) for item in effective_completed}
    murky = {str(item) for item in unclear}
    clash = {str(item) for item in conflict}
    rows = dict(completion or {})

    regions: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    topics: dict[str, dict[str, int]] = {}
    total = 0
    remaining = 0
    for item in collectibles:
        pid = str(item.get("source_point_id") or "")
        if not pid:
            continue
        total += 1
        if pid in clash:
            state = STATE_CONFLICT
        elif pid in done:
            state = STATE_COMPLETED
        elif pid in murky:
            state = STATE_UNCLEAR
        else:
            state = STATE_REMAINING
        if state != STATE_COMPLETED:
            remaining += 1
        entry = point_entry(item, completion=rows.get(pid), state=state)
        zone = entry["zone"] or "（未标明大区）"
        bucket = regions.setdefault(zone, {
            "zone": zone, "collectible": 0, "remaining": 0, "completed": 0,
            "maps": OrderedDict(),
        })
        bucket["collectible"] += 1
        if state == STATE_COMPLETED:
            bucket["completed"] += 1
        else:
            bucket["remaining"] += 1
        amap = bucket["maps"].setdefault((entry["region"], entry["map_name"] or "（未标明地图）"), {
            "region": entry["region"], "map_name": entry["map_name"],
            "map_path": entry["map_path"], "map_id": entry["map_id"],
            "collectible": 0, "remaining": 0, "points": [],
        })
        amap["collectible"] += 1
        if state != STATE_COMPLETED:
            amap["remaining"] += 1
        amap["points"].append(entry)
        topic = entry["topic"]
        if topic:
            slot = topics.setdefault(topic, {"collectible": 0, "remaining": 0, "completed": 0})
            slot["collectible"] += 1
            if state == STATE_COMPLETED:
                slot["completed"] += 1
            else:
                slot["remaining"] += 1

    order = list(region_order or ())
    def region_key(name: str) -> tuple[int, int, str]:
        try:
            return (0, order.index(name), name)
        except ValueError:
            return (1, 0, name)

    region_list: list[dict[str, Any]] = []
    for name in sorted(regions, key=region_key):
        bucket = regions[name]
        maps = sorted(bucket["maps"].values(),
                      key=lambda m: (-int(m["remaining"]), str(m["region"]), str(m["map_name"])))
        for amap in maps:
            #: 剩余点排前面（同图内按点位 id 稳定排序），已完成的沉底，方便「照着清图」。
            amap["points"].sort(key=lambda p: (p["state"] == STATE_COMPLETED, str(p["source_point_id"])))
        bucket["maps"] = maps
        region_list.append(bucket)

    return {
        "totals": {
            "collectible": total,
            "effective_completed": total - remaining,
            "remaining": remaining,
            "conflict": len(clash),
            "unclear": len(murky),
        },
        "gate": dict(gate or {}),
        "regions": region_list,
        "topics": [
            {"topic": name, **counts}
            for name, counts in sorted(topics.items(), key=lambda kv: (-int(kv[1]["remaining"]), kv[0]))
        ],
    }


def render(atlas: Mapping[str, Any], *, limit: int = 12, region: str | None = None) -> str:
    """把剩余清单排成能直接看的一段话（spec §15 的那个形状）。"""
    totals = atlas.get("totals") or {}
    lines = [
        "剩余清单 / Remaining Atlas"
        f"（可收集 {totals.get('collectible', 0)} · 已完成 {totals.get('effective_completed', 0)}"
        f" · 剩余 {totals.get('remaining', 0)}）",
        "",
    ]
    gate = atlas.get("gate") or {}
    allowed = gate.get("allowed_remote_semantics") or []
    lines.append("  有效完成口径：本地手动勾选"
                 + ("；另接受 " + "、".join(allowed) if allowed else "；远端状态暂无可推导权（Gate 0 未定）"))
    if totals.get("conflict"):
        lines.append(f"  ⚠ 冲突 {totals['conflict']} 个点位：本地已完成、远端说没有 —— 只展示，不自动改")
    if totals.get("unclear"):
        lines.append(f"  ? 说不清 {totals['unclear']} 个点位：远端给了语义未证实的状态")
    lines.append("")
    shown = 0
    for bucket in atlas.get("regions") or []:
        if region and str(bucket.get("zone")) != region:
            continue
        lines.append(f"{bucket['zone']}  {bucket['completed']} / {bucket['collectible']}")
        for amap in bucket.get("maps") or []:
            label = amap["map_name"] or "（未标明地图）"
            if amap.get("region") and amap["region"] != label:
                label = f"{amap['region']} · {label}"
            lines.append(f"  ├─ {label}  {amap['collectible'] - amap['remaining']} / {amap['collectible']}")
            for point in amap.get("points") or []:
                if point["state"] == STATE_COMPLETED:
                    continue
                if shown >= limit:
                    continue
                mark = {"remaining": "·", "conflict": "!", "unclear": "?"}.get(point["state"], "·")
                bits = [f"    {mark} #{point['source_point_id']} {point['label']}".rstrip()]
                evidence = []
                evidence.append("LOCATE ✓" if point["locate_evidence"] else "LOCATE ✗")
                evidence.append("SOLVE ✓" if point["solve_evidence"] else "SOLVE ✗")
                bits.append("      " + " · ".join(evidence)
                            + (f" · {point['solve_kind']}" if point.get("solve_kind") else ""))
                if point.get("title"):
                    bits.append(f"      → {point['title']}")
                lines.extend(bits)
                shown += 1
        lines.append("")
    if shown >= limit:
        lines.append(f"…（只显示前 {limit} 个剩余点位，--limit 可调）")
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# 组装真实输入（查库；不联网）
# --------------------------------------------------------------------------- #

def collect_official(
    *,
    topics: Iterable[str] | None = None,
    ctx: Any = None,
    topic_map: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """官方可收集集合：主题层给出的点位（带区域 / 地图 / 坐标）。"""
    from hsrmap.guides.topics.loader import list_topics
    from hsrmap.guides.topics.official import official_points_for_topic

    own = ctx is None
    if own:
        from hsrmap.viewer_bind import bind_viewer

        ctx = bind_viewer()
    try:
        keys = [str(key) for key in (topics or [item["topic_key"] for item in list_topics(enabled_only=True)])]
        out: list[dict[str, Any]] = []
        for key in keys:
            try:
                points = official_points_for_topic(key, ctx=ctx) or []
            except Exception:  # noqa: BLE001 - 未知主题不该让清单整体失败
                points = []
            for point in points:
                item = dict(point)
                item.setdefault("topic", (topic_map or {}).get(key, key))
                out.append(item)
        return out
    finally:
        if own:
            ctx.close()


def completion_index(guide_db: Any) -> dict[str, dict[str, Any]]:
    """点位的六状态 + LOCATE/SOLVE 证据（复用完整性报告的行，不另造一份判定）。"""
    from hsrmap.guides.stages import completeness_report

    report = completeness_report(guide_db)
    return {
        str(row.get("point") or ""): dict(row)
        for row in (report.get("rows") or [])
        if str(row.get("point") or "")
    }


def progress_sets(
    user_db: Any,
    *,
    profile_id: str | None = None,
    import_map_mark: bool = False,
    verified: Iterable[str] | None = None,
) -> dict[str, set[str]]:
    """本地完成 / 有效完成 / 说不清 / 冲突，四份集合一次算清。"""
    from hsrmap.progress import diff as progress_diff
    from hsrmap.progress import store
    from hsrmap.progress.models import SEMANTIC_MANUAL, SEMANTIC_UNKNOWN
    from hsrmap.progress.resolver import resolve

    observations = store.observations(user_db, profile_id=profile_id)
    local = progress_diff.local_completed(user_db, profile_id=profile_id)
    decision = resolve(observations, import_map_mark=import_map_mark, verified=verified)
    allowed = allowed_semantics(import_map_mark=import_map_mark, verified=verified)

    accepted = decision.completed - local
    unclear = {pid for pid in decision.unclear if pid not in local}
    #: 冲突 = 本地完成 + 远端在**有推导权的语义**下明说没完成。
    denied: set[str] = set()
    for observation in observations:
        if observation.semantic in allowed and observation.completed is False:
            denied.add(observation.source_point_id)
    conflict = local & denied
    return {
        "local": local,
        "accepted": accepted,
        "unclear": unclear,
        "conflict": conflict,
        "manual": {o.source_point_id for o in observations
                   if o.semantic == SEMANTIC_MANUAL and o.completed},
        "unknown": {o.source_point_id for o in observations if o.semantic == SEMANTIC_UNKNOWN},
    }


def graph_coverage(core_conn: Any, graph_conn: Any | None = None) -> dict[str, Any]:
    """进度层的**图口径**（a1-8-1 §二十/§二十四）：可渲染地图集合 + 深层地图 + 点位分布。

    「进度查询使用 graph map set，而非 tree leaf set」在这里落地：tree leaf 只是结构事实，
    玩家真正走得进去的地图是`可渲染地图 ∪ 可导航边的目标`。缺表/缺图库时如实返回 0，不抛异常。
    """
    from hsrmap.graph import (
        NAVIGABLE_EDGE_TYPES,
        known_map_ids,
        load_edges,
        unresolved_targets,
    )

    conn = graph_conn if graph_conn is not None else core_conn
    if conn is None:
        return {"available": False, "reason": "没有可用的地图库"}
    try:
        renderable = {str(item) for item in known_map_ids(conn, renderable_only=True)}
        edges = list(load_edges(conn))
    except Exception as exc:  # noqa: BLE001 - 老库没有图结构：如实说「没有」，别让清单整体失败
        return {"available": False, "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}

    navigable = [edge for edge in edges if str(edge.edge_type) in NAVIGABLE_EDGE_TYPES]
    deep = {
        str(edge.target_map_id): str(edge.source_map_id)
        for edge in navigable
        if str(edge.target_map_id) and str(edge.target_map_id) not in renderable
    }
    points_on_deep = 0
    points_total = 0
    if core_conn is not None:
        try:
            #: `points.map_id` 是指向 `maps.id` 的整数外键，不是地图 source_id：必须 join 回 source_id
            #: 才能和边里的 target_map_id 对上（这里错过一次，会让「深层点位」永远统计成 0）。
            rows = core_conn.execute(
                "SELECT m.source_id AS map_source_id, COUNT(*) AS n"
                " FROM points p JOIN maps m ON m.id = p.map_id GROUP BY m.source_id"
            ).fetchall()
        except Exception:  # noqa: BLE001 - 没有 points/maps 表就当 0
            rows = []
        for row in rows:
            count = int(row[1] or 0)
            points_total += count
            if str(row[0]) in deep:
                points_on_deep += count
    unresolved = unresolved_targets(edges, renderable, navigable_only=True)
    return {
        "available": True,
        "renderable_maps": len(renderable),
        "edges_total": len(edges),
        "navigable_edges": len(navigable),
        "deep_maps": len(deep),
        "points_total": points_total,
        "points_on_deep_maps": points_on_deep,
        "unresolved_navigable_targets": len(unresolved),
        "note": "deep_maps = 可导航边指向、但自身不在可渲染集合里的目标（发布门禁看 unresolved_navigable_targets）",
    }


def remaining_atlas(
    *,
    guide_db: Any,
    user_db: Any | None = None,
    topics: Iterable[str] | None = None,
    profile_id: str | None = None,
    import_map_mark: bool = False,
    verified: Iterable[str] | None = None,
    ctx: Any = None,
    collectibles: Iterable[Mapping[str, Any]] | None = None,
    graph_conn: Any | None = None,
    core_conn: Any | None = None,
) -> dict[str, Any]:
    """Remaining Atlas：官方可收集 − 有效完成。

    `graph_conn` / `core_conn` 给了就顺带带上**图口径**（可渲染地图数 / 深层地图 / 深层点位），
    没给就如实不带 —— 清单本身不依赖图，缺图也能算。
    """
    allowed = sorted(allowed_semantics(import_map_mark=import_map_mark, verified=verified))
    gate = {
        "allowed_remote_semantics": allowed,
        "map_mark_accepted": bool(import_map_mark),
        "viewer_network": 0,
    }
    if user_db is None:
        sets = {"local": set(), "accepted": set(), "unclear": set(), "conflict": set()}
    else:
        sets = progress_sets(user_db, profile_id=profile_id, import_map_mark=import_map_mark, verified=verified)
    pool = list(collectibles) if collectibles is not None else collect_official(topics=topics, ctx=ctx)
    atlas = assemble(
        collectibles=pool,
        completion=completion_index(guide_db) if guide_db is not None else {},
        effective_completed=sets["local"] | sets["accepted"],
        unclear=sets["unclear"],
        conflict=sets["conflict"],
        gate=gate,
    )
    if graph_conn is not None or core_conn is not None:
        atlas["graph"] = graph_coverage(core_conn, graph_conn)
    return atlas
