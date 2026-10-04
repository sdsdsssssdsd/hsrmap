"""get_ctx rebuilds the provider if a concurrent request only bound the snapshot."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_data_source_rebuilds_provider_if_missing(tmp_path):
    app = create_app(user_path=tmp_path / "user.db")
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    del app.state.provider
    body = client.get("/api/v1/data-source").json()
    assert body["source"] == "snapshot"
    assert body["mode"] == "offline"
