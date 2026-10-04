"""Guides stay out of detail.db and attach to a source point."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_guide_empty_then_local_entry(guide_dbs):
    #: viewer 只读写、不建库（a1-8 四.2）：工作库 / 发布库由夹具显式建出来。
    client = TestClient(create_app(guide_path=guide_dbs / "guide.db"))
    empty = client.get("/api/v1/guides/by-point/5171").json()
    assert empty["entries"] == []
    created = client.post(
        "/api/v1/guides",
        json={
            "source_point_id": "5171",
            "title": "海原市浮脂",
            "summary": "先转再点",
            "source_name": "本地",
            "source_kind": "Local",
            "author": "self",
            "status": "published",
            "steps": [{"text": "Step 1 对准旋转机关", "images": []}],
        },
    )
    assert created.status_code == 200
    listed = client.get("/api/v1/guides/by-point/5171").json()["entries"]
    assert listed[0]["title"] == "海原市浮脂"
    assert listed[0]["source_kind"] == "Local"
    assert listed[0]["steps"][0]["text"].startswith("Step 1")
