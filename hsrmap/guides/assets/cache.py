"""Asset cache: one download per image, keyed by canonical SHA256.

Layout (derived data — safe to delete and rebuild):

```text
data/guide-cache/
  assets/sha256/ab/abcdef...        original bytes, shared across articles
  variants/sha256/ab/abcdef/        derived images (thumb / vision preview)
```

The `guide_asset_cache` row remembers where a source URL came from, so a repeat
crawl of the same article is a `CACHE_HIT` with no request to the host at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hsrmap.guides.assets.fetcher import CACHE_HIT, AssetFetchResult
from hsrmap.guides.assets.phash import SIMILARITY_THRESHOLD, duplicate_groups, phash


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class AssetCache:
    """Filesystem + DB cache for fetched assets."""

    root: Path
    db: Any = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.assets_root = self.root / "assets" / "sha256"
        self.variants_root = self.root / "variants" / "sha256"

    # ------------------------------------------------------------------ #

    def file_path(self, sha256: str, ext: str = "") -> Path:
        return self.assets_root / sha256[:2] / f"{sha256}{ext}"

    def existing_file(self, sha256: str) -> Path | None:
        folder = self.assets_root / sha256[:2]
        if not folder.is_dir():
            return None
        for path in folder.glob(f"{sha256}*"):
            if path.is_file():
                return path
        return None

    def variant_dir(self, sha256: str) -> Path:
        target = self.variants_root / sha256[:2] / sha256
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ------------------------------------------------------------------ #

    def lookup(self, source_url: str) -> AssetFetchResult | None:
        """A CACHE_HIT result when this URL was already downloaded and kept."""
        if self.db is None:
            return None
        row = self.db.conn.execute(
            "SELECT * FROM guide_asset_cache WHERE source_url = ?", (source_url,)
        ).fetchone()
        if row is None or not row["sha256"]:
            return None
        path = self.existing_file(str(row["sha256"]))
        if path is None:
            return None
        try:
            body = path.read_bytes()
        except OSError:
            return None
        return AssetFetchResult(
            status=CACHE_HIT,
            source_url=source_url,
            final_url=row["final_url"] or source_url,
            content_type=row["mime_type"],
            sha256=str(row["sha256"]),
            width=row["width"],
            height=row["height"],
            format=row["format"],
            byte_size=row["byte_size"] if row["byte_size"] is not None else len(body),
            body=body,
            path=str(path),
            cache_hit=True,
        )

    def store(self, result: AssetFetchResult) -> dict[str, Any]:
        """Persist the bytes once and remember the URL mapping."""
        if not result.sha256 or result.body is None:
            return {}
        ext = Path(urlparse(result.source_url).path).suffix
        if not ext or len(ext) > 8:
            ext = f".{result.format}" if result.format else ".bin"
        dest = self.file_path(result.sha256, ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(result.body)
        digest = phash(result.body)
        if self.db is not None:
            self.db.conn.execute(
                """
                INSERT INTO guide_asset_cache(
                    source_url, final_url, sha256, mime_type, width, height,
                    byte_size, format, status, phash, downloaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_url) DO UPDATE SET
                    final_url = excluded.final_url,
                    sha256 = excluded.sha256,
                    mime_type = excluded.mime_type,
                    width = excluded.width,
                    height = excluded.height,
                    byte_size = excluded.byte_size,
                    format = excluded.format,
                    status = excluded.status,
                    phash = excluded.phash,
                    downloaded_at = excluded.downloaded_at
                """,
                (
                    result.source_url,
                    result.final_url,
                    result.sha256,
                    result.content_type,
                    result.width,
                    result.height,
                    result.byte_size,
                    result.format,
                    result.status,
                    digest,
                    _now(),
                ),
            )
            self.db.conn.commit()
        return {"path": str(dest), "sha256": result.sha256, "phash": digest}

    # ------------------------------------------------------------------ #

    def phash_of(self, sha256: str, *, compute: bool = True) -> str:
        """The perceptual hash of a cached asset, computed once and remembered."""
        if self.db is None or not sha256:
            return ""
        row = self.db.conn.execute(
            "SELECT phash FROM guide_asset_cache WHERE sha256 = ? AND phash IS NOT NULL AND phash != '' LIMIT 1",
            (sha256,),
        ).fetchone()
        if row is not None and row["phash"]:
            return str(row["phash"])
        if not compute:
            return ""
        path = self.existing_file(sha256)
        if path is None:
            return ""
        digest = phash(path=path)
        if digest:
            self.db.conn.execute(
                "UPDATE guide_asset_cache SET phash = ? WHERE sha256 = ? AND (phash IS NULL OR phash = '')",
                (digest, sha256),
            )
            self.db.conn.commit()
        return digest

    def visual_duplicates(
        self,
        *,
        threshold: int = SIMILARITY_THRESHOLD,
        compute_missing: bool = True,
        limit: int = 4000,
    ) -> list[list[str]]:
        """Groups of cached assets that are the same picture in another encoding.

        Assets cached before the perceptual hash existed are hashed on first use
        (bounded by `limit`), so the report works on an old cache too.
        """
        if self.db is None:
            return []
        rows = self.db.conn.execute(
            "SELECT DISTINCT sha256, phash FROM guide_asset_cache WHERE sha256 IS NOT NULL"
        ).fetchall()
        records: list[tuple[str, str]] = []
        computed = 0
        for row in rows:
            digest = str(row["sha256"] or "")
            value = str(row["phash"] or "")
            if not value and compute_missing and computed < limit:
                value = self.phash_of(digest)
                computed += 1
            if digest and value:
                records.append((digest, value))
        return duplicate_groups(records, threshold=threshold)

    def stats(self) -> dict[str, int]:
        files = [p for p in self.assets_root.rglob("*") if p.is_file()] if self.assets_root.exists() else []
        rows = 0
        if self.db is not None:
            rows = int(
                self.db.conn.execute("SELECT COUNT(*) AS c FROM guide_asset_cache").fetchone()["c"]
            )
        return {"files": len(files), "rows": rows}
