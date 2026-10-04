import pytest
"""Point matcher binds headings to official grease points without inventing coordinates."""

from fastapi.testclient import TestClient

from hsrmap.guides.matching.cv import locate_on_raster
from hsrmap.guides.matching.matcher import match_sections
from hsrmap.viewer_app import create_app


def _points_and_blocks():
    topic = TestClient(create_app()).get("/api/v1/topics/floating-grease").json()["origin"]
    points = []
    blocks = []
    for item in topic["maps"]:
        heading = item["name"] or item.get("path") or item["map_id"]
        blocks.append({"type": "heading", "text": heading})
        for index, point in enumerate(item["points"], start=1):
            points.append(
                {
                    "source_point_id": point["source_id"],
                    "map_name": item["name"],
                    "map_id": item["map_id"],
                    "name": "浮脂溯源",
                    "label": "浮脂溯源",
                    "path": item.get("path") or item["name"] or item["map_id"],
                }
            )
            blocks.append({"type": "paragraph", "text": f"第{index}处 浮脂溯源"})
    return topic, points, blocks

@pytest.mark.data

def test_haiyuan_heading_matches_official_points():
    _, points, _ = _points_and_blocks()
    blocks = [
        {"type": "heading", "text": "海原市"},
        {"type": "paragraph", "text": "地图共有3个浮脂溯源"},
    ]
    haiyuan = [p for p in points if p["map_name"] == "海原市"]
    results = match_sections(blocks, haiyuan)
    assert {r["source_point_id"] for r in results} == {p["source_point_id"] for p in haiyuan}
    assert all(r["confidence"] >= 0.5 for r in results)

@pytest.mark.data

def test_matcher_covers_all_48_grease_points():
    topic, points, blocks = _points_and_blocks()
    assert topic["count"] == 48
    results = match_sections(blocks, points)
    assert {r["source_point_id"] for r in results} == {p["source_point_id"] for p in points}
    assert len({r["source_point_id"] for r in results}) == 48

@pytest.mark.data

def test_golden_20_map_sections_match():
    topic, points, _ = _points_and_blocks()
    maps = topic["maps"][:20]
    blocks = []
    expected = set()
    subset = []
    for item in maps:
        heading = item["name"] or item.get("path") or item["map_id"]
        blocks.append({"type": "heading", "text": heading})
        blocks.append({"type": "paragraph", "text": "浮脂溯源"})
        for point in item["points"]:
            expected.add(point["source_id"])
            subset.append(
                {
                    "source_point_id": point["source_id"],
                    "map_name": item["name"],
                    "map_id": item["map_id"],
                    "name": "浮脂溯源",
                    "label": "浮脂溯源",
                    "path": item.get("path") or item["name"] or item["map_id"],
                }
            )
    results = match_sections(blocks, subset)
    assert {r["source_point_id"] for r in results} == expected
    assert len(maps) == 20


def test_cv_hook_is_noop_without_fixture():
    from hsrmap.guides.matching.cv import match_location_map

    assert locate_on_raster("aaa") is None
    located = locate_on_raster("aaa", fixture_hash="deadbeef")
    assert located is not None
    assert "x" in located
    honest = match_location_map(None, None, [])
    assert honest["status"] == "NO_MATCH"
    assert honest["candidate"] is None
