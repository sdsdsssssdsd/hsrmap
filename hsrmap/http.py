from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
NO_RETRY_STATUS = {400, 401, 403, 404}
BACKOFF = (1, 2, 4, 8, 16)


@dataclass
class HttpResponse:
    url: str
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class RateLimitedClient:
    def __init__(self, min_interval: float = 0.3):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def _wait(self) -> None:
        with self._lock:
            gap = time.monotonic() - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()

    def get(
        self, url: str, timeout: int = 30, headers: dict[str, str] | None = None
    ) -> HttpResponse:
        #: 有些站要额外的头（米游社的 JSON API 缺 Referer 就是 403），所以给一个口子。
        merged = {"User-Agent": USER_AGENT, **(headers or {})}
        last_error = None
        for attempt, delay in enumerate((0,) + BACKOFF):
            if delay:
                time.sleep(delay)
            self._wait()
            req = Request(url, headers=merged)
            try:
                with urlopen(req, timeout=timeout) as resp:
                    return HttpResponse(
                        url=url,
                        status=resp.status,
                        body=resp.read(),
                        headers={k.lower(): v for k, v in resp.headers.items()},
                    )
            except HTTPError as exc:
                last_error = exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    try:
                        time.sleep(float(retry_after))
                    except ValueError:
                        pass
                if exc.code in NO_RETRY_STATUS or exc.code not in RETRY_STATUS:
                    raise
            except (URLError, TimeoutError, ConnectionResetError, OSError) as exc:
                last_error = exc
        raise RuntimeError(f"GET failed after retries: {url}: {last_error}")

    def get_unchecked(self, url: str, timeout: int = 30) -> HttpResponse:
        self._wait()
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=timeout) as resp:
                return HttpResponse(
                    url=url,
                    status=resp.status,
                    body=resp.read(),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                body = b""
            return HttpResponse(
                url=url,
                status=exc.code,
                body=body,
                headers={k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])},
            )

    def api_get(self, host: str, path: str, params: dict[str, Any]) -> HttpResponse:
        return self.get(f"{host}{path}?{urlencode(params)}")
