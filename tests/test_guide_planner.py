"""Target-driven discovery, priority frontier and yield (a1-6 §六–§八)."""

import json

import pytest

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.evidence import record_search
from hsrmap.guides.planner import (
    FRONTIER_WEIGHTS,
    discovery_plan,
    frontier,
    host_health,
    query_families,
    score_candidate,
    source_yield,
    target_yield,
)


POINTS = [
    {"source_point_id": "5538", "map_id": "149", "map_name": "1层", "region": "千星城中心城区", "label": "浮脂溯源"},
    {"source_point_id": "5539", "map_id": "150", "map_name": "2层", "region": "千星城中心城区", "label": "浮脂溯源"},
]


def _db(tmp_path):
    return GuideDatabase(tmp_path / "guide.db")


def _page(db, url, *, qa_status=None, qa_reason=None):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": "标题", "author": "作者"})
    if qa_status or qa_reason:
        db.conn.execute(
            "UPDATE guide_page SET qa_status = ?, qa_reason = ? WHERE id = ?",
            (qa_status, qa_reason, page["id"]),
        )
        db.conn.commit()
    return page


def test_query_families_uses_topic_place_and_both_game_names():
    queries = query_families("浮脂溯源·二次元ROTATE！", POINTS[0])
    # the two game-name shapes for both names, plus the two suffix shapes (§七)
    assert len(queries) == 6
    assert queries[0] == "崩坏星穹铁道 浮脂溯源·二次元ROTATE！ 1层"
    assert queries[1] == "崩坏星穹铁道 1层 浮脂溯源·二次元ROTATE！"
    assert queries[2].endswith("1层 全收集") and queries[3].endswith("1层 解谜")
    assert any(query.startswith("崩铁 ") for query in queries)
    assert len(set(queries)) == len(queries)
    # a target with no place still produces usable queries
    assert query_families("浮脂溯源", {})


def test_discovery_plan_lists_gaps_and_marks_searched_ones(tmp_path):
    db = _db(tmp_path)
    plan = discovery_plan(db, "fake_topic", official_points=POINTS, limit=10)
    assert plan["targets_with_gap"] == 2
    assert plan["targets_never_searched"] == 2
    assert {item["target_key"] for item in plan["plan"]} == {"point:5538", "point:5539"}
    assert plan["plan"][0]["queries"]

    record_search(
        db,
        topic="fake_topic",
        target_key="point:5538",
        query="崩铁 浮脂 1层",
        results=[{"rank": 1, "url": "https://a.test/1", "decision": "ACCEPTED"}],
    )
    after = discovery_plan(db, "fake_topic", official_points=POINTS, limit=10)
    assert after["targets_never_searched"] == 1
    by_key = {item["target_key"]: item for item in after["plan"]}
    assert by_key["point:5538"]["searched"] is True and by_key["point:5538"]["accepted_urls"] == 1
    assert by_key["point:5539"]["searched"] is False
    only_new = discovery_plan(db, "fake_topic", official_points=POINTS, limit=10, include_searched=False)
    assert [item["target_key"] for item in only_new["plan"]] == ["point:5539"]
    db.close()


def test_score_candidate_applies_every_frontier_rule():
    health = {
        "good.test": {"published": 2, "qa_fail": 0, "js_only": 0, "high_yield": True},
        "bad.test": {"published": 0, "qa_fail": 3, "js_only": 0, "high_yield": False},
        "js.test": {"published": 0, "qa_fail": 0, "js_only": 2, "high_yield": False},
    }
    good = score_candidate(
        {"url": "https://good.test/a.html", "topic": "t", "target_key": "point:1", "status": "NEEDS_SOURCE"},
        health=health,
    )
    rules = {item["rule"]: item["weight"] for item in good["reasons"]}
    assert rules["needs_source"] == FRONTIER_WEIGHTS["needs_source"] == 50
    assert rules["high_yield_host"] == 30 and rules["unseen_url"] == 10
    assert good["score"] == 90

    bad = score_candidate({"url": "https://bad.test/a.html", "needs_source": False}, health=health)
    bad_rules = {item["rule"]: item["weight"] for item in bad["reasons"]}
    assert bad_rules["qa_fail_history"] == -30

    js = score_candidate({"url": "https://js.test/a.html"}, health=health)
    assert {item["rule"] for item in js["reasons"]} == {"js_only", "unseen_url"}
    assert js["score"] == -70

    mirror = score_candidate({"url": "https://m.gamersky.com/handbook/202404/1729233.shtml"})
    assert {item["rule"] for item in mirror["reasons"]} == {"known_mirror", "unseen_url"}
    assert mirror["score"] == -90

    duplicate = score_candidate(
        {"url": "https://good.test/a.html", "continuation": True},
        known_families={"good.test/a.html"},
    )
    dup_rules = {item["rule"]: item["weight"] for item in duplicate["reasons"]}
    assert dup_rules["duplicate_family"] == -50 and dup_rules["family_continuation"] == 20
    assert duplicate["score"] == -30


def test_frontier_puts_the_target_gap_first(tmp_path):
    db = _db(tmp_path)
    _page(db, "https://known.test/a.html")
    rows = frontier(
        db,
        [
            {"url": "https://known.test/a.html", "topic": "t"},
            {"url": "https://m.gamersky.com/handbook/202404/1729233.shtml", "topic": "t"},
            {"url": "https://fresh.test/1.html", "topic": "t", "target_key": "point:1", "status": "NEEDS_SOURCE"},
        ],
    )
    assert [row["url"] for row in rows] == [
        "https://fresh.test/1.html",
        "https://known.test/a.html",
        "https://m.gamersky.com/handbook/202404/1729233.shtml",
    ]
    assert rows[0]["score"] > rows[1]["score"] > rows[2]["score"]
    db.close()


def test_host_health_and_source_yield(tmp_path):
    db = _db(tmp_path)
    _page(db, "https://good.test/a.html", qa_status="QA_PASS")
    _page(db, "https://good.test/b.html", qa_status="QA_FAIL", qa_reason="JS_RENDER_REQUIRED")
    _page(db, "https://good.test/c.html")  # never checked
    _page(db, "https://bad.test/a.html", qa_status="QA_FAIL")
    health = host_health(db)
    assert health["good.test"]["pages"] == 3
    assert health["good.test"]["qa_pass"] == 1 and health["good.test"]["qa_fail"] == 1
    assert health["good.test"]["qa_unchecked"] == 1 and health["good.test"]["js_only"] == 1
    assert health["good.test"]["qa_pass_rate"] == 0.5
    assert health["good.test"]["asset_success_rate"] is None  # no assets cached for it
    assert health["bad.test"]["qa_pass_rate"] == 0.0

    db.conn.execute(
        """INSERT INTO guide_asset_cache(source_url, sha256, status, downloaded_at)
           VALUES ('https://cdn.test/a.png', 'a' || '0' , 'FETCHED', 'now'),
                  ('https://cdn.test/b.png', 'b' || '0', 'HTTP_BLOCKED', 'now')"""
    )
    db.conn.commit()
    db.create_entry({
        "source_point_id": "5538",
        "title": "攻略",
        "status": "published",
        "source_url": "https://good.test/a.html",
        "steps": [{"text": "第一步 转", "images": []}],
    })
    health = host_health(db)
    assert health["good.test"]["published"] == 1 and health["good.test"]["high_yield"] is True
    assert health["cdn.test"]["assets_ok"] == 1 and health["cdn.test"]["assets_blocked"] == 1
    assert health["cdn.test"]["asset_success_rate"] == 0.5
    ranked = source_yield(db)
    assert ranked[0]["host"] == "good.test"

    report = target_yield(db, "fake_topic", official_points=POINTS)
    assert report["targets_with_guides"] == 1
    assert {row["target_key"] for row in report["targets"]} == {"point:5538", "point:5539"}
    assert [row["guides"] for row in report["targets"]] == [1, 0]
    db.close()


def test_map_label_gaps_resolve_their_map_name_for_queries(tmp_path):
    """A MAP_LABEL gap is searched by its map name, not by the bare topic."""
    from hsrmap.guides.ledger import topic_ledger  # noqa: F401  (documents the source)
    from hsrmap.guides.planner import target_gaps

    db = _db(tmp_path)
    maps = [{"map_id": "398", "name": "特殊房间", "path": "翁法罗斯 / 雅努萨波利斯"}]
    gaps = target_gaps(db, "nymph", official_points=POINTS, official_maps=maps)
    assert gaps == [] or all(gap.get("status") in {"NEEDS_SOURCE", "NO_PUBLIC_SOURCE_FOUND"} for gap in gaps)

    points = [{"source_point_id": "9001", "map_id": "398", "map_name": "特殊房间", "region": "翁法罗斯", "label": "若虫"}]
    gaps = target_gaps(db, "nymph", official_points=points, official_maps=maps)
    assert len(gaps) == 1
    assert gaps[0]["target_key"] == "map:398:topic:nymph"
    assert gaps[0]["map_name"] == "特殊房间"
    db.close()

def test_cli_frontier_and_yield(tmp_path, capsys):
    db_path = tmp_path / "guide.db"
    db = GuideDatabase(db_path)
    _page(db, "https://known.test/a.html", qa_status="QA_PASS")
    record_search(
        db,
        topic="fake_topic",
        target_key="point:1",
        query="q",
        results=[{"rank": 1, "url": "https://fresh.test/1.html", "decision": "ACCEPTED"}],
    )
    db.close()
    code = main(["guides", "frontier", "--db", str(db_path), "--topic", "fake_topic", "--limit", "5"])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["candidates"] == 1
    assert body["frontier"][0]["url"] == "https://fresh.test/1.html"
    assert body["frontier"][0]["target_key"] == "point:1"
    assert code == main(["guides", "yield", "--db", str(db_path), "--topic", "fake_topic"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["sources"][0]["host"] == "known.test"

def test_query_families_follows_the_missing_evidence():
    """用户 2026-10：缺定位就搜位置，缺解法就搜解法——不再用同一组查询打两种缺口。"""
    target = {"map_name": "1层", "label": "浮脂溯源"}
    solve = query_families("浮脂溯源", target, missing="SOLVE")
    assert solve and all(any(word in query for word in ("解谜", "步骤", "怎么解")) for query in solve)
    locate = query_families("浮脂溯源", target, missing="LOCATE")
    assert locate and all(any(word in query for word in ("位置", "在哪")) for query in locate)
    # 留空 = 还不知道缺什么：两种都试（保持既有行为）
    assert len(query_families("浮脂溯源", target)) == 6


@pytest.mark.data
def test_the_plan_drops_ledger_gaps_the_completion_model_calls_done(tmp_path):
    """账本缺口也要过完成模型：JUMP 归入第 1 阶段后，它的点位不再是待办。"""
    from hsrmap.guides.topics.official import official_points_for_topic

    db = _db(tmp_path)
    points = official_points_for_topic("jump") or []
    assert points
    pid = str(points[0]["source_point_id"])
    db.conn.execute(
        "INSERT INTO guide_target_status(topic_key, target_key, source_point_id, map_id, status, updated_at)"
        " VALUES ('jump', ?, ?, ?, 'NEEDS_SOURCE', datetime('now'))",
        (f"point:{pid}", pid, str(points[0].get("map_id") or "")),
    )
    db.conn.commit()
    plan = discovery_plan(db, "jump", official_points=points, limit=0)
    assert all(row["target_key"] != f"point:{pid}" for row in plan["plan"])
    db.close()


@pytest.mark.data  # 用真实官方点位（data/snapshots）验证缺口，submit 副本里跳过
def test_completion_gaps_name_what_each_point_still_needs(tmp_path):
    """下一轮的工作队列来自完成模型：这个点位到底缺 LOCATE 还是缺 SOLVE。"""
    from hsrmap.guides.planner import completion_gaps
    from hsrmap.guides.topics.official import official_points_for_topic

    db = _db(tmp_path)
    points = official_points_for_topic("floating_grease") or []
    assert points, "official floating_grease points should be available"
    located = points[0]
    db.create_entry(
        {
            "source_point_id": str(located["source_point_id"]),
            "title": "只有位置的攻略",
            "status": "published",
            "steps": [{"text": "位于千星城中心城区。"}],
        }
    )
    db.conn.commit()
    gaps = {item["target_key"]: item for item in completion_gaps(db, "floating_grease")}
    key = f"point:{located['source_point_id']}"
    assert key in gaps
    # 浮脂溯源是「到了还要解」的主题：位置有了、解法还没有
    assert gaps[key]["missing"] == "SOLVE"
    assert gaps[key]["requirement"] == "LOCATE_AND_SOLVE"
    db.close()


@pytest.mark.data
def test_the_discovery_plan_asks_for_the_missing_evidence(tmp_path):
    from hsrmap.guides.topics.official import official_points_for_topic

    db = _db(tmp_path)
    points = official_points_for_topic("floating_grease") or []
    assert points
    #: 空库时每个点都是「无证据」；先给一个点发一条只有位置的攻略，它才变成「缺解法」
    located = points[0]
    db.create_entry(
        {
            "source_point_id": str(located["source_point_id"]),
            "title": "只有位置的攻略",
            "status": "published",
            "steps": [{"text": "位于千星城中心城区。"}],
        }
    )
    db.conn.commit()
    plan = discovery_plan(db, "floating_grease", official_points=points, limit=0)
    assert plan["targets_missing_solve"] >= 1
    assert any(
        row["target_key"] == f"point:{located['source_point_id']}" for row in plan["plan"]
    )
    #: 工作队列里「缺解法」的目标问的是解法，不是位置
    solve_rows = [row for row in plan["plan"] if row["missing"] == "SOLVE"]
    assert solve_rows
    for row in solve_rows[:3]:
        assert any(("解谜" in query or "步骤" in query) for query in row["queries"])
        assert row["gap_source"] == "completion"
    locate_rows = [row for row in plan["plan"] if row["missing"] == "LOCATE"]
    for row in locate_rows[:3]:
        assert any(("位置" in query or "在哪" in query) for query in row["queries"])
    db.close()

