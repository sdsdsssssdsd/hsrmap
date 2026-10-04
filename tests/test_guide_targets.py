"""正规关系表与字符串键的 shadow 对比（a1-8 十二）。"""

from __future__ import annotations

from pathlib import Path

from hsrmap.guide_db import GuideDatabase, count_queries
from hsrmap.guides.stages import EntryIndex
from hsrmap.guides.targets import (
    key_members,
    key_topic,
    relation_ids,
    relations_in_sync,
    shadow_compare,
    sync_relations,
)


def _db(tmp_path: Path) -> GuideDatabase:
    db = GuideDatabase.create(tmp_path / "guide.db")
    db.conn.execute("INSERT INTO guide_source(id, name, domain) VALUES (1, 't', 't.test')")
    db.conn.execute(
        "INSERT INTO guide_page(id, source_id, canonical_url, title) VALUES (1, 1, 'https://t.test/a', 't')"
    )
    db.conn.commit()
    return db


def _entry(db: GuideDatabase, key: str, *, title: str = "攻略") -> dict:
    return db.create_entry({
        "source_point_id": key,
        "title": title,
        "status": "published",
        "source_url": "https://t.test/a",
        "steps": [{"text": "第一步"}],
    })


def test_key_members_follow_the_decision_layer_rule():
    assert key_members("408") == ["408"]
    assert key_members("set:3556-3622:topic:golden_scapegoat") == ["3556", "3622"]
    #: map:/global: 键不算点位的证据——判定层只认「完全相等」和 set: 成员。
    assert key_members("map:408:topic:x") == []
    assert key_members("global:topic:x") == []
    assert key_members("") == []
    assert key_topic("set:1-2:topic:dream_ticker") == "dream_ticker"
    assert key_topic("408") == "point_index"


def test_sync_is_idempotent_and_reports_its_plan(tmp_path):
    db = _db(tmp_path)
    _entry(db, "1")
    _entry(db, "set:2-3:topic:t")
    _entry(db, "map:4:topic:t")
    dry = sync_relations(db, apply=False)
    assert dry["members"] == 3 and dry["bindings_added"] == 3 and dry["targets_created"] == 3
    assert relations_in_sync(db)["in_sync"] is False, "干跑不许写库"

    first = sync_relations(db, apply=True)
    assert first["bindings_added"] == 3
    state = relations_in_sync(db)
    assert state["in_sync"] is True and state["missing"] == 0
    assert state["expected_bindings"] == 3

    again = sync_relations(db, apply=True)
    assert again["bindings_added"] == 0 and again["targets_created"] == 0
    assert relations_in_sync(db)["expected_bindings"] == 3
    db.close()


def test_shadow_compare_is_clean_and_detects_a_missing_binding(tmp_path):
    db = _db(tmp_path)
    _entry(db, "1")
    _entry(db, "set:2-3:topic:t")
    sync_relations(db, apply=True)
    same = shadow_compare(db, ["1", "2", "3", "99"])
    assert same["equal"] is True and same["points"] == 4 and same["mismatches"] == 0

    #: 抽掉一条绑定：字符串键照样找得到，关系表找不到 → 必须报出来，不许静默。
    db.conn.execute("DELETE FROM guide_entry_target WHERE guide_id = 2 AND target_id = (SELECT id FROM guide_target WHERE source_point_id = '2')")
    db.conn.commit()
    broken = shadow_compare(db, ["1", "2", "3"])
    assert broken["equal"] is False and broken["mismatches"] == 1
    assert broken["sample"][0]["point"] == "2"
    assert broken["sample"][0]["relation_only"] == []
    assert relations_in_sync(db)["in_sync"] is False
    db.close()


def test_entry_index_lookup_modes(tmp_path):
    db = _db(tmp_path)
    _entry(db, "1")
    _entry(db, "set:2-3:topic:t")
    string_index = EntryIndex(db, lookup="string")
    assert [row["id"] for row in string_index.matching("2")] == [2]

    #: 关系表没补齐：auto 退回字符串，relation 直接拒绝（不许悄悄漏攻略）。
    assert EntryIndex(db, lookup="auto").lookup == "string"
    try:
        EntryIndex(db, lookup="relation")
    except ValueError as exc:
        assert "target-shadow --apply" in str(exc)
    else:  # pragma: no cover - 走不到
        raise AssertionError("关系表没补齐时必须拒绝")

    sync_relations(db, apply=True)
    auto = EntryIndex(db, lookup="auto")
    assert auto.lookup == "relation" and auto.sync_state["in_sync"] is True
    assert [row["id"] for row in auto.matching("2")] == [2]
    assert [row["id"] for row in auto.matching("1")] == [1]
    assert auto.matching("99") == []
    assert relation_ids(db, "3") == [2]
    db.close()


def test_matching_is_free_after_the_index_is_built(tmp_path):
    """a1-8 十二：整个点位循环不再碰数据库。"""
    db = _db(tmp_path)
    for index in range(30):
        _entry(db, str(index), title=f"攻略{index}")
    sync_relations(db, apply=True)
    with count_queries(db) as measured:
        for lookup in ("string", "relation"):
            index = EntryIndex(db, lookup=lookup)
            for point in range(200):
                index.matching(str(point))
    #: 两次建索引；点位循环本身一条查询都不发。
    assert measured["count"] <= 12, measured["statements"]
    db.close()


def test_cli_target_shadow_dry_run_and_apply(tmp_path, capsys):
    import json

    from hsrmap.cli import main

    db = _db(tmp_path)
    _entry(db, "1")
    _entry(db, "set:2-3:topic:t")
    db.close()
    path = tmp_path / "guide.db"
    code = main(["guides", "target-shadow", "--db", str(path), "--no-points"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["relations"]["in_sync"] is False
    assert report["plan"]["bindings_added"] == 3

    #: --apply 之后关系齐了，且没有官方点位可对比时空对比也算一致。
    code = main(["guides", "target-shadow", "--db", str(path), "--no-points", "--apply"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["relations"]["in_sync"] is True
