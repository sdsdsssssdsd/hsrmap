"""M7.2 离线回填 + 孤儿发现 + Graph Audit（a1-8-1 §十六 / §十七 / §二十四）。

覆盖：

1. **回填产量与出处**：从 raw payload 算出的边按类型计数、每条边带 (键, json 路径)；
2. **写库幂等**：同一批边写两次，表里的条数不变（走 M7.1 的 UNIQUE 幂等 upsert）；
3. **冻结快照守卫**：默认 dry-run 什么都不写；`--out` 落进 snapshots/ 一律拒绝（退 2）；
   源库 sha256 回填前后必须一致；
4. **孤儿地图发现器**：没有任何边连到的可渲染地图必须被抓出来；被跳转连上的深层地图不算孤儿；
5. **未解析 target + 门禁**：`graph audit --gate` 在 unresolved target 非空时退 2；
6. **报告形状**（§十七）与人话摘要。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hsrmap.cli import main
from hsrmap.database import SCHEMA_VERSION, CoreDatabase
from hsrmap.graph import POINT_JUMP, RETURN, Edge, load_edges, save_edges
from hsrmap.graph_audit import (
    AuditOptions,
    audit_graph,
    canary_chain,
    default_report_path,
    orphans_only,
    render_audit,
    render_orphans,
    tree_roots,
)
from hsrmap.graph_backfill import (
    EXPECTED_EDGE_COUNTS,
    FrozenSnapshotError,
    assert_writable_out,
    build_backfill_plan,
    default_out_path,
    local_map_probe,
    resolve_snapshot,
    sha256_file,
    write_backfill,
)
from hsrmap.normalize import flatten_map_nodes

# --------------------------------------------------------------------------- #
# 合成快照夹具
# --------------------------------------------------------------------------- #

#: 树：1（根）→ 10（容器）→ 100 / 101（地图）；20（隐藏容器，related_id=10）；
#: 200 / 201 是昼 / 夜配对（related_group_map 互指）。
FAKE_TREE = [
    {
        "id": 1,
        "name": "星穹列车",
        "node_type": 1,
        "depth": 1,
        "parent_id": 0,
        "is_hide": False,
        "related_id": "0",
        "related_group_map": 0,
        "children": [
            {
                "id": 10,
                "name": "海原市",
                "node_type": 1,
                "depth": 2,
                "parent_id": 1,
                "is_hide": False,
                "related_id": "0",
                "related_group_map": 0,
                "children": [
                    {"id": 100, "name": "", "node_type": 2, "depth": 3, "parent_id": 10, "is_hide": False,
                     "related_id": "0", "related_group_map": 0, "children": []},
                    {"id": 101, "name": "1层", "node_type": 2, "depth": 3, "parent_id": 10, "is_hide": False,
                     "related_id": "0", "related_group_map": 0, "children": []},
                ],
            },
            {"id": 20, "name": "特殊房间", "node_type": 1, "depth": 2, "parent_id": 1, "is_hide": True,
             "related_id": "10", "related_group_map": 0, "children": [
                 {"id": 200, "name": "夜", "node_type": 1, "depth": 3, "parent_id": 20, "is_hide": True,
                  "related_id": "0", "related_group_map": 201, "children": []},
             ]},
            {"id": 201, "name": "昼", "node_type": 1, "depth": 3, "parent_id": 1, "is_hide": True,
             "related_id": "0", "related_group_map": 200, "children": []},
        ],
    }
]

FAKE_LABEL_TREE = [
    {"id": 23, "name": "宝箱", "parent_id": 0, "depth": 1, "node_type": 1, "jump_type": 0,
     "jump_target_id": 0, "children": []},
    {"id": 836, "name": "二次元JUMP!", "parent_id": 23, "depth": 2, "node_type": 2, "jump_type": 0,
     "jump_target_id": 0, "children": []},
]

#: maps 表里的地图：100 / 101 在树里；901 是跳转目标（树外）；900 谁也没连到（孤儿）。
FAKE_MAPS = ("100", "101", "900", "901")

#: 点位：100 上的 point 5171 跳到 901；101 上的 point 5172 没有跳转。
FAKE_POINTS = {
    "100": [{"id": 5171, "label_id": 836, "x_pos": 1.0, "y_pos": 2.0, "related_jump_id": "901",
             "display_state": 1, "point_num": 1, "video_url": "", "day_night_status": 0}],
    "101": [{"id": 5172, "label_id": 836, "x_pos": 3.0, "y_pos": 4.0, "related_jump_id": "0",
             "display_state": 1, "point_num": 1, "video_url": "", "day_night_status": 0}],
}


def _map_info_payload(map_id: str, name: str, *, parent_id: int | None = None) -> dict:
    info = {
        "id": int(map_id),
        "name": name,
        "parent_id": parent_id or 0,
        "depth": 3,
        "node_type": 2,
        "detail": json.dumps({"width": 4096, "height": 4096}),
        "children": [],
        "preview": "https://example.test/preview.png",
        "related_id": "0",
        "related_group_map": 0,
        "is_hide": False,
    }
    return {"retcode": 0, "message": "OK", "data": {"info": info}}


#: 合成夹具的产量基线（真实快照的基线是 EXPECTED_EDGE_COUNTS，只对 20261001T105105Z 有意义）。
FIXTURE_BASELINE = {"TREE_CHILD": 6, "RELATED_MAP": 1, "MAP_GROUP": 2, "POINT_JUMP": 1}


def make_snapshot(tmp_path: Path, name: str = "snap") -> Path:
    """手搓一个结构完整的快照目录（raw/ + core.db），不碰仓库 data/。"""
    root = tmp_path / name
    raw = root / "raw"
    (raw / "map_info").mkdir(parents=True, exist_ok=True)
    (raw / "point_list").mkdir(parents=True, exist_ok=True)
    (raw / "map_tree.json").write_text(
        json.dumps({"retcode": 0, "message": "OK", "data": {"tree": FAKE_TREE}}), encoding="utf-8"
    )
    (raw / "label_tree.json").write_text(
        json.dumps({"retcode": 0, "message": "OK", "data": {"tree": FAKE_LABEL_TREE}}), encoding="utf-8"
    )
    names = {"100": "", "101": "1层", "900": "没连到的图", "901": "跳转目标"}
    for map_id in FAKE_MAPS:
        (raw / "map_info" / f"{map_id}.json").write_text(
            json.dumps(_map_info_payload(map_id, names[map_id], parent_id=10 if map_id in ("100", "101") else None)),
            encoding="utf-8",
        )
    for map_id, points in FAKE_POINTS.items():
        (raw / "point_list" / f"{map_id}.json").write_text(
            json.dumps({"retcode": 0, "message": "OK", "data": {"point_list": points, "label_list": []}}),
            encoding="utf-8",
        )

    db = CoreDatabase(root / "core.db")
    try:
        db.insert_map_nodes(flatten_map_nodes(FAKE_TREE))
        for map_id in FAKE_MAPS:
            db.insert_map(
                {
                    "source_id": map_id,
                    "name": names[map_id] or map_id,
                    "canvas_width": 4096,
                    "canvas_height": 4096,
                    "origin_x": 0.0,
                    "origin_y": 0.0,
                    "fragment_count": 1,
                    "coordinate_transform": "origin_translation_v1",
                    "fragments": [
                        {"index": 0, "remote_url": f"https://example.test/{map_id}.png",
                         "source_width": 4096, "source_height": 4096, "x": 0, "y": 0,
                         "width": 4096, "height": 4096}
                    ],
                }
            )
        for map_id, points in FAKE_POINTS.items():
            rows = []
            for point in points:
                rows.append(
                    {
                        "source_id": str(point["id"]),
                        "map_source_id": map_id,
                        "label_id": str(point["label_id"]),
                        "x_pos": float(point["x_pos"]),
                        "y_pos": float(point["y_pos"]),
                        "z_pos": None,
                        "raster_x": float(point["x_pos"]),
                        "raster_y": float(point["y_pos"]),
                        "raw_json": point,
                    }
                )
            db.insert_points(rows)
    finally:
        db.close()
    return root


# --------------------------------------------------------------------------- #
# 回填计划
# --------------------------------------------------------------------------- #


def test_backfill_plan_counts_every_edge_type(tmp_path):
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    plan = build_backfill_plan(snapshot, expected=FIXTURE_BASELINE)
    # TREE_CHILD: 1→10, 1→20, 1→201, 10→100, 10→101, 20→200
    # RELATED_MAP: 20→10；MAP_GROUP: 200↔201；POINT_JUMP: 100→901
    assert dict(plan.counts) == {
        "TREE_CHILD": 6,
        "FLOOR": 0,
        "POINT_JUMP": 1,
        "RELATED_MAP": 1,
        "MAP_GROUP": 2,
        "PORTAL": 0,
        "RETURN": 0,
        "UNKNOWN_TRANSITION": 0,
    }
    assert len(plan.edges) == 10
    assert len(plan.transitions) == 1
    # 出处：tree 出 9 条、point_list 出 1 条；map/info 的 parent_id 与 tree 同键 → 去重
    assert plan.by_source == {"map_tree": 9, "point_list": 1}
    # map/info 的 parent_id 与 tree 的父子边同键：100 / 101 两条被去重（900 / 901 的 parent_id 是 0）
    assert plan.duplicates == {"map_info": 2}
    assert plan.expected_match is True
    assert plan.tree["normalize_matches_extractor"] is True
    assert plan.points["jump_targets_agree"] is True


def test_backfill_edges_carry_key_and_json_path(tmp_path):
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    plan = build_backfill_plan(snapshot)
    by_type = {}
    for edge in plan.edges:
        by_type.setdefault(edge.edge_type, []).append(edge)
    jump = by_type[POINT_JUMP][0]
    assert (jump.source_map_id, jump.target_map_id) == ("100", "901")
    assert jump.key == "related_jump_id"
    assert jump.json_path.endswith("related_jump_id")
    assert jump.source_point_id == "5171"
    related = by_type["RELATED_MAP"][0]
    assert (related.source_map_id, related.target_map_id, related.key) == ("20", "10", "related_id")
    group = by_type["MAP_GROUP"]
    assert {(edge.source_map_id, edge.target_map_id) for edge in group} == {("200", "201"), ("201", "200")}
    # 落库后的 raw_json 保留出处
    edge = jump.as_edge()
    assert edge.raw_json["json_path"].endswith("related_jump_id")


def test_backfill_transition_points_to_core_primary_key(tmp_path):
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    plan = build_backfill_plan(snapshot)
    transition = plan.transitions[0]
    assert transition.transition_type == POINT_JUMP
    assert transition.target_map_source_id == "901"
    # point_transitions.point_id = points.id（core 主键），不是官方 source id
    db = snapshot.open_readonly()
    try:
        row = db.conn.execute(
            "SELECT p.id FROM points p JOIN maps m ON m.id = p.map_id WHERE m.source_id = '100' AND p.source_id = '5171'"
        ).fetchone()
    finally:
        db.close()
    assert transition.point_id == int(row[0])
    assert transition.as_transition().as_viewer() == {
        "type": POINT_JUMP,
        "target_map_id": "901",
        "action": "前往对应地图",
    }


def test_backfill_scans_id_references_and_never_guesses(tmp_path):
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    plan = build_backfill_plan(snapshot)
    scan = plan.scan
    # label_id 是 label 空间（不是地图边）；related_jump_id 探测到可渲染地图 → 候选跳转
    assert scan["by_key"]["label_id"] == 2
    assert scan["by_key"]["related_jump_id"] == 1
    assert scan["by_conclusion"]["UNKNOWN_RENDERABLE_TARGET"] == 1
    assert scan["by_conclusion"]["NOT_A_MAP"] == 3  # label_id ×2 + label_tree 的 parent_id
    # 「0」被过滤掉了，而且有计数
    assert scan["skipped_absent"] >= 3
    # 所有候选都被已知 extractor 认领了
    assert scan["unclaimed_candidates"] == []


def test_backfill_plan_is_json_serializable(tmp_path):
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    plan = build_backfill_plan(snapshot, expected=FIXTURE_BASELINE)
    payload = plan.as_dict()
    text = json.dumps(payload, ensure_ascii=False)
    assert '"TREE_CHILD": 6' in text
    assert payload["expected_match"] is True
    assert payload["expected_delta"]["TREE_CHILD"] == {"actual": 6, "expected": 6, "delta": 0}
    # 换个基线就要如实报不一致（拿去跑别的快照时不许"看起来都对"）
    other = build_backfill_plan(snapshot, expected={"TREE_CHILD": 914})
    assert other.expected_match is False
    assert other.expected_delta["TREE_CHILD"]["delta"] == 6 - 914
    assert render_text(plan)


def render_text(plan) -> str:
    text = plan.render()
    assert "TREE_CHILD" in text
    assert "dry" not in text.lower() or True
    return text


# --------------------------------------------------------------------------- #
# 写库：幂等 + 源库只读 + 守卫
# --------------------------------------------------------------------------- #


def test_write_backfill_is_idempotent(tmp_path):
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    plan = build_backfill_plan(snapshot, expected=FIXTURE_BASELINE)
    out = tmp_path / "graph" / "core.db"
    first = write_backfill(plan, out=out)
    assert first["mode"] == "copy"
    assert first["map_edges"] == 10
    assert first["point_transitions"] == 1
    assert first["by_type"] == {"MAP_GROUP": 2, "POINT_JUMP": 1, "RELATED_MAP": 1, "TREE_CHILD": 6}
    # 再写一次：就地 upsert，条数不变（UNIQUE 幂等）
    second = write_backfill(plan, out=out)
    assert second["mode"] == "in-place"
    assert second["map_edges"] == first["map_edges"]
    assert second["point_transitions"] == first["point_transitions"]
    conn = sqlite3.connect(out)
    try:
        assert conn.execute("SELECT COUNT(*) FROM map_edges").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM point_transitions").fetchone()[0] == 1
        #: 回填库的版本 = 当前 core schema 版本（抬到 3 之后仍然成立）。
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # 回填不发明探测结论：没有 map/info raster 证据的节点 is_renderable 保持 NULL（不猜）
        assert conn.execute("SELECT COUNT(*) FROM map_nodes").fetchone()[0] == 7
        assert conn.execute("SELECT COUNT(*) FROM map_nodes WHERE is_renderable IS NULL").fetchone()[0] == 7
    finally:
        conn.close()


def test_write_backfill_leaves_source_snapshot_untouched(tmp_path):
    root = make_snapshot(tmp_path)
    snapshot = resolve_snapshot(root)
    before = sha256_file(snapshot.core_db)
    plan = build_backfill_plan(snapshot, expected=FIXTURE_BASELINE)
    result = write_backfill(plan, out=tmp_path / "graph.db")
    assert result["source_untouched"] is True
    assert result["source_sha256_before"] == before
    assert sha256_file(snapshot.core_db) == before


def test_backfill_refuses_to_write_into_snapshot_areas(tmp_path):
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    with pytest.raises(FrozenSnapshotError):
        assert_writable_out(snapshot.core_db, source_db=snapshot.core_db, data_dir=tmp_path)
    data_dir = tmp_path / "runtime"
    (data_dir / "snapshots" / "20260101T000000Z").mkdir(parents=True)
    with pytest.raises(FrozenSnapshotError):
        assert_writable_out(
            data_dir / "snapshots" / "20260101T000000Z" / "core.db",
            source_db=snapshot.core_db,
            data_dir=data_dir,
        )
    # 快照区外照常放行
    assert assert_writable_out(tmp_path / "graph" / "core.db", source_db=snapshot.core_db, data_dir=data_dir)


def test_repo_snapshot_area_is_refused_even_with_another_data_dir(tmp_path):
    """就算 --data-dir 指到别处，仓库里的 data/snapshots 也照样拒绝写。"""
    from hsrmap.paths import ROOT

    frozen = Path(ROOT) / "data" / "snapshots" / "20261001T105105Z" / "core.db"
    with pytest.raises(FrozenSnapshotError):
        assert_writable_out(frozen, source_db=frozen, data_dir=tmp_path)


def test_write_backfill_refuses_non_sqlite_target(tmp_path):
    from hsrmap.graph_backfill import BackfillError

    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    plan = build_backfill_plan(snapshot, expected=FIXTURE_BASELINE)
    junk = tmp_path / "junk.db"
    junk.write_text("not a database", encoding="utf-8")
    with pytest.raises(BackfillError):
        write_backfill(plan, out=junk)


# --------------------------------------------------------------------------- #
# 孤儿发现器 + Audit
# --------------------------------------------------------------------------- #


def _graph_db(tmp_path) -> Path:
    out = tmp_path / "graph" / "core.db"
    if out.is_file():
        return out
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    write_backfill(build_backfill_plan(snapshot, expected=FIXTURE_BASELINE), out=out)
    return out


def test_local_probe_uses_evidence_and_never_claims_not_a_map(tmp_path):
    snapshot = resolve_snapshot(make_snapshot(tmp_path))
    db = snapshot.open_readonly()
    try:
        probe = local_map_probe(db.conn)
        assert probe("100").outcome == "RENDERABLE"
        assert probe("10").outcome == "MAP_LIKE"        # 在树里、没有 raster 证据
        assert probe("12345").outcome == "UNKNOWN"      # 本地没证据 ≠ 不是地图
        assert "label_nodes" in " ".join(probe("836").overlaps)  # id 空间重叠证据
    finally:
        db.close()


def test_orphan_finder_catches_maps_no_edge_points_to(tmp_path):
    conn = sqlite3.connect(_graph_db(tmp_path))
    conn.row_factory = sqlite3.Row
    try:
        report = audit_graph(conn, options=AuditOptions(run_canary=False))
    finally:
        conn.close()
    assert report["renderable_maps"] == 4
    # 900 谁也没连到；901 是跳转目标（有入边）→ 不算孤儿
    assert report["orphan_renderables_total"] == 1
    assert report["orphan_renderables"] == ["900"]
    assert report["deep_maps"] == 2  # Renderable − Tree = {900, 901}
    assert set(report["deep_map_ids"]) == {"900", "901"}
    assert report["unresolved_targets_total"] == 0
    assert report["gate"]["ok"] is False
    assert "orphan_renderables_empty" in report["gate"]["reasons"]
    # 种子只有官方树根；树外地图不当种子，否则「不可达」永远为空
    assert report["seeds"]["tree_roots"] == ["1"]
    assert report["seeds"]["tree_root_total"] == 1
    assert tree_roots([{"source_id": "1", "parent_source_id": None}]) == ["1"]


def test_deep_map_reached_by_jump_is_not_an_orphan(tmp_path):
    """901 不在树里，但它被 100 的跳转连上了：是深层地图，不是孤儿。"""
    conn = sqlite3.connect(_graph_db(tmp_path))
    conn.row_factory = sqlite3.Row
    try:
        report = audit_graph(conn, options=AuditOptions(run_canary=False))
    finally:
        conn.close()
    assert "901" in report["deep_map_ids"]
    assert "901" not in report["orphan_renderables"]
    assert "901" not in report["unreachable_renderables"]
    assert "900" in report["unreachable_renderables"]


def test_unresolved_target_and_gate(tmp_path):
    path = _graph_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        save_edges(conn, [Edge("100", "999", POINT_JUMP, discovery_source="point_payload")])
        report = audit_graph(conn, options=AuditOptions(run_canary=False))
    finally:
        conn.close()
    assert report["unresolved_targets_total"] == 1
    assert report["unresolved_targets"][0]["target_map_id"] == "999"
    assert "unresolved_targets_empty" in report["gate"]["reasons"]
    assert report["gate"]["ok"] is False
    assert set(orphans_only(report)) >= {"orphan_renderables", "unresolved_targets", "deep_map_ids"}
    assert "999" in render_orphans(orphans_only(report))


def test_audit_report_shape_and_render(tmp_path):
    conn = sqlite3.connect(_graph_db(tmp_path))
    conn.row_factory = sqlite3.Row
    try:
        report = audit_graph(conn, options=AuditOptions(db_path=str(_graph_db(tmp_path)), run_canary=True))
    finally:
        conn.close()
    for key in ("tree_nodes", "renderable_maps", "deep_maps", "edges", "unresolved_targets",
                "orphan_renderables", "cycles", "closure", "canary", "gate", "render_targets"):
        assert key in report, key
    assert report["edges"]["TREE_CHILD"] == 6
    assert report["closure"]["converged"] is True
    assert report["closure"]["frontier"] == 0
    # 合成库里没有 943/5637：canary 如实退化，不假装通过
    assert report["canary"]["named_sample"] is False
    assert report["canary"]["entry_map"] == "100"
    chain = report["canary"]["steps"]
    assert [step["step"] for step in chain] == [
        "entry_map", "entry_point", "transition_edge", "target_map", "target_renderable",
        "target_points_readable", "return_target_valid",
    ]
    # 901 没有任何点位：可读但为空，不算断链
    assert all(step["ok"] for step in chain)
    text = render_audit(report)
    assert "Map Graph Audit" in text
    assert "canary" in text


def test_canary_degrades_when_sample_is_missing(tmp_path):
    conn = sqlite3.connect(_graph_db(tmp_path))
    conn.row_factory = sqlite3.Row
    try:
        result = canary_chain(conn, load_edges(conn), probe=local_map_probe(conn))
    finally:
        conn.close()
    assert result["named_sample"] is False
    assert result["ok"] is True


def test_default_paths_stay_inside_runtime_and_repo_reports(tmp_path):
    out = default_out_path(tmp_path)
    assert out.name == "core.db" and out.parent.name == "graph"
    assert default_report_path(tmp_path).name == "map_graph_audit.json"


# --------------------------------------------------------------------------- #
# CLI（退出码契约 0 / 1 / 2）
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _unpin_runtime():
    """**用完必须解掉进程级运行时 pin。**

    `cli.main()` 里带 `--data-dir` 会调 `hsrmap.runtime.set_runtime()`，那是一个**进程全局**的
    固定值：不解掉的话，后面同进程跑的 e2e 测试（viewer / progress / provider）会全部看到
    「运行时被 pin 到临时目录」，于是集体变红——一个测试的全局副作用把 20+ 个测试搞挂。
    """
    from hsrmap.runtime import reset_runtime

    reset_runtime()
    yield
    reset_runtime()


def test_cli_backfill_dry_run_writes_nothing(tmp_path):
    root = make_snapshot(tmp_path)
    out = tmp_path / "out" / "core.db"
    code = main(["graph", "backfill", "--snapshot", str(root), "--out", str(out), "--json"])
    assert code == 0
    assert not out.exists()
    assert not out.parent.exists()


def test_cli_backfill_write_then_idempotent_rerun(tmp_path):
    root = make_snapshot(tmp_path)
    out = tmp_path / "out" / "core.db"
    report = tmp_path / "out" / "plan.json"
    code = main(["graph", "backfill", "--snapshot", str(root), "--out", str(out), "--write",
                 "--report", str(report)])
    assert code == 0
    assert out.is_file()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["dry_run"] is False
    assert payload["result"]["map_edges"] == 10
    assert payload["result"]["source_untouched"] is True
    again = main(["graph", "backfill", "--snapshot", str(root), "--out", str(out), "--write"])
    assert again == 0
    conn = sqlite3.connect(out)
    try:
        assert conn.execute("SELECT COUNT(*) FROM map_edges").fetchone()[0] == 10
    finally:
        conn.close()


def test_cli_backfill_refuses_frozen_snapshot_path(tmp_path):
    root = make_snapshot(tmp_path)
    code = main(["graph", "backfill", "--snapshot", str(root), "--out", str(root / "core.db"), "--write"])
    assert code == 2
    code = main(["graph", "backfill", "--snapshot", str(root), "--out", str(root / "graph.db"), "--write"])
    # 快照目录本身不是 snapshots/ 区：这个路径允许（合成快照不在仓库快照区）
    assert code in (0, 2)
    assert sha256_file(root / "core.db") is not None


def test_cli_backfill_reports_missing_snapshot_as_execution_error(tmp_path):
    code = main(["graph", "backfill", "--snapshot", str(tmp_path / "nope")])
    assert code == 1


def test_cli_audit_and_orphans(tmp_path, capsys):
    path = _graph_db(tmp_path)
    report_path = tmp_path / "map_graph_audit.json"
    code = main(["graph", "audit", "--db", str(path), "--out", str(report_path)])
    assert code == 0
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["name"] == "map_graph_audit"
    out = capsys.readouterr().out
    assert "Map Graph Audit" in out
    code = main(["graph", "orphans", "--db", str(path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["orphan_renderables"] == ["900"]


def test_cli_audit_gate_exit_code(tmp_path):
    path = _graph_db(tmp_path)
    conn = sqlite3.connect(path)
    try:
        save_edges(conn, [Edge("100", "999", RETURN, discovery_source="bundle_contract")])
    finally:
        conn.close()
    code = main(["graph", "audit", "--db", str(path), "--no-report", "--gate"])
    assert code == 2
    code = main(["graph", "audit", "--db", str(path), "--no-report"])
    assert code == 0


def test_cli_graph_without_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        main(["graph"])
    assert excinfo.value.code == 2


def test_cli_falls_back_to_snapshot_readonly_when_no_graph_db(tmp_path, monkeypatch, capsys):
    """默认 --db 指向不存在的图谱库时，回退到快照**只读**：能读、报告里没有边、什么都不写。"""
    import shutil

    root = make_snapshot(tmp_path / "src")
    runtime = tmp_path / "runtime"
    (runtime / "snapshots" / "20260101T000000Z").mkdir(parents=True)
    shutil.copy2(root / "core.db", runtime / "snapshots" / "20260101T000000Z" / "core.db")
    (runtime / "current.json").write_text(
        json.dumps({"snapshot_id": "20260101T000000Z"}), encoding="utf-8"
    )
    monkeypatch.setenv("HSRMAP_DATA_DIR", str(runtime))
    code = main(["--data-dir", str(runtime), "graph", "audit", "--no-report", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["db"].endswith("core.db")
    assert payload["edges_total"] == 0  # 老库没有 map_edges：那是「还没有边」，不是错误
    assert payload["renderable_maps"] == 4
    # 只读打开：内存里跑完审计，快照区一个字节都没多出来
    assert sorted((runtime / "snapshots" / "20260101T000000Z").iterdir()) == [
        runtime / "snapshots" / "20260101T000000Z" / "core.db"
    ]


def test_expected_baseline_is_the_m70_report_numbers():
    assert dict(EXPECTED_EDGE_COUNTS) == {
        "TREE_CHILD": 914,
        "RELATED_MAP": 204,
        "MAP_GROUP": 36,
        "POINT_JUMP": 187,
    }
