"""M7.1 Graph Schema（a1-8-1 §三/§四/§六/§九/§十三/§十八/§二十四）。

覆盖：

- `map_edges` / `point_transitions` 的 schema 与唯一约束**幂等**（重复发现只留一条边）；
- `UNKNOWN_TRANSITION` 必须被保留，不许当噪声丢掉（§五）；
- 闭包遍历收敛，cycle（A→B→A、自环）不死循环（§六 / §十八）；
- `unresolved_targets` 能报出「指向未知地图的边」（§二十四 的 gate 输入）；
- 旧库兼容：没有新列 / 新表的 core.db 读出来不炸，`is_renderable` 的 624 不变。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hsrmap.database import SCHEMA_VERSION, CoreDatabase, has_table, table_columns
from hsrmap.graph import (
    EDGE_KEY_COLUMNS,
    EDGE_TYPES,
    FLOOR,
    MAP_GROUP,
    NAVIGABLE_EDGE_TYPES,
    POINT_JUMP,
    RELATED_MAP,
    RETURN,
    STRUCTURAL_EDGE_TYPES,
    TREE_CHILD,
    TREE_CHILD,
    UNKNOWN_TRANSITION,
    ClosureResult,
    Edge,
    PointTransition,
    closure,
    edge_type_counts,
    known_map_ids,
    load_edges,
    load_map_nodes,
    load_point_transitions,
    navigable_edges,
    save_edges,
    save_point_transitions,
    unresolved_targets,
)
from hsrmap.normalize import flatten_map_nodes
from hsrmap.render_probe import (
    DISCOVERY_TREE,
    INVALID,
    LEGACY_TREE_HINT,
    TREE_MAP_NODE_TYPE,
    UNKNOWN,
    VALID,
    RenderEvidence,
    RenderProbe,
    evidence_from_db,
    probe_map_renderability,
    probe_renderable,
    refresh_render_probes,
    tree_map_candidates,
)

# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

#: v1（M7.1 之前）的 core.db 长这样：没有 map_edges / point_transitions，
#: map_nodes 也没有 tree_leaf / render_probe_state / discovery_method。
LEGACY_SCHEMA = """
CREATE TABLE map_nodes (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    parent_source_id TEXT,
    node_type INTEGER,
    name TEXT,
    depth INTEGER,
    sort_order INTEGER,
    is_renderable INTEGER,
    raw_sha256 TEXT,
    raw_json TEXT
);
CREATE TABLE maps (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    node_id INTEGER,
    name TEXT,
    canvas_width REAL,
    canvas_height REAL,
    origin_x REAL,
    origin_y REAL,
    padding_json TEXT,
    fragment_count INTEGER,
    map_info_sha256 TEXT,
    coordinate_transform TEXT
);
CREATE TABLE map_fragments (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL,
    source_index INTEGER,
    remote_url TEXT,
    asset_sha256 TEXT,
    source_width INTEGER,
    source_height INTEGER,
    position_json TEXT,
    metadata_json TEXT,
    raw_json TEXT
);
"""

#: 老库里的三个节点：1 = 目录，10 / 11 = 两张地图（老语义下 is_renderable 由 node_type 猜出来）。
LEGACY_NODES = (
    ("1", None, 1, "二相乐园", 1, 0, 0),
    ("10", "1", 2, "海原市", 3, 0, 1),
    ("11", "1", 2, "匹诺康尼", 3, 1, 1),
)


def legacy_core_db(tmp_path: Path, name: str = "core.db") -> Path:
    """按 v1 的 DDL 手搓一个老库（没有新表、没有新列）。"""
    path = tmp_path / name
    conn = sqlite3.connect(path)
    try:
        conn.executescript(LEGACY_SCHEMA)
        conn.executemany(
            "INSERT INTO map_nodes(source_id, parent_source_id, node_type, name, depth, sort_order, is_renderable)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            LEGACY_NODES,
        )
        for source_id, width, height in (("10", 8192, 4096), ("11", 4096, 4096)):
            cur = conn.execute(
                "INSERT INTO maps(source_id, name, canvas_width, canvas_height, origin_x, origin_y, fragment_count)"
                " VALUES (?, ?, ?, ?, 0, 0, 1)",
                (source_id, source_id, width, height),
            )
            conn.execute(
                "INSERT INTO map_fragments(map_id, source_index, remote_url, source_width, source_height)"
                " VALUES (?, 0, ?, ?, ?)",
                (cur.lastrowid, f"https://example.test/{source_id}.png", width, height),
            )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()
    return path


def _mapped_map(source_id: str, *, width: float = 8192, height: float = 4096, fragments: int = 1) -> dict:
    return {
        "source_id": source_id,
        "name": f"map-{source_id}",
        "canvas_width": width,
        "canvas_height": height,
        "origin_x": 0.0,
        "origin_y": 0.0,
        "padding_json": None,
        "fragment_count": fragments,
        "coordinate_transform": "origin_translation_v1",
        "fragments": [
            {
                "index": index,
                "remote_url": f"https://example.test/{source_id}-{index}.png",
                "source_width": width,
                "source_height": height,
                "x": 0,
                "y": 0,
                "width": width,
                "height": height,
            }
            for index in range(fragments)
        ],
    }


def _sample_tree() -> list[dict]:
    return [
        {
            "id": 1,
            "name": "二相乐园",
            "node_type": 1,
            "depth": 1,
            "parent_id": 0,
            "children": [
                {"id": 10, "name": "海原市", "node_type": 2, "depth": 3, "parent_id": 1, "children": []},
                {"id": 11, "name": "匹诺康尼", "node_type": 2, "depth": 3, "parent_id": 1, "children": []},
            ],
        }
    ]


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #


def test_map_edges_table_matches_spec(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        assert [row[1] for row in db.conn.execute("PRAGMA table_info(map_edges)")] == [
            "id",
            "source_map_id",
            "target_map_id",
            "edge_type",
            "source_point_id",
            "source_label_id",
            "discovery_source",
            "confidence",
            "bidirectional",
            "raw_json",
            "discovered_at",
        ]
        ddl = db.conn.execute("SELECT sql FROM sqlite_master WHERE name = 'map_edges'").fetchone()[0]
        compact = ddl.replace(" ", "").replace("\n", "")
        assert EDGE_KEY_COLUMNS == ("source_map_id", "target_map_id", "edge_type", "source_point_id")
        assert f"UNIQUE({','.join(EDGE_KEY_COLUMNS)})" in compact
        assert "confidence REAL NOT NULL DEFAULT 1.0" in ddl
        assert "bidirectional INTEGER NOT NULL DEFAULT 0" in ddl
        # SQLite 的 UNIQUE 把 NULL 当互不相等：没有点位来源的边要有部分唯一索引兜底。
        assert has_table(db.conn, "map_edges")
        assert any(row[1] == "idx_map_edges_unsourced" for row in db.conn.execute("PRAGMA index_list(map_edges)"))
    finally:
        db.close()


def test_point_transitions_table_matches_spec(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        info = list(db.conn.execute("PRAGMA table_info(point_transitions)"))
        assert [row[1] for row in info] == [
            "point_id",
            "target_map_source_id",
            "transition_type",
            "action_label",
            "raw_json",
        ]
        # PRIMARY KEY(point_id, target_map_source_id)
        assert [(row[1], row[5]) for row in info if row[5]] == [("point_id", 1), ("target_map_source_id", 2)]
    finally:
        db.close()


def test_core_schema_version_bumped_and_open_is_idempotent(tmp_path):
    path = tmp_path / "core.db"
    first = CoreDatabase(path)
    try:
        #: 版本号本身会随阶段往上抬（M7.1 = 2，M7.3 起 = 3）：这里守的是
        #: 「图结构那一版之后就是当前版本」+「落库的 user_version 与常量一致」，
        #: 而不是把某一个具体数字焊死（焊死就会在每次合法抬版时假红）。
        assert SCHEMA_VERSION >= 2
        assert int(first.conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
    finally:
        first.close()
    again = CoreDatabase(path)
    try:
        assert table_columns(again.conn, "map_nodes") >= {
            "tree_leaf",
            "render_probe_state",
            "discovery_method",
            "is_renderable",
        }
        assert int(again.conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
    finally:
        again.close()


# --------------------------------------------------------------------------- #
# 边的幂等与分类
# --------------------------------------------------------------------------- #


def test_edge_upsert_is_idempotent_with_and_without_point_source(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        without_point = Edge(
            source_map_id="943",
            target_map_id="1287",
            edge_type=POINT_JUMP,
            discovery_source="point_payload",
            discovered_at="2026-01-01T00:00:00+00:00",
        )
        with_point = Edge(
            source_map_id="943",
            target_map_id="1287",
            edge_type=POINT_JUMP,
            source_point_id="12345",
            discovery_source="point_payload",
        )
        for _ in range(3):
            assert save_edges(db.conn, [without_point, with_point]) == 2
        rows = list(
            db.conn.execute(
                "SELECT source_point_id, confidence, discovered_at FROM map_edges ORDER BY source_point_id NULLS FIRST"
            )
        )
        assert len(rows) == 2
        assert rows[0][0] is None
        assert rows[0][2] == "2026-01-01T00:00:00+00:00"  # 第一次发现的时间保留
        # 重复发现：confidence 取更强者，不允许被一条更弱的证据覆盖
        weaker = Edge(
            source_map_id="943",
            target_map_id="1287",
            edge_type=POINT_JUMP,
            discovery_source="point_payload",
            confidence=0.4,
        )
        save_edges(db.conn, [weaker])
        assert db.conn.execute("SELECT COUNT(*) FROM map_edges").fetchone()[0] == 2
        assert (
            db.conn.execute("SELECT confidence FROM map_edges WHERE source_point_id IS NULL").fetchone()[0] == 1.0
        )
    finally:
        db.close()


def test_unknown_transition_is_kept_not_dropped(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        edge = Edge(
            source_map_id="943",
            target_map_id="1287",
            edge_type=UNKNOWN_TRANSITION,
            discovery_source="point_payload",
            raw_json={"why": "还不知道官方把它定义成 JUMP / portal / related"},
        )
        save_edges(db.conn, [edge])
        loaded = load_edges(db.conn)
        assert [item.edge_type for item in loaded] == [UNKNOWN_TRANSITION]
        assert loaded[0].raw_json == edge.raw_json
        assert edge_type_counts(loaded)[UNKNOWN_TRANSITION] == 1
        assert edge_type_counts(loaded)[POINT_JUMP] == 0
        # 可达性：不知道语义 ≠ 可以当作不存在
        assert navigable_edges(loaded) == loaded
        assert unresolved_targets(loaded, {"943"}) == loaded
    finally:
        db.close()


def test_edge_type_vocabulary_is_closed(tmp_path):
    assert EDGE_TYPES == (
        TREE_CHILD,
        FLOOR,
        POINT_JUMP,
        "RELATED_MAP",
        "MAP_GROUP",
        "PORTAL",
        RETURN,
        UNKNOWN_TRANSITION,
    )
    #: M7.6 定稿：结构边（树/楼层/关联/地图组）的目标本来就可以是容器节点，
    #: 不参与「跳到的地方必须有 raster」这条发布 invariant；可导航集合 = 全集 − 结构边。
    assert STRUCTURAL_EDGE_TYPES == frozenset({TREE_CHILD, FLOOR, RELATED_MAP, MAP_GROUP})
    assert NAVIGABLE_EDGE_TYPES == frozenset(EDGE_TYPES) - STRUCTURAL_EDGE_TYPES
    assert NAVIGABLE_EDGE_TYPES | STRUCTURAL_EDGE_TYPES == frozenset(EDGE_TYPES)
    assert not (NAVIGABLE_EDGE_TYPES & STRUCTURAL_EDGE_TYPES)
    with pytest.raises(ValueError):
        Edge(source_map_id="1", target_map_id="2", edge_type="JUMP")
    with pytest.raises(ValueError):
        Edge(source_map_id="1", target_map_id="2", edge_type=POINT_JUMP, confidence=1.5)


def test_load_edges_filters_by_type_and_endpoint(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        save_edges(
            db.conn,
            [
                Edge("943", "1287", POINT_JUMP, discovery_source="point_payload"),
                Edge("943", "325", TREE_CHILD, discovery_source="map_tree"),
                Edge("1287", "943", RETURN, discovery_source="navigation_stack"),
            ],
        )
        assert len(load_edges(db.conn)) == 3
        assert [item.target_map_id for item in load_edges(db.conn, source_map_id="943")] == ["1287", "325"]
        assert [item.source_map_id for item in load_edges(db.conn, target_map_id="943")] == ["1287"]
        assert [item.edge_type for item in load_edges(db.conn, edge_types=[RETURN])] == [RETURN]
    finally:
        db.close()


def test_point_transitions_roundtrip_and_idempotent(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        item = PointTransition(
            point_id=5171,
            target_map_source_id="1287",
            transition_type=POINT_JUMP,
            action_label="前往对应地图",
            raw_json={"jump": {"map_id": 1287}},
        )
        for _ in range(3):
            assert save_point_transitions(db.conn, [item]) == 1
        assert db.conn.execute("SELECT COUNT(*) FROM point_transitions").fetchone()[0] == 1
        loaded = load_point_transitions(db.conn, [5171])
        assert len(loaded) == 1
        assert loaded[0].as_viewer() == {
            "type": POINT_JUMP,
            "target_map_id": "1287",
            "action": "前往对应地图",
        }
        assert load_point_transitions(db.conn, [999]) == []
        with pytest.raises(ValueError):
            PointTransition(point_id=1, target_map_source_id="2", transition_type="JUMP")
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# unresolved targets / known map set
# --------------------------------------------------------------------------- #


def test_unresolved_targets_reports_edges_into_unknown_maps(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        db.insert_map_nodes(flatten_map_nodes(_sample_tree()))
        db.insert_map(_mapped_map("10"))
        resolved = Edge("10", "1", TREE_CHILD, discovery_source="map_tree")
        dangling = Edge("10", "1287", POINT_JUMP, discovery_source="point_payload")
        edges = [resolved, dangling]
        known = known_map_ids(db.conn)
        assert known == {"1", "10", "11"}
        assert unresolved_targets(edges, known) == [dangling]
        assert unresolved_targets(edges, known_map_ids(db.conn, renderable_only=True)) == [
            resolved,
            dangling,
        ]
        assert unresolved_targets(edges, known | {"1287"}) == []
    finally:
        db.close()


def test_closure_converges_and_survives_cycles():
    edges = [
        Edge("A", "B", POINT_JUMP, discovery_source="point_payload"),
        Edge("B", "A", RETURN, discovery_source="navigation_stack"),
        Edge("B", "C", POINT_JUMP, discovery_source="point_payload"),
        Edge("C", "C", FLOOR, discovery_source="map_tree"),
        Edge("D", "E", RELATED_MAP, discovery_source="map_info"),
    ]
    result = closure(edges, seeds=["A"])
    assert isinstance(result, ClosureResult)
    assert result.visited == ("A", "B", "C")
    assert result.frontier == ()
    assert result.converged is True
    assert result.unevidenced == ()
    # cycle 是合法的（§十八）：A→B→A 与自环都被记下来，但不会再展开一次
    assert set(result.cycles) == {("B", "A"), ("C", "C")}
    assert result.steps == 4
    assert [edge.target_map_id for edge in result.traversed] == ["B", "A", "C", "C"]
    # 与种子无关的孤岛不许被卷进来
    assert "D" not in result.visited
    assert "E" not in result.visited


def test_closure_expand_is_the_m7_2_hook():
    fetched: list[str] = []

    def expand(map_id: str) -> list[Edge] | None:
        fetched.append(map_id)
        if map_id == "1":
            return [Edge("1", "2", POINT_JUMP, discovery_source="point_payload")]
        if map_id == "2":
            return [
                Edge("2", "1", RETURN, discovery_source="navigation_stack"),
                Edge("2", "3", UNKNOWN_TRANSITION, discovery_source="point_payload"),
            ]
        return None  # 拿不到 3 的证据

    result = closure(seeds=["1"], expand=expand)
    assert fetched == ["1", "2", "3"]  # 每个地图只展开一次：cycle 不会重复拉
    assert result.visited == ("1", "2", "3")
    assert result.unevidenced == ("3",)
    assert result.converged is False  # 拿不到证据就不假装收敛
    assert set(result.cycles) == {("2", "1")}


def test_closure_expand_must_return_edges_of_that_map():
    def expand(map_id: str) -> list[Edge]:
        return [Edge("other", "target", POINT_JUMP, discovery_source="point_payload")]

    with pytest.raises(ValueError):
        closure(seeds=["1"], expand=expand)


def test_closure_of_empty_frontier_is_converged():
    result = closure([], seeds=[])
    assert result.visited == ()
    assert result.converged is True
    assert result.steps == 0


# --------------------------------------------------------------------------- #
# 可渲染探测
# --------------------------------------------------------------------------- #


def test_probe_states_are_three_not_two():
    unknown = probe_renderable(RenderEvidence("1287"))
    assert unknown.state == UNKNOWN
    assert unknown.renderable is None
    assert unknown.column_value is None  # 「没问过」不许落成 0
    assert unknown.reasons

    failed = probe_renderable(RenderEvidence("1287", map_info_present=False))
    assert failed.state == INVALID
    assert failed.renderable is False
    assert failed.column_value == 0

    no_raster = probe_renderable(RenderEvidence("1287", map_info_present=True, raster_fragment_count=0))
    assert no_raster.state == INVALID

    without_url = probe_renderable(
        RenderEvidence("1287", map_info_present=True, raster_fragment_count=1, raster_fragments_with_url=0)
    )
    assert without_url.state == INVALID

    valid = probe_renderable(
        RenderEvidence(
            "1287",
            map_info_present=True,
            raster_fragment_count=2,
            raster_fragments_with_url=2,
            canvas=(8192.0, 4096.0),
        )
    )
    assert valid.state == VALID
    assert valid.renderable is True
    assert valid.column_value == 1
    assert valid.reasons

    with pytest.raises(ValueError):
        RenderProbe("1287", "MAYBE")


def test_tree_map_candidates_is_a_hint_not_a_verdict():
    nodes = flatten_map_nodes(_sample_tree())
    candidates = tree_map_candidates(nodes)
    assert [node["source_id"] for node in candidates] == ["10", "11"]
    assert TREE_MAP_NODE_TYPE == 2
    # 候选是从官方树来的结构提示；可渲染判定这时还必须是 UNKNOWN
    assert all(node["is_renderable"] is None for node in candidates)
    assert all(node["discovery_method"] == DISCOVERY_TREE for node in nodes)


def test_refresh_render_probes_writes_back_and_keeps_unknown_null(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        db.insert_map_nodes(flatten_map_nodes(_sample_tree()))
        db.insert_map(_mapped_map("10"))
        probes = refresh_render_probes(db.conn, ["1", "10", "11"])
        assert probes["10"].state == VALID
        assert probes["11"].state == UNKNOWN  # map/info 没落库
        assert probes["1"].state == UNKNOWN
        rows = {row[0]: row[1] for row in db.conn.execute("SELECT source_id, is_renderable FROM map_nodes")}
        assert rows["10"] == 1
        assert rows["11"] is None
        assert rows["1"] is None
        states = {row[0]: row[1] for row in db.conn.execute("SELECT source_id, render_probe_state FROM map_nodes")}
        assert states["10"] == VALID
        assert states["11"] == UNKNOWN
        # 幂等：再跑一次结论不变、计数不变
        again = refresh_render_probes(db.conn, ["1", "10", "11"])
        assert {key: value.state for key, value in again.items()} == {
            key: value.state for key, value in probes.items()
        }
        assert db.conn.execute("SELECT COUNT(*) FROM map_nodes WHERE is_renderable = 1").fetchone()[0] == 1
    finally:
        db.close()


def test_evidence_from_db_reports_absence_as_unknown_not_false(tmp_path):
    db = CoreDatabase(tmp_path / "core.db")
    try:
        db.insert_map_nodes(flatten_map_nodes(_sample_tree()))
        db.insert_map(_mapped_map("10", fragments=0))
        evidence = evidence_from_db(db.conn, ["10", "11"])
        assert evidence["10"].map_info_present is True
        assert evidence["11"].map_info_present is None
        probes = probe_map_renderability(db.conn, ["10", "11"])
        assert probes["10"].state == INVALID  # map/info 落库了，但 raster spec 没有 fragment
        assert probes["11"].state == UNKNOWN
        assert probe_map_renderability(db.conn) == {"10": probes["10"]}
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 旧库兼容（M7.1 硬约束）
# --------------------------------------------------------------------------- #


def test_legacy_core_db_without_new_tables_or_columns_still_reads(tmp_path):
    path = legacy_core_db(tmp_path)
    db = CoreDatabase(path, readonly=True)
    try:
        # 老库没有新列
        columns = table_columns(db.conn, "map_nodes")
        assert "tree_leaf" not in columns
        assert "render_probe_state" not in columns
        # 老库没有新表：那是「还没有边」，不是错误
        assert not has_table(db.conn, "map_edges")
        assert not has_table(db.conn, "point_transitions")
        assert load_edges(db.conn) == []
        assert load_point_transitions(db.conn) == []
        assert db.counts()["map_edges"] == 0
        assert db.counts()["point_transitions"] == 0

        # is_renderable 沿用落库值（2 张），回退到老的树叶子语义并如实标注
        nodes = {node["source_id"]: node for node in load_map_nodes(db.conn)}
        assert nodes["10"]["is_renderable"] is True
        assert nodes["10"]["render_probe_state"] == LEGACY_TREE_HINT
        assert nodes["10"]["tree_leaf"] is True  # 老库没有 tree_leaf 列：按「有没有孩子」现算
        assert nodes["1"]["is_renderable"] is False
        assert nodes["1"]["tree_leaf"] is False  # 节点 1 有孩子
        assert sum(1 for node in nodes.values() if node["is_renderable"]) == 2

        # 只读探测：能读老库、不写库；没证据的 id 是 UNKNOWN
        probes = probe_map_renderability(db.conn)
        assert {probe.state for probe in probes.values()} == {VALID}
        assert probe_map_renderability(db.conn, ["404"])["404"].state == UNKNOWN
        assert known_map_ids(db.conn) == {"1", "10", "11"}
    finally:
        db.close()


def test_write_back_refuses_a_legacy_schema_loudly(tmp_path):
    """老库没有探测列时，写回必须响亮地失败：静默跳过等于「假装探测过了」。"""
    path = legacy_core_db(tmp_path)
    conn = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            refresh_render_probes(conn, ["10"])
    finally:
        conn.close()


def test_legacy_core_db_migrates_additively_without_rewriting_values(tmp_path):
    path = legacy_core_db(tmp_path)
    db = CoreDatabase(path)
    try:
        assert table_columns(db.conn, "map_nodes") >= {"tree_leaf", "render_probe_state", "discovery_method"}
        assert has_table(db.conn, "map_edges")
        assert has_table(db.conn, "point_transitions")
        assert int(db.conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        # 老行一个不少、老值一个不改
        assert db.conn.execute("SELECT COUNT(*) FROM map_nodes").fetchone()[0] == 3
        assert db.conn.execute("SELECT COUNT(*) FROM map_nodes WHERE is_renderable = 1").fetchone()[0] == 2
        assert db.conn.execute("SELECT COUNT(*) FROM map_nodes WHERE is_renderable = 0").fetchone()[0] == 1
        # 新列全是 NULL：迁移不编造探测结论（不许把老库的猜测冒充成探测结果）
        assert db.conn.execute("SELECT COUNT(*) FROM map_nodes WHERE tree_leaf IS NOT NULL").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM map_nodes WHERE render_probe_state IS NOT NULL").fetchone()[0] == 0
    finally:
        db.close()

    # 迁移幂等：再开一次不炸、值不变
    again = CoreDatabase(path)
    try:
        assert again.conn.execute("SELECT COUNT(*) FROM map_nodes WHERE is_renderable = 1").fetchone()[0] == 2
        assert again.conn.execute("SELECT COUNT(*) FROM map_nodes").fetchone()[0] == 3
        assert table_columns(again.conn, "map_nodes") >= {"tree_leaf", "render_probe_state", "discovery_method"}
    finally:
        again.close()


# --------------------------------------------------------------------------- #
# 真实快照：624 不许变（需要 data/，--run-data-e2e）
# --------------------------------------------------------------------------- #


@pytest.mark.data
def test_snapshot_still_reports_exactly_624_renderable_maps(snapshot_data):
    current = json.loads((snapshot_data / "current.json").read_text(encoding="utf-8"))
    core_path = snapshot_data / "snapshots" / current["snapshot_id"] / "core.db"
    db = CoreDatabase(core_path, readonly=True, immutable=True)
    try:
        # M7.1 不动快照：老库里的 624 还是 624（新列/新表在这一版库里根本不存在）
        assert db.conn.execute("SELECT COUNT(*) FROM map_nodes WHERE is_renderable = 1").fetchone()[0] == 624
        assert db.conn.execute("SELECT COUNT(*) FROM maps").fetchone()[0] == 624
        assert not has_table(db.conn, "map_edges")
        assert not has_table(db.conn, "point_transitions")
        assert load_edges(db.conn) == []

        nodes = load_map_nodes(db.conn)
        assert len(nodes) == 923
        renderable = [node for node in nodes if node["is_renderable"] is True]
        assert len(renderable) == 624
        assert {node["render_probe_state"] for node in renderable} == {LEGACY_TREE_HINT}
        assert not any(node["is_renderable"] is None for node in nodes)  # 老库的值原样读出来

        # 只读探测：624 张 maps 全部 VALID；树里的目录节点没有 map/info 证据 → UNKNOWN
        probes = probe_map_renderability(db.conn)
        assert len(probes) == 624
        assert {probe.state for probe in probes.values()} == {VALID}
        folder = next(node["source_id"] for node in nodes if node["is_renderable"] is False)
        assert probe_map_renderability(db.conn, [folder])[folder].state == UNKNOWN
    finally:
        db.close()
