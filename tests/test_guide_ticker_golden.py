import pytest
from hsrmap.guides.discover import load_seeds_for_topic
from hsrmap.guides.topics.official import official_points_for_topic

@pytest.mark.data

def test_dream_ticker_official_points_are_core_ids():
    points = official_points_for_topic("dream_ticker")
    assert len(points) == 43
    assert all(str(row["source_point_id"]).isdigit() for row in points)
    assert all("迷钟" in row["label"] for row in points)
    assert len({row["source_point_id"] for row in points}) == 43


def test_dream_ticker_seeds_are_map_guides_not_hanu_minigame():
    seeds = load_seeds_for_topic("dream_ticker")
    urls = " ".join(seeds["urls"])
    assert "1708650" in urls
    assert "1708098" not in urls
    assert "786205107434815910" not in urls
