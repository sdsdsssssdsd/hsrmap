"""P6.7 路线规划：只聚类 + 排序，不编耗时与米数。"""

from __future__ import annotations

from hsrmap.progress import atlas as atlas_mod
from hsrmap.progress import routes as routes_mod

COLLECTIBLES = [
    #: 同一张图上的四个点：故意乱序，坐标两两相邻。
    {"source_point_id": "1", "label": "甲", "region": "月台", "map_name": "月台",
     "map_path": "空间站 / 月台 / 1", "map_id": "1", "x": 0.0, "y": 0.0, "topic": "t"},
    {"source_point_id": "2", "label": "乙", "region": "月台", "map_name": "月台",
     "map_path": "空间站 / 月台 / 1", "map_id": "1", "x": 10.0, "y": 0.0, "topic": "t"},
    {"source_point_id": "3", "label": "丙", "region": "月台", "map_name": "月台",
     "map_path": "空间站 / 月台 / 1", "map_id": "1", "x": 20.0, "y": 0.0, "topic": "t"},
    {"source_point_id": "4", "label": "丁", "region": "月台", "map_name": "月台",
     "map_path": "空间站 / 月台 / 1", "map_id": "1", "x": 30.0, "y": 0.0, "topic": "t"},
    #: 另一张图的点：没有坐标，且缺解法。
    {"source_point_id": "5", "label": "戊", "region": "甲板", "map_name": "甲板",
     "map_path": "空间站 / 甲板 / 2", "map_id": "2", "x": None, "y": None, "topic": "t"},
]

COMPLETION = {
    "1": {"status": "COMPLETE", "locate_evidence": "direct", "solve_evidence": "direct", "solve_kind": "PUZZLE"},
    "2": {"status": "COMPLETE", "locate_evidence": "direct", "solve_evidence": "direct", "solve_kind": "PUZZLE"},
    "3": {"status": "LOCATE_COMPLETE", "locate_evidence": "direct", "solve_evidence": "", "solve_kind": "NONE"},
    "4": {"status": "COMPLETE", "locate_evidence": "direct", "solve_evidence": "direct", "solve_kind": "INTERACT"},
    "5": {"status": "SOLVE_MISSING", "locate_evidence": "direct", "solve_evidence": "", "solve_kind": "NONE"},
}


def _plan(**kwargs):
    atlas = atlas_mod.assemble(collectibles=COLLECTIBLES, completion=COMPLETION, effective_completed={"4"})
    return routes_mod.plan_routes(atlas, **kwargs)


def test_completed_points_never_enter_a_route() -> None:
    plan = _plan()
    ids = [step["source_point_id"] for route in plan["routes"] for step in route["steps"]]
    assert "4" not in ids, "已完成点位不该出现在剩余路线里"
    assert plan["remaining"] == 4


def test_route_orders_points_by_nearest_neighbour() -> None:
    plan = _plan()
    route = next(r for r in plan["routes"] if r["map_id"] == "1")
    assert [step["source_point_id"] for step in route["steps"]] == ["1", "2", "3"]
    assert route["steps"][0]["distance_units"] is None
    assert route["steps"][1]["distance_units"] == 10.0
    assert route["distance_units"] == 20.0


def test_ready_clusters_rank_before_unready_ones() -> None:
    plan = _plan()
    assert plan["routes"][0]["map_id"] == "1", "攻略齐的图应该排在前面"
    assert plan["routes"][0]["ready"] == 2 and plan["routes"][0]["planned"] == 3


def test_points_without_coordinates_are_kept_at_the_end() -> None:
    plan = _plan()
    deck = next(r for r in plan["routes"] if r["map_id"] == "2")
    assert [step["source_point_id"] for step in deck["steps"]] == ["5"]
    assert deck["steps"][0]["x"] is None and deck["steps"][0]["readiness"] == routes_mod.NEED_SOLVE


def test_no_eta_and_no_fake_meters() -> None:
    plan = _plan()
    for route in plan["routes"]:
        assert route["eta"] is None
        assert "不是米" in route["unit"]
    assert "不估算耗时" in plan["note"] or "不给分钟数" in plan["note"]
    assert "分钟" not in routes_mod.render(plan)


def test_plan_respects_point_and_route_limits() -> None:
    plan = _plan(max_points=2, max_routes=1)
    assert len(plan["routes"]) == 1
    assert plan["routes"][0]["planned"] == 2
    assert plan["routes"][0]["remaining"] == 3, "剩余数按整张图算，不因为展示上限而缩水"


def test_zone_filter_and_conflict_visibility() -> None:
    atlas = atlas_mod.assemble(collectibles=COLLECTIBLES, completion=COMPLETION, conflict={"1"})
    plan = routes_mod.plan_routes(atlas, zone="空间站")
    route = plan["routes"][0]
    assert route["conflict"] == 1
    assert plan["routes"][0]["steps"][0]["state"] == "conflict"
    assert routes_mod.plan_routes(atlas, zone="不存在的区域")["routes"] == []


def test_next_actions_prefers_ready_points() -> None:
    plan = _plan()
    actions = routes_mod.next_actions(plan, limit=2)
    assert [item["source_point_id"] for item in actions] == ["1", "2"]
    assert all(item["readiness"] == routes_mod.READY for item in actions)
    assert all("route_id" in item for item in actions)
    more = routes_mod.next_actions(plan, limit=10)
    assert len(more) == 4, "够格的点位不足时，剩下的用缺证据的点位补齐"


def test_render_shows_counts_and_gaps() -> None:
    text = routes_mod.render(_plan(), limit=2, steps=2)
    assert "Route #1" in text and "剩余 4 个点位" in text
    assert "坐标距离" in text
    assert "还有" in text
