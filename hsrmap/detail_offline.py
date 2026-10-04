from __future__ import annotations

import random
import urllib.request
from typing import Any

from hsrmap.database import CoreDatabase
from hsrmap.detail_db import DetailDatabase
from hsrmap.paths import ASSETS


def validate_details_offline(
    core: CoreDatabase,
    detail: DetailDatabase,
    extra_source_ids: set[str] | None = None,
    sample_size: int = 100,
) -> dict[str, Any]:
    original = urllib.request.urlopen
    hits = {"count": 0}

    def blocked(*_args, **_kwargs):
        hits["count"] += 1
        raise AssertionError("offline detail validation attempted network access")

    urllib.request.urlopen = blocked  # type: ignore[assignment]
    try:
        rows = list(core.conn.execute("SELECT id, source_id FROM points"))
        sample = rows if len(rows) <= sample_size else random.Random(20261001).sample(rows, sample_size)
        wanted = {int(row["id"]) for row in sample}
        if extra_source_ids:
            for row in core.conn.execute(
                f"SELECT id, source_id FROM points WHERE source_id IN ({','.join('?' * len(extra_source_ids))})",
                tuple(extra_source_ids),
            ):
                wanted.add(int(row["id"]))
        inspected = []
        missing = []
        for core_point_id in sorted(wanted):
            binding = detail.conn.execute(
                "SELECT * FROM point_detail_bindings WHERE core_point_id = ?",
                (core_point_id,),
            ).fetchone()
            if binding is None:
                missing.append(core_point_id)
                continue
            row = detail.conn.execute("SELECT * FROM point_details WHERE id = ?", (binding["detail_id"],)).fetchone()
            images = list(detail.conn.execute("SELECT * FROM point_detail_assets WHERE detail_id = ?", (binding["detail_id"],)))
            for image in images:
                sha = image["asset_sha256"]
                if sha and not any((ASSETS / sha[:2]).glob(f"{sha}.*")):
                    missing.append(f"asset:{sha}")
            inspected.append(
                {
                    "core_point_id": core_point_id,
                    "source_point_id": None if row is None else row["source_point_id"],
                    "title": None if row is None else row["title"],
                    "plain_text": None if row is None else row["plain_text"],
                    "images": len(images),
                }
            )
        return {
            "passed": hits["count"] == 0 and not missing,
            "network_hits": hits["count"],
            "inspected": len(inspected),
            "missing": missing,
        }
    finally:
        urllib.request.urlopen = original
