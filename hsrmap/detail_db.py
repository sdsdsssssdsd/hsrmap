from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


#: 明细库的 schema 版本（a1-8 十六.7）。
SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS schema_versions (
    name TEXT PRIMARY KEY,
    fingerprint TEXT,
    observed_at TEXT
);

CREATE TABLE IF NOT EXISTS detail_requests (
    id INTEGER PRIMARY KEY,
    request_key TEXT NOT NULL UNIQUE,
    endpoint_name TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_http_status INTEGER,
    last_retcode INTEGER,
    last_error TEXT,
    raw_relpath TEXT,
    raw_sha256 TEXT,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS point_details (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL UNIQUE,
    source_point_id TEXT,
    title TEXT,
    subtitle TEXT,
    plain_text TEXT,
    content_format TEXT,
    content_raw TEXT,
    is_empty INTEGER NOT NULL DEFAULT 0,
    detail_state TEXT,
    raw_sha256 TEXT,
    FOREIGN KEY(request_id) REFERENCES detail_requests(id)
);

CREATE TABLE IF NOT EXISTS point_detail_bindings (
    core_point_id INTEGER NOT NULL UNIQUE,
    detail_id INTEGER NOT NULL,
    source_point_id TEXT,
    map_source_id TEXT,
    FOREIGN KEY(detail_id) REFERENCES point_details(id)
);

CREATE TABLE IF NOT EXISTS point_detail_assets (
    id INTEGER PRIMARY KEY,
    detail_id INTEGER NOT NULL,
    asset_sha256 TEXT,
    role TEXT,
    sort_order INTEGER,
    remote_url TEXT,
    alt_text TEXT,
    metadata_json TEXT,
    state TEXT,
    FOREIGN KEY(detail_id) REFERENCES point_details(id)
);

CREATE TABLE IF NOT EXISTS unclassified_asset_candidates (
    id INTEGER PRIMARY KEY,
    detail_id INTEGER,
    remote_url TEXT NOT NULL,
    reason TEXT,
    FOREIGN KEY(detail_id) REFERENCES point_details(id)
);

CREATE TABLE IF NOT EXISTS asset_sources (
    remote_url TEXT PRIMARY KEY,
    sha256 TEXT,
    etag TEXT,
    last_modified TEXT
);

CREATE INDEX IF NOT EXISTS idx_detail_state ON detail_requests(state);
CREATE INDEX IF NOT EXISTS idx_detail_source ON point_details(source_point_id);
CREATE INDEX IF NOT EXISTS idx_bind_detail ON point_detail_bindings(detail_id);
CREATE INDEX IF NOT EXISTS idx_assets_detail ON point_detail_assets(detail_id);
"""

TERMINAL = {
    "COMPLETE",
    "COMPLETE_EMPTY",
    "SOURCE_NOT_FOUND",
    "FAILED_PERMANENT",
    "FAILED_SCHEMA",
    "SOURCE_ASSET_BROKEN",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class DetailDatabase:
    def __init__(self, path: Path, readonly: bool = False, immutable: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        self._lock = threading.Lock()
        if readonly:
            query = "mode=ro"
            if immutable:
                query += "&immutable=1"
            uri = f"file:{self.path.as_posix()}?{query}"
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        #: 明确写进 SQLite 自己的版本位（a1-8 十六.7），与 schema_versions 表互为佐证。
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        if not self.readonly:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def integrity_ok(self) -> bool:
        row = self.conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] == "ok"

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            (key, value if isinstance(value, str) else _dump(value)),
        )
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def upsert_request(self, item: dict[str, Any]) -> int:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO detail_requests
                (request_key, endpoint_name, parameters_json, state, attempts, last_http_status, last_retcode, last_error, raw_relpath, raw_sha256, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_key) DO UPDATE SET
                    endpoint_name=excluded.endpoint_name,
                    parameters_json=excluded.parameters_json,
                    state=excluded.state,
                    attempts=COALESCE(excluded.attempts, detail_requests.attempts),
                    last_http_status=excluded.last_http_status,
                    last_retcode=excluded.last_retcode,
                    last_error=excluded.last_error,
                    raw_relpath=COALESCE(excluded.raw_relpath, detail_requests.raw_relpath),
                    raw_sha256=COALESCE(excluded.raw_sha256, detail_requests.raw_sha256),
                    started_at=COALESCE(excluded.started_at, detail_requests.started_at),
                    completed_at=COALESCE(excluded.completed_at, detail_requests.completed_at)
                """,
                (
                    item["request_key"],
                    item.get("endpoint_name", "point_info"),
                    item.get("parameters_json") or _dump(item.get("parameters") or {}),
                    item.get("state", "PENDING"),
                    item.get("attempts", 0),
                    item.get("last_http_status"),
                    item.get("last_retcode"),
                    item.get("last_error"),
                    item.get("raw_relpath"),
                    item.get("raw_sha256"),
                    item.get("started_at"),
                    item.get("completed_at"),
                ),
            )
            self.conn.commit()
            return int(self.get_request(item["request_key"])["id"])

    def get_request(self, request_key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM detail_requests WHERE request_key = ?", (request_key,)).fetchone()

    def mark_state(self, request_key: str, state: str, **fields: Any) -> None:
        with self._lock:
            self._mark_state(request_key, state, **fields)

    def _mark_state(self, request_key: str, state: str, **fields: Any) -> None:
        assignments = ["state = ?"]
        values: list[Any] = [state]
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(value)
        if state == "FETCHING":
            assignments.append("started_at = COALESCE(started_at, ?)")
            values.append(_now())
            assignments.append("attempts = attempts + 1")
        if state in TERMINAL:
            assignments.append("completed_at = ?")
            values.append(_now())
        values.append(request_key)
        self.conn.execute(f"UPDATE detail_requests SET {', '.join(assignments)} WHERE request_key = ?", values)
        self.conn.commit()

    def reset_in_flight(self) -> None:
        self.conn.execute("UPDATE detail_requests SET state = 'PENDING' WHERE state = 'FETCHING'")
        self.conn.commit()

    def pending_keys(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT request_key FROM detail_requests WHERE state IN ('PENDING', 'FAILED_RETRYABLE')"
        ).fetchall()
        return [row["request_key"] for row in rows]

    def enqueue_queue(self, queue: dict[str, Any]) -> None:
        for request in queue["requests"]:
            existing = self.get_request(request["request_key"])
            if existing is None:
                self.upsert_request({**request, "state": "PENDING"})

    def save_detail(self, request_key: str, parsed: dict[str, Any], raw_sha256: str) -> int:
        with self._lock:
            return self._save_detail(request_key, parsed, raw_sha256)

    def _save_detail(self, request_key: str, parsed: dict[str, Any], raw_sha256: str) -> int:
        request = self.get_request(request_key)
        if request is None:
            raise ValueError(f"missing request {request_key}")
        existing = self.conn.execute("SELECT id FROM point_details WHERE request_id = ?", (request["id"],)).fetchone()
        values = (
            parsed.get("source_point_id"),
            parsed.get("title"),
            parsed.get("subtitle"),
            parsed.get("plain_text"),
            parsed.get("content_format"),
            parsed.get("content_raw"),
            1 if parsed.get("is_empty") else 0,
            parsed.get("detail_state"),
            raw_sha256,
        )
        if existing:
            detail_id = int(existing["id"])
            self.conn.execute("DELETE FROM unclassified_asset_candidates WHERE detail_id = ?", (detail_id,))
            self.conn.execute("DELETE FROM point_detail_assets WHERE detail_id = ?", (detail_id,))
            self.conn.execute(
                """
                UPDATE point_details SET
                    source_point_id=?, title=?, subtitle=?, plain_text=?, content_format=?,
                    content_raw=?, is_empty=?, detail_state=?, raw_sha256=?
                WHERE id=?
                """,
                (*values, detail_id),
            )
        else:
            cur = self.conn.execute(
                """
                INSERT INTO point_details
                (source_point_id, title, subtitle, plain_text, content_format, content_raw, is_empty, detail_state, raw_sha256, request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values, request["id"]),
            )
            detail_id = int(cur.lastrowid)
        for image in parsed.get("images") or []:
            self.conn.execute(
                """
                INSERT INTO point_detail_assets
                (detail_id, asset_sha256, role, sort_order, remote_url, alt_text, metadata_json, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detail_id,
                    image.get("asset_sha256"),
                    image.get("role", "image"),
                    image.get("sort_order", 0),
                    image.get("remote_url"),
                    image.get("alt_text"),
                    _dump(image.get("metadata") or {}),
                    image.get("state", "PENDING"),
                ),
            )
        for item in parsed.get("unclassified_asset_candidates") or []:
            self.conn.execute(
                "INSERT INTO unclassified_asset_candidates(detail_id, remote_url, reason) VALUES (?, ?, ?)",
                (detail_id, item["remote_url"], item.get("reason")),
            )
        self.conn.commit()
        return detail_id

    def bind_core_point(self, core_point_id: int, detail_id: int, source_point_id: str | None, map_source_id: str | None) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO point_detail_bindings(core_point_id, detail_id, source_point_id, map_source_id)
            VALUES (?, ?, ?, ?)
            """,
            (core_point_id, detail_id, source_point_id, map_source_id),
        )
        self.conn.commit()

    def set_asset_sha(self, detail_id: int, remote_url: str, sha256: str | None, state: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE point_detail_assets SET asset_sha256 = ?, state = ? WHERE detail_id = ? AND remote_url = ?",
                (sha256, state, detail_id, remote_url),
            )
            if sha256:
                self.conn.execute(
                    "INSERT OR REPLACE INTO asset_sources(remote_url, sha256) VALUES (?, ?)",
                    (remote_url, sha256),
                )
            self.conn.commit()

    def count_details(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM point_details").fetchone()["n"])

    def detail_by_source(self, source_point_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM point_details WHERE source_point_id = ?", (str(source_point_id),)).fetchone()

    def asset_count_for_source(self, source_point_id: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM point_detail_assets a
            JOIN point_details d ON d.id = a.detail_id
            WHERE d.source_point_id = ?
            """,
            (str(source_point_id),),
        ).fetchone()
        return int(row["n"])

    def request_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, COUNT(*) AS n FROM detail_requests GROUP BY state")
        return {row["state"]: int(row["n"]) for row in rows}

    def bind_queue(self, queue: dict[str, Any]) -> None:
        for binding in queue["bindings"]:
            request = self.get_request(binding["request_key"])
            if request is None:
                continue
            detail = self.conn.execute("SELECT id FROM point_details WHERE request_id = ?", (request["id"],)).fetchone()
            if detail is None:
                continue
            self.bind_core_point(binding["core_point_id"], detail["id"], binding["source_point_id"], binding["map_source_id"])
