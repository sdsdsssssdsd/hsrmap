import pytest
from fastapi.testclient import TestClient

from hsrmap.guides.layout.planner import plan_layout
from hsrmap.viewer_app import create_app


def test_plan_layout_emits_puzzle_blocks():
    out = plan_layout(
        {
            "topic_key": "dream_ticker",
            "target_type": "POINT",
            "target_key": "point:99",
            "steps": [{"text": "调指针", "images": ["abc"]}],
        }
    )
    assert out["profile"] == "puzzle_steps_v1"
    assert out["blocks"][0]["type"] == "step"
    assert out["blocks"][0]["text"] == "调指针"
    assert out["target"]["key"] == "point:99"


def test_plan_layout_emits_collection_items():
    out = plan_layout(
        {
            "topic_key": "origami_bird",
            "target_type": "MAP_LABEL",
            "steps": [{"text": "第1只在喷泉", "images": []}],
        }
    )
    assert out["profile"] == "collection_route_v1"
    assert out["blocks"][0]["type"] == "item"

@pytest.mark.data

def test_approve_map_label_without_inventing_point_id(tmp_path):
    app = create_app(
        guide_path=tmp_path / "guide.db",
        published_path=tmp_path / "published.db",
        guide_assets=tmp_path / "ga",
        user_path=tmp_path / "user.db",
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/review/items",
        json={
            "page": {"title": "黄金的时刻小鸟", "canonical_url": "https://t.test/bird"},
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "origami_bird",
                "target_type": "MAP_LABEL",
                "target_key": "map:508:topic:origami_bird",
                "map_name": "黄金的时刻",
                "steps": [{"text": "第1只在喷泉", "images": []}],
            },
        },
    )
    item_id = created.json()["id"]
    listed = client.get("/api/v1/review/items?topic=origami-bird").json()
    assert listed["items"][0]["draft"]["target_type"] == "MAP_LABEL"
    assert listed["items"][0]["layout"]["blocks"]
    approved = client.post(f"/api/v1/review/items/{item_id}/approve")
    assert approved.status_code == 200
    entries = client.get("/api/v1/guides/by-point/map:508:topic:origami_bird").json()["entries"]
    assert entries[0]["title"]
    assert entries[0]["source_point_id"] == "map:508:topic:origami_bird"

@pytest.mark.data

def test_approve_map_label_without_target_key_is_rejected(tmp_path):
    app = create_app(
        guide_path=tmp_path / "guide.db",
        published_path=tmp_path / "published.db",
        guide_assets=tmp_path / "ga",
        user_path=tmp_path / "user.db",
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/review/items",
        json={
            "page": {"title": "小鸟", "canonical_url": "https://t.test/bird2"},
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {"topic_key": "origami_bird", "target_type": "MAP_LABEL", "steps": []},
        },
    )
    resp = client.post(f"/api/v1/review/items/{created.json()['id']}/approve")
    assert resp.status_code == 409


def test_review_exposes_official_candidate_thumbs_and_human_bind(tmp_path):
    _make_guide_dbs(tmp_path)
    app = create_app(
        guide_path=tmp_path / "guide.db",
        published_path=tmp_path / "published.db",
        guide_assets=tmp_path / "ga",
        user_path=tmp_path / "user.db",
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/review/items",
        json={
            "page": {"title": "酒店迷钟", "canonical_url": "https://t.test/hotel-ticker"},
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {
                "topic_key": "dream_ticker",
                "map_name": "「白日梦」酒店-梦境",
                "candidate_points": [{"source_point_id": "1681"}, {"source_point_id": "1672"}],
            },
        },
    )
    item_id = created.json()["id"]
    patched = client.patch(
        f"/api/v1/review/items/{item_id}",
        json={"source_point_id": "1681", "binding_method": "HUMAN_REVIEW"},
    )
    assert patched.status_code == 200
    assert patched.json()["source_point_id"] == "1681"
    assert patched.json()["draft"]["binding_method"] == "HUMAN_REVIEW"
    js = client.get("/review.js").text
    assert "official_image_url" in js or "official-cands" in js
    html = client.get("/review").text
    assert "官方候选" in html


def _make_guide_dbs(tmp_path):
    """viewer 不再隐式建库（a1-8 四.2）：需要库的测试自己把空库建出来。"""
    from hsrmap.guide_db import GuideDatabase

    for name in ("guide.db", "published.db"):
        GuideDatabase.create(tmp_path / name).close()

def test_review_console_mentions_preview_and_target(tmp_path):
    _make_guide_dbs(tmp_path)
    #: 同上：页面内容测试不该用默认路径建库（会在仓库/submit 里建出 data/）。
    app_kwargs = {"guide_path": tmp_path / "guide.db", "user_path": tmp_path / "user.db"}
    html = TestClient(create_app(**app_kwargs)).get("/review").text
    js = TestClient(create_app(**app_kwargs)).get("/review.js").text
    assert "preview" in html.lower() or "预览" in html
    assert "target" in html.lower() or "target_type" in js
