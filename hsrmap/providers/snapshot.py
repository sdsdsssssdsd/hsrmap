from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hsrmap.viewer_bind import ViewerContext
from hsrmap.viewer_repo import (
    map_labels_payload,
    map_payload,
    map_tree_payload,
    point_detail,
    points_for_map,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SnapshotProvider:
    def __init__(self, ctx: ViewerContext):
        self.ctx = ctx

    def provenance(self) -> dict[str, Any]:
        return {
            "source": "snapshot",
            "fetched_at": _now(),
            "snapshot_fallback": self.ctx.snapshot_id,
            "stale": False,
        }

    def get_tree(self, refresh: bool = False) -> list[dict[str, Any]]:
        return map_tree_payload(self.ctx)

    def get_map(self, map_id: str, refresh: bool = False) -> dict[str, Any] | None:
        return map_payload(self.ctx, map_id)

    def get_points(
        self,
        map_id: str,
        semantic_key: str | None = None,
        label_id: str | None = None,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        return points_for_map(self.ctx, map_id, semantic_key=semantic_key, label_id=label_id)

    def get_labels(self, map_id: str, refresh: bool = False) -> list[dict[str, Any]] | None:
        return map_labels_payload(self.ctx, map_id)

    def get_point(self, core_point_id: int, refresh: bool = False) -> dict[str, Any] | None:
        return point_detail(self.ctx, core_point_id)
