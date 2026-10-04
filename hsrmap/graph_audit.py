"""M7.2 Graph Audit（a1-8-1 §十六 / §十七 / §十八 / §二十三 / §二十四）。

三件事：

1. **孤儿地图发现器**（§十六）：集合运算 `Known / Tree / TransitionTarget / Renderable`，
   重点输出 `Renderable − Tree`（可以拿到 raster 却不在主树里的地图）与
   `TransitionTarget − SyncedRenderable`（**必须为空**，否则发布失败）；
2. **Graph Audit 报告**（§十七）：机器可读 JSON（默认 `reports/map_graph_audit.json`）+ 人话摘要；
3. **canary_deep_map_transition**（§二十三）：入口图 → 入口 point → transition 边 → target
   可渲染 → target 有点位 → 能回到入口图，逐链路验证。

全部**只读**：只吃一个 core.db（冻结快照或回填后的图谱库都行），不写库、不发网络。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from hsrmap.database import has_table, table_columns
from hsrmap.discovery import MapProbe, PROBE_RENDERABLE, dump_json
from hsrmap.graph import (
    EDGE_TYPES,
    MAP_GROUP,
    POINT_JUMP,
    PORTAL,
    RELATED_MAP,
    RETURN,
    UNKNOWN_TRANSITION,
    Edge,
    closure,
    edge_type_counts,
    known_map_ids,
    load_edges,
    load_map_nodes,
    load_point_transitions,
    navigable_edges,
    unresolved_targets,
)
from hsrmap.graph_backfill import IdSpaceIndex, load_id_space, local_map_probe, sha256_file

#: 报告默认落地位置（§十七 的 `reports/map_graph_audit.json`）。
REPORT_RELATIVE = Path("reports") / "map_graph_audit.json"

#: 目标必须是**一张可渲染地图**的边类型：§二十四 的发布 invariant 就写在这个集合上。
#: 结构类边（TREE_CHILD / RELATED_MAP / MAP_GROUP）指向的是树里的容器节点，不是 raster 地图——
#: 把它们一起塞进门禁会出现「数据没变、门禁变红」（M7.6 决定最终集合，这里如实把两类都报出来）。
RENDER_TARGET_EDGE_TYPES: tuple[str, ...] = (POINT_JUMP, PORTAL, RETURN, UNKNOWN_TRANSITION)

#: 互为正反两条边时算「预期内的环」的类型（§十八：expected reciprocal cycle）。
RECIPROCAL_EDGE_TYPES: frozenset[str] = frozenset({RELATED_MAP, MAP_GROUP, RETURN})

#: 本快照的 canary（M7.0 runbook §1）：入口图 943 → point 5637「二次元JUMP!」→ map 979。
CANARY_ENTRY_MAP = "943"
CANARY_POINT_SOURCE_ID = "5637"


def default_report_path(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root) / REPORT_RELATIVE
    from hsrmap.paths import ROOT

    return Path(ROOT) / REPORT_RELATIVE


# --------------------------------------------------------------------------- #
# 集合运算（§十六）
# --------------------------------------------------------------------------- #


def tree_roots(nodes: Sequence[Mapping[str, Any]]) -> list[str]:
    """树的根：没有 parent 的节点（官方树 9 个根 = 区域入口）。"""
    return [str(node["source_id"]) for node in nodes if node.get("parent_source_id") in (None, "", "0")]


def incoming_degree(edges: Iterable[Edge]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for edge in edges:
        counter[str(edge.target_map_id)] += 1
    return counter


def reachable_from(edges: Iterable[Edge], seeds: Iterable[str]) -> tuple[str, ...]:
    """从种子出发能走到的地图（§十八：visited 防环）。"""
    return closure(edges, seeds=seeds).visited


def reciprocal_cycles(edges: Iterable[Edge]) -> dict[str, list[dict[str, Any]]]:
    """A→B 且 B→A 的环，分成「预期内（related / group / return）」与「意外」。"""
    pairs: dict[tuple[str, str], list[Edge]] = {}
    for edge in edges:
        key = (str(edge.source_map_id), str(edge.target_map_id))
        pairs.setdefault(key, []).append(edge)
    expected: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for (source, target), forward in sorted(pairs.items()):
        if source == target:
            continue
        backward = pairs.get((target, source))
        if not backward:
            continue
        key = tuple(sorted((source, target)))
        if key in seen:
            continue
        seen.add(key)
        record = {
            "a": source,
            "b": target,
            "forward": sorted({edge.edge_type for edge in forward}),
            "backward": sorted({edge.edge_type for edge in backward}),
        }
        types = set(record["forward"]) | set(record["backward"])
        (expected if types & RECIPROCAL_EDGE_TYPES else unexpected).append(record)
    return {"expected_reciprocal": expected, "unexpected": unexpected}


def self_loops(edges: Iterable[Edge]) -> list[dict[str, Any]]:
    return [
        {"map_id": str(edge.source_map_id), "edge_type": edge.edge_type}
        for edge in edges
        if str(edge.source_map_id) == str(edge.target_map_id)
    ]


def multi_parent_nodes(edges: Iterable[Edge]) -> list[dict[str, Any]]:
    """一个节点被多个父节点指向 = 树事实自相矛盾（审计要看得见）。"""
    parents: dict[str, set[str]] = {}
    for edge in edges:
        if edge.edge_type != "TREE_CHILD":
            continue
        parents.setdefault(str(edge.target_map_id), set()).add(str(edge.source_map_id))
    return [{"map_id": key, "parents": sorted(value)} for key, value in sorted(parents.items()) if len(value) > 1]


# --------------------------------------------------------------------------- #
# 点位跳转链（§二十三 的 canary 能力）
# --------------------------------------------------------------------------- #


def transition_chains(
    conn: Any,
    edges: Sequence[Edge],
    *,
    probe: MapProbe,
    sample_limit: int = 10,
) -> dict[str, Any]:
    """逐条验证 §二十三 的链：入口图 → 入口 point → transition 边 → target 可渲染 → target 有点位。

    `能返回入口图` 的前端部分（导航栈）属于 M7.5；这里验证图上的返回目标确实存在且可渲染。
    """
    point_rows = {
        (str(row[0]), str(row[1])): True
        for row in conn.execute(
            "SELECT m.source_id, p.source_id FROM points p JOIN maps m ON m.id = p.map_id"
        )
    }
    point_counts = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            "SELECT m.source_id, COUNT(p.id) FROM maps m LEFT JOIN points p ON p.map_id = m.id GROUP BY m.source_id"
        )
    }
    jumps = [edge for edge in edges if edge.edge_type == POINT_JUMP]
    broken: list[dict[str, Any]] = []
    without_points = 0
    for edge in jumps:
        reasons: list[str] = []
        if edge.source_point_id is None:
            reasons.append("边上没有 source_point_id：不知道是哪个点跳的")
        elif (edge.source_map_id, str(edge.source_point_id)) not in point_rows:
            reasons.append(f"point {edge.source_point_id} 不在 points 表（或不属于 {edge.source_map_id}）")
        if edge.target_map_id not in point_counts:
            reasons.append(f"target {edge.target_map_id} 不在 maps 表")
        elif probe(edge.target_map_id).outcome != PROBE_RENDERABLE:
            reasons.append(f"target {edge.target_map_id} 不是可渲染地图")
        if point_counts.get(edge.target_map_id) == 0:
            without_points += 1
        if reasons:
            broken.append(
                {
                    "source_map_id": edge.source_map_id,
                    "source_point_id": edge.source_point_id,
                    "target_map_id": edge.target_map_id,
                    "reasons": reasons,
                }
            )
    connected = [edge for edge in jumps if edge.source_map_id in point_counts]
    return {
        "checked": len(jumps),
        "ok": len(jumps) - len(broken),
        "broken": broken[:sample_limit],
        "broken_total": len(broken),
        "targets_without_points": without_points,
        "entry_maps_known": len(connected),
    }


def canary_chain(
    conn: Any,
    edges: Sequence[Edge],
    *,
    probe: MapProbe,
    entry_map: str | None = None,
    point_source_id: str | None = None,
) -> dict[str, Any]:
    """§二十三 的 `canary_deep_map_transition`：把 M7.0 抓到的 JUMP 样本变成永久检查。

    默认点名 runbook 的 canary（943 → point 5637 → 979）；样本不在库里就退化到「图上第一条
    POINT_JUMP 边」，并如实标注这不是点名的那一条。
    """
    wanted_entry = str(entry_map or CANARY_ENTRY_MAP)
    wanted_point = str(point_source_id or CANARY_POINT_SOURCE_ID)
    jumps = [edge for edge in edges if edge.edge_type == POINT_JUMP]
    picked = next(
        (edge for edge in jumps if edge.source_map_id == wanted_entry and str(edge.source_point_id) == wanted_point),
        None,
    )
    named = picked is not None
    if picked is None:
        picked = next(iter(sorted(jumps, key=lambda item: (item.source_map_id, str(item.source_point_id)))), None)

    steps: list[dict[str, Any]] = []
    if picked is None:
        return {
            "name": "canary_deep_map_transition",
            "ok": False,
            "named_sample": False,
            "steps": [{"step": "transition_edge", "ok": False, "detail": "图里没有任何 POINT_JUMP 边"}],
        }

    entry, point_id, target = picked.source_map_id, str(picked.source_point_id), picked.target_map_id
    entry_row = conn.execute(
        "SELECT source_id, name, is_renderable FROM map_nodes WHERE source_id = ?", (entry,)
    ).fetchone()
    steps.append(
        {
            "step": "entry_map",
            "ok": entry_row is not None,
            "detail": f"入口图 {entry} 在 map_nodes 里" if entry_row is not None else f"入口图 {entry} 不在 map_nodes",
        }
    )
    point_row = conn.execute(
        """
        SELECT p.id, p.source_id, m.source_id AS map_id
        FROM points p JOIN maps m ON m.id = p.map_id
        WHERE m.source_id = ? AND p.source_id = ?
        """,
        (entry, point_id),
    ).fetchone()
    steps.append(
        {
            "step": "entry_point",
            "ok": point_row is not None,
            "detail": f"point {point_id} 属于入口图 {entry}" if point_row is not None else f"point {point_id} 不在 {entry}",
        }
    )
    steps.append(
        {
            "step": "transition_edge",
            "ok": True,
            "detail": f"{entry} --{picked.edge_type}({picked.discovery_source})--> {target}",
        }
    )
    target_row = conn.execute("SELECT source_id, name FROM maps WHERE source_id = ?", (target,)).fetchone()
    steps.append(
        {
            "step": "target_map",
            "ok": target_row is not None,
            "detail": f"target {target} 在 maps 表" if target_row is not None else f"target {target} 不在 maps 表",
        }
    )
    probe_result = probe(target)
    steps.append(
        {
            "step": "target_renderable",
            "ok": probe_result.outcome == PROBE_RENDERABLE,
            "detail": probe_result.as_dict(),
        }
    )
    count_row = conn.execute(
        "SELECT COUNT(p.id) FROM points p JOIN maps m ON m.id = p.map_id WHERE m.source_id = ?", (target,)
    ).fetchone()
    steps.append(
        {
            "step": "target_points_readable",
            "ok": count_row is not None,
            "detail": f"target {target} 的 point_list 可读：{int(count_row[0])} 个点",
        }
    )
    entry_renderable = probe(entry).outcome == PROBE_RENDERABLE
    steps.append(
        {
            "step": "return_target_valid",
            "ok": entry_renderable,
            "detail": (
                f"返回目标 {entry} 是可渲染地图（前端导航栈 origin_map_id 属于 M7.5，不在本轮）"
                if entry_renderable
                else f"返回目标 {entry} 不是可渲染地图"
            ),
        }
    )
    return {
        "name": "canary_deep_map_transition",
        "ok": all(step["ok"] for step in steps),
        "named_sample": named,
        "entry_map": entry,
        "point_source_id": point_id,
        "target_map_id": target,
        "steps": steps,
    }


# --------------------------------------------------------------------------- #
# 审计主体
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuditOptions:
    """审计参数（都只影响报告，不影响判定口径）。"""

    sample_limit: int = 10
    canary_entry_map: str | None = None
    canary_point_source_id: str | None = None
    db_path: str | None = None
    run_canary: bool = True
    run_chains: bool = True
    id_space: IdSpaceIndex | None = None


def audit_graph(conn: Any, *, options: AuditOptions | None = None) -> dict[str, Any]:
    """只读审计：集合运算 + 孤儿 + 环 + 闭包 + canary + 门禁输入。"""
    opts = options or AuditOptions()
    nodes = load_map_nodes(conn)
    edges = load_edges(conn)
    counts = edge_type_counts(edges)
    tree_ids = {str(node["source_id"]) for node in nodes}
    space = opts.id_space if opts.id_space is not None else load_id_space(conn)
    renderable = set(space.maps)
    known = known_map_ids(conn)
    probe = local_map_probe(conn, id_space=space)

    # ---- 集合运算（§十六） ------------------------------------------------- #
    edge_targets: set[str] = set()
    transition_targets: set[str] = set()
    for edge in edges:
        edge_targets.add(str(edge.target_map_id))
        if edge.edge_type in RENDER_TARGET_EDGE_TYPES:
            transition_targets.add(str(edge.target_map_id))

    deep_maps = sorted(renderable - tree_ids)
    jump_targets = sorted({str(edge.target_map_id) for edge in edges if edge.edge_type == POINT_JUMP})
    names = {str(node["source_id"]): str(node.get("name") or "").strip() for node in nodes}
    hidden_ids = _hidden_nodes(conn)
    hidden_renderables = sorted(renderable & hidden_ids)

    # ---- 未解析 target（§二十四 的 gate 输入） ----------------------------- #
    #: 两个用法别混（runbook §6.3）：
    #: - **发现 frontier**（缺省、全集）：任何类型的边指向未知目标都要继续探；
    #: - **发布门禁**（navigable_only=True）：只有「跳过去必须是另一张可渲染地图」的边才算违规，
    #:   结构边（TREE_CHILD / RELATED_MAP / MAP_GROUP）的目标本来就是容器节点，单独统计。
    unresolved = unresolved_targets(edges, known)
    unresolved_navigable = unresolved_targets(edges, known, navigable_only=True)
    unresolved_records = [
        {
            "source_map_id": edge.source_map_id,
            "target_map_id": edge.target_map_id,
            "edge_type": edge.edge_type,
            "source_point_id": edge.source_point_id,
            "discovery_source": edge.discovery_source,
        }
        for edge in unresolved
    ]
    unresolved_navigable_records = [
        {
            "source_map_id": edge.source_map_id,
            "target_map_id": edge.target_map_id,
            "edge_type": edge.edge_type,
            "source_point_id": edge.source_point_id,
            "discovery_source": edge.discovery_source,
        }
        for edge in unresolved_navigable
    ]

    # ---- 孤儿（§十六 + 任务口径「没有任何边连到它」） ---------------------- #
    #: 种子 = 官方树的根（区域入口）。**不把树外的可渲染地图当种子**：那正是要发现的对象，
    #: 把它们当入口会让「不可达」永远为空，等于把问题藏起来。
    roots = tree_roots(nodes)
    root_set = set(roots)
    reachable = set(reachable_from(edges, roots))
    degree = incoming_degree(edges)
    no_incoming = sorted(map_id for map_id in renderable if degree.get(map_id, 0) == 0 and map_id not in root_set)
    unreachable = sorted(renderable - reachable)
    orphan_renderables = sorted(set(no_incoming) | (set(unreachable) - root_set))
    orphan_tree_nodes = sorted(tree_ids - reachable)
    unreachable_targets = sorted(set(transition_targets) - reachable)

    # ---- 环（§十八） ------------------------------------------------------- #
    cycles = reciprocal_cycles(edges)
    closure_result = closure(edges, seeds=sorted(known))

    # ---- 目标类型统计（§二十四 的发布 invariant 输入） --------------------- #
    by_type: dict[str, dict[str, Any]] = {}
    for edge_type in EDGE_TYPES:
        typed = [edge for edge in edges if edge.edge_type == edge_type]
        if not typed:
            by_type[edge_type] = {"edges": 0, "distinct_targets": 0, "targets_renderable": 0,
                                  "missing_targets": [], "missing_total": 0,
                                  "render_requiring": edge_type in RENDER_TARGET_EDGE_TYPES}
            continue
        targets = sorted({str(edge.target_map_id) for edge in typed})
        missing = [target for target in targets if target not in renderable]
        by_type[edge_type] = {
            "edges": len(typed),
            "distinct_targets": len(targets),
            "targets_renderable": len(targets) - len(missing),
            "missing_targets": missing[: opts.sample_limit],
            "missing_total": len(missing),
            "render_requiring": edge_type in RENDER_TARGET_EDGE_TYPES,
        }
    render_requiring_missing = {
        edge_type: payload["missing_total"]
        for edge_type, payload in by_type.items()
        if payload["render_requiring"] and payload["missing_total"]
    }

    # ---- 名字缺口（M7.3 的输入与产出） ------------------------------------ #
    #: 两个口径分开报，别混：
    #: - without_name      = **官方树**里没有名字（M7.2 的硬数字：可渲染 341/624、跳转目标 142/187）；
    #: - without_any_name  = 树名、maps.name、maps.display_name（M7.3 补的真名）全都没有 —— 这才是真缺口。
    displays = _display_names(conn)
    effective = {map_id: (displays.get(map_id) or names.get(map_id) or "") for map_id in set(names) | set(displays)}
    unnamed_renderables = sorted(map_id for map_id in renderable if not names.get(map_id))
    unnamed_jump_targets = sorted(target for target in jump_targets if not names.get(target))
    unnamed_any_renderables = sorted(map_id for map_id in renderable if not effective.get(map_id))
    unnamed_any_jump_targets = sorted(target for target in jump_targets if not effective.get(target))
    naming = {
        "renderables_without_name": len(unnamed_renderables),
        "jump_targets_without_name": len(unnamed_jump_targets),
        "renderables_without_any_name": len(unnamed_any_renderables),
        "jump_targets_without_any_name": len(unnamed_any_jump_targets),
        "display_names": len(displays),
        "tree_nodes_without_name": sum(1 for value in names.values() if not value),
        "samples": unnamed_any_jump_targets[: opts.sample_limit] or unnamed_jump_targets[: opts.sample_limit],
        "note": (
            "真名来自容器（node_type=1）map/info 的 children[].name（M7.0）；"
            "M7.3 的 sync 会把 923 个树节点（含 299 个容器）的 map/info 抓下来写进 maps.display_name，"
            "without_any_name 才是补完之后剩下的真缺口"
        ),
    }

    #: 过门禁的是**可导航边**那一份（结构边的容器目标不是违规，§6.3）。
    gate_checks = {
        "unresolved_targets_empty": not unresolved_navigable_records,
        "orphan_renderables_empty": not orphan_renderables,
        "render_requiring_targets_renderable": not render_requiring_missing,
        "closure_converged": bool(closure_result.converged),
    }
    gate_reasons = [name for name, ok in gate_checks.items() if not ok]

    report: dict[str, Any] = {
        "schema": 1,
        "name": "map_graph_audit",
        "generated_at": _now(),
        "db": opts.db_path,
        "db_sha256": _db_sha(opts.db_path),
        "tree_nodes": len(nodes),
        "renderable_maps": len(renderable),
        "labels": len(space.labels),
        "points": len(space.points),
        "deep_maps": len(deep_maps),
        "deep_map_ids": deep_maps[: opts.sample_limit],
        "hidden_renderables": len(hidden_renderables),
        "edges_total": len(edges),
        "edges": {edge_type: counts.get(edge_type, 0) for edge_type in EDGE_TYPES},
        "point_transitions": len(load_point_transitions(conn)),
        "edge_targets": len(edge_targets),
        "transition_targets": len(transition_targets),
        "transition_targets_detail": {
            "renderable": len(transition_targets & renderable),
            "missing": sorted(transition_targets - renderable)[: opts.sample_limit],
        },
        "unresolved_targets": unresolved_records[: opts.sample_limit],
        "unresolved_targets_total": len(unresolved_records),
        "unresolved_navigable_targets": unresolved_navigable_records[: opts.sample_limit],
        "unresolved_navigable_targets_total": len(unresolved_navigable_records),
        "orphan_renderables": orphan_renderables[: opts.sample_limit],
        "orphan_renderables_total": len(orphan_renderables),
        "orphan_tree_nodes_total": len(orphan_tree_nodes),
        "unreachable_renderables_total": len(unreachable),
        "unreachable_renderables": unreachable[: opts.sample_limit],
        "unreachable_targets": unreachable_targets[: opts.sample_limit],
        "renderables_without_incoming_edge": no_incoming[: opts.sample_limit],
        "renderables_without_incoming_edge_total": len(no_incoming),
        "cycles": {
            "expected_reciprocal": cycles["expected_reciprocal"][: opts.sample_limit],
            "expected_reciprocal_total": len(cycles["expected_reciprocal"]),
            "unexpected": cycles["unexpected"][: opts.sample_limit],
            "unexpected_total": len(cycles["unexpected"]),
            "self_loops": self_loops(edges)[: opts.sample_limit],
            "multi_parent_nodes": multi_parent_nodes(edges)[: opts.sample_limit],
        },
        "closure": {
            "seeds": len(known),
            "visited": len(closure_result.visited),
            "frontier": len(closure_result.frontier),
            "unevidenced": list(closure_result.unevidenced[: opts.sample_limit]),
            "cycles": len(closure_result.cycles),
            "steps": closure_result.steps,
            "converged": bool(closure_result.converged),
        },
        "render_targets": {
            "by_type": by_type,
            "render_requiring_types": list(RENDER_TARGET_EDGE_TYPES),
            "render_requiring_missing": render_requiring_missing,
            "navigable_edges": len(navigable_edges(edges)),
            "note": (
                "结构类边（TREE_CHILD / RELATED_MAP / MAP_GROUP）指向树里的容器节点，不是 raster 地图；"
                "§二十四 的发布 invariant 只对 RENDER_TARGET_EDGE_TYPES 有定义（M7.6 定稿）"
            ),
        },
        "naming": naming,
        "seeds": {
            "tree_roots": roots[: opts.sample_limit],
            "tree_root_total": len(roots),
            "reachable_total": len(reachable),
            "note": "种子 = 官方树的根（区域入口）；树外的可渲染地图**不当种子**，否则「不可达」永远为空",
        },
        "gate": {"ok": not gate_reasons, "reasons": gate_reasons, "checks": gate_checks},
    }
    if opts.run_chains:
        report["transition_chains"] = transition_chains(conn, edges, probe=probe, sample_limit=opts.sample_limit)
    if opts.run_canary:
        report["canary"] = canary_chain(
            conn,
            edges,
            probe=probe,
            entry_map=opts.canary_entry_map,
            point_source_id=opts.canary_point_source_id,
        )
    return report


def _display_names(conn: Any) -> dict[str, str]:
    """maps 的真名索引（M7.3 起有 display_name 列）：display_name 优先，回退 maps.name。

    老库（v1/v2）没有 display_name 列 → 只回退到 maps.name，**绝不回算、绝不编名字**。
    """
    if not has_table(conn, "maps"):
        return {}
    columns = table_columns(conn, "maps")
    select = "source_id, name, " + ("display_name" if "display_name" in columns else "NULL AS display_name")
    out: dict[str, str] = {}
    for row in conn.execute(f"SELECT {select} FROM maps"):
        display = str(row[2] or "").strip()
        raw = str(row[1] or "").strip()
        if display or raw:
            out[str(row[0])] = display or raw
    return out


def _hidden_nodes(conn: Any) -> set[str]:
    """`is_hide=true` 的节点：map_nodes 没有这一列，值在 raw_json 里（树事实）。

    「可见性」不是可渲染判定：本快照 624 张可渲染地图的 is_hide 全是 false，隐藏的是容器。
    """
    if not has_table(conn, "map_nodes"):
        return set()
    out: set[str] = set()
    for row in conn.execute("SELECT source_id, raw_json FROM map_nodes"):
        if not row[1]:
            continue
        try:
            payload = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, Mapping) and payload.get("is_hide"):
            out.add(str(row[0]))
    return out


def _now() -> str:
    from hsrmap.graph import now_iso

    return now_iso()


def _db_sha(db_path: str | None) -> str | None:
    if not db_path:
        return None
    path = Path(db_path)
    return sha256_file(path) if path.is_file() else None


# --------------------------------------------------------------------------- #
# 报告输出
# --------------------------------------------------------------------------- #


def orphans_only(report: Mapping[str, Any]) -> dict[str, Any]:
    """`graph orphans` 子命令要的那一块（从完整审计里切出来，口径完全一致）。"""
    return {
        "generated_at": report.get("generated_at"),
        "db": report.get("db"),
        "tree_nodes": report.get("tree_nodes"),
        "renderable_maps": report.get("renderable_maps"),
        "deep_maps": report.get("deep_maps"),
        "deep_map_ids": report.get("deep_map_ids"),
        "orphan_renderables": report.get("orphan_renderables"),
        "orphan_renderables_total": report.get("orphan_renderables_total"),
        "orphan_tree_nodes_total": report.get("orphan_tree_nodes_total"),
        "unreachable_renderables": report.get("unreachable_renderables"),
        "unreachable_renderables_total": report.get("unreachable_renderables_total"),
        "renderables_without_incoming_edge": report.get("renderables_without_incoming_edge"),
        "renderables_without_incoming_edge_total": report.get("renderables_without_incoming_edge_total"),
        "unreachable_targets": report.get("unreachable_targets"),
        "renderables_without_incoming_edge": report.get("renderables_without_incoming_edge"),
        "unresolved_targets": report.get("unresolved_targets"),
        "unresolved_targets_total": report.get("unresolved_targets_total"),
        "transition_targets": report.get("transition_targets"),
        "seeds": report.get("seeds"),
        "naming": report.get("naming"),
    }


def render_orphans(report: Mapping[str, Any]) -> str:
    lines = [
        "Map Graph 孤儿地图发现器（a1-8-1 §十六）",
        f"  树节点 .......... {report.get('tree_nodes')}",
        f"  可渲染地图 ...... {report.get('renderable_maps')}",
        f"  深层地图 ........ {report.get('deep_maps')}（Renderable − Tree：在 maps 里但不在主树）",
        f"  跳转目标 ........ {report.get('transition_targets')}",
        f"  孤儿 ............ {report.get('orphan_renderables_total')}"
        f"（没有入边 {report.get('renderables_without_incoming_edge_total')} / 从树根不可达 "
        f"{report.get('unreachable_renderables_total')}）",
        f"  未解析 target .... {report.get('unresolved_targets_total')}  ← 必须为 0，否则发布失败",
    ]
    for label, key in (("深层地图", "deep_map_ids"), ("孤儿", "orphan_renderables"),
                       ("未解析 target", "unresolved_targets")):
        items = report.get(key) or []
        if items:
            lines.append(f"  {label}明细：")
            for item in items:
                lines.append(f"    - {json.dumps(item, ensure_ascii=False)}")
    naming = report.get("naming") or {}
    if naming:
        lines.append(
            f"  名字缺口 ........ 可渲染 {naming.get('renderables_without_name')} / 跳转目标 "
            f"{naming.get('jump_targets_without_name')}（M7.3 从容器 map/info 的 children[].name 补）"
        )
    return "\n".join(lines) + "\n"


def render_audit(report: Mapping[str, Any]) -> str:
    """人话摘要（§十七）。"""
    edges = report.get("edges") or {}
    lines = [
        "Map Graph Audit（a1-8-1 §十七）",
        f"  库 .............. {report.get('db')}"
        + (f"（sha256 {str(report.get('db_sha256'))[:16]}…）" if report.get("db_sha256") else ""),
        f"  生成时间 ........ {report.get('generated_at')}",
        "",
        f"  tree_nodes ...... {report.get('tree_nodes')}",
        f"  renderable_maps . {report.get('renderable_maps')}",
        f"  deep_maps ....... {report.get('deep_maps')}",
        f"  edges_total ..... {report.get('edges_total')}",
    ]
    for edge_type, value in edges.items():
        if value:
            lines.append(f"    {edge_type:<20} {value}")
    lines += [
        f"  point_transitions {report.get('point_transitions')}",
        f"  transition_targets {report.get('transition_targets')}",
        "",
        f"  unresolved_targets ... {report.get('unresolved_targets_total')}",
        f"  orphan_renderables ... {report.get('orphan_renderables_total')}"
        f"（无入边 {report.get('renderables_without_incoming_edge_total')} / 不可达 "
        f"{report.get('unreachable_renderables_total')}）",
        f"  cycles ............... 预期内 {report.get('cycles', {}).get('expected_reciprocal_total')} / "
        f"意外 {report.get('cycles', {}).get('unexpected_total')} / "
        f"自环 {len(report.get('cycles', {}).get('self_loops') or [])}",
        f"  closure .............. converged={report.get('closure', {}).get('converged')} "
        f"visited={report.get('closure', {}).get('visited')} frontier={report.get('closure', {}).get('frontier')}",
    ]
    render_targets = report.get("render_targets") or {}
    missing = render_targets.get("render_requiring_missing") or {}
    lines.append(
        "  render 目标缺口 ...... "
        + (", ".join(f"{key}={value}" for key, value in missing.items()) if missing else "0（可渲染目标齐全）")
    )
    structural = {
        key: payload.get("missing_total", 0)
        for key, payload in (render_targets.get("by_type") or {}).items()
        if not payload.get("render_requiring") and payload.get("missing_total")
    }
    if structural:
        lines.append(
            "  （结构类边指向容器节点，不算缺口）："
            + ", ".join(f"{key}={value}" for key, value in sorted(structural.items()))
        )
    naming = report.get("naming") or {}
    if naming:
        lines.append(
            f"  名字缺口 ............. 可渲染 {naming.get('renderables_without_name')} / "
            f"跳转目标 {naming.get('jump_targets_without_name')}"
        )
    chains = report.get("transition_chains")
    if chains:
        lines.append(
            f"  跳转链校验 ........... {chains.get('ok')}/{chains.get('checked')} 通过"
            f"（target 没有点位的 {chains.get('targets_without_points')} 条）"
        )
        for item in chains.get("broken") or []:
            lines.append(f"    ! {json.dumps(item, ensure_ascii=False)}")
    canary = report.get("canary")
    if canary:
        mark = "PASS" if canary.get("ok") else "FAIL"
        sample = "点名样本" if canary.get("named_sample") else "退化样本（runbook 的 943/5637 不在库里）"
        lines.append(f"  canary ............... {mark}（{sample}：{canary.get('entry_map')} → "
                     f"point {canary.get('point_source_id')} → {canary.get('target_map_id')}）")
        for step in canary.get("steps") or []:
            lines.append(f"    [{'ok' if step.get('ok') else '!!'}] {step.get('step')}: {str(step.get('detail'))[:120]}")
    gate = report.get("gate") or {}
    lines += [
        "",
        "  门禁输入：",
        *[f"    {'ok' if value else '!!'} {key}" for key, value in (gate.get("checks") or {}).items()],
        f"  GATE ................ {'PASS' if gate.get('ok') else 'FAIL（' + ', '.join(gate.get('reasons') or []) + '）'}",
    ]
    return "\n".join(lines) + "\n"


def write_report(path: str | Path, report: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_json(report) + "\n", encoding="utf-8")
    return target


__all__ = [
    "AuditOptions",
    "CANARY_ENTRY_MAP",
    "CANARY_POINT_SOURCE_ID",
    "RECIPROCAL_EDGE_TYPES",
    "RENDER_TARGET_EDGE_TYPES",
    "REPORT_RELATIVE",
    "audit_graph",
    "canary_chain",
    "default_report_path",
    "dump_json",
    "incoming_degree",
    "multi_parent_nodes",
    "orphans_only",
    "reachable_from",
    "reciprocal_cycles",
    "render_audit",
    "render_orphans",
    "self_loops",
    "transition_chains",
    "tree_roots",
    "write_report",
]
