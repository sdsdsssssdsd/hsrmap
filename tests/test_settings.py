"""Settings stay offline: snapshot facts, storage, no remote update."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_settings_report_offline_snapshot_and_forbid_remote_update():
    client = TestClient(create_app())
    body = client.get("/api/v1/settings").json()
    assert body["snapshot_id"] == "20261001T105105Z"
    assert body["maps"] == 624
    assert body["points"] == 5330
    assert body["storage"]["core_assets_bytes"] > 0
    assert body["storage"]["detail_assets_bytes"] > 0
    check = client.get("/api/v1/updates/check").json()
    assert check["remote_enabled"] is False
    assert "外网" in check["message"] or "offline" in check["message"].lower() or "离线" in check["message"]
