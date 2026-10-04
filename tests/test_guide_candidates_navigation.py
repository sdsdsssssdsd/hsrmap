"""§十九：候选匹配只多带导航上下文，判定结果一个都不许变。

这是「Guide Matcher 能识别 navigation path」的**不回归证明**：
同一个输入，不给图库与给图库时**命中的点位集合必须完全相同**，只是多了字段。
"""

from __future__ import annotations

import sqlite3

from hsrmap.graph import Edge, POINT_JUMP, TREE_CHILD, save_edges
from hsrmap.guides.matching.candidates import query_candidates, query_candidates_by_text

POINTS = [
    {"source_point_id": "5637", "map_id": "943", "label": "二次元JUMP!", "map_name": "2层",
     "region": "千星城中心城区", "map_path": "千星城 / 千星城中心城区 / 2层"},
    {"source_point_id": "1", "map_id": "101", "label": "浮脂溯源", "map_name": "残雪庭院",
     "region": "雅利洛-VI", "map_path": "雅利洛-VI / 残雪庭院 / 1层"},
]


def _graph(tmp_path) -> sqlite3.Connection:
    from hsrmap.database import SCHEMA

    conn = sqlite3.connect(tmp_path / "core.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for source_id, parent, name in (("938", None, "千星城"), ("939", "938", "千星城中心城区"), ("943", "939", "2层")):
        conn.execute(
            "INSERT INTO map_nodes(source_id, parent_source_id, name, node_type, depth, sort_order, is_renderable)"
            " VALUES (?, ?, ?, 2, 3, 0, 1)",
            (source_id, parent, name),
        )
    conn.execute("INSERT INTO maps(source_id, name, fragment_count) VALUES ('943', '2层', 1)")
    conn.execute("INSERT INTO maps(source_id, name, fragment_count) VALUES ('979', '', 1)")
    conn.commit()
    save_edges(conn, [
        Edge(source_map_id="939", target_map_id="943", edge_type=TREE_CHILD, discovery_source="map_tree"),
        Edge(source_map_id="943", target_map_id="979", edge_type=POINT_JUMP,
             source_point_id="5637", discovery_source="point_list"),
    ])
    return conn


def test_query_candidates_hits_are_identical_with_and_without_graph(tmp_path) -> None:
    conn = _graph(tmp_path)
    plain = query_candidates("2层", [dict(point) for point in POINTS], semantic="二次元JUMP!")
    enriched = query_candidates("2层", [dict(point) for point in POINTS], semantic="二次元JUMP!",
                                navigation_conn=conn)
    assert [point["source_point_id"] for point in plain] == [point["source_point_id"] for point in enriched]
    assert all("navigation_context" not in point for point in plain), "缺省路径不许偷偷加字段"
    assert enriched[0]["navigation_context"]["navigation_kind"] == "tree"


def test_query_candidates_by_text_decision_is_unchanged_by_the_graph(tmp_path) -> None:
    conn = _graph(tmp_path)
    text = "千星城中心城区2层"
    plain = query_candidates_by_text(text, [dict(point) for point in POINTS])
    enriched = query_candidates_by_text(text, [dict(point) for point in POINTS], navigation_conn=conn)
    assert [point["source_point_id"] for point in plain] == [point["source_point_id"] for point in enriched]
    if enriched:
        assert "navigation_context" in enriched[0]


def test_missing_graph_connection_keeps_the_old_behaviour(tmp_path) -> None:
    points = [dict(point) for point in POINTS]
    assert query_candidates("残雪庭院", points, semantic="浮脂溯源") == query_candidates(
        "残雪庭院", points, semantic="浮脂溯源", navigation_conn=None
    )
