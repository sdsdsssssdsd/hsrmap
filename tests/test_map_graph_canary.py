"""§二十三 的永久回归 Canary：JUMP 深层地图链路（a1-8-1）。

「二次元界 JUMP」那次事故必须只发生一次。这个文件把当时的真实样本钉成永久检查：

```text
入口地图 943 存在
  → 入口 point 5637 存在
  → transition 边（POINT_JUMP 943 → 979）存在
  → target 979 存在且可渲染（证据来自 maps + map_fragments，不是 node_type 猜的）
  → 979 的 point_list 可读
  → 返回入口 943 可渲染（前端导航栈用，M7.5）
```

任何一环断了 —— 官方改字段、extractor 退化、回填没跑 —— 这里就红。
数据库用**旁挂派生库**（`data/graph/core.db`）或新快照的 core.db；两者都没有时 skip（不是 fail）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hsrmap.graph_audit import CANARY_ENTRY_MAP, CANARY_POINT_SOURCE_ID, audit_graph
from hsrmap.paths import DATA

pytestmark = pytest.mark.data


def _has_edges(path: Path) -> bool:
    import sqlite3

    if not path.is_file():
        return False
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return bool(conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='map_edges'"
        ).fetchone()[0])
    finally:
        conn.close()


def _graph_sources() -> list[tuple[str, Path]]:
    """**所有**可用的图来源：旁挂回填库 + 当前快照（如果它自己带 map_edges）。

    两个都要能过 canary：M7.2 的回填产物与 M7.3 之后「边直接进 core.db」的新快照
    是两条真实路径，只验一条等于给另一条留暗门。
    """
    sources: list[tuple[str, Path]] = []
    sidecar = Path(DATA) / "graph" / "core.db"
    if _has_edges(sidecar):
        sources.append(("sidecar", sidecar))
    current = Path(DATA) / "current.json"
    if current.is_file():
        try:
            #: utf-8-sig：指针文件带 BOM 时也要读得出来（Windows 脚本很常见）。
            payload = json.loads(current.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            payload = {}
        snapshot_db = Path(DATA) / str(payload.get("core_db") or "")
        if _has_edges(snapshot_db):
            sources.append(("snapshot", snapshot_db))
    return sources


def _source_ids() -> list[str]:
    names = [name for name, _ in _graph_sources()]
    return names or ["missing"]


@pytest.fixture(params=_source_ids())
def graph_db(request) -> Path:
    """参数化：每个可用图来源都要单独过一遍（skip 只在**一个来源都没有**时发生）。"""
    sources = dict(_graph_sources())
    path = sources.get(str(request.param))
    if path is None:
        pytest.skip("SKIPPED: 没有图库（先跑 python -m hsrmap graph backfill --write）")
    return path


def _audit(graph_db: Path) -> dict:
    import sqlite3

    conn = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return audit_graph(conn)
    finally:
        conn.close()


def test_canary_deep_map_transition_passes(graph_db: Path) -> None:
    report = _audit(graph_db)
    canary = report["canary"]
    assert canary["name"] == "canary_deep_map_transition"
    assert canary["ok"] is True, json.dumps(canary, ensure_ascii=False, indent=2)
    steps = {str(item["step"]): item for item in canary["steps"]}
    #: 七步链一个都不能少（§二十三）。
    assert {"entry_map", "entry_point", "transition_edge", "target_map",
            "target_renderable", "target_points_readable", "return_target_valid"} <= set(steps)
    assert all(item["ok"] for item in canary["steps"]), json.dumps(canary["steps"], ensure_ascii=False)
    assert canary["target_map_id"] == "979"
    render = steps["target_renderable"]["detail"]
    assert render["map_id"] == "979" and render["outcome"] == "RENDERABLE"
    #: 判据必须来自落库证据（maps + 切片），不是 node_type 猜的（§九）。
    assert "map_fragments" in render["source"]
    #: id 空间重叠要如实标出来（禁止按区间猜语义）。
    assert len(render["overlaps"]) >= 3


def test_canary_sample_is_the_one_from_the_report(graph_db: Path) -> None:
    """canary 必须真的点在 M7.0 的样本上，而不是「图上随便第一条边」。"""
    report = _audit(graph_db)
    canary = report["canary"]
    assert str(canary["entry_map"]) == CANARY_ENTRY_MAP
    assert str(canary["point_source_id"]) == CANARY_POINT_SOURCE_ID
    assert canary["named_sample"] is True, "样本不是点名的那条链，退化到了「图上随便第一条边」"


def test_publish_invariant_inputs_are_empty(graph_db: Path) -> None:
    """§二十四 的发布门禁输入：可导航边的 target 必须都在已同步的可渲染集合里。"""
    report = _audit(graph_db)
    gate = report["gate"]
    assert gate["ok"] is True, json.dumps(gate, ensure_ascii=False, indent=2)
    checks = gate["checks"]
    assert checks["unresolved_targets_empty"] is True
    assert checks["render_requiring_targets_renderable"] is True
    assert checks["closure_converged"] is True
    assert int(report["unresolved_targets_total"]) == 0
    assert int(report["orphan_renderables_total"]) == 0


def test_structural_edges_are_reported_separately(graph_db: Path) -> None:
    """结构边（树/楼层/关联/地图组）的目标本来就是容器：单独统计，不算违规。"""
    report = _audit(graph_db)
    counts = report["edges"]
    for name in ("TREE_CHILD", "RELATED_MAP", "MAP_GROUP", "POINT_JUMP"):
        assert int(counts.get(name) or 0) > 0, f"{name} 边不该消失（回填回归）"
    #: 结构类的目标可以是容器，可导航类的目标必须是可渲染地图（§6.3 口径）。
    assert int(counts["TREE_CHILD"]) == 914
    assert int(counts["POINT_JUMP"]) == 187
    assert int(report["point_transitions"]) == 187
