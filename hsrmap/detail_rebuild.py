from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hsrmap.detail_db import DetailDatabase
from hsrmap.detail_normalize import normalize_point_info
from hsrmap.detail_queue import point_info_request_key, point_info_request_params


def rebuild_detail_from_raw(raw_dir: Path, db_path: Path, app_version: str) -> DetailDatabase:
    db = DetailDatabase(db_path)
    for path in sorted(Path(raw_dir).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        parsed = normalize_point_info(payload)
        source_id = parsed.get("source_point_id") or path.stem
        key = point_info_request_key(source_id, app_version) if parsed.get("source_point_id") else path.stem
        db.upsert_request(
            {
                "request_key": key,
                "endpoint_name": "point_info",
                "parameters": point_info_request_params(source_id, app_version),
                "state": parsed["job_state"],
                "last_retcode": parsed.get("retcode"),
                "raw_relpath": str(path),
                "raw_sha256": digest,
            }
        )
        db.save_detail(key, parsed, digest)
    return db
