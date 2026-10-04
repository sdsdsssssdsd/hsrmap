"""User progress lives in user.db and never writes core.db / detail.db."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_user_progress_roundtrip_import_export(tmp_path):
    app = create_app(user_path=tmp_path / "user.db")
    client = TestClient(app)
    assert client.get("/health").json()["user"] == "READY"
    missing = client.get("/api/v1/user/points/5171").json()
    assert missing == {"source_point_id": "5171", "completed": False, "favorite": False, "note": None, "stable_key": None}
    saved = client.put(
        "/api/v1/user/points/5171",
        json={"completed": True, "favorite": True, "note": "海原市第三处", "stable_key": "842:5171"},
    )
    assert saved.status_code == 200
    got = client.get("/api/v1/user/points/5171").json()
    assert got["completed"] is True
    assert got["favorite"] is True
    assert got["note"] == "海原市第三处"
    exported = client.get("/api/v1/user/export").json()
    assert exported["version"] == 1
    assert exported["points"][0]["source_point_id"] == "5171"
    other = TestClient(create_app(user_path=tmp_path / "user2.db"))
    imported = other.post("/api/v1/user/import", json=exported)
    assert imported.status_code == 200
    assert other.get("/api/v1/user/points/5171").json()["completed"] is True
