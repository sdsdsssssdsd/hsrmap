from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


#: 用户库的 schema 版本（a1-8 十六.7）。
SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS point_progress (
    source_point_id TEXT PRIMARY KEY,
    stable_key TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class UserDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema', '1')")
        #: 明确写进 SQLite 自己的版本位（a1-8 十六.7），与 metadata.schema 互为佐证。
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_point(self, source_point_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM point_progress WHERE source_point_id = ?",
            (str(source_point_id),),
        ).fetchone()
        if row is None:
            return {
                "source_point_id": str(source_point_id),
                "completed": False,
                "favorite": False,
                "note": None,
                "stable_key": None,
            }
        return {
            "source_point_id": row["source_point_id"],
            "completed": bool(row["completed"]),
            "favorite": bool(row["favorite"]),
            "note": row["note"],
            "stable_key": row["stable_key"],
        }

    def upsert_point(self, source_point_id: str, body: dict[str, Any]) -> dict[str, Any]:
        current = self.get_point(source_point_id)
        completed = bool(body["completed"]) if "completed" in body else current["completed"]
        favorite = bool(body["favorite"]) if "favorite" in body else current["favorite"]
        note = body["note"] if "note" in body else current["note"]
        stable_key = body["stable_key"] if "stable_key" in body else current["stable_key"]
        self.conn.execute(
            """
            INSERT INTO point_progress(source_point_id, stable_key, completed, favorite, note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_point_id) DO UPDATE SET
                stable_key = excluded.stable_key,
                completed = excluded.completed,
                favorite = excluded.favorite,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (str(source_point_id), stable_key, int(completed), int(favorite), note, _now()),
        )
        self.conn.commit()
        return self.get_point(source_point_id)

    def list_points(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM point_progress ORDER BY updated_at").fetchall()
        return [
            {
                "source_point_id": row["source_point_id"],
                "stable_key": row["stable_key"],
                "completed": bool(row["completed"]),
                "favorite": bool(row["favorite"]),
                "note": row["note"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def export_payload(self) -> dict[str, Any]:
        return {"version": 1, "points": self.list_points()}

    def import_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        for item in payload.get("points") or []:
            source_id = item.get("source_point_id")
            if not source_id:
                continue
            self.upsert_point(str(source_id), item)
        return {"imported": len(payload.get("points") or [])}
