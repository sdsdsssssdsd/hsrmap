from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.topics.official import official_points_for_topic
from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def _published_app(tmp_path, source_point_id: str, title: str):
    published = GuideDatabase(tmp_path / "p.db")
    source = published.upsert_source({"name": "t", "domain": "t.test"})
    page = published.add_page(source["id"], {"canonical_url": "https://t.test/route", "title": title})
    published.create_entry(
        {
            "source_point_id": source_point_id,
            "title": title,
            "status": "published",
            "page_id": page["id"],
        }
    )
    published.close()
    return TestClient(
        create_app(guide_path=tmp_path / "w.db", published_path=tmp_path / "p.db", guide_assets=tmp_path / "ga")
    )


def test_by_point_returns_map_label_route_for_official_bird(tmp_path):
    birds = [row for row in official_points_for_topic("origami_bird") if str(row.get("map_id")) == "150"]
    assert birds, "official 筑梦边境 birds required"
    pid = str(birds[0]["source_point_id"])
    other = next(
        str(row["source_point_id"])
        for row in official_points_for_topic("origami_bird")
        if str(row.get("map_id")) == "149"
    )
    client = _published_app(tmp_path, "map:150:topic:origami_bird", "筑梦边境折纸")
    hit = client.get(f"/api/v1/guides/by-point/{pid}").json()["entries"]
    assert hit and hit[0]["title"] == "筑梦边境折纸"
    miss = client.get(f"/api/v1/guides/by-point/{other}").json()["entries"]
    assert miss == []


def test_guide_index_marks_official_points_on_published_map(tmp_path):
    birds = [row for row in official_points_for_topic("origami_bird") if str(row.get("map_id")) == "150"]
    assert birds
    pid = str(birds[0]["source_point_id"])
    client = _published_app(tmp_path, "map:150:topic:origami_bird", "筑梦边境折纸")
    index = client.get("/api/v1/guides/index").json()["points"]
    assert pid in index
    assert "map:150:topic:origami_bird" in index
