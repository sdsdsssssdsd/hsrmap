"""Host scheduler, circuit breaker and robots cache (a1-6 §14/§15/§21).

A single global sleep is both too slow (one host at a time) and too trusting
(a host that starts answering 403 keeps receiving requests). This module keeps
one state per host:

```text
last_request_at, min_interval, backoff_until, consecutive_errors
```

so a polite per-host interval can be honoured while different hosts proceed
independently, and a host that fails repeatedly is paused instead of hammered
(the doc's example: 17 consecutive 403 on an asset host, circuit opened, 83
requests suppressed).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

HOST_OK = "HOST_OK"
HOST_DEGRADED = "HOST_DEGRADED"
HOST_BLOCKED = "HOST_BLOCKED"

#: Status for "robots.txt forbids this path" — a policy answer, not a failure.
ROBOTS_DENIED = "ROBOTS_DENIED"


def host_of(url: str) -> str:
    return (urlparse(str(url or "")).netloc or "").lower().split(":")[0]


@dataclass
class HostState:
    host: str
    min_interval: float = 1.0
    last_request_at: float | None = None
    backoff_until: float | None = None
    consecutive_errors: int = 0
    requests: int = 0
    suppressed: int = 0
    status: str = HOST_OK
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "status": self.status,
            "reason": self.reason,
            "requests": self.requests,
            "suppressed": self.suppressed,
            "consecutive_errors": self.consecutive_errors,
            "min_interval": self.min_interval,
            "last_request_at": self.last_request_at,
            "backoff_until": self.backoff_until,
        }


class HostScheduler:
    """Per-host politeness plus a circuit breaker (a1-6 §14/§15)."""

    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        error_threshold: int = 10,
        cooldown: float = 1800.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = float(min_interval)
        self.error_threshold = int(error_threshold)
        self.cooldown = float(cooldown)
        self.hosts: dict[str, HostState] = {}
        self._clock = clock
        self._sleep = sleep
        self.waits = 0

    # -- state --------------------------------------------------------- #

    def state(self, url: str) -> HostState:
        host = host_of(url)
        entry = self.hosts.get(host)
        if entry is None:
            entry = HostState(host=host, min_interval=self.min_interval)
            self.hosts[host] = entry
        return entry

    def snapshot(self) -> dict[str, Any]:
        rows = [entry.as_dict() for entry in self.hosts.values()]
        opened = [row for row in rows if row["status"] != HOST_OK]
        return {
            "hosts": rows,
            "opened": opened,
            "suppressed": sum(int(row["suppressed"]) for row in rows),
            "waits": self.waits,
        }

    # -- scheduling ---------------------------------------------------- #

    def before(self, url: str) -> bool:
        """Wait for this host's interval; False means the circuit is open."""
        entry = self.state(url)
        now = self._clock()
        if entry.backoff_until is not None and now < entry.backoff_until:
            entry.suppressed += 1
            entry.status = HOST_BLOCKED if entry.status == HOST_OK else entry.status
            return False
        if entry.backoff_until is not None and now >= entry.backoff_until:
            entry.backoff_until = None
            entry.consecutive_errors = 0
            entry.status = HOST_OK
            entry.reason = ""
        if entry.last_request_at is not None:
            gap = entry.min_interval - (now - entry.last_request_at)
            if gap > 0:
                self._sleep(gap)
                self.waits += 1
                now = self._clock()
        entry.last_request_at = now
        entry.requests += 1
        return True

    def after(self, url: str, *, ok: bool, reason: str = "", blocked: bool = False) -> HostState:
        """Record the outcome; enough consecutive failures open the circuit."""
        entry = self.state(url)
        if ok:
            entry.consecutive_errors = 0
            if entry.backoff_until is None:
                entry.status = HOST_OK
            return entry
        entry.consecutive_errors += 1
        if entry.consecutive_errors >= self.error_threshold:
            entry.backoff_until = self._clock() + self.cooldown
            entry.status = HOST_BLOCKED if blocked else HOST_DEGRADED
            entry.reason = reason or f"{entry.consecutive_errors} consecutive failures"
        return entry


class RobotsCache:
    """robots.txt per host with a TTL, and ROBOTS_DENIED as a status (§21)."""

    def __init__(
        self,
        *,
        fetch: Callable[[str], str | None] | None = None,
        ttl: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fetch = fetch
        self.ttl = float(ttl)
        self._clock = clock
        self.entries: dict[str, dict[str, Any]] = {}
        self.fetches = 0

    def get(self, url: str) -> str | None:
        """The cached robots.txt of this URL's host, fetching it once per TTL."""
        host = host_of(url)
        now = self._clock()
        entry = self.entries.get(host)
        if entry is not None and now < entry["expires_at"]:
            return entry["text"]
        if self.fetch is None:
            return entry["text"] if entry else None
        parsed = urlparse(str(url or ""))
        robots_url = f"{parsed.scheme or 'https'}://{parsed.netloc}/robots.txt"
        text = None
        try:
            text = self.fetch(robots_url)
        except Exception:
            text = None
        self.fetches += 1
        self.entries[host] = {"text": text or "", "fetched_at": now, "expires_at": now + self.ttl}
        return text

    def allowed(self, url: str, path: str | None = None) -> bool:
        from hsrmap.guides.crawler.robots import path_allowed

        text = self.get(url)
        if not text:
            return True
        target = path if path is not None else (urlparse(str(url or "")).path or "/")
        return bool(path_allowed(text, target))

    def state(self) -> dict[str, Any]:
        return {
            "hosts": len(self.entries),
            "fetches": self.fetches,
            "entries": {host: {"expires_at": entry["expires_at"]} for host, entry in self.entries.items()},
        }
