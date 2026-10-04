"""P1.1 smoke matrix: what do real hosts actually do to our asset fetches?

Re-runs asset fetching over pages already stored in the working DB (no page is
re-crawled), so the answer comes from real hosts without adding crawl load:

```text
Host              pages  images  FETCHED  CACHE  BLOCKED  404  NOT_IMAGE  INVALID  OTHER
3dmgame.com           8      62       58      0        0    0          4        0      0
9game.cn              6      41        2      0       39    0          0        0      0
17173.com             3      24       24      0        0    0          0        0      0
```

The verdict decides the policy (documented in `AssetPolicy`): generic headers,
a declarative per-host policy, or `blocked=True` recorded as
`SOURCE_ASSET_BLOCKED` instead of building an anti-bot system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from hsrmap.guides.assets.fetcher import ASSET_STATUSES, AssetFetcher
from hsrmap.guides.crawler.base import adapter_for
from hsrmap.guides.extract.blocks import is_ad_image

SMOKE_COLUMNS = ("FETCHED", "CACHE_HIT", "HTTP_BLOCKED", "HTTP_NOT_FOUND", "NOT_IMAGE", "INVALID_IMAGE", "OTHER")


def _page_host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().split(":")[0]


def smoke_matrix(
    db,
    hosts: Iterable[str],
    *,
    limit: int = 8,
    fetcher: AssetFetcher | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Fetch the assets of stored pages per host and summarise the outcomes."""
    fetcher = fetcher or AssetFetcher()
    hosts = [str(host).lower() for host in hosts]
    results: list[dict[str, Any]] = []
    for host in hosts:
        pages = [
            dict(row)
            for row in db.conn.execute(
                """
                SELECT id, canonical_url, raw_html_path, title FROM guide_page
                WHERE canonical_url LIKE ? AND raw_html_path IS NOT NULL
                ORDER BY id DESC LIMIT ?
                """,
                (f"%//%{host}/%", int(limit)),
            )
        ]
        counts = {column: 0 for column in SMOKE_COLUMNS}
        urls = 0
        for page in pages:
            path = Path(page["raw_html_path"] or "")
            if not path.exists():
                continue
            html = path.read_text(encoding="utf-8", errors="replace")
            adapter = adapter_for(page["canonical_url"])
            for src in adapter.extract_assets(html):
                if is_ad_image(src):
                    continue
                urls += 1
                outcome = fetcher.fetch(src, page["canonical_url"], refresh=refresh)
                key = outcome.status if outcome.status in counts else "OTHER"
                counts[key] += 1
        results.append(
            {
                "host": host,
                "pages": len(pages),
                "images": urls,
                "counts": counts,
            }
        )
    return {"hosts": results, "columns": list(SMOKE_COLUMNS)}


def render_matrix(matrix: dict[str, Any]) -> str:
    header = "%-22s %6s %7s %8s %6s %8s %5s %10s %8s %6s" % (
        "Host",
        "pages",
        "images",
        "FETCHED",
        "CACHE",
        "BLOCKED",
        "404",
        "NOT_IMAGE",
        "INVALID",
        "OTHER",
    )
    lines = [header, "-" * len(header)]
    for row in matrix.get("hosts") or []:
        counts = row["counts"]
        lines.append(
            "%-22s %6d %7d %8d %6d %8d %5d %10d %8d %6d"
            % (
                row["host"],
                row["pages"],
                row["images"],
                counts.get("FETCHED", 0),
                counts.get("CACHE_HIT", 0),
                counts.get("HTTP_BLOCKED", 0),
                counts.get("HTTP_NOT_FOUND", 0),
                counts.get("NOT_IMAGE", 0),
                counts.get("INVALID_IMAGE", 0),
                counts.get("OTHER", 0),
            )
        )
    return "\n".join(lines)


def verdict(matrix: dict[str, Any]) -> dict[str, str]:
    """Case A / B / C per host, straight from the measured numbers."""
    out: dict[str, str] = {}
    for row in matrix.get("hosts") or []:
        counts = row["counts"]
        images = int(row["images"]) or 1
        fetched = counts.get("FETCHED", 0) + counts.get("CACHE_HIT", 0)
        ratio = fetched / images
        if ratio >= 0.95:
            out[row["host"]] = "A: generic policy works"
        elif counts.get("HTTP_BLOCKED", 0) / images >= 0.5:
            out[row["host"]] = "C: host refuses assets (record SOURCE_ASSET_BLOCKED)"
        else:
            out[row["host"]] = "B: needs a declarative host policy"
    return out


def statuses() -> tuple[str, ...]:
    return ASSET_STATUSES
