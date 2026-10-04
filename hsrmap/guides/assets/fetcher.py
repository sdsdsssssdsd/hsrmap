"""Asset ingestion: one place that turns an image URL into a typed, cached asset.

Every asset failure used to collapse into `images_in_guide_assets=False`, which
says nothing about *why* a host refuses us. This module keeps the reason:
blocked, missing, not an image, undecodable, rate limited, network error. A
crawl report can then explain a host's behaviour, a policy can be added from
evidence instead of guesswork, and a page whose assets cannot be fetched can be
quarantined instead of quietly polluting Review.

Statuses (the vocabulary reports and downstream gates use):

```text
FETCHED        bytes downloaded and decoded
CACHE_HIT      served from the asset cache, no request made
HTTP_BLOCKED   401/403/406/451 — the host refuses this client
HTTP_NOT_FOUND 404/410
NOT_IMAGE      HTML error page, empty body, or a non-image content type
INVALID_IMAGE  bytes arrived but no image decoder accepts them
RATE_LIMITED   429 (retried, still limited)
NETWORK_ERROR  timeouts, resets, DNS, 5xx after retries
UNSUPPORTED    over the size ceiling, or a policy marked the host unfetchable
```
"""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass, field
from http.client import HTTPException
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from hsrmap.http import USER_AGENT, RateLimitedClient

FETCHED = "FETCHED"
CACHE_HIT = "CACHE_HIT"
HTTP_BLOCKED = "HTTP_BLOCKED"
HTTP_NOT_FOUND = "HTTP_NOT_FOUND"
NOT_IMAGE = "NOT_IMAGE"
INVALID_IMAGE = "INVALID_IMAGE"
RATE_LIMITED = "RATE_LIMITED"
NETWORK_ERROR = "NETWORK_ERROR"
UNSUPPORTED = "UNSUPPORTED"

ASSET_STATUSES = (
    FETCHED,
    CACHE_HIT,
    HTTP_BLOCKED,
    HTTP_NOT_FOUND,
    NOT_IMAGE,
    INVALID_IMAGE,
    RATE_LIMITED,
    NETWORK_ERROR,
    UNSUPPORTED,
)

#: statuses that produced usable bytes
OK_STATUSES = frozenset({FETCHED, CACHE_HIT})

#: statuses worth another attempt inside one fetch
RETRY_STATUSES = frozenset({RATE_LIMITED, NETWORK_ERROR})

_BLOCKED_HTTP = {401, 403, 406, 451}
_MISSING_HTTP = {404, 410}
_HTML_PREFIXES = (b"<", b"<!DOCTYPE", b"<?xml")


@dataclass(frozen=True)
class AssetPolicy:
    """How one host's images may be fetched.

    Data, not code: a host earns an entry here only after the smoke matrix shows
    the generic policy is not enough, and the entry stays declarative (headers /
    referer), never an `if host` branch inside the fetcher.
    """

    name: str = "generic"
    referer: str | None = "page"
    headers: dict[str, str] = field(default_factory=dict)
    max_bytes: int = 12 * 1024 * 1024
    max_attempts: int = 2
    blocked: bool = False
    reason: str | None = None


DEFAULT_POLICY = AssetPolicy()

#: Populated from smoke-matrix evidence (P1.1); empty means "generic works".
HOST_POLICIES: dict[str, AssetPolicy] = {}


@dataclass
class FetchAttempt:
    """One request inside a fetch, kept for diagnosis."""

    url: str
    outcome: str
    http_status: int | None = None
    reason: str | None = None
    policy: str = "generic"
    elapsed_ms: int | None = None

    def to_report(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "outcome": self.outcome,
            "http_status": self.http_status,
            "reason": self.reason,
            "policy": self.policy,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class AssetFetchResult:
    """The outcome of one asset fetch, with the reason it failed when it did."""

    status: str
    source_url: str
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    format: str | None = None
    byte_size: int | None = None
    reason: str | None = None
    body: bytes | None = None
    path: str | None = None
    cache_hit: bool = False
    attempts: list[FetchAttempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in OK_STATUSES

    def to_report(self) -> dict[str, Any]:
        """JSON-safe view (never carries the bytes)."""
        return {
            "status": self.status,
            "source_url": self.source_url,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "byte_size": self.byte_size,
            "reason": self.reason,
            "cache_hit": self.cache_hit,
            "attempts": [item.to_report() for item in self.attempts],
        }


def host_policy(url: str, policies: dict[str, AssetPolicy] | None = None) -> AssetPolicy:
    """Resolve a host's policy by registrable suffix, longest match first."""
    table = HOST_POLICIES if policies is None else policies
    host = (urlparse(url).netloc or "").lower().split(":")[0]
    best: AssetPolicy | None = None
    best_len = -1
    for suffix, policy in table.items():
        suffix = suffix.lower()
        if (host == suffix or host.endswith("." + suffix)) and len(suffix) > best_len:
            best, best_len = policy, len(suffix)
    return best or DEFAULT_POLICY


def _default_transport(url: str, headers: dict[str, str], timeout: int) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        body = response.read()
        return response.status, {k.lower(): v for k, v in response.headers.items()}, body


class AssetFetcher:
    """Fetch, validate and cache one image at a time."""

    def __init__(
        self,
        *,
        client: RateLimitedClient | None = None,
        cache: Any = None,
        policies: dict[str, AssetPolicy] | None = None,
        timeout: int = 20,
        transport: Callable[[str, dict[str, str], int], tuple[int, dict[str, str], bytes]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        scheduler: Any = None,
    ) -> None:
        self.client = client or RateLimitedClient(min_interval=0.5)
        self.cache = cache
        self.policies = dict(HOST_POLICIES if policies is None else policies)
        self.timeout = timeout
        self.transport = transport or _default_transport
        self.sleep = sleep
        #: Optional HostScheduler: a host that keeps answering 403 is paused
        #: instead of being asked another 200 times (a1-6 §15).
        self.scheduler = scheduler

    # ------------------------------------------------------------------ #

    def policy_for(self, url: str) -> AssetPolicy:
        return host_policy(url, self.policies)

    def fetch(
        self,
        asset_url: str,
        page_url: str | None = None,
        *,
        refresh: bool = False,
    ) -> AssetFetchResult:
        policy = self.policy_for(asset_url)
        if policy.blocked:
            return AssetFetchResult(
                status=UNSUPPORTED,
                source_url=asset_url,
                reason=policy.reason or f"{policy.name} assets are not fetchable",
            )
        if self.cache is not None and not refresh:
            hit = self.cache.lookup(asset_url)
            if hit is not None:
                return hit
        if not asset_url.lower().startswith(("http://", "https://")):
            return AssetFetchResult(
                status=UNSUPPORTED, source_url=asset_url, reason="not an http url"
            )

        if self.scheduler is not None and not self.scheduler.before(asset_url):
            state = self.scheduler.state(asset_url)
            return AssetFetchResult(
                status=HTTP_BLOCKED if state.status == "HOST_BLOCKED" else RATE_LIMITED,
                source_url=asset_url,
                reason=f"circuit open for {state.host}: {state.reason}",
            )

        headers = {"User-Agent": USER_AGENT}
        if policy.referer == "page" and page_url:
            headers["Referer"] = page_url
        headers.update(policy.headers)

        attempts: list[FetchAttempt] = []
        result: AssetFetchResult | None = None
        for index in range(max(1, policy.max_attempts)):
            started = time.monotonic()
            try:
                status, response_headers, body = self.transport(asset_url, headers, self.timeout)
            except HTTPError as exc:
                outcome = _http_outcome(exc.code)
                attempts.append(
                    FetchAttempt(
                        url=asset_url,
                        outcome=outcome,
                        http_status=exc.code,
                        reason=f"HTTP {exc.code}",
                        policy=policy.name,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                )
                result = AssetFetchResult(
                    status=outcome,
                    source_url=asset_url,
                    http_status=exc.code,
                    reason=f"HTTP {exc.code}",
                    attempts=attempts,
                )
                if outcome not in RETRY_STATUSES:
                    break
            # HTTPException covers a truncated response (http.client.IncompleteRead):
            # the server announced N bytes and closed early. It is not an OSError,
            # so before this it escaped the fetcher and killed the whole run.
            except (URLError, TimeoutError, ConnectionResetError, OSError, HTTPException) as exc:
                attempts.append(
                    FetchAttempt(
                        url=asset_url,
                        outcome=NETWORK_ERROR,
                        reason=type(exc).__name__,
                        policy=policy.name,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                )
                result = AssetFetchResult(
                    status=NETWORK_ERROR,
                    source_url=asset_url,
                    reason=type(exc).__name__,
                    attempts=attempts,
                )
            else:
                content_type = str(response_headers.get("content-type") or "").split(";")[0].strip().lower()
                inspected = _inspect(body, content_type, policy.max_bytes)
                attempts.append(
                    FetchAttempt(
                        url=asset_url,
                        outcome=inspected["status"],
                        http_status=status,
                        reason=inspected.get("reason"),
                        policy=policy.name,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                )
                result = AssetFetchResult(
                    status=inspected["status"],
                    source_url=asset_url,
                    final_url=asset_url,
                    http_status=status,
                    content_type=content_type or None,
                    sha256=inspected.get("sha256"),
                    width=inspected.get("width"),
                    height=inspected.get("height"),
                    format=inspected.get("format"),
                    byte_size=len(body),
                    reason=inspected.get("reason"),
                    body=body if inspected["status"] == FETCHED else None,
                    attempts=attempts,
                )
                if inspected["status"] not in RETRY_STATUSES:
                    break
            if index + 1 < max(1, policy.max_attempts):
                self.sleep(min(2.0 * (index + 1), 5.0))
        assert result is not None
        if self.scheduler is not None:
            self.scheduler.after(
                asset_url,
                ok=result.ok,
                blocked=result.status == HTTP_BLOCKED,
                reason=str(result.reason or result.status),
            )
        if result.ok and self.cache is not None:
            stored = self.cache.store(result)
            result.path = stored.get("path")
        return result


def _http_outcome(code: int) -> str:
    if code in _BLOCKED_HTTP:
        return HTTP_BLOCKED
    if code in _MISSING_HTTP:
        return HTTP_NOT_FOUND
    if code == 429:
        return RATE_LIMITED
    return NETWORK_ERROR


def _inspect(body: bytes, content_type: str, max_bytes: int) -> dict[str, Any]:
    """Decode the payload once and name the outcome."""
    if not body:
        return {"status": NOT_IMAGE, "reason": "empty body"}
    if len(body) > max_bytes:
        return {"status": UNSUPPORTED, "reason": f"{len(body)} bytes over the ceiling"}
    stripped = body.lstrip()[:16]
    if any(stripped.startswith(prefix) for prefix in _HTML_PREFIXES):
        return {"status": NOT_IMAGE, "reason": "html body"}
    if content_type and not content_type.startswith("image/"):
        return {"status": NOT_IMAGE, "reason": f"content-type {content_type}"}
    digest = hashlib.sha256(body).hexdigest()
    try:
        from PIL import Image

        with Image.open(io.BytesIO(body)) as image:
            image.verify()
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
            image_format = image.format
    except Exception as exc:  # Pillow raises a family of decoder errors
        return {"status": INVALID_IMAGE, "reason": type(exc).__name__, "sha256": digest}
    return {
        "status": FETCHED,
        "sha256": digest,
        "width": int(width),
        "height": int(height),
        "format": (image_format or "").lower() or None,
    }
