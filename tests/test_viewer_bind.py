"""Viewer must refuse mismatched core/detail and allow CORE_ONLY."""

import json
from pathlib import Path

import pytest

from hsrmap.database import CoreDatabase
from hsrmap.detail_db import DetailDatabase
from hsrmap.viewer_bind import SnapshotMismatchError, bind_viewer


def test_bind_rejects_detail_from_other_snapshot(tmp_path: Path):
    core = CoreDatabase(tmp_path / "core.db")
    core.close()
    current = {"snapshot_id": "AAA", "path": "snapshots/AAA", "core_db": "core.db"}
    (tmp_path / "current.json").write_text(json.dumps(current), encoding="utf-8")
    detail = DetailDatabase(tmp_path / "detail.db")
    detail.set_meta("core_snapshot_id", "BBB")
    detail.set_meta("core_manifest_sha256", "deadbeef")
    detail.close()
    with pytest.raises(SnapshotMismatchError):
        bind_viewer(tmp_path / "current.json", tmp_path / "core.db", tmp_path / "detail.db", core_manifest_sha256="abc")


def test_bind_allows_missing_detail_as_core_only(tmp_path: Path):
    core = CoreDatabase(tmp_path / "core.db")
    core.close()
    current = {"snapshot_id": "AAA", "path": "snapshots/AAA", "core_db": "core.db"}
    (tmp_path / "current.json").write_text(json.dumps(current), encoding="utf-8")
    ctx = bind_viewer(tmp_path / "current.json", tmp_path / "core.db", tmp_path / "missing-detail.db", core_manifest_sha256="abc")
    assert ctx.detail_state == "CORE_ONLY"
    assert ctx.detail is None
    ctx.close()
