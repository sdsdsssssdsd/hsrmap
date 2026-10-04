"""审核台与离线地图是两个分区（用户 2026-10 要求：不要耦合）。

* map-only：不打开 guide.db / published.db；
* review-only：不绑定快照 / core.db；
* all：两者都在（兼容老用法的回归）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hsrmap.guide_db import GuideDatabase
from hsrmap.serve import KINDS, build_app, resolve_kind, resolve_port
from hsrmap.viewer_app import create_app, create_map_app, create_review_app


def test_resolve_kind_and_ports() -> None:
    assert resolve_kind(None) == "map"
    assert resolve_kind("review") == "review"
    assert resolve_port(None, "map") == 8766
    assert resolve_port(None, "review") == 8767
    assert KINDS["all"] == ("map", "review")
    with pytest.raises(SystemExit):
        resolve_kind("nope")


def test_map_only_needs_no_guide_database(tmp_path: Path) -> None:
    """地图进程里根本没有攻略库：能启动、有地图端点、没有审核端点。"""
    missing_guide = tmp_path / "guide.db"
    missing_published = tmp_path / "published.db"
    app = create_map_app(
        user_path=tmp_path / "user.db",
        guide_path=missing_guide,
        published_path=missing_published,
        guide_assets=tmp_path / "ga",
        live_cache=tmp_path / "live",
    )
    assert app.state.guide is None and app.state.published is None
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/review/items").status_code == 404
    assert not missing_guide.exists() and not missing_published.exists(), "地图进程不许建攻略库"


def test_review_only_needs_no_snapshot(tmp_path: Path) -> None:
    """审核进程里没有快照/core.db：能启动、有审核页、没有地图端点。"""
    for name in ("guide.db", "published.db"):
        GuideDatabase.create(tmp_path / name).close()
    app = create_review_app(
        user_path=tmp_path / "user.db",
        guide_path=tmp_path / "guide.db",
        published_path=tmp_path / "published.db",
        guide_assets=tmp_path / "ga",
    )
    client = TestClient(app)
    assert client.get("/review").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/maps/tree").status_code == 404
    assert client.get("/", follow_redirects=False).status_code in (302, 307)


def test_all_sections_keeps_both(tmp_path: Path) -> None:
    for name in ("guide.db", "published.db"):
        GuideDatabase.create(tmp_path / name).close()
    app = create_app(
        user_path=tmp_path / "user.db",
        guide_path=tmp_path / "guide.db",
        published_path=tmp_path / "published.db",
        guide_assets=tmp_path / "ga",
    )
    paths = set(app.openapi()["paths"])
    assert "/review" in paths
    assert "/api/v1/review/items" in paths
    assert "/api/v1/maps/tree" in paths


def test_build_app_selects_sections(tmp_path: Path, monkeypatch) -> None:
    for name in ("guide.db", "published.db"):
        GuideDatabase.create(tmp_path / name).close()
    app = build_app(
        "review",
        data_mode="offline",
        user_path=tmp_path / "user.db",
        guide_path=tmp_path / "guide.db",
        published_path=tmp_path / "published.db",
        guide_assets=tmp_path / "ga",
    )
    paths = set(app.openapi()["paths"])
    assert "/review" in paths
    assert "/api/v1/maps/tree" not in paths
