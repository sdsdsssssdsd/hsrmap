from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from hsrmap.guides.crawler.robots import path_allowed


def fetch_page(url: str, client: Any, robots_txt: str | None = None) -> dict[str, Any]:
    path = urlparse(url).path or "/"
    if robots_txt is not None and not path_allowed(robots_txt, path):
        return {"status": "NEEDS_MANUAL_IMPORT", "reason": "robots", "url": url}
    response = client.get(url)
    body = response.body
    html = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
    return {"status": "ok", "html": html, "url": url, "http_status": getattr(response, "status", 200)}
