"""M7.3 Deep Sync（a1-8-1 §六 / §七 / §九 / §十五 / §二十四）。

三件事各自钉住：

1. **发布门禁**：可导航边的 target 不在已同步的可渲染集合里 → 本次 sync **不许发布**，
   退出码 2，current.json 一个字节都不动；结构边（TREE_CHILD / RELATED_MAP / MAP_GROUP）
   的容器目标**不算违规**（runbook §6.3 口径）。
2. **真名**：容器的 children[].name 写进 maps.display_name + name_source，
   **不覆盖** map_nodes.name、也不覆盖 maps.name；老库加性迁移、绝不回算。
3. **闭包诚实**：expand 拿不到证据就记 unevidenced、converged=False，不假装收敛。

全部在 tmp_path 上构造：不发网络、不碰真实快照、不碰 data/graph/core.db。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

from hsrmap.database import CoreDatabase, table_columns
from hsrmap.graph import POINT_JUMP, RELATED_MAP, TREE_CHILD, Edge, closure, save_edges
from hsrmap.sync import (
    GATE_FAILURE,
    NAME_SOURCE_CHILDREN,
    NAME_SOURCE_OWN,
    CoreSync,
)


def _sync(tmp_path: Path) -> CoreSync:
    """一个绑在临时 staging 根上的 CoreSync：只用到本地判定，永远不发请求。"""
    root = tmp_path / "staging"
    root.mkdir(parents=True, exist_ok=True)
    sync = CoreSync()
    sync.root = root
    sync.snapshot_id = root.name
    sync.status = {"snapshot_id": root.name}
    sync.db = CoreDatabase(root / "core.db")
    sync.log = logging.getLogger("test-deep-sync")
    sync.started_at = time.monotonic()
    sync.graph_report = {}
    sync.name_report = {}
    return sync


def _insert_node(sync: CoreSync, source_id: str, parent: str | None, name: str, *, node_type: int = 2) -> None:
    sync.db.conn.execute(
        "INSERT OR REPLACE INTO map_nodes"
        "(source_id, parent_source_id, node_type, name, depth, sort_order, tree_leaf, is_renderable)"
        " VALUES (?, ?, ?, ?, 1, 0, 1, 1)",
        (source_id, parent, node_type, name),
    )
    sync.db.conn.commit()


def _insert_map(sync: CoreSync, source_id: str, name: str, *, fragment: bool = True) -> None:
    cur = sync.db.conn.execute(
        "INSERT OR REPLACE INTO maps (source_id, name, canvas_width, canvas_height, fragment_count)"
        " VALUES (?, ?, 100, 100, 1)",
        (source_id, name),
    )
    if fragment:
        sync.db.conn.execute(
            "INSERT INTO map_fragments (map_id, source_index, remote_url, asset_sha256, source_width, source_height)"
            " VALUES (?, 0, ?, 'sha', 100, 100)",
            (cur.lastrowid, f"https://example.invalid/{source_id}.png"),
        )
    sync.db.conn.commit()


def _write_map_info(root: Path, map_id: str, *, name: str, children: tuple[tuple[str, str], ...] = ()) -> None:
    payload = {
        "retcode": 0,
        "message": "OK",
        "data": {
            "info": {
                "id": int(map_id),
                "name": name,
                "node_type": 1 if children else 2,
                "children": [{"id": int(child_id), "name": child_name} for child_id, child_name in children],
            }
        },
    }
    path = root / "raw" / "map_info" / f"{map_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1) §二十四 发布门禁
# --------------------------------------------------------------------------- #


def test_navigable_edge_into_a_missing_map_fails_the_gate(tmp_path) -> None:
    sync = _sync(tmp_path)
    _insert_node(sync, "943", None, "2层")
    _insert_map(sync, "943", "2层")
    save_edges(sync.db.conn, [Edge(
        source_map_id="943", target_map_id="979", edge_type=POINT_JUMP,
        source_point_id="5637", discovery_source="point_list",
    )])
    gate = sync._map_graph_gate()
    assert gate["ok"] is False
    assert gate["violations_total"] == 1
    assert gate["violations"][0]["target_map_id"] == "979"
    assert any("invariant" in reason for reason in gate["reasons"])
    #: 门禁报告落在 staging 里，别只活在内存里。
    written = json.loads((sync.root / "reports" / "map-graph-gate.json").read_text(encoding="utf-8"))
    assert written["violations"][0]["target_map_id"] == "979"


def test_structural_edges_into_containers_are_not_violations(tmp_path) -> None:
    """§6.3 口径：TREE_CHILD / RELATED_MAP / MAP_GROUP 的目标可以是容器，不算违规。"""
    sync = _sync(tmp_path)
    _insert_node(sync, "938", None, "千星城")
    _insert_node(sync, "955", "938", "特殊房间", node_type=1)  # 容器：没有 maps 行
    _insert_map(sync, "938", "千星城")
    save_edges(sync.db.conn, [
        Edge(source_map_id="938", target_map_id="955", edge_type=TREE_CHILD, discovery_source="map_tree"),
        Edge(source_map_id="938", target_map_id="955", edge_type=RELATED_MAP, discovery_source="map_tree"),
    ])
    gate = sync._map_graph_gate()
    assert gate["violations_total"] == 0
    assert gate["navigable_edges"] == 0
    assert gate["structural_edge_types"] == ["RELATED_MAP", "TREE_CHILD"]


def _point_pointer_at(tmp_path, monkeypatch):
    """把 sync 的 current.json 指针搬到 tmp_path 上。

    **绝不允许**在任何测试里写真实的运行目录指针（曾经发生过一次真实事故：同步进行中
    data/current.json 被某条测试写成哨兵值，所有并发读者都读到了不存在的快照）。
    这里 monkeypatch 的是 hsrmap.sync 的模块级名字，测试进程碰不到真实 data/。
    """
    pointer = tmp_path / "pointer" / "current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"snapshot_id": "OLD"}), encoding="utf-8")
    monkeypatch.setattr("hsrmap.sync.CURRENT_PATH", pointer)
    return pointer


def test_release_gate_failure_does_not_switch_current_json(tmp_path, monkeypatch) -> None:
    """门禁不通过 = 不发布：_publish 一次都不许被调用，指针原样。"""
    sync = _sync(tmp_path)
    pointer = _point_pointer_at(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(sync, "_publish", lambda: calls.append("publish") or {})
    reports = {
        "validation": {
            "passed": False,
            "failures": [GATE_FAILURE],
            "map_graph_gate": {"reasons": ["§二十四 invariant 违规：1 条"]},
        }
    }
    with pytest.raises(SystemExit) as exc:
        sync._decide_publication(reports)
    assert exc.value.code == 2
    assert calls == []
    assert json.loads(pointer.read_text(encoding="utf-8"))["snapshot_id"] == "OLD"
    assert json.loads((sync.root / "status.json").read_text(encoding="utf-8"))["state"] == "FAILED_VALIDATION"


def test_passing_gate_switches_the_pointer_to_the_new_snapshot(tmp_path, monkeypatch) -> None:
    """通过 → _publish 真的原子切换指针（tmp+replace），并且把 staging 移成快照目录。"""
    sync = _sync(tmp_path)
    pointer = _point_pointer_at(tmp_path, monkeypatch)
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr("hsrmap.sync.SNAPSHOTS", snapshots)
    published = sync._decide_publication({"validation": {"passed": True, "failures": [], "warnings": []}})
    assert published["snapshot_id"] == sync.snapshot_id
    assert json.loads(pointer.read_text(encoding="utf-8"))["snapshot_id"] == sync.snapshot_id
    assert (snapshots / sync.snapshot_id / "manifest.json").is_file()
    assert not (tmp_path / "staging").exists()


def test_other_validation_failure_exits_one(tmp_path, monkeypatch) -> None:
    sync = _sync(tmp_path)
    monkeypatch.setattr(sync, "_publish", lambda: pytest.fail("不许发布"))
    with pytest.raises(SystemExit) as exc:
        sync._decide_publication({"validation": {"passed": False, "failures": ["golden_points"]}})
    #: SystemExit("...") 就是退出码 1（Python 会把它打到 stderr）；只有门禁那条显式退 2。
    assert exc.value.code == "core validation failed"


# --------------------------------------------------------------------------- #
# 2) 真名（maps.display_name / name_source）
# --------------------------------------------------------------------------- #


def test_display_name_is_written_without_touching_tree_or_map_name(tmp_path) -> None:
    sync = _sync(tmp_path)
    _insert_node(sync, "979", "955", "")  # 树里没名字
    _insert_map(sync, "979", "")          # map/info 自己的 name 也是空
    updated = sync.db.update_map_names([{"source_id": "979", "display_name": "1", "name_source": NAME_SOURCE_CHILDREN}])
    assert updated == 1
    row = sync.db.conn.execute("SELECT name, display_name, name_source FROM maps WHERE source_id = '979'").fetchone()
    assert row["name"] == ""                      # maps.name 不动
    assert row["display_name"] == "1"
    assert row["name_source"] == NAME_SOURCE_CHILDREN
    assert sync.db.conn.execute("SELECT name FROM map_nodes WHERE source_id = '979'").fetchone()["name"] == ""
    #: 幂等 + 不在 maps 里的 id 不算数
    assert sync.db.update_map_names([{"source_id": "955", "display_name": "特殊房间", "name_source": NAME_SOURCE_CHILDREN}]) == 0
    assert sync.db.update_map_names([{"source_id": "979", "display_name": "", "name_source": NAME_SOURCE_CHILDREN}]) == 0


def test_children_name_wins_and_conflicts_are_counted(tmp_path) -> None:
    sync = _sync(tmp_path)
    _insert_node(sync, "955", None, "特殊房间", node_type=1)
    _insert_node(sync, "979", "955", "")
    _insert_node(sync, "980", "955", "旧名")
    _insert_map(sync, "979", "")
    _insert_map(sync, "980", "自称的名字")
    _write_map_info(sync.root, "955", name="千星城中心区7", children=(("979", "1"), ("980", "真名")))
    _write_map_info(sync.root, "979", name="")
    _write_map_info(sync.root, "980", name="自称的名字")
    sync.map_info_targets = ["955", "979", "980"]
    report = sync._apply_display_names()
    assert report["display_names_written"] == 2
    assert report["children_vs_own_conflicts_total"] == 1
    assert report["children_vs_own_conflicts"][0]["map_id"] == "980"
    assert report["before"]["unnamed_renderables"] == 1
    assert report["after"]["unnamed_renderables"] == 0
    rows = {row["source_id"]: row for row in sync.db.conn.execute("SELECT source_id, display_name, name_source FROM maps")}
    assert rows["979"]["display_name"] == "1"
    assert rows["980"]["display_name"] == "真名"
    assert rows["979"]["name_source"] == NAME_SOURCE_CHILDREN
    #: 没有 children 的图回退到自己 info.name，并如实标 name_source。
    sync2 = _sync(tmp_path / "second")
    _insert_node(sync2, "42", None, "")
    _insert_map(sync2, "42", "")
    _write_map_info(sync2.root, "42", name="42层")
    sync2.map_info_targets = ["42"]
    sync2._apply_display_names()
    row = sync2.db.conn.execute("SELECT display_name, name_source FROM maps WHERE source_id = '42'").fetchone()
    assert row["display_name"] == "42层"
    assert row["name_source"] == NAME_SOURCE_OWN


def test_legacy_core_db_gains_the_name_columns_additively(tmp_path) -> None:
    """v2 的老库（没有 display_name / name_source）打开后加列，值一个都不改。"""
    legacy = tmp_path / "legacy.db"
    import sqlite3

    conn = sqlite3.connect(legacy)
    conn.executescript(
        """
        CREATE TABLE maps (id INTEGER PRIMARY KEY, source_id TEXT NOT NULL UNIQUE, node_id INTEGER,
            name TEXT, canvas_width REAL, canvas_height REAL, origin_x REAL, origin_y REAL,
            padding_json TEXT, fragment_count INTEGER, map_info_sha256 TEXT, coordinate_transform TEXT);
        INSERT INTO maps(source_id, name, fragment_count) VALUES ('979', '', 1);
        """
    )
    conn.commit()
    conn.close()
    db = CoreDatabase(legacy)
    try:
        assert {"display_name", "name_source"} <= table_columns(db.conn, "maps")
        row = db.conn.execute("SELECT name, display_name, name_source FROM maps WHERE source_id = '979'").fetchone()
        assert row["name"] == ""
        assert row["display_name"] is None  #: 老库不许编一个真名出来
        assert row["name_source"] is None
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 3) §六 闭包：拿不到证据就如实说
# --------------------------------------------------------------------------- #


def test_expand_returns_none_when_there_is_no_evidence(tmp_path) -> None:
    sync = _sync(tmp_path)
    sync.host = None  # 没有网络通道 = 下探也拿不到证据
    assert sync._expand_map("1287") is None
    result = closure([Edge(source_map_id="943", target_map_id="1287", edge_type=POINT_JUMP,
                           source_point_id="1", discovery_source="point_list")],
                     seeds=["943"], expand=lambda map_id: None)
    #: 入口图与它发现的目标都没有证据 → 两个都记 unevidenced，converged 必须是 False。
    assert result.unevidenced == ("943", "1287")
    assert result.converged is False
    assert result.frontier == ()


def test_expand_reads_the_local_payload(tmp_path) -> None:
    sync = _sync(tmp_path)
    sync.host = None
    _write_map_info(sync.root, "955", name="千星城中心区7", children=(("979", "1"),))
    (sync.root / "raw" / "point_list").mkdir(parents=True, exist_ok=True)
    (sync.root / "raw" / "point_list" / "955.json").write_text(
        json.dumps({"retcode": 0, "data": {"point_list": [{"id": 5637, "label_id": 836, "related_jump_id": "979"}]}}),
        encoding="utf-8",
    )
    edges = sync._expand_map("955")
    assert [(edge.edge_type, edge.target_map_id) for edge in edges] == [(POINT_JUMP, "979")]
    assert all(edge.source_map_id == "955" for edge in edges)


def test_payload_with_a_nonzero_retcode_is_not_evidence(tmp_path) -> None:
    sync = _sync(tmp_path)
    path = sync.root / "raw" / "map_info" / "979.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"retcode": -502001, "message": "no"}), encoding="utf-8")
    assert sync._payload("map_info", "979") is None
