"""Map Graph（a1-8-1 §四/§六/§十八）：地图之间的边 + 可达闭包遍历。

这一层只做**数据模型与遍历**：边从哪来（tree extractor / map-info extractor / point-list
extractor）是 M7.2 的事，本模块不碰网络、不碰 data/。

约定：

- `edge_type` 的取值全部是本文件的模块级常量，别处不要再写字符串字面量（§四）；
- 语义不明的跳转一律记 `UNKNOWN_TRANSITION`，绝不允许因为「不知道它是什么」就丢掉 target（§五）；
- cycle 合法：A→B→A 是地图导航的常态，遍历靠 visited 收敛，不要求 DAG（§十八）。

数据库 schema 在 hsrmap/database.py 的 SCHEMA 里（map_edges / point_transitions）。
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

from hsrmap.database import has_table
from hsrmap.render_probe import LEGACY_TREE_HINT

# --------------------------------------------------------------------------- #
# edge_type（§四）
# --------------------------------------------------------------------------- #

#: 树的父子关系：官方 map/tree 的 parent_id → child。
TREE_CHILD = "TREE_CHILD"
#: 同区域的楼层关系（1层 ↔ 2层），来自树的兄弟结构。
FLOOR = "FLOOR"
#: 地图点位上的 JUMP / 入口：点下去弹窗「前往对应地图」。
POINT_JUMP = "POINT_JUMP"
#: map/info 里的 related_id / related_map 之类的关联地图。
RELATED_MAP = "RELATED_MAP"
#: related_group_map / map_group_type 之类的地图组目标。
MAP_GROUP = "MAP_GROUP"
#: 传送 / 门 / 特殊入口。
PORTAL = "PORTAL"
#: 返回边（§十二）：source 是深层地图，target 是进来的入口地图。
RETURN = "RETURN"
#: 明知有一条跳转、但还说不清语义。**必须保留**，等 bundle/payload 升级（§五）。
UNKNOWN_TRANSITION = "UNKNOWN_TRANSITION"

#: 第一版允许的全部 edge_type。
EDGE_TYPES: tuple[str, ...] = (
    TREE_CHILD,
    FLOOR,
    POINT_JUMP,
    RELATED_MAP,
    MAP_GROUP,
    PORTAL,
    RETURN,
    UNKNOWN_TRANSITION,
)
EDGE_TYPE_SET = frozenset(EDGE_TYPES)

#: 结构边：它们描述「地图宇宙长什么样」，但目标**不一定是可渲染地图**
#: （TREE_CHILD 的目标里有 290 个 node_type=1 容器、MAP_GROUP/RELATED_MAP 的目标是区域容器），
#: 所以它们不参与「跳到的地方必须是一张有 raster 的图」这条发布 invariant。
STRUCTURAL_EDGE_TYPES: frozenset[str] = frozenset({TREE_CHILD, FLOOR, RELATED_MAP, MAP_GROUP})
#: 非导航边（M7.1 留的钩子，调用点不用动）。
NON_NAVIGABLE_EDGE_TYPES: frozenset[str] = STRUCTURAL_EDGE_TYPES
#: 用户能顺着走到**另一张地图**的边：§二十四 的发布 invariant 写在这个集合上
#: （M7.6 定稿：只对「目标必须是可渲染地图」的边生效，结构类边单独统计、不误报）。
NAVIGABLE_EDGE_TYPES: frozenset[str] = EDGE_TYPE_SET - NON_NAVIGABLE_EDGE_TYPES

#: map_edges 的 UNIQUE 约束（§四）。
EDGE_KEY_COLUMNS: tuple[str, ...] = ("source_map_id", "target_map_id", "edge_type", "source_point_id")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Edge:
    """一条地图之间的边。字段与 map_edges 一一对应。"""

    source_map_id: str
    target_map_id: str
    edge_type: str
    source_point_id: str | None = None
    source_label_id: str | None = None
    discovery_source: str = "unknown"
    confidence: float = 1.0
    bidirectional: bool = False
    raw_json: Any = None
    discovered_at: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        if self.edge_type not in EDGE_TYPE_SET:
            raise ValueError(f"unknown edge_type {self.edge_type!r}; 语义不明请用 {UNKNOWN_TRANSITION}")
        if not str(self.source_map_id):
            raise ValueError("edge source_map_id is empty")
        if not str(self.target_map_id):
            raise ValueError("edge target_map_id is empty")
        if not str(self.discovery_source):
            raise ValueError("edge discovery_source is empty")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(f"edge confidence out of range: {self.confidence}")

    @property
    def navigable(self) -> bool:
        return self.edge_type in NAVIGABLE_EDGE_TYPES

    @property
    def key(self) -> tuple[str, str, str, str | None]:
        """map_edges 的 UNIQUE 键。"""
        return (str(self.source_map_id), str(self.target_map_id), self.edge_type, self.source_point_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_map_id": self.source_map_id,
            "target_map_id": self.target_map_id,
            "edge_type": self.edge_type,
            "source_point_id": self.source_point_id,
            "source_label_id": self.source_label_id,
            "discovery_source": self.discovery_source,
            "confidence": self.confidence,
            "bidirectional": self.bidirectional,
            "raw_json": self.raw_json,
            "discovered_at": self.discovered_at,
        }


# --------------------------------------------------------------------------- #
# 落库 / 装载
# --------------------------------------------------------------------------- #

_EDGE_COLUMNS = (
    "source_map_id, target_map_id, edge_type, source_point_id, source_label_id, "
    "discovery_source, confidence, bidirectional, raw_json, discovered_at"
)

#: 有 source_point_id 的边：走表上的 UNIQUE 约束。
_UPSERT_EDGE = f"""
INSERT INTO map_edges ({_EDGE_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (source_map_id, target_map_id, edge_type, source_point_id) DO UPDATE SET
    source_label_id = COALESCE(excluded.source_label_id, map_edges.source_label_id),
    discovery_source = excluded.discovery_source,
    confidence = MAX(excluded.confidence, map_edges.confidence),
    bidirectional = MAX(excluded.bidirectional, map_edges.bidirectional),
    raw_json = COALESCE(excluded.raw_json, map_edges.raw_json),
    discovered_at = COALESCE(map_edges.discovered_at, excluded.discovered_at)
"""

#: 没有 source_point_id 的边：SQLite 的 UNIQUE 把 NULL 当作互不相等，靠 database.py 里的
#: 部分唯一索引（WHERE source_point_id IS NULL）兜底，否则同一类跳转会被反复插进来。
_UPSERT_EDGE_WITHOUT_POINT = f"""
INSERT INTO map_edges ({_EDGE_COLUMNS})
VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
ON CONFLICT (source_map_id, target_map_id, edge_type) WHERE source_point_id IS NULL DO UPDATE SET
    source_label_id = COALESCE(excluded.source_label_id, map_edges.source_label_id),
    discovery_source = excluded.discovery_source,
    confidence = MAX(excluded.confidence, map_edges.confidence),
    bidirectional = MAX(excluded.bidirectional, map_edges.bidirectional),
    raw_json = COALESCE(excluded.raw_json, map_edges.raw_json),
    discovered_at = COALESCE(map_edges.discovered_at, excluded.discovered_at)
"""


def _edge_values(edge: Edge) -> tuple[Any, ...]:
    raw = None if edge.raw_json is None else json.dumps(edge.raw_json, ensure_ascii=False)
    return (
        str(edge.source_map_id),
        str(edge.target_map_id),
        edge.edge_type,
        edge.source_label_id,
        edge.discovery_source,
        float(edge.confidence),
        1 if edge.bidirectional else 0,
        raw,
        edge.discovered_at or now_iso(),
    )


def save_edges(conn: sqlite3.Connection, edges: Iterable[Edge], *, commit: bool = True) -> int:
    """幂等写入边（同一把 UNIQUE 键重复写只会有一条），返回写入条数。

    重复发现时的合并规则：**第一次**的 discovered_at 保留，confidence / bidirectional 取更强者，
    discovery_source / source_label_id / raw_json 取最新一条非空值。
    """
    written = 0
    for edge in edges:
        values = _edge_values(edge)
        if edge.source_point_id is None:
            conn.execute(_UPSERT_EDGE_WITHOUT_POINT, values)
        else:
            conn.execute(_UPSERT_EDGE, (values[0], values[1], values[2], str(edge.source_point_id), *values[3:]))
        written += 1
    if commit:
        conn.commit()
    return written


def _row_to_edge(row: Sequence[Any]) -> Edge:
    return Edge(
        id=int(row[0]),
        source_map_id=str(row[1]),
        target_map_id=str(row[2]),
        edge_type=str(row[3]),
        source_point_id=None if row[4] is None else str(row[4]),
        source_label_id=None if row[5] is None else str(row[5]),
        discovery_source=str(row[6]),
        confidence=float(row[7]),
        bidirectional=bool(row[8]),
        raw_json=None if row[9] is None else json.loads(row[9]),
        discovered_at=None if row[10] is None else str(row[10]),
    )


def load_edges(
    conn: sqlite3.Connection,
    *,
    source_map_id: str | None = None,
    target_map_id: str | None = None,
    edge_types: Iterable[str] | None = None,
) -> list[Edge]:
    """装载边。**老库兼容**：没有 map_edges 表（M7.1 之前的库）= 还没有发现过任何边，返回 []。"""
    if not has_table(conn, "map_edges"):
        return []
    sql = f"SELECT id, {_EDGE_COLUMNS} FROM map_edges"
    clauses: list[str] = []
    args: list[Any] = []
    if source_map_id is not None:
        clauses.append("source_map_id = ?")
        args.append(str(source_map_id))
    if target_map_id is not None:
        clauses.append("target_map_id = ?")
        args.append(str(target_map_id))
    types = None if edge_types is None else [str(item) for item in edge_types]
    if types:
        clauses.append(f"edge_type IN ({', '.join('?' for _ in types)})")
        args.extend(types)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    return [_row_to_edge(row) for row in conn.execute(sql, args)]


def navigable_edges(edges: Iterable[Edge]) -> list[Edge]:
    """用户真的能顺着导航过去的边（§二十四 的 invariant 输入）。"""
    return [edge for edge in edges if edge.navigable]


def edge_type_counts(edges: Iterable[Edge]) -> dict[str, int]:
    counts = Counter(edge.edge_type for edge in edges)
    return {edge_type: counts.get(edge_type, 0) for edge_type in EDGE_TYPES}


def known_map_ids(conn: sqlite3.Connection, *, renderable_only: bool = False) -> set[str]:
    """本地已知的地图 id 集合。

    默认 = map_nodes ∪ maps（树里见过的 + 已经同步过的）；
    renderable_only=True 只取 maps —— §二十四 的 synced_renderable_maps。
    """
    ids: set[str] = set()
    if not renderable_only and has_table(conn, "map_nodes"):
        ids.update(str(row[0]) for row in conn.execute("SELECT source_id FROM map_nodes"))
    if has_table(conn, "maps"):
        ids.update(str(row[0]) for row in conn.execute("SELECT source_id FROM maps"))
    return ids


def unresolved_targets(
    edges: Iterable[Edge],
    known: Iterable[str],
    *,
    navigable_only: bool = False,
) -> list[Edge]:
    """target 不在已知地图集合里的边。

    两种用法，别混：

    * **发现 frontier（缺省，`navigable_only=False`）**：任何类型的边只要指向未知目标就要去探
      —— 结构边（TREE_CHILD/RELATED_MAP/MAP_GROUP）指向的容器同样要落库（§六 递归闭包）；
    * **M7.6 发布门禁（`navigable_only=True`）**：只有「跳过去必须是另一张可渲染地图」的边才算违规，
      结构边单独统计（§二十四）。
    """
    known_set = {str(item) for item in known}
    return [
        edge for edge in edges
        if (edge.navigable or not navigable_only) and edge.target_map_id not in known_set
    ]


def load_map_nodes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """读 map_nodes，并对**老库做回退**（M7.1 之前没有新列）：

    - 没有 tree_leaf 列  → 用「有没有孩子」现算（树事实可以算）；
    - 没有 render_probe_state 列 → `LEGACY_TREE_HINT`：那时的 is_renderable 是结构猜测；
    - `is_renderable` **一律沿用落库值，绝不回算** —— 回算就是把历史结论变成新的猜测。
    """
    if not has_table(conn, "map_nodes"):
        return []
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(map_nodes)")}
    select = ["source_id", "parent_source_id", "node_type", "name", "depth", "sort_order", "is_renderable"]
    select.append("tree_leaf" if "tree_leaf" in columns else "NULL AS tree_leaf")
    select.append("render_probe_state" if "render_probe_state" in columns else "NULL AS render_probe_state")
    select.append("discovery_method" if "discovery_method" in columns else "NULL AS discovery_method")
    rows = list(conn.execute(f"SELECT {', '.join(select)} FROM map_nodes ORDER BY sort_order, id"))
    parents = {str(row[1]) for row in rows if row[1] is not None}
    nodes: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row[0])
        stored_renderable = None if row[6] is None else bool(row[6])
        stored_state = None if row[8] is None else str(row[8])
        if row[7] is None:
            tree_leaf = source_id not in parents
        else:
            tree_leaf = bool(row[7])
        nodes.append(
            {
                "source_id": source_id,
                "parent_source_id": None if row[1] is None else str(row[1]),
                "node_type": None if row[2] is None else int(row[2]),
                "name": row[3],
                "depth": None if row[4] is None else int(row[4]),
                "sort_order": None if row[5] is None else int(row[5]),
                "is_renderable": stored_renderable,
                "tree_leaf": tree_leaf,
                "render_probe_state": stored_state if stored_state is not None else LEGACY_TREE_HINT,
                "discovery_method": None if row[9] is None else str(row[9]),
            }
        )
    return nodes


# --------------------------------------------------------------------------- #
# Point transition（§十三）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PointTransition:
    """地图点位上的跳转（§十三）：前端只要读这个，不需要认识 HoYo 的原始字段。"""

    point_id: int
    target_map_source_id: str
    transition_type: str
    action_label: str | None = None
    raw_json: Any = None

    def __post_init__(self) -> None:
        if self.transition_type not in EDGE_TYPE_SET:
            raise ValueError(f"unknown transition_type {self.transition_type!r}; 语义不明请用 {UNKNOWN_TRANSITION}")
        if not str(self.target_map_source_id):
            raise ValueError("transition target_map_source_id is empty")

    def as_viewer(self) -> dict[str, Any]:
        """§十三 约定的 Viewer 形状。"""
        return {
            "type": self.transition_type,
            "target_map_id": str(self.target_map_source_id),
            "action": self.action_label,
        }


def save_point_transitions(
    conn: sqlite3.Connection,
    transitions: Iterable[PointTransition],
    *,
    commit: bool = True,
) -> int:
    """幂等写入 point_transitions：PRIMARY KEY(point_id, target_map_source_id)。"""
    written = 0
    for item in transitions:
        raw = None if item.raw_json is None else json.dumps(item.raw_json, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO point_transitions
            (point_id, target_map_source_id, transition_type, action_label, raw_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (point_id, target_map_source_id) DO UPDATE SET
                transition_type = excluded.transition_type,
                action_label = COALESCE(excluded.action_label, point_transitions.action_label),
                raw_json = COALESCE(excluded.raw_json, point_transitions.raw_json)
            """,
            (int(item.point_id), str(item.target_map_source_id), item.transition_type, item.action_label, raw),
        )
        written += 1
    if commit:
        conn.commit()
    return written


def load_point_transitions(conn: sqlite3.Connection, point_ids: Iterable[int] | None = None) -> list[PointTransition]:
    """装载点位跳转。老库没有 point_transitions 表 = 还没有发现过跳转，返回 []。"""
    if not has_table(conn, "point_transitions"):
        return []
    sql = "SELECT point_id, target_map_source_id, transition_type, action_label, raw_json FROM point_transitions"
    args: list[Any] = []
    if point_ids is not None:
        ids = [int(item) for item in point_ids]
        sql += f" WHERE point_id IN ({', '.join('?' for _ in ids)})"
        args.extend(ids)
    sql += " ORDER BY point_id, target_map_source_id"
    return [
        PointTransition(
            point_id=int(row[0]),
            target_map_source_id=str(row[1]),
            transition_type=str(row[2]),
            action_label=None if row[3] is None else str(row[3]),
            raw_json=None if row[4] is None else json.loads(row[4]),
        )
        for row in conn.execute(sql, args)
    ]


# --------------------------------------------------------------------------- #
# 闭包遍历（§六 / §十八）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClosureResult:
    """一次闭包遍历的结果。"""

    seeds: tuple[str, ...]
    #: 展开过的地图，按发现顺序（§六 伪代码里的 seen）。
    visited: tuple[str, ...]
    #: 走过的边（含回边）。
    traversed: tuple[Edge, ...]
    #: 回边 (source, target)：target 已经展开过。cycle 不是 bug（§十八）。
    cycles: tuple[tuple[str, str], ...]
    #: 发现到、但 expand 明确说「拿不到证据」的地图。
    unevidenced: tuple[str, ...]
    #: 终止时还没展开的地图：正常收敛就是空（§六 的 frontier == empty）。
    frontier: tuple[str, ...]
    steps: int
    #: 收敛 = frontier 为空 **且** 每个地图都拿到了证据。
    converged: bool


def closure(
    edges: Iterable[Edge] = (),
    seeds: Iterable[str] = (),
    *,
    expand: Callable[[str], Iterable[Edge] | None] | None = None,
) -> ClosureResult:
    """§六 的递归闭包：`queue = seeds; seen = set()`，跑到 frontier 为空。

    - `edges` 是已知的边（离线 / 测试用；可以只给一部分，剩下的靠 expand 拿）；
    - `expand(map_id)` 是 M7.2 的注入点：拉 map/info + point/list、跑 extractor、返回以该地图为
      source 的边。返回 `None` 表示「这次拿不到证据」——此时**不假装收敛**，记进 unevidenced；
    - 遍历只展开每个地图一次：`A → B → A` 走完 B 就停，cycle 不会死循环（§十八）。
    """
    outgoing: dict[str, list[Edge]] = {}
    for edge in edges:
        outgoing.setdefault(str(edge.source_map_id), []).append(edge)

    queued: dict[str, None] = {}
    pending: deque[str] = deque()
    seen: set[str] = set()
    visited: list[str] = []
    traversed: list[Edge] = []
    cycles: list[tuple[str, str]] = []
    unevidenced: list[str] = []
    seed_order: list[str] = []

    def enqueue(map_id: str) -> None:
        if map_id not in queued:
            queued[map_id] = None
            pending.append(map_id)

    for seed in seeds:
        text = str(seed)
        seed_order.append(text)
        enqueue(text)

    steps = 0
    while pending:
        current = pending.popleft()
        if current in seen:
            continue
        seen.add(current)
        visited.append(current)
        local = list(outgoing.pop(current, ()))
        if expand is not None:
            extra = expand(current)
            if extra is None:
                unevidenced.append(current)
            else:
                local.extend(extra)
        for edge in local:
            if str(edge.source_map_id) != current:
                raise ValueError(f"expand({current}) 返回了 source={edge.source_map_id} 的边")
            steps += 1
            traversed.append(edge)
            target = str(edge.target_map_id)
            if target in seen:
                cycles.append((current, target))
                continue
            enqueue(target)

    frontier = tuple(map_id for map_id in queued if map_id not in seen)
    return ClosureResult(
        seeds=tuple(seed_order),
        visited=tuple(visited),
        traversed=tuple(traversed),
        cycles=tuple(cycles),
        unevidenced=tuple(unevidenced),
        frontier=frontier,
        steps=steps,
        converged=not frontier and not unevidenced,
    )
