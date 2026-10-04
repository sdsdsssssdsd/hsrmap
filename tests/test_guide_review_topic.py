import pytest
from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app

@pytest.mark.data

def test_review_list_filters_by_topic(tmp_path, guide_dbs):
    #: viewer 只读写、不建库（a1-8 四.2）：工作库 / 发布库由夹具显式建出来。
    app = create_app(guide_path=guide_dbs / "guide.db", guide_assets=tmp_path / "ga")
    client = TestClient(app)
    client.post(
        "/api/v1/review/items",
        json={
            "page": {"id": 1, "title": "黄金的时刻", "canonical_url": "https://t.test/ticker"},
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {"map_name": "黄金的时刻", "topic_key": "dream_ticker", "steps": []},
        },
    )
    client.post(
        "/api/v1/review/items",
        json={
            "page": {"id": 2, "title": "海原市", "canonical_url": "https://t.test/grease"},
            "source_point_id": "",
            "status": "NEEDS_REVIEW",
            "draft": {"map_name": "海原市", "topic_key": "floating_grease", "steps": []},
        },
    )
    ticker = client.get("/api/v1/review/items?topic=dream-ticker").json()["items"]
    grease = client.get("/api/v1/review/items?topic=floating-grease").json()["items"]
    assert len(ticker) == 1
    assert ticker[0]["draft"]["topic_key"] == "dream_ticker"
    assert len(grease) == 1
    assert grease[0]["draft"]["topic_key"] == "floating_grease"


def test_review_console_mentions_topic_filter(tmp_path, guide_dbs):
    #: 这条只验证页面/脚本内容，却用默认路径建 app —— 那会在 checkout 根目录建出 data/
    #: （submit 副本里就是一次真实的目录污染）。给它一个临时库，别碰默认路径。
    app_kwargs = {"guide_path": guide_dbs / "guide.db", "user_path": tmp_path / "user.db"}
    html = TestClient(create_app(**app_kwargs)).get("/review").text
    js = TestClient(create_app(**app_kwargs)).get("/review.js").text
    assert "topic" in html.lower()
    assert "topic=" in js or "topic=" in html
    assert "浮脂" not in html or "官方候选" in html
    assert "body.items" in js
