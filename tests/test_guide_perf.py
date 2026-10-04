"""a1-8 十二：性能验收先固定结构指标——查询数不随点位数增长。

不写「必须 2 秒」这种拍脑袋的门槛；墙钟基线记在 `docs/runbooks/hygiene-s7.md`，
作为后续回归的预算，而不是断言（机器不同、CI 抖动都会让秒数断言变成噪音）。
"""

from __future__ import annotations

import pytest

from hsrmap.guide_db import GuideDatabase, count_queries
from hsrmap.guides.stages import completeness_report
from hsrmap.paths import GUIDE_DB


def _seed(tmp_path, entries: int) -> GuideDatabase:
    db = GuideDatabase.create(tmp_path / "guide.db")
    db.conn.execute("INSERT INTO guide_source(id, name, domain) VALUES (1, 't', 't.test')")
    db.conn.execute(
        "INSERT INTO guide_page(id, source_id, canonical_url, title) VALUES (1, 1, 'https://t.test/a', 't')"
    )
    for index in range(entries):
        entry = db.create_entry({
            "source_point_id": str(index),
            "title": f"攻略{index}",
            "status": "published",
            "source_url": "https://t.test/a",
            "steps": [{"text": "第1步：向右", "images": ["a" * 64]}, {"text": "第2步：向下"}],
        })
    db.conn.commit()
    return db


def test_list_for_keys_takes_one_round_of_queries(tmp_path):
    """一条和三十条都是 3 条 SQL：entries / steps / assets（a1-8 十二）。"""
    db = _seed(tmp_path, 30)
    with count_queries(db) as one:
        entries = db.list_for_keys(["0"])
    with count_queries(db) as many:
        all_entries = db.list_for_keys([str(index) for index in range(30)])
    assert len(entries) == 1 and len(all_entries) == 30
    assert one["count"] == many["count"] == 3, many["statements"]
    #: 子行确实挂上了（bulk 加载不能只是「少查了」，还得查对）。
    assert all_entries[3]["steps"][0]["text"] == "第1步：向右"
    assert all_entries[3]["steps"][0]["images"] == ["a" * 64]
    assert len(all_entries[3]["steps"]) == 2
    db.close()


def test_list_for_point_keeps_its_shape(tmp_path):
    db = _seed(tmp_path, 3)
    rows = db.list_for_point("2")
    assert [row["source_point_id"] for row in rows] == ["2"]
    assert rows[0]["status"] == "published" and rows[0]["steps"][1]["text"] == "第2步：向下"
    db.close()


@pytest.mark.data  # 需要真实官方点位（data/snapshots），submit 副本里没有
def test_completeness_query_count_is_independent_of_point_count():
    from hsrmap.guides.stages import completeness_report

    db = GuideDatabase.open_readonly(GUIDE_DB)
    small = completeness_report(db, topics=["golden_scapegoat"], profile=True)
    big = completeness_report(db, topics=["nymph"], profile=True)
    db.close()
    assert big["points"] > small["points"] > 0
    assert big["sql"]["queries"] == small["sql"]["queries"], (
        f"查询数跟着点位涨了：{small['sql']} vs {big['sql']}"
    )
    #: 一次索引 + 每个主题一条官方点位查询的量级，不随点位数变化。
    assert big["sql"]["queries"] <= 12
    assert big["lookup"]["mode"] in {"string", "relation"}
