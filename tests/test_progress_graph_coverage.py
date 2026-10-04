"""进度层的图口径（a1-8-1 §二十/§二十四）：不再把 tree leaf 当地图全集。"""

from __future__ import annotations

import sqlite3

from hsrmap.graph import Edge, POINT_JUMP, TREE_CHILD, save_edges
from hsrmap.progress.atlas import graph_coverage, remaining_atlas


def _db(tmp_path, *, with_graph: bool = True):
    from hsrmap.database import SCHEMA

    conn = sqlite3.connect(tmp_path / "core.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for source_id, parent, name, renderable in (
        ("943", "939", "2层", 1),
        ("979", "955", "", 1),
    ):
        conn.execute(
            "INSERT INTO map_nodes(source_id, parent_source_id, name, node_type, depth, sort_order, is_renderable)"
            " VALUES (?, ?, ?, 2, 3, 0, ?)",
            (source_id, parent, name, renderable),
        )
        conn.execute(
            "INSERT INTO maps(source_id, name, fragment_count) VALUES (?, ?, 1)",
            (source_id, name),
        )
    #: points.map_id 是指向 maps.id 的整数外键（不是 source_id）—— 这里踩过一次，别再踩。
    map_pk = {
        str(row["source_id"]): int(row["id"])
        for row in conn.execute("SELECT id, source_id FROM maps")
    }
    for point_id, map_source_id in (("5637", "943"), ("6001", "979"), ("6002", "979")):
        conn.execute(
            "INSERT INTO points(source_id, map_id, x_pos, y_pos, z_pos, raster_x, raster_y)"
            " VALUES (?, ?, 0, 0, 0, 0, 0)",
            (point_id, map_pk[map_source_id]),
        )
    conn.commit()
    if with_graph:
        save_edges(conn, [
            Edge(source_map_id="938", target_map_id="943", edge_type=TREE_CHILD, discovery_source="map_tree"),
            Edge(source_map_id="943", target_map_id="979", edge_type=POINT_JUMP,
                 source_point_id="5637", discovery_source="point_list"),
        ])
    return conn


def test_graph_coverage_counts_renderable_and_deep_maps(tmp_path) -> None:
    conn = _db(tmp_path)
    coverage = graph_coverage(conn)
    assert coverage["available"] is True
    assert coverage["renderable_maps"] == 2
    assert coverage["edges_total"] == 2 and coverage["navigable_edges"] == 1
    #: 979 是可渲染的 → 不算 deep；deep 的定义是「可导航边指向、但不在可渲染集合里」
    assert coverage["deep_maps"] == 0
    assert coverage["points_total"] == 3
    assert coverage["unresolved_navigable_targets"] == 0


def test_graph_coverage_reports_unresolved_navigable_target(tmp_path) -> None:
    conn = _db(tmp_path, with_graph=False)
    save_edges(conn, [
        Edge(source_map_id="943", target_map_id="12345", edge_type=POINT_JUMP,
             source_point_id="5637", discovery_source="point_list"),
    ])
    coverage = graph_coverage(conn)
    assert coverage["unresolved_navigable_targets"] == 1
    assert coverage["deep_maps"] == 1


def test_atlas_without_graph_still_works_and_says_nothing_about_graph(tmp_path) -> None:
    conn = _db(tmp_path)
    report = remaining_atlas(guide_db=None, collectibles=[{"source_point_id": "5637", "label": "x"}])
    assert "graph" not in report
    with_graph = remaining_atlas(
        guide_db=None,
        collectibles=[{"source_point_id": "5637", "label": "x"}],
        core_conn=conn,
    )
    assert with_graph["graph"]["available"] is True
    assert with_graph["totals"]["collectible"] == 1


def test_graph_coverage_survives_a_database_without_points_table(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "bare.db")
    coverage = graph_coverage(conn)
    #: 没有图/没有表：如实报 0（`load_edges` 对缺表有回退），而不是抛异常把剩余清单一起带崩。
    assert coverage["available"] is True
    assert coverage["renderable_maps"] == 0 and coverage["edges_total"] == 0
    assert coverage["unresolved_navigable_targets"] == 0
