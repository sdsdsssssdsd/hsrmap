from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hsrmap.database import CoreDatabase
from hsrmap.detail_db import DetailDatabase
from hsrmap.detail_enrich import file_sha256
#: 运行态路径按「调用时」解析：长驻进程里 import 时算死的常量会锁住错误的运行目录（a1-8 四.2）。
from hsrmap import paths


class SnapshotMismatchError(RuntimeError):
    pass


def _open_immutable(path: Path, opener):
    return opener(path, readonly=True, immutable=True)


@dataclass
class ViewerContext:
    snapshot_id: str
    core: CoreDatabase
    detail: DetailDatabase | None
    detail_state: str
    core_manifest_sha256: str | None = None
    #: Map Graph（M7.4）：`viewer_repo.graph_handle()` 惰性挂上来的只读图句柄。
    #: core.db 里有 map_edges 时它就是 core 自己的连接（owned=False，不重复关），
    #: 否则是旁挂库 data/graph/core.db 的只读连接（owned=True，由这里负责关）。
    graph: Any = None

    def close(self) -> None:
        handle = self.graph
        if getattr(handle, "owned", False) and getattr(handle, "conn", None) is not None:
            handle.conn.close()
        self.graph = None
        self.core.close()
        if self.detail is not None:
            self.detail.close()


def bind_viewer(
    current_path: Path | None = None,
    core_path: Path | None = None,
    detail_path: Path | None = None,
    core_manifest_sha256: str | None = None,
) -> ViewerContext:
    current_path = Path(current_path or paths.CURRENT_PATH)
    current = json.loads(current_path.read_text(encoding="utf-8"))
    snapshot_id = current["snapshot_id"]
    if core_path is None:
        core_path = paths.SNAPSHOTS / snapshot_id / "core.db"
    core = _open_immutable(Path(core_path), CoreDatabase)
    if core_manifest_sha256 is None:
        manifest = paths.SNAPSHOTS / snapshot_id / "manifest.json"
        core_manifest_sha256 = file_sha256(manifest) if manifest.exists() else None
    resolved_detail = Path(detail_path) if detail_path is not None else paths.ENRICHMENTS / snapshot_id / "detail.db"
    if not resolved_detail.exists():
        return ViewerContext(snapshot_id, core, None, "CORE_ONLY", core_manifest_sha256)
    detail = _open_immutable(resolved_detail, DetailDatabase)
    bound = detail.get_meta("core_snapshot_id")
    bound_manifest = detail.get_meta("core_manifest_sha256")
    if bound != snapshot_id:
        detail.close()
        core.close()
        raise SnapshotMismatchError(f"detail snapshot {bound} != {snapshot_id}")
    if core_manifest_sha256 and bound_manifest and bound_manifest != core_manifest_sha256:
        detail.close()
        core.close()
        raise SnapshotMismatchError("detail core_manifest_sha256 mismatch")
    return ViewerContext(snapshot_id, core, detail, "READY", core_manifest_sha256)
