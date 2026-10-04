from pathlib import Path

from hsrmap.guide_db import GuideDatabase


def test_v3_point_and_map_label_targets(tmp_path: Path):
    db = GuideDatabase(tmp_path / "guide.db")
    topic = db.upsert_topic(
        {
            "topic_key": "floating_grease",
            "display_name": "浮脂溯源",
            "guide_kind": "PUZZLE",
            "scope_type": "POINT",
            "matcher_profile": "puzzle_point_v2",
            "layout_profile": "puzzle_steps_v1",
            "priority": 100,
        }
    )
    point = db.upsert_target(
        {
            "topic_id": topic["id"],
            "target_type": "POINT",
            "target_key": "point:5171",
            "source_point_id": "5171",
        }
    )
    route = db.upsert_target(
        {
            "topic_id": topic["id"],
            "target_type": "MAP_LABEL",
            "target_key": "map:508:label:447",
            "map_id": "508",
            "label_id": "447",
        }
    )
    entry = db.create_entry({"source_point_id": "5171", "title": "t", "steps": []})
    db.bind_entry_target(entry["id"], point["id"], role="primary", order_index=0)
    db.bind_entry_target(entry["id"], route["id"], role="route", order_index=1)
    assert {item["target_key"] for item in db.targets_for_entry(entry["id"])} == {"point:5171", "map:508:label:447"}
    assert [item["id"] for item in db.entries_for_target(point["id"])] == [entry["id"]]
    db.close()


def test_page_can_bind_multiple_topics(tmp_path: Path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/all", "title": "全收集"})
    db.bind_page_topic(page["id"], "origami_bird", 0.98)
    db.bind_page_topic(page["id"], "dream_ticker", 0.91)
    assert {row["topic_key"] for row in db.topics_for_page(page["id"])} == {"origami_bird", "dream_ticker"}
    db.close()
