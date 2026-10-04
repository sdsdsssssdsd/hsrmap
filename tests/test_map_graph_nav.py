"""导航上下文（a1-8-1 §十一/§十九）：深层地图怎么进去的、两种路径别混。"""

from __future__ import annotations

import sqlite3

import pytest

from hsrmap.database import table_columns
from hsrmap.graph import Edge, POINT_JUMP, TREE_CHILD, save_edges
from hsrmap.graph_nav import (
    attach_navigation_context,
    canonical_path,
    map_display_name,
    navigation_blob,
    navigation_context,
)


def _schema_sql() -> str:
    from hsrmap.database import SCHEMA

    return SCHEMA


def _db(tmp_path) -> sqlite3.Connection:
    from hsrmap.database import SCHEMA

    path = tmp_path / "core.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for source_id, parent, name in (
        ("938", None, "千星城"),
        ("939", "938", "千星城中心城区"),
        ("943", "939", "2层"),
        ("979", "955", ""),
    ):
        conn.execute(
            "INSERT INTO map_nodes(source_id, parent_source_id, name, node_type, depth, sort_order, is_renderable)"
            " VALUES (?, ?, ?, 2, 3, 0, 1)",
            (source_id, parent, name),
        )
    conn.execute("INSERT INTO maps(source_id, name, fragment_count) VALUES ('943', '2层', 1)")
    conn.execute("INSERT INTO maps(source_id, name, fragment_count) VALUES ('979', '', 1)")
    conn.commit()
    save_edges(conn, [
        Edge(source_map_id="938", target_map_id="939", edge_type=TREE_CHILD, discovery_source="map_tree"),
        Edge(source_map_id="939", target_map_id="943", edge_type=TREE_CHILD, discovery_source="map_tree"),
        Edge(source_map_id="955", target_map_id="979", edge_type=TREE_CHILD, discovery_source="map_tree"),
        Edge(source_map_id="943", target_map_id="979", edge_type=POINT_JUMP,
             source_point_id="5637", discovery_source="point_list"),
    ])
    return conn


def test_deep_map_reports_entry_and_navigation_path(tmp_path) -> None:
    conn = _db(tmp_path)
    context = navigation_context(conn, "979")
    assert context["navigation_kind"] == "deep"
    assert context["entry_map_id"] == "943"
    assert context["entry_point_id"] == "5637"
    assert context["entry_edge_type"] == POINT_JUMP
    #: 导航链 = 入口图的树路径 + 深层图自己；缺名字时用 id 兜底（不编名字）。
    assert context["navigation_path"] == ["千星城", "千星城中心城区", "2层", "979"]
    #: 树路径是事实那一份：979 挂在 955 下（955 不在 nodes 里 → 断在 979）。
    assert context["tree_path"] == ["979"]


def test_tree_map_has_no_entry_edge(tmp_path) -> None:
    conn = _db(tmp_path)
    context = navigation_context(conn, "943")
    assert context["navigation_kind"] == "tree"
    assert context["entry_map_id"] == ""
    assert context["navigation_path"] == context["tree_path"] == ["千星城", "千星城中心城区", "2层"]


def test_display_name_prefers_display_name_column(tmp_path) -> None:
    conn = _db(tmp_path)
    assert map_display_name(conn, "943") == "2层"
    assert map_display_name(conn, "979") == ""  #: 树里是空名，不许编
    #: M7.3 之后 SCHEMA 里本来就有 display_name；没有才补（测试要能同时跑在老库/新库上）。
    if "display_name" not in table_columns(conn, "maps"):
        conn.execute("ALTER TABLE maps ADD COLUMN display_name TEXT")
    conn.execute("UPDATE maps SET display_name='1' WHERE source_id='979'")
    conn.commit()
    assert map_display_name(conn, "979") == "1"
    assert navigation_context(conn, "979")["map_name"] == "1"


def test_canonical_path_survives_a_parent_cycle(tmp_path) -> None:
    conn = _db(tmp_path)
    conn.execute("UPDATE map_nodes SET parent_source_id='943' WHERE source_id='938'")
    conn.commit()
    path = canonical_path(conn, "943")
    #: 环被 seen 切断（不死循环）；断点之后不再往回编造祖先。
    assert path[-1] == "2层" and len(path) <= 4, path
    assert path == ["千星城", "千星城中心城区", "2层"]


@pytest.mark.data
def test_real_deep_map_has_the_reported_entry() -> None:
    """真实数据上的 979：入口 943 / 入口点位 5637（M7.0 的 canary 样本）。"""
    import json
    from pathlib import Path

    from hsrmap.paths import DATA

    graph_db = Path(DATA) / "graph" / "core.db"
    if not graph_db.is_file():
        pytest.skip("SKIPPED: 没有图库（先跑 python -m hsrmap graph backfill --write）")
    conn = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        context = navigation_context(conn, "979")
        assert context["entry_map_id"] == "943"
        assert context["entry_point_id"] == "5637"
        assert context["navigation_path"][:3] == ["千星城", "千星城中心城区", "2层"]
        #: 名字缺口是 M7.3 的活儿：现在补不上就如实用 id，不许编。
        assert context["navigation_path"][-1] in {"979", context["map_name"] or ""} or context["map_name"]
        assert json.loads(json.dumps(context))["navigation_kind"] == "deep"
    finally:
        conn.close()


def test_attach_navigation_context_is_additive_and_cached(tmp_path) -> None:
    conn = _db(tmp_path)
    points = [{"source_point_id": "1", "map_id": "979"}, {"source_point_id": "2", "map_id": "979"},
              {"source_point_id": "3", "map_id": "943"}, {"source_point_id": "4"}]
    enriched = attach_navigation_context(points, conn)
    #: 原 dict 不被修改，新列表带上下文。
    assert "navigation_context" not in points[0]
    assert enriched[0]["navigation_context"]["entry_map_id"] == "943"
    assert enriched[1]["navigation_context"] is enriched[0]["navigation_context"], "同图缓存同一个对象"
    assert enriched[2]["navigation_context"]["navigation_kind"] == "tree"
    assert enriched[3]["navigation_context"] is None
    #: 没有图库时如实空着，绝不编。
    blank = attach_navigation_context([{"map_id": "979"}], None)
    assert blank[0]["navigation_context"] is None


def test_navigation_blob_is_searchable_text(tmp_path) -> None:
    conn = _db(tmp_path)
    context = navigation_context(conn, "979")
    blob = navigation_blob(context)
    assert "千星城中心城区" in blob and "point:5637" in blob
    assert navigation_blob(None) == ""


def test_open_graph_connection_prefers_the_core_db_then_the_sidecar(tmp_path) -> None:
    from hsrmap.graph_nav import open_graph_connection, sidecar_graph_path

    #: 1) core 里就有 map_edges → 用它，且 owned=False（不归调用方关）。
    core = _db(tmp_path)
    conn, owned = open_graph_connection(core, data_root=tmp_path)
    assert conn is core and owned is False

    #: 2) core 里没有 → 用旁挂库。
    sidecar_dir = tmp_path / "data"
    graph_path = sidecar_graph_path(sidecar_dir)
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    side = sqlite3.connect(graph_path)
    side.executescript(_schema_sql())
    side.commit()
    side.close()
    plain = sqlite3.connect(tmp_path / "plain.db")
    conn2, owned2 = open_graph_connection(plain, data_root=sidecar_dir)
    try:
        assert conn2 is not None and owned2 is True
    finally:
        if owned2:
            conn2.close()

    #: 3) 两边都没有 → (None, False)：如实说没有，不造库。
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    none_conn, none_owned = open_graph_connection(plain, data_root=empty_root)
    assert none_conn is None and none_owned is False
    assert not (empty_root / "graph").exists(), "不许顺手造一个空图库"


def test_missing_tables_do_not_explode(tmp_path) -> None:
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    context = navigation_context(conn, "1")
    assert context["navigation_kind"] == "tree"
    assert context["navigation_path"] == ["1"]
