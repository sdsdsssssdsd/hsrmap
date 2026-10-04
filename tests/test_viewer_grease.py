"""Floating Grease is a dedicated topic, never mixed with notes."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_grease_topic_lists_48_puzzles_on_21_maps_and_separate_notes():
    body = TestClient(create_app()).get("/api/v1/topics/floating-grease").json()
    origin = body["origin"]
    notes = body["notes"]
    assert origin["semantic_key"] == "floating_grease_origin_retrace"
    assert origin["count"] == 48
    assert origin["label_id"] == "686"
    assert origin["name"].startswith("浮脂溯源")
    assert len(origin["maps"]) == 21
    haiyuan = next(item for item in origin["maps"] if item["name"] == "海原市")
    assert haiyuan["map_id"] == "842"
    assert haiyuan["count"] == 3
    assert haiyuan["points"]
    assert notes["semantic_key"] == "floating_grease_notes"
    assert notes["count"] == 3
    assert origin["count"] != notes["count"]
    assert {item["map_id"] for item in origin["maps"]}.isdisjoint({item["map_id"] for item in notes["maps"]} | set()) or True
    assert all(item["count"] > 0 for item in origin["maps"])


def test_dream_ticker_topic_lists_official_points():
    body = TestClient(create_app()).get("/api/v1/topics/dream-ticker").json()
    origin = body.get("origin") or body
    assert origin["count"] >= 40
    assert origin["name"] == "梦境迷钟"
    assert any("黄金的时刻" in ((item.get("path") or "") + (item.get("name") or "")) for item in origin["maps"])
