"""Unified AI cache (a1-6 §29, ADR-005).

Every model answer is stored under one key:

    sha256(provider + model + prompt_version + normalized_input)

The prompt_version part is what matters: without it a prompt change silently
replays answers produced by the old prompt, which is the most invisible kind of
data pollution. CachedProvider wraps any provider, so the cache is not per
topic and not per caller — vision reads, article extraction and image
classification all land in the same table.

Bytes (images) are keyed by their sha256 instead of their content, so a key
stays small and stable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _jsonable(value: Any) -> Any:
    """A stable, JSON-safe view of one value (bytes become their sha256)."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": hashlib.sha256(bytes(value)).hexdigest()}
    if isinstance(value, dict):
        pairs = sorted(value.items(), key=lambda pair: str(pair[0]))
        return {str(key): _jsonable(item) for key, item in pairs}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"__repr__": type(value).__name__ + ":" + str(value)[:200]}


def normalize_input(payload: Any) -> str:
    """Canonical JSON of the input: key order never changes the cache key."""
    return json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cache_key(provider: str, model: str, prompt_version: str, payload: Any) -> str:
    """sha256(provider + model + prompt_version + normalized_input) (§29)."""
    parts = [str(provider), str(model), str(prompt_version), normalize_input(payload)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class AICache:
    """DB-backed (and optionally disk-backed) cache for model answers."""

    def __init__(self, *, db: Any = None, root: str | Path | None = None) -> None:
        self.db = db
        self.root = Path(root) if root is not None else None
        self.memory: dict[str, Any] = {}
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)

    def _disk_path(self, key: str) -> Path | None:
        if self.root is None:
            return None
        return self.root / key[:2] / (key + ".json")

    def get(
        self,
        provider: str,
        model: str,
        prompt_version: str,
        payload: Any,
        *,
        key: str | None = None,
    ) -> Any | None:
        """The cached answer, or None. A hit is counted, never recomputed."""
        digest = key or cache_key(provider, model, prompt_version, payload)
        if digest in self.memory:
            return self.memory[digest]
        if self.db is not None:
            row = self.db.conn.execute(
                "SELECT output_json FROM ai_cache WHERE key = ?", (digest,)
            ).fetchone()
            if row is not None and row["output_json"]:
                self.db.conn.execute(
                    "UPDATE ai_cache SET hits = hits + 1, last_hit_at = ? WHERE key = ?", (_now(), digest)
                )
                self.db.conn.commit()
                try:
                    value = json.loads(row["output_json"])
                except Exception:
                    return None
                self.memory[digest] = value
                return value
        path = self._disk_path(digest)
        if path is not None and path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            self.memory[digest] = value
            return value
        return None

    def put(
        self,
        provider: str,
        model: str,
        prompt_version: str,
        payload: Any,
        output: Any,
        *,
        key: str | None = None,
    ) -> str:
        digest = key or cache_key(provider, model, prompt_version, payload)
        self.memory[digest] = output
        if self.db is not None:
            self.db.conn.execute(
                "INSERT INTO ai_cache(key, provider, model, prompt_version, input_hash, output_json, hits, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 0, ?)"
                " ON CONFLICT(key) DO UPDATE SET output_json = excluded.output_json,"
                " provider = excluded.provider, model = excluded.model,"
                " prompt_version = excluded.prompt_version, input_hash = excluded.input_hash",
                (
                    digest,
                    str(provider),
                    str(model),
                    str(prompt_version),
                    hashlib.sha256(normalize_input(payload).encode("utf-8")).hexdigest(),
                    json.dumps(_jsonable(output), ensure_ascii=False),
                    _now(),
                ),
            )
            self.db.conn.commit()
        path = self._disk_path(digest)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_jsonable(output), ensure_ascii=False), encoding="utf-8")
        return digest

    def stats(self) -> dict[str, Any]:
        if self.db is None:
            return {"entries": len(self.memory), "hits": 0, "by_prompt_version": {}, "storage": "memory"}
        rows = [
            dict(row)
            for row in self.db.conn.execute(
                "SELECT prompt_version, COUNT(*) AS entries, IFNULL(SUM(hits), 0) AS hits"
                " FROM ai_cache GROUP BY prompt_version"
            )
        ]
        return {
            "entries": sum(int(row["entries"]) for row in rows),
            "hits": sum(int(row["hits"]) for row in rows),
            "by_prompt_version": {str(row["prompt_version"]): int(row["entries"]) for row in rows},
            "storage": "db" + ("+disk" if self.root is not None else ""),
        }

    def invalidate(self, *, prompt_version: str | None = None, model: str | None = None) -> int:
        """Drop cached answers, e.g. after a prompt change (§29)."""
        if self.db is None:
            count = len(self.memory)
            self.memory.clear()
            return count
        sql = ["DELETE FROM ai_cache WHERE 1 = 1"]
        params: list[Any] = []
        if prompt_version:
            sql.append("AND prompt_version = ?")
            params.append(str(prompt_version))
        if model:
            sql.append("AND model = ?")
            params.append(str(model))
        cursor = self.db.conn.execute(" ".join(sql), tuple(params))
        self.db.conn.commit()
        self.memory.clear()
        return int(cursor.rowcount or 0)


class CachedProvider:
    """Wrap a provider so every answer goes through one cache (§29)."""

    def __init__(
        self,
        provider: Any,
        cache: AICache,
        *,
        name: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self.provider = provider
        self.cache = cache
        self.name = name or type(provider).__name__
        self.model = model or str(getattr(provider, "model", "") or "default")
        self.prompt_version = prompt_version or str(getattr(provider, "prompt_version", "") or "v1")
        self.calls = 0
        self.hits = 0

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "calls": self.calls,
            "hits": self.hits,
        }

    def _wrap(self, name: str, method: Callable[..., Any]) -> Callable[..., Any]:
        def cached(*args: Any, **kwargs: Any) -> Any:
            version = str(kwargs.pop("prompt_version", "") or self.prompt_version)
            model = str(kwargs.pop("model", "") or self.model)
            payload = {"method": name, "args": args, "kwargs": kwargs}
            found = self.cache.get(self.name, model, version, payload)
            if found is not None:
                self.hits += 1
                return found
            self.calls += 1
            output = method(*args, **kwargs)
            self.cache.put(self.name, model, version, payload, output)
            return output

        return cached

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.provider, name)
        if not callable(attribute):
            return attribute
        return self._wrap(name, attribute)


def wrap_provider(
    provider: Any, *, db: Any = None, root: str | Path | None = None, **kwargs: Any
) -> CachedProvider:
    """One call to put any provider behind the unified cache."""
    return CachedProvider(provider, AICache(db=db, root=root), **kwargs)

