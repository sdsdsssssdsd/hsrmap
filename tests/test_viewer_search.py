"""Search prefers semantic label names, then maps and point text."""

from fastapi.testclient import TestClient

from hsrmap.viewer_app import create_app
import pytest

pytestmark = pytest.mark.data


def test_search_floating_grease_hits_semantic_label_first():
    body = TestClient(create_app()).get("/api/v1/search", params={"q": "浮脂溯源"}).json()
    assert body["labels"]
    assert body["labels"][0]["name"].startswith("浮脂溯源")
    assert body["labels"][0]["count"] == 48
    assert any("海原市" in (item.get("path") or item.get("name") or "") for item in body["maps"] + body["points"])
