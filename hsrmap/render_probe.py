"""可渲染探测（a1-8 §九）：renderable 是判出来的，不是从 node_type 猜出来的。

规格给的 canonical 判定：

    Renderable(map_id) = map/info 成功 AND detail 存在 AND raster spec 有效

M7.1 只用**已经落库**的证据（`maps` + `map_fragments`，它们都由 map/info 的 normalizer 产生），
不发任何网络请求；M7.3 拿到更深层的证据后喂进同一个 probe，调用点不用改。

三个状态，不是两个：

    VALID    map/info 落库 + raster spec 有效（有 fragment 且带 url）  → renderable = True
    INVALID  有证据说它不是可渲染地图（没有 raster / fragment 没有 url）→ renderable = False
    UNKNOWN  还没有证据（没问过官方，或者还没落库）                    → renderable = None

UNKNOWN 必须如实保留：把「没问过」写成 True 是猜，写成 False 也是猜（§三 / §九）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: 探测状态（写进 map_nodes.render_probe_state）。
VALID = "VALID"
INVALID = "INVALID"
UNKNOWN = "UNKNOWN"

#: 新写入只可能是这三个状态。
RENDER_PROBE_STATES: tuple[str, ...] = (VALID, INVALID, UNKNOWN)

#: M7.1 之前落库的行没有 render_probe_state：那时的 is_renderable 是 node_type==2 的结构猜测，
#: 不是探测结果（§三）。读老库时如实标成这个状态，并且**沿用**落库的 is_renderable，绝不回算。
LEGACY_TREE_HINT = "LEGACY_TREE_HINT"
LEGACY_PROBE_STATES: tuple[str, ...] = (LEGACY_TREE_HINT,)
ALL_PROBE_STATES: tuple[str, ...] = RENDER_PROBE_STATES + LEGACY_PROBE_STATES

#: 官方 map/tree 用 node_type=2 标注「这是一张地图」。这只是**发现提示**（决定初始 frontier
#: 去问哪些 map/info），不是可渲染判定：有 children 的节点一样可能有 raster（§三 / §九）。
TREE_MAP_NODE_TYPE = 2

#: 节点进入图谱的方式（map_nodes.discovery_method）。TREE = 来自 map/tree；
#: 其余取值就是 hsrmap/graph.py 里的 edge_type（POINT_JUMP / RELATED_MAP / ...，§九 的例子）。
DISCOVERY_TREE = "TREE"


@dataclass(frozen=True)
class RenderEvidence:
    """一张地图的可渲染证据。全部来自已经同步下来的官方 payload 派生物。"""

    map_id: str
    #: None = 本地没有任何 map/info 证据（没问过 / 没落库）；True = map/info 成功且 detail 有效；
    #: False = 有 map/info 失败的证据（M7.3 才会写进来）。
    map_info_present: bool | None = None
    #: raster spec 里的 fragment 数（None = 还没有这份证据）。
    raster_fragment_count: int | None = None
    #: 其中带 remote_url 的 fragment 数。
    raster_fragments_with_url: int | None = None
    canvas: tuple[float, float] | None = None
    source: str = "core.db:maps+map_fragments"


@dataclass(frozen=True)
class RenderProbe:
    """一次探测的结论：状态 + 依据（reasons 逐条写清楚是拿哪条证据判的）。"""

    map_id: str
    state: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in ALL_PROBE_STATES:
            raise ValueError(f"unknown render probe state: {self.state!r}")

    @property
    def renderable(self) -> bool | None:
        """True / False / None（UNKNOWN）—— None 不是 False。"""
        if self.state == VALID:
            return True
        if self.state == INVALID:
            return False
        return None

    @property
    def column_value(self) -> int | None:
        """map_nodes.is_renderable 的落库值：NULL 表示还没探测过。"""
        value = self.renderable
        return None if value is None else (1 if value else 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "state": self.state,
            "renderable": self.renderable,
            "reasons": list(self.reasons),
        }


def probe_renderable(evidence: RenderEvidence) -> RenderProbe:
    """按 §九 的判定从证据推出状态，并逐条说明依据。"""
    map_id = str(evidence.map_id)
    if evidence.map_info_present is None:
        return RenderProbe(map_id, UNKNOWN, ("还没有 map/info 证据：不知道它有没有 raster，不猜",))
    if not evidence.map_info_present:
        return RenderProbe(map_id, INVALID, ("map/info 失败或 detail 无效",))
    if evidence.raster_fragment_count is None:
        return RenderProbe(map_id, UNKNOWN, ("map/info 已落库，但 raster fragment 证据缺失",))
    if evidence.raster_fragment_count <= 0:
        return RenderProbe(map_id, INVALID, ("map/info 落库但 raster spec 里没有 fragment",))
    if evidence.raster_fragments_with_url is None:
        return RenderProbe(map_id, UNKNOWN, ("有 fragment，但还没有 url 证据",))
    if evidence.raster_fragments_with_url <= 0:
        return RenderProbe(map_id, INVALID, ("raster fragment 没有 remote_url",))
    if evidence.canvas is None:
        return RenderProbe(map_id, UNKNOWN, ("有 raster fragment，但 canvas 证据缺失",))
    width, height = evidence.canvas
    if width <= 0 or height <= 0:
        return RenderProbe(map_id, INVALID, (f"canvas 尺寸无效：{width}x{height}",))
    return RenderProbe(
        map_id,
        VALID,
        (
            "map/info 成功",
            f"detail 有效（raster spec {int(width)}x{int(height)}）",
            f"{evidence.raster_fragments_with_url} 个 fragment 带 remote_url",
        ),
    )


def evidence_from_db(conn: sqlite3.Connection, map_ids: Iterable[str] | None = None) -> dict[str, RenderEvidence]:
    """从已经落库的 maps + map_fragments 收集证据（两条 SQL，不做逐点查询）。

    map_ids=None 表示探测库里所有已经同步过的地图；给了 map_ids 时，库里查不到证据的 id
    会得到 `map_info_present=None`（UNKNOWN），而不是被当成「不可渲染」。
    """
    wanted = None if map_ids is None else {str(item) for item in map_ids}
    rows: dict[str, tuple[int, float, float]] = {}
    for row in conn.execute("SELECT id, source_id, canvas_width, canvas_height FROM maps"):
        source_id = str(row[1])
        if wanted is not None and source_id not in wanted:
            continue
        rows[source_id] = (int(row[0]), float(row[2] or 0.0), float(row[3] or 0.0))
    fragments: dict[int, tuple[int, int]] = {}
    for row in conn.execute(
        """
        SELECT map_id,
               COUNT(*) AS total,
               SUM(CASE WHEN COALESCE(remote_url, '') <> '' THEN 1 ELSE 0 END) AS with_url
        FROM map_fragments
        GROUP BY map_id
        """
    ):
        fragments[int(row[0])] = (int(row[1] or 0), int(row[2] or 0))
    ids = sorted(wanted) if wanted is not None else sorted(rows)
    out: dict[str, RenderEvidence] = {}
    for source_id in ids:
        hit = rows.get(source_id)
        if hit is None:
            out[source_id] = RenderEvidence(map_id=source_id, map_info_present=None)
            continue
        map_pk, width, height = hit
        total, with_url = fragments.get(map_pk, (0, 0))
        out[source_id] = RenderEvidence(
            map_id=source_id,
            map_info_present=True,
            raster_fragment_count=total,
            raster_fragments_with_url=with_url,
            canvas=(width, height),
        )
    return out


def probe_map_renderability(
    conn: sqlite3.Connection,
    map_ids: Iterable[str] | None = None,
    *,
    extra_evidence: Mapping[str, RenderEvidence] | None = None,
) -> dict[str, RenderProbe]:
    """只读探测：不写库、不发网络。

    extra_evidence 是 M7.3 留的口子：maps + map_fragments 看不到的证据（例如「map/info 成功
    返回、但 payload 里根本没有 raster detail」的容器节点）由调用方喂进来，判定口径不变。
    """
    evidence = evidence_from_db(conn, map_ids)
    if extra_evidence:
        evidence = {**evidence, **dict(extra_evidence)}
    return {map_id: probe_renderable(item) for map_id, item in evidence.items()}


def refresh_render_probes(
    conn: sqlite3.Connection,
    map_ids: Iterable[str],
    *,
    extra_evidence: Mapping[str, RenderEvidence] | None = None,
) -> dict[str, RenderProbe]:
    """探测 + 把结论写回 map_nodes（幂等）：sync / rebuild 在 map_info 落库之后调用。

    UNKNOWN 会把 is_renderable 写成 NULL —— 「还没探测」不能落成 0（那也是猜，§九）。
    需要 M7.1 之后的 map_nodes 列；老库请先用 CoreDatabase 打开（打开时会做加性迁移）。
    """
    probes = probe_map_renderability(conn, map_ids, extra_evidence=extra_evidence)
    for map_id, probe in probes.items():
        conn.execute(
            "UPDATE map_nodes SET is_renderable = ?, render_probe_state = ? WHERE source_id = ?",
            (probe.column_value, probe.state, map_id),
        )
    conn.commit()
    return probes


def probe_summary(probes: Mapping[str, RenderProbe] | Iterable[RenderProbe]) -> dict[str, int]:
    """按状态计数，给 sync 的状态/审计报告用。"""
    items = probes.values() if isinstance(probes, Mapping) else probes
    summary = {state: 0 for state in RENDER_PROBE_STATES}
    for probe in items:
        summary[probe.state] = summary.get(probe.state, 0) + 1
    return summary


def tree_map_candidates(nodes: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """初始 frontier 的**结构候选**：官方树标成地图（node_type=2）的节点。

    这只是「去问官方要 map/info 证据」的起点，不是可渲染判定（§三 / §九）。特意**不**要求
    tree_leaf：有 children 的节点一样可能自己带 raster（§三 的 A 地图）。
    """
    return [node for node in nodes if node.get("node_type") == TREE_MAP_NODE_TYPE]
