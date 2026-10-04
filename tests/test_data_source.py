"""Data source mode is explicit and defaults to snapshot for tests."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_default_app_reports_offline_snapshot_source():
    body = TestClient(create_app()).get("/api/v1/data-source").json()
    assert body["mode"] == "offline"
    assert body["source"] == "snapshot"
    assert body["snapshot_id"] == "20261001T105105Z"
    assert body["remote_enabled"] is False
