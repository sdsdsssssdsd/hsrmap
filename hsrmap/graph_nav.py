"""导航上下文（a1-8-1 §十一 / §十九）：一个地图「怎么进去的、路径长什么样」。

两种路径必须分开存、分开用：

```text
Canonical Tree Path   官方树的父子链（千星城 / 千星城中心城区 / 2层）—— 事实
Navigation Path       玩家实际走进来的链（… / 2层 / 二次元界JUMP）—— 体验
```

Guide Matcher 需要的是后者：第三方攻略写「千星城中心城区2层二次元界JUMP」时，
要能绑到深层地图，而不是只能绑到入口图 943。

这一层**只读**：不建表、不写库、不发网络请求。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from hsrmap.database import has_table, table_columns
from hsrmap.graph import (
    NAVIGABLE_EDGE_TYPES,
    POINT_JUMP,
    Edge,
    load_edges,
    load_map_nodes,
)

#: 走进深层地图的边（有入口点位的那些）。
ENTRY_EDGE_TYPES: tuple[str, ...] = tuple(sorted(NAVIGABLE_EDGE_TYPES))


def _name_index(conn: sqlite3.Connection, nodes: Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    """地图名：maps.display_name（M7.3 起）→ maps.name → 树节点名 → 空。

    **只用于显示**；缺名字时返回空串，让调用方自己决定怎么兜底（不要编一个名字出来）。
    """
    names: dict[str, str] = {}
    if has_table(conn, "maps"):
        columns = table_columns(conn, "maps")
        select = ["source_id", "name"]
        select.append("display_name" if "display_name" in columns else "NULL AS display_name")
        for row in conn.execute(f"SELECT {', '.join(select)} FROM maps"):
            display = str(row[2] or "").strip()
            raw = str(row[1] or "").strip()
            names[str(row[0])] = display or raw
    for node in nodes or ():
        source_id = str(node.get("source_id") or "")
        if source_id and not names.get(source_id):
            names[source_id] = str(node.get("name") or "").strip()
    return names


def map_display_name(conn: sqlite3.Connection, map_id: str, *, names: Mapping[str, str] | None = None) -> str:
    index = dict(names) if names is not None else _name_index(conn, None)
    return str(index.get(str(map_id)) or "")


def canonical_path(
    conn: sqlite3.Connection,
    map_id: str,
    *,
    nodes: Sequence[Mapping[str, Any]] | None = None,
    names: Mapping[str, str] | None = None,
) -> list[str]:
    """官方树的父子链（根 → 目标）。带环保护：官方数据出问题时不死循环。"""
    pool = list(nodes) if nodes is not None else load_map_nodes(conn)
    index = dict(names) if names is not None else _name_index(conn, pool)
    parents = {str(node.get("source_id")): node.get("parent_source_id") for node in pool}
    known = set(parents)
    chain: list[str] = []
    seen: set[str] = set()
    current = str(map_id)
    while current and current not in seen:
        seen.add(current)
        chain.append(index.get(current) or current)
        parent = str(parents.get(current) or "")
        if parent and parent not in known:
            #: 父节点不在树里（快照不完整）：到此为止，**不编一个名字**给它。
            break
        current = parent
    chain.reverse()
    return chain


def navigation_context(
    conn: sqlite3.Connection,
    map_id: str,
    *,
    edges: Iterable[Edge] | None = None,
    nodes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """一个地图的导航上下文（§十九 的 `navigation_context`）。"""
    pool = list(nodes) if nodes is not None else load_map_nodes(conn)
    names = _name_index(conn, pool)
    target = str(map_id)
    all_edges = list(edges) if edges is not None else load_edges(conn, target_map_id=target)
    entries = [
        edge for edge in all_edges
        if str(edge.target_map_id) == target and edge.edge_type in ENTRY_EDGE_TYPES
    ]
    #: 优先带点位的入口（POINT_JUMP/PORTAL），其次任意可导航边；多条都留着，别丢信息。
    entries.sort(key=lambda edge: (edge.edge_type != POINT_JUMP, edge.source_map_id))
    primary = entries[0] if entries else None
    tree_path = canonical_path(conn, target, nodes=pool, names=names)
    name = names.get(target) or ""
    if primary is not None:
        entry_map_id = str(primary.source_map_id)
        base = canonical_path(conn, entry_map_id, nodes=pool, names=names)
        navigation_path = base + ([name] if name else [target])
        kind = "deep"
    else:
        entry_map_id = ""
        navigation_path = tree_path
        kind = "tree"
    return {
        "map_id": target,
        "map_name": name,
        "navigation_kind": kind,
        "entry_map_id": entry_map_id,
        "entry_point_id": str(primary.source_point_id) if primary and primary.source_point_id else "",
        "entry_edge_type": str(primary.edge_type) if primary else "",
        "entries": [
            {
                "entry_map_id": str(edge.source_map_id),
                "entry_point_id": str(edge.source_point_id or ""),
                "edge_type": str(edge.edge_type),
                "discovery_source": str(edge.discovery_source),
            }
            for edge in entries
        ],
        "navigation_path": navigation_path,
        "tree_path": tree_path,
    }

def attach_navigation_context(
    points: Iterable[Mapping[str, Any]],
    conn: sqlite3.Connection | None,
    *,
    map_key: str = "map_id",
) -> list[dict[str, Any]]:
    """给候选点位批量补上 `navigation_context`（a1-8-1 §十九）。

    只**加字段**，不改任何命中/排序逻辑：第三方攻略写「千星城中心城区2层二次元界JUMP」时，
    matcher 手里得先有导航链，才谈得上绑定到深层地图。

    * `conn` 是图库；没有图库传 None → 每个点位的 `navigation_context` 是 None（如实空着，不编）；
    * 同一次调用里按 map_id 缓存，不在循环里重复查库；
    * 传进来的 dict **不被修改**，返回的是浅拷贝列表。
    """
    pool = [dict(point) for point in points]
    if conn is None:
        for point in pool:
            point["navigation_context"] = None
        return pool
    nodes = load_map_nodes(conn)
    cache: dict[str, dict[str, Any]] = {}
    for point in pool:
        map_id = str(point.get(map_key) or "")
        if not map_id:
            point["navigation_context"] = None
            continue
        if map_id not in cache:
            cache[map_id] = navigation_context(conn, map_id, nodes=nodes)
        point["navigation_context"] = cache[map_id]
    return pool


def navigation_blob(context: Mapping[str, Any] | None) -> str:
    """把导航链压成一段可搜索文本（matcher 做 token 命中用）；空上下文返回空串。"""
    if not context:
        return ""
    parts = [str(item) for item in (context.get("navigation_path") or []) if str(item or "").strip()]
    if context.get("entry_point_id"):
        parts.append(f"point:{context['entry_point_id']}")
    return " ".join(parts)

#: 旁挂图库的默认位置（M7.2 的回填写在这里；`hsrmap graph backfill --out` 的默认值）。
SIDECAR_RELATIVE = ("graph", "core.db")


def sidecar_graph_path(data_root: Any):
    """`<数据根>/graph/core.db`。"""
    from pathlib import Path

    return Path(data_root).joinpath(*SIDECAR_RELATIVE)


def open_graph_connection(core_conn: sqlite3.Connection | None, data_root: Any = None):
    """决定「边到底在哪」，返回 `(conn, owned)`；拿不到就是 `(None, False)`。

    优先级（runbook §6.2）：

    1. 传进来的 `core_conn` 里有 `map_edges` 表 → 直接用它（新快照就是这样，`owned=False`）；
    2. 否则看 `<data_root>/graph/core.db` 旁挂派生库（M7.2 回填的产物，`owned=True`，**调用方负责关**）；
    3. 都没有 → `(None, False)`：调用方如实说「没有跳转信息」，**不要自己造一个空库**。
    """
    from pathlib import Path

    if core_conn is not None and has_table(core_conn, "map_edges"):
        return core_conn, False
    if data_root is None:
        try:
            from hsrmap.paths import DATA

            data_root = DATA
        except Exception:  # noqa: BLE001 - 没有运行态路径就退化成「没有图库」
            return None, False
    candidate = Path(data_root).joinpath(*SIDECAR_RELATIVE)
    if not candidate.is_file():
        return None, False
    conn = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn, True


