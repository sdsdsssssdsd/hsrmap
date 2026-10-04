from hsrmap.guides.topics.loader import get_topic, list_topics
from hsrmap.guides.units.registry import build_units_for_topic


def test_wave1_extra_topics_are_registered():
    keys = {item["topic_key"] for item in list_topics()}
    assert "zagreus_hand" in keys
    assert "pioneer_fairy" in keys
    hand = get_topic("zagreus_hand")
    fairy = get_topic("pioneer_fairy")
    assert hand["guide_kind"] == "PUZZLE"
    assert "扎格列斯之手" in hand["official_labels"]["names"]
    assert fairy["guide_kind"] == "CHALLENGE"
    assert fairy["enabled"] is True


def test_jump_challenge_builder_still_splits_ordinals():
    section = {
        "map_name": "珠星大厦",
        "texts": ["第1个二次元 JUMP", "先跳", "第2个二次元 JUMP", "再跳"],
        "observations": [],
    }
    units = build_units_for_topic("jump", [section])
    assert len(units) == 2
    assert [item["article_ordinal"] for item in units] == [1, 2]
