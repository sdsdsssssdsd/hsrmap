import pytest
from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.publishing.sync import sync_published
from hsrmap.viewer_app import create_app


def test_working_draft_does_not_appear_in_published_db(tmp_path):
    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    working.create_entry({"source_point_id": "1", "title": "draft", "status": "draft", "steps": []})
    working.create_entry({"source_point_id": "5171", "title": "海原", "status": "published", "steps": [{"text": "转", "images": []}]})
    copied = sync_published(working, published)
    assert copied == 1
    assert published.list_for_point("1") == []
    assert published.list_for_point("5171")[0]["title"] == "海原"
    assert working.list_for_point("1")[0]["status"] == "draft"
    working.close()
    published.close()

@pytest.mark.data

def test_viewer_public_index_reads_published_not_working(tmp_path, guide_dbs):
    #: viewer 只读写、不建库（a1-8 四.2）：工作库 + 发布库都由夹具显式建出来。
    working = guide_dbs / "guide.db"
    published = guide_dbs / "published.db"
    GuideDatabase(working).create_entry(
        {"source_point_id": "5171", "title": "只在工作库", "status": "draft", "steps": []}
    )
    app = create_app(guide_path=working, published_path=published, guide_assets=tmp_path / "ga")
    client = TestClient(app)
    assert client.get("/api/v1/guides/index").json()["points"] == {}
    created = client.post(
        "/api/v1/guides",
        json={"source_point_id": "5171", "title": "正式", "status": "published", "steps": [{"text": "转", "images": []}]},
    )
    assert created.status_code == 200
    assert client.get("/api/v1/guides/index").json()["points"]["5171"] >= 1
    titles = [row["title"] for row in client.get("/api/v1/guides/by-point/5171").json()["entries"]]
    assert "正式" in titles
    assert "只在工作库" not in titles
