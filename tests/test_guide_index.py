"""Published guides appear in the offline index and never mix official assets."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_guide_index_and_guide_asset(tmp_path, guide_dbs):
    guide_path = guide_dbs / "guide.db"
    assets = tmp_path / "guide-assets" / "sha256"
    assets.mkdir(parents=True)
    sha = "ab" + "c" * 62
    dest = assets / sha[:2]
    dest.mkdir(parents=True)
    (dest / f"{sha}.png").write_bytes(b"PNG-guide")
    app = create_app(guide_path=guide_path, guide_assets=assets)
    client = TestClient(app)
    empty = client.get("/api/v1/guides/index").json()
    assert empty["points"] == {}
    created = client.post(
        "/api/v1/guides",
        json={
            "source_point_id": "5171",
            "title": "海原市浮脂",
            "source_kind": "Community",
            "status": "published",
            "steps": [{"text": "转", "images": [sha]}],
        },
    )
    assert created.status_code == 200
    index = client.get("/api/v1/guides/index").json()
    assert index["points"]["5171"] >= 1
    listed = client.get("/api/v1/guides/by-point/5171").json()["entries"]
    assert listed[0]["steps"][0]["images"] == [sha]
    asset = client.get(f"/guide-assets/{sha}")
    assert asset.status_code == 200
    assert asset.content == b"PNG-guide"
