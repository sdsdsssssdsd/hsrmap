"""P6.5 剩余清单：remaining = 官方可收集 − 有效完成（a1-9 §15）。"""

from __future__ import annotations

import pytest

from hsrmap.progress import atlas as atlas_mod
from hsrmap.progress import store
from hsrmap.progress.models import (
    SEMANTIC_MANUAL,
    SEMANTIC_MAP_MARK,
    SEMANTIC_UNKNOWN,
    SOURCE_HOYOLAB_MAP,
    ProgressObservation,
)
from hsrmap.user_db import UserDatabase

PROFILE = "p1"

COLLECTIBLES = [
    {"source_point_id": "1", "label": "宝箱甲", "region": "雅利洛-Ⅵ", "map_name": "残雪庭院",
     "map_path": "雅利洛-Ⅵ / 残雪庭院", "map_id": "101", "x": 1.5, "y": -2.0, "topic": "t1"},
    {"source_point_id": "2", "label": "宝箱乙", "region": "雅利洛-Ⅵ", "map_name": "残雪庭院",
     "map_path": "雅利洛-Ⅵ / 残雪庭院", "map_id": "101", "x": 3.0, "y": 4.0, "topic": "t1"},
    {"source_point_id": "3", "label": "折纸小鸟", "region": "匹诺康尼", "map_name": "黄金的时刻",
     "map_path": "匹诺康尼 / 黄金的时刻", "map_id": "202", "x": 9.0, "y": 8.0, "topic": "t2"},
]

COMPLETION = {
    "1": {"topic": "t1", "status": "LOCATE_COMPLETE", "requirement": "LOCATE_ONLY",
          "locate_evidence": "direct", "solve_evidence": "missing", "guide_id": 7,
          "title": "残雪庭院宝箱路线", "missing": ""},
    "3": {"topic": "t2", "status": "SOLVE_MISSING", "requirement": "LOCATE_AND_SOLVE",
          "locate_evidence": "direct", "solve_evidence": "missing", "guide_id": None,
          "title": "", "missing": "SOLVE_MISSING", "solve_kind": "PUZZLE"},
}


def test_remaining_is_collectible_minus_effective_completed() -> None:
    report = atlas_mod.assemble(
        collectibles=COLLECTIBLES, completion=COMPLETION, effective_completed={"1"},
    )
    assert report["totals"] == {
        "collectible": 3, "effective_completed": 1, "remaining": 2, "conflict": 0, "unclear": 0,
    }
    points = {p["source_point_id"]: p for region in report["regions"] for m in region["maps"] for p in m["points"]}
    assert points["1"]["state"] == "completed" and points["1"]["completed"] is True
    assert points["2"]["state"] == "remaining"
    assert points["3"]["state"] == "remaining"
    #: 证据与攻略信息一起带出来（P6.5 要求的 join）。
    assert points["1"]["locate_evidence"] == "direct" and points["1"]["guide_id"] == 7
    assert points["3"]["solve_kind"] == "PUZZLE" and points["3"]["missing"] == "SOLVE_MISSING"


def test_regions_and_maps_are_grouped_with_counts() -> None:
    report = atlas_mod.assemble(collectibles=COLLECTIBLES, completion=COMPLETION, effective_completed={"1"})
    regions = {r["zone"]: r for r in report["regions"]}
    assert regions["雅利洛-Ⅵ"]["collectible"] == 2 and regions["雅利洛-Ⅵ"]["remaining"] == 1
    assert regions["匹诺康尼"]["remaining"] == 1
    amap = regions["雅利洛-Ⅵ"]["maps"][0]
    assert amap["map_name"] == "残雪庭院" and amap["collectible"] == 2 and amap["remaining"] == 1
    #: 剩余点排前面，已完成的沉底。
    assert [p["source_point_id"] for p in amap["points"]] == ["2", "1"]
    topics = {t["topic"]: t for t in report["topics"]}
    assert topics["t1"]["remaining"] == 1 and topics["t2"]["remaining"] == 1


def test_unclear_and_conflict_are_visible_but_never_counted_as_completed() -> None:
    report = atlas_mod.assemble(
        collectibles=COLLECTIBLES, completion=COMPLETION,
        effective_completed={"1"}, unclear={"2"}, conflict={"3"},
    )
    assert report["totals"]["effective_completed"] == 1
    assert report["totals"]["unclear"] == 1 and report["totals"]["conflict"] == 1
    points = {p["source_point_id"]: p for region in report["regions"] for m in region["maps"] for p in m["points"]}
    assert points["2"]["state"] == "unclear" and points["2"]["completed"] is False
    assert points["3"]["state"] == "conflict" and points["3"]["completed"] is False


def test_render_marks_conflicts_and_unclear_points() -> None:
    report = atlas_mod.assemble(
        collectibles=COLLECTIBLES, completion=COMPLETION,
        effective_completed=set(), unclear={"2"}, conflict={"3"},
        gate={"allowed_remote_semantics": []},
    )
    text = atlas_mod.render(report, limit=10)
    assert "剩余 3" in text
    assert "冲突 1" in text and "说不清 1" in text
    assert "Gate 0 未定" in text
    assert "#3" in text and "SOLVE ✗" in text


def test_render_respects_the_limit() -> None:
    report = atlas_mod.assemble(collectibles=COLLECTIBLES, completion=COMPLETION)
    text = atlas_mod.render(report, limit=1)
    assert "只显示前 1 个剩余点位" in text


def _user_db(tmp_path):
    return UserDatabase(tmp_path / "user.db")


def test_progress_sets_separate_manual_from_unproven_remote(tmp_path) -> None:
    db = _user_db(tmp_path)
    try:
        db.upsert_point("1", {"completed": True})
        store.upsert_observation(db, ProgressObservation(
            source_point_id="2", profile_id=PROFILE, semantic=SEMANTIC_MAP_MARK,
            completed=True, source=SOURCE_HOYOLAB_MAP))
        store.upsert_observation(db, ProgressObservation(
            source_point_id="3", profile_id=PROFILE, semantic=SEMANTIC_UNKNOWN,
            completed=True, source=SOURCE_HOYOLAB_MAP))
        sets = atlas_mod.progress_sets(db)
        assert sets["local"] == {"1"}
        assert sets["accepted"] == set(), "Gate 0 之前 map_mark 不算有效完成"
        assert sets["unclear"] == {"2", "3"}
        opted = atlas_mod.progress_sets(db, import_map_mark=True)
        assert opted["accepted"] == {"2"}
        assert opted["unclear"] == {"3"}, "unknown 语义永远进不了有效完成"
    finally:
        db.close()


def test_conflict_requires_a_denial_under_an_allowed_semantic(tmp_path) -> None:
    db = _user_db(tmp_path)
    try:
        db.upsert_point("1", {"completed": True})
        #: 没有推导权时，远端说「没有」不该算冲突（我们还没承认它的语义）。
        store.upsert_observation(db, ProgressObservation(
            source_point_id="1", profile_id=PROFILE, semantic=SEMANTIC_MAP_MARK,
            completed=False, source=SOURCE_HOYOLAB_MAP))
        assert atlas_mod.progress_sets(db)["conflict"] == set()
        assert atlas_mod.progress_sets(db, import_map_mark=True)["conflict"] == {"1"}
    finally:
        db.close()


def test_remaining_atlas_end_to_end_without_touching_the_network(tmp_path) -> None:
    db = _user_db(tmp_path)
    try:
        db.upsert_point("1", {"completed": True})
        store.upsert_observation(db, ProgressObservation(
            source_point_id="2", profile_id=PROFILE, semantic=SEMANTIC_MANUAL, completed=True))
        report = atlas_mod.remaining_atlas(
            guide_db=None, user_db=db, collectibles=COLLECTIBLES, import_map_mark=True)
        assert report["totals"]["collectible"] == 3
        assert report["totals"]["effective_completed"] == 2  # 1 手动勾选 + 2 手动观察
        assert report["totals"]["remaining"] == 1
        assert report["gate"]["viewer_network"] == 0
        assert report["gate"]["allowed_remote_semantics"] == [SEMANTIC_MAP_MARK]
    finally:
        db.close()


def test_map_path_is_normalised_when_the_source_fields_are_missing() -> None:
    """源数据里 map_name 有空、region 有数字的情况（官方地图路径的三段式）。"""
    item = {"source_point_id": "9001", "label": "无名尘灵", "map_id": "532",
            "map_name": "", "region": "", "map_path": "二相乐园 / 特殊房间 / 532"}
    entry = atlas_mod.point_entry(item)
    assert (entry["zone"], entry["region"], entry["map_name"]) == ("二相乐园", "特殊房间", "特殊房间")
    fallback = atlas_mod.point_entry({"source_point_id": "9002", "map_name": "", "map_path": ""})
    assert fallback["map_name"] == "（未标明地图）" or fallback["map_name"] == ""


def test_unknown_semantics_never_reduce_the_remaining_count() -> None:
    report = atlas_mod.assemble(
        collectibles=COLLECTIBLES, effective_completed=set(), unclear={"1", "2", "3"},
    )
    assert report["totals"]["remaining"] == 3
