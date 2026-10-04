from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_guides_atlas_lists_enabled_topics():
    body = TestClient(create_app()).get("/api/v1/guides/atlas").json()
    topics = {item["topic"]: item for item in body["topics"]}
    assert topics["floating_grease"]["official_points"] == 48
    assert topics["dream_ticker"]["official_targets"] >= 40
    assert topics["nymph"]["official_targets"] >= 200
    assert topics["nameless_dust_spirit"]["official_targets"] >= 150
    assert topics["origami_bird"]["scope"] == "MAP_LABEL"
    assert body["publish"] == "never_auto"
    assert "external_network" not in body or body.get("viewer_network") == 0
    assert topics["magnetic_puzzle"]["official_targets"] == 0
    assert topics["magnetic_puzzle"]["official_status"] == "LABEL_EXISTS_NO_POINTS"
    assert topics["unworldly_material"]["official_status"] == "LABEL_EXISTS_NO_POINTS"
    assert topics["hidden_treasure"]["enabled"] is False
    assert topics["hidden_treasure"]["official_targets"] == 0
    assert topics["hidden_treasure"]["official_status"] in {"NO_OFFICIAL_TARGET", "LABEL_EXISTS_NO_POINTS"}


def test_atlas_counts_map_label_published_routes(tmp_path, make_guide_db):
    #: viewer 只读写、不建库（a1-8 四.2）：工作库先显式建出来。
    make_guide_db("w.db")
    published = GuideDatabase(tmp_path / "p.db")
    source = published.upsert_source({"name": "t", "domain": "t.test"})
    page = published.add_page(source["id"], {"canonical_url": "https://t.test/bird", "title": "t"})
    published.create_entry(
        {
            "source_point_id": "map:149:topic:origami_bird",
            "title": "黄金的时刻小鸟",
            "status": "published",
            "page_id": page["id"],
        }
    )
    published.close()
    body = TestClient(
        create_app(guide_path=tmp_path / "w.db", published_path=tmp_path / "p.db", guide_assets=tmp_path / "ga")
    ).get("/api/v1/guides/atlas").json()
    bird = next(item for item in body["topics"] if item["topic"] == "origami_bird")
    assert bird["real_guide_coverage"]["with_approved_guide"] >= 1
