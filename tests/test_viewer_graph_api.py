"""M7.4 Graph API（a1-8-1 §十 / §十一 / §十三 / §十四）。

三件事必须在**没有真实快照**的地方也能验：

1. **读取优先级**（runbook §6.2）：core.db 有 map_edges 就用它 → 否则旁挂库
   data/graph/core.db → 都没有就如实报「没有跳转信息」；
2. **只读**：读一个不存在的图谱库**不产生任何文件**，快照 core.db 一个字节都不变；
3. **深层地图不进 tree**（§十）：跳转只通过 transitions / point.transition 表达。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hsrmap.database import CoreDatabase
from hsrmap.graph import POINT_JUMP, TREE_CHILD, Edge, PointTransition, save_edges, save_point_transitions
from hsrmap.viewer_app import create_map_app

SNAPSHOT = "TESTSNAP"
ROOT_MAP = "100"
ENTRY_MAP = "200"
DEEP_MAP = "900"
ENTRY_POINT_SOURCE = "5637"
DEEP_POINT_SOURCE = "5593"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_snapshot(path: Path) -> None:
    """最小地图宇宙：树 100 → 200，已同步地图 200 + 深层图 900（900 **不在树里**）。"""
    db = CoreDatabase(path)
    conn = db.conn
    conn.execute(
        "INSERT INTO map_nodes (source_id, parent_source_id, node_type, name, depth, sort_order, is_renderable)"
        " VALUES (?, NULL, 1, ?, 0, 0, 0)",
        (ROOT_MAP, "测试区域"),
    )
    conn.execute(
        "INSERT INTO map_nodes (source_id, parent_source_id, node_type, name, depth, sort_order, is_renderable)"
        " VALUES (?, ?, 2, ?, 1, 1, 1)",
        (ENTRY_MAP, ROOT_MAP, "2层"),
    )
    for source_id, name in ((ENTRY_MAP, "2层"), (DEEP_MAP, "JUMP内部")):
        conn.execute(
            "INSERT INTO maps (source_id, name, canvas_width, canvas_height, origin_x, origin_y, fragment_count)"
            " VALUES (?, ?, 100, 100, 0, 0, 1)",
            (source_id, name),
        )
        map_pk = conn.execute("SELECT id FROM maps WHERE source_id = ?", (source_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO map_fragments (map_id, source_index, remote_url, asset_sha256, source_width, source_height)"
            " VALUES (?, 0, ?, ?, 100, 100)",
            (map_pk, f"https://example.invalid/{source_id}.png", "a" * 64),
        )
    for source_id, map_source in ((ENTRY_POINT_SOURCE, ENTRY_MAP), (DEEP_POINT_SOURCE, DEEP_MAP)):
        map_pk = conn.execute("SELECT id FROM maps WHERE source_id = ?", (map_source,)).fetchone()[0]
        conn.execute(
            "INSERT INTO points (source_id, map_id, x_pos, y_pos, raster_x, raster_y) VALUES (?, ?, 1.0, 2.0, 1.0, 2.0)",
            (source_id, map_pk),
        )
    conn.commit()
    db.close()


def _write_edges(path: Path) -> None:
    """把图写进这个库：TREE_CHILD 100→200 + POINT_JUMP 200→900 + 一条点位跳转。"""
    db = CoreDatabase(path)
    conn = db.conn
    point_pk = conn.execute("SELECT id FROM points WHERE source_id = ?", (ENTRY_POINT_SOURCE,)).fetchone()[0]
    save_edges(
        conn,
        [
            Edge(source_map_id=ROOT_MAP, target_map_id=ENTRY_MAP, edge_type=TREE_CHILD, discovery_source="map_tree"),
            Edge(
                source_map_id=ENTRY_MAP,
                target_map_id=DEEP_MAP,
                edge_type=POINT_JUMP,
                source_point_id=ENTRY_POINT_SOURCE,
                discovery_source="point_list",
            ),
        ],
    )
    save_point_transitions(
        conn,
        [
            PointTransition(
                point_id=int(point_pk),
                target_map_source_id=DEEP_MAP,
                transition_type=POINT_JUMP,
                action_label="前往对应地图",
            )
        ],
    )
    db.close()


def _drop_graph_tables(path: Path) -> None:
    """把库退回 M7.1 之前的样子（老快照没有 map_edges / point_transitions）。

    用原生 sqlite3：CoreDatabase 的写模式会顺带把 v2 schema 建回来，那正是要避免的。
    """
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE IF EXISTS map_edges")
    conn.execute("DROP TABLE IF EXISTS point_transitions")
    conn.commit()
    conn.close()


def build_world(tmp_path: Path, *, edges_in_core: bool, with_derived_graph: bool = False) -> Path:
    """造一个运行时目录，返回它的 data/ 根。

    - edges_in_core=True  → 快照自己带边（M7.1 之后的新快照，第一优先级）；
    - with_derived_graph  → 快照没有边，边在 data/graph/core.db（M7.2 的回填产物，第二优先级）。
    """
    data = tmp_path / "data"
    snapshot_dir = data / "snapshots" / SNAPSHOT
    snapshot_dir.mkdir(parents=True)
    (data / "current.json").write_text(
        json.dumps({"snapshot_id": SNAPSHOT, "path": f"snapshots/{SNAPSHOT}", "core_db": f"snapshots/{SNAPSHOT}/core.db"}),
        encoding="utf-8",
    )
    core = snapshot_dir / "core.db"
    _seed_snapshot(core)
    if edges_in_core:
        _write_edges(core)
    if with_derived_graph:
        _write_edges(core)
        derived = data / "graph" / "core.db"
        derived.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(core, derived)
        _drop_graph_tables(core)
    if not edges_in_core and not with_derived_graph:
        _drop_graph_tables(core)
    return data


@pytest.fixture
def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _make(data: Path) -> TestClient:
        #: 运行目录按调用时解析（a1-8 四.2）：这里把它指到测试自己的 data/。
        monkeypatch.setenv("HSRMAP_DATA_DIR", str(data))
        return TestClient(create_map_app(user_path=tmp_path / "user.db", live_cache=tmp_path / "live-cache"))

    return _make


def point_pk(data: Path, source_id: str) -> int:
    conn = sqlite3.connect(data / "snapshots" / SNAPSHOT / "core.db")
    try:
        return int(conn.execute("SELECT id FROM points WHERE source_id = ?", (source_id,)).fetchone()[0])
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# ① 降级：没有图库 → 没有跳转信息，页面照常
# --------------------------------------------------------------------------- #


def test_graph_api_degrades_without_any_graph_data(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=False)
    client = make_client(data)

    body = client.get("/api/v1/map/graph").json()
    assert body["available"] is False
    assert body["message"] == "没有跳转信息"
    assert body["edges"] == []
    assert body["audit"]["edges_total"] == 0
    assert body["counts"]["edges"] == 0
    assert body["source"]["origin"] == "none"
    assert body["source"]["available"] is False
    #: 节点照旧（降级的是「跳转」，不是地图本身）：树 100/200 + 树外深层图 900
    assert [node["id"] for node in body["nodes"]] == [ROOT_MAP, ENTRY_MAP, DEEP_MAP]
    assert [node["in_tree"] for node in body["nodes"]] == [True, True, False]

    transitions = client.get(f"/api/v1/maps/{ENTRY_MAP}/transitions").json()
    assert transitions["available"] is False
    assert transitions["transitions"] == []
    assert transitions["message"] == "没有跳转信息"
    assert transitions["known"] is True
    #: §十一：没有图库时导航上下文退化成树路径，不是空
    assert transitions["navigation"]["navigation_kind"] == "tree"
    assert transitions["navigation"]["navigation_path"] == ["测试区域", "2层"]


def test_point_detail_has_no_transition_without_graph_data(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=False)
    client = make_client(data)
    body = client.get(f"/api/v1/points/{point_pk(data, ENTRY_POINT_SOURCE)}").json()
    assert body["transition"] is None
    assert body["transition_targets"] == []


# --------------------------------------------------------------------------- #
# ② 有图：core.db 自带 → 用它；否则旁挂库
# --------------------------------------------------------------------------- #


def test_graph_api_reads_edges_from_core_snapshot(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=True)
    body = make_client(data).get("/api/v1/map/graph").json()
    assert body["available"] is True
    assert body["source"]["origin"] == "core.db"
    assert body["counts"] == {
        "nodes": 3,
        "tree_nodes": 2,
        "deep_maps": 1,
        "renderable_maps": 2,
        "maps": 2,
        "edges": 2,
        "point_transitions": 1,
    }
    assert {edge["edge_type"] for edge in body["edges"]} == {TREE_CHILD, POINT_JUMP}
    audit = body["audit"]
    #: §6.3：结构类与导航类**分开**统计
    assert audit["structural"]["edges"] == 1
    assert audit["structural"]["edge_types"] == ["FLOOR", "MAP_GROUP", "RELATED_MAP", "TREE_CHILD"]
    assert audit["navigable"]["edges"] == 1
    assert audit["navigable"]["edge_types"] == ["POINT_JUMP", "PORTAL", "RETURN", "UNKNOWN_TRANSITION"]
    assert audit["navigable"]["missing_total"] == 0
    assert audit["unresolved_targets"] == {"total": 0, "navigable_total": 0, "sample": []}
    assert audit["orphan_renderables"]["total"] == 0
    assert audit["deep_maps"] == 1
    assert audit["closure"]["converged"] is True
    assert audit["gate"]["ok"] is True


def test_graph_api_falls_back_to_derived_graph_db(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=False, with_derived_graph=True)
    body = make_client(data).get("/api/v1/map/graph").json()
    assert body["available"] is True
    assert body["source"]["origin"] == "graph/core.db"
    assert body["source"]["file"] == "core.db"
    assert body["counts"]["edges"] == 2
    assert body["message"] is None
    #: 快照自己没有边：这一份是旁挂库给的
    assert (data / "graph" / "core.db").is_file()


def test_graph_db_path_matches_backfill_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旁挂库路径只有一个口径：与 M7.2 的回填输出一致（写入口在 graph_backfill）。"""
    from hsrmap.graph_backfill import default_out_path
    from hsrmap.viewer_repo import graph_db_path

    monkeypatch.setenv("HSRMAP_DATA_DIR", str(tmp_path / "data"))
    assert graph_db_path() == default_out_path()


# --------------------------------------------------------------------------- #
# ③ transitions / point.transition（§十三 / §十四）
# --------------------------------------------------------------------------- #


def test_map_transitions_lists_targets(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=True)
    body = make_client(data).get(f"/api/v1/maps/{ENTRY_MAP}/transitions").json()
    assert body["available"] is True
    assert body["known"] is True
    assert body["counts"] == {"total": 1, "navigable": 1, "renderable_targets": 1}
    entry = body["transitions"][0]
    assert entry == {
        "type": POINT_JUMP,
        "target_map_id": DEEP_MAP,
        "target_name": "JUMP内部",
        "action": "前往对应地图",
        "source_point_id": ENTRY_POINT_SOURCE,
        "renderable": True,
        "navigable": True,
        "discovery_source": "point_list",
        "confidence": 1.0,
    }
    #: 树边不在这里重复（§十：父子关系走 /api/v1/maps/tree）
    assert all(item["type"] != TREE_CHILD for item in body["transitions"])


def test_deep_map_navigation_context_is_deep(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=True)
    body = make_client(data).get(f"/api/v1/maps/{DEEP_MAP}/transitions").json()
    navigation = body["navigation"]
    assert navigation["navigation_kind"] == "deep"
    assert navigation["entry_map_id"] == ENTRY_MAP
    assert navigation["entry_point_id"] == ENTRY_POINT_SOURCE
    assert navigation["navigation_path"] == ["测试区域", "2层", "JUMP内部"]
    #: 树路径与导航路径是两条（§十一）
    assert navigation["tree_path"] == ["JUMP内部"]
    assert body["transitions"] == []


def test_point_detail_carries_transition(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=True)
    client = make_client(data)
    body = client.get(f"/api/v1/points/{point_pk(data, ENTRY_POINT_SOURCE)}").json()
    #: §十三：恰好是 PointTransition.as_viewer() 的形状
    assert body["transition"] == {"type": POINT_JUMP, "target_map_id": DEEP_MAP, "action": "前往对应地图"}
    assert body["transition_targets"][0]["renderable"] is True
    assert body["transition_targets"][0]["name"] == "JUMP内部"
    assert body["navigation"]["navigation_kind"] == "tree"
    #: 深层图里的点没有跳转
    other = client.get(f"/api/v1/points/{point_pk(data, DEEP_POINT_SOURCE)}").json()
    assert other["transition"] is None
    assert other["navigation"]["navigation_kind"] == "deep"
    assert other["navigation"]["entry_map_id"] == ENTRY_MAP


def test_derived_point_transitions_only_for_matching_point(tmp_path: Path, make_client) -> None:
    """旁挂库和快照的 points.id 必须指同一个点：错位时宁可不说（降级，不报假边）。"""
    data = build_world(tmp_path, edges_in_core=False, with_derived_graph=True)
    client = make_client(data)
    body = client.get(f"/api/v1/points/{point_pk(data, ENTRY_POINT_SOURCE)}").json()
    assert body["transition"]["target_map_id"] == DEEP_MAP
    #: 旁挂库里不存在的 core 主键 → 没有跳转，而不是拿到别人的
    assert client.get("/api/v1/points/999999").status_code == 404


# --------------------------------------------------------------------------- #
# ④ §十：深层地图不许被塞回 tree；§6.2：只读，不建库
# --------------------------------------------------------------------------- #


def test_deep_map_is_not_injected_into_tree(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=True)
    tree = make_client(data).get("/api/v1/maps/tree").json()
    assert [node["id"] for node in tree] == [ROOT_MAP]
    children = tree[0]["children"]
    assert [node["id"] for node in children] == [ENTRY_MAP]
    #: 900 是跳转目标，不是树的子节点
    assert DEEP_MAP not in json.dumps(tree)


def test_reading_missing_graph_db_creates_no_file(tmp_path: Path, make_client) -> None:
    data = build_world(tmp_path, edges_in_core=False)
    snapshot_dir = data / "snapshots" / SNAPSHOT
    core = snapshot_dir / "core.db"
    before_sha = _sha256(core)
    before_listing = sorted(str(path.relative_to(data)) for path in data.rglob("*"))
    client = make_client(data)
    for url in ("/api/v1/map/graph", f"/api/v1/maps/{ENTRY_MAP}/transitions", f"/api/v1/points/{point_pk(data, ENTRY_POINT_SOURCE)}"):
        assert client.get(url).status_code == 200
    assert not (data / "graph").exists(), "只读读取居然把 data/graph 建出来了"
    assert list(data.rglob("*.db")) == [core], "只读读取不该产生任何新的 .db"
    assert _sha256(core) == before_sha
    #: 快照目录里也不许多出 -journal / -wal 之类的残留
    assert sorted(str(path.relative_to(data)) for path in data.rglob("*")) == before_listing
