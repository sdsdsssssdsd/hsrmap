"""Phase 3 must open core.db read-only so the frozen snapshot bytes do not change."""

from pathlib import Path

from hsrmap.database import CoreDatabase
from hsrmap.detail_enrich import file_sha256


def test_readonly_open_does_not_change_core_bytes(tmp_path: Path):
    path = tmp_path / "core.db"
    db = CoreDatabase(path)
    db.conn.execute(
        "INSERT INTO map_nodes(source_id, parent_source_id, node_type, name, is_renderable) VALUES ('1', NULL, 1, 'root', 0)"
    )
    db.conn.commit()
    db.close()
    before = file_sha256(path)
    readonly = CoreDatabase(path, readonly=True)
    assert readonly.conn.execute("SELECT COUNT(*) n FROM map_nodes").fetchone()["n"] == 1
    readonly.close()
    assert file_sha256(path) == before
