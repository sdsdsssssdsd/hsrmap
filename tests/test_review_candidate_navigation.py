"""§十九 接进审核队列：只给深层地图上的候选点加 navigation，别的不许动。

三条约束的机器版本：
1. 没有图库 → 一个字段都不加（老快照 + 新代码必须完全一样）；
2. 普通树上地图 → 也不加（不把审核页首屏载荷重新吹大）；
3. 深层地图 → 只加 `entry_map_id / entry_point_id / path` 四样（紧凑块，不是整份上下文）。
"""

from __future__ import annotations

import sqlite3

from hsrmap.graph import Edge, POINT_JUMP, TREE_CHILD, save_edges
from hsrmap.guides.review.service import navigation_for_candidates


def _graph_with_deep_map(tmp_path) -> sqlite3.Connection:
    from hsrmap.database import SCHEMA

    conn = sqlite3.connect(tmp_path / "graph.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for source_id, parent, name in (("938", None, "千星城"), ("939", "938", "千星城中心城区"), ("943", "939", "2层")):
        conn.execute(
            "INSERT INTO map_nodes(source_id, parent_source_id, name, node_type, depth, sort_order, is_renderable)"
            " VALUES (?, ?, ?, 2, 3, 0, 1)",
            (source_id, parent, name),
        )
    for source_id, name in (("943", "2层"), ("979", "1")):
        conn.execute("INSERT INTO maps(source_id, name, fragment_count) VALUES (?, ?, 1)", (source_id, name))
    conn.commit()
    save_edges(conn, [
        Edge(source_map_id="939", target_map_id="943", edge_type=TREE_CHILD, discovery_source="map_tree"),
        Edge(source_map_id="943", target_map_id="979", edge_type=POINT_JUMP,
             source_point_id="5637", discovery_source="point_list"),
    ])
    return conn


def test_without_a_graph_nothing_is_added() -> None:
    cands = [{"source_point_id": "1", "map_id": "979"}]
    assert navigation_for_candidates(cands, None) == cands
    assert "navigation" not in navigation_for_candidates(cands, None)[0]


def test_tree_map_candidates_stay_untouched(tmp_path) -> None:
    conn = _graph_with_deep_map(tmp_path)
    cands = [{"source_point_id": "2", "map_id": "943"}]
    out = navigation_for_candidates(cands, conn)
    assert out[0] == cands[0], "普通地图上的候选点不许被加字段（首屏载荷）"


def test_deep_map_candidate_gets_a_compact_navigation_block(tmp_path) -> None:
    conn = _graph_with_deep_map(tmp_path)
    out = navigation_for_candidates([{"source_point_id": "5637", "map_id": "979"}], conn)
    navigation = out[0]["navigation"]
    assert navigation["kind"] == "deep"
    assert navigation["entry_map_id"] == "943"
    assert navigation["entry_point_id"] == "5637"
    assert navigation["path"][:3] == ["千星城", "千星城中心城区", "2层"]
    #: 紧凑块：不放整份上下文（没有 entries/tree_path 这些）。
    assert set(navigation) == {"kind", "entry_map_id", "entry_point_id", "path"}
    assert "navigation_context" not in out[0]
