"""Local viewer API is offline, versioned, and never exposes filesystem paths."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def client() -> TestClient:
    return TestClient(create_app())


def test_health_and_meta_bind_current_snapshot():
    response = client().get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["core"] == "READY"
    meta = client().get("/api/v1/meta").json()
    assert meta["api_version"] == 1
    assert meta["core_snapshot"] == "20261001T105105Z"
    assert meta["maps"] == 624
    assert meta["points"] == 5330
    assert meta["floating_grease"]["count"] == 48


def test_map_842_uses_raster_not_tile_grid():
    body = client().get(" /api/v1/maps/842".replace(" ", "")).json()
    assert body["id"] == "842"
    assert body["name"] == "海原市"
    assert body["width"] == 8192
    assert body["height"] == 4096
    assert body["origin"] == [3827.0, 2217.0]
    assert body["padding"] == [1706, 219]
    assert "asset" in body["raster"]
    assert len(body["raster"]["asset"]) == 64


def test_points_use_source_xy_and_include_core_id():
    points = client().get("/api/v1/maps/842/points").json()
    assert len(points) == 48
    grease = [p for p in points if any(label.get("name", "").startswith("浮脂溯源") for label in p["labels"])]
    hit = next(p for p in points if p["source_id"] == "5171")
    assert hit["x"] == 85.5
    assert hit["y"] == -81.5
    assert "id" in hit
    assert grease


def test_point_5171_has_local_detail_image_and_5260_is_empty():
    points = {p["source_id"]: p for p in client().get("/api/v1/maps/842/points").json()}
    nonempty = client().get(f"/api/v1/points/{points['5171']['id']}").json()
    assert nonempty["detail"]["state"] == "NONEMPTY"
    assert "浮脂溯源" in (nonempty["detail"]["text"] or "")
    assert nonempty["detail"]["images"]
    assert nonempty["detail"]["images"][0]["url"].startswith("/assets/")
    empty = client().get(f"/api/v1/points/{points['5260']['id']}").json()
    assert empty["detail"]["state"] == "EMPTY"
    assert empty["detail"]["text"] is None
    assert empty["detail"]["images"] == []


def test_asset_rejects_path_escape_and_serves_sha():
    forbidden = client().get("/assets/../core.db")
    assert forbidden.status_code in {400, 404}
    sha = client().get("/api/v1/maps/842").json()["raster"]["asset"]
    ok = client().get(f"/assets/{sha}")
    assert ok.status_code == 200
    assert ok.headers["etag"].strip('"') == sha
    assert "immutable" in ok.headers.get("cache-control", "")
