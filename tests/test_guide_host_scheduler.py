"""Per-host pacing, the circuit breaker and the robots cache (a1-6 §14/§15/§21)."""

from urllib.error import HTTPError

from hsrmap.guides.assets.fetcher import HTTP_BLOCKED, AssetFetcher
from hsrmap.guides.crawler.hosts import (
    HOST_BLOCKED,
    HOST_DEGRADED,
    HOST_OK,
    ROBOTS_DENIED,
    HostScheduler,
    RobotsCache,
    host_of,
)


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def test_each_host_is_paced_independently():
    clock = _Clock()
    scheduler = HostScheduler(min_interval=1.0, clock=clock, sleep=clock.sleep)
    assert scheduler.before("https://a.test/1") is True
    assert scheduler.before("https://b.test/1") is True  # a different host never waits
    assert clock.slept == []
    assert scheduler.before("https://a.test/2") is True
    assert clock.slept == [1.0]  # the same host waits its interval
    assert scheduler.state("https://a.test/2").requests == 2
    assert host_of("https://a.test:8443/x") == "a.test"
    assert scheduler.snapshot()["waits"] == 1


def test_circuit_opens_after_consecutive_failures_and_suppresses_requests():
    clock = _Clock()
    scheduler = HostScheduler(min_interval=1.0, error_threshold=3, cooldown=60.0, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        assert scheduler.before("https://flaky.test/a") is True
        scheduler.after("https://flaky.test/a", ok=False, blocked=True, reason="HTTP 403")
    state = scheduler.state("https://flaky.test/a")
    assert state.status == HOST_BLOCKED and state.consecutive_errors == 3
    assert "HTTP 403" in state.reason

    assert scheduler.before("https://flaky.test/b") is False
    assert scheduler.before("https://flaky.test/c") is False
    assert scheduler.state("https://flaky.test/b").suppressed == 2
    assert scheduler.snapshot()["suppressed"] == 2
    assert scheduler.snapshot()["opened"][0]["host"] == "flaky.test"

    clock.now += 61.0  # the cooldown passes and the host is tried again
    assert scheduler.before("https://flaky.test/d") is True
    assert scheduler.state("https://flaky.test/d").status == HOST_OK
    assert scheduler.state("https://flaky.test/d").consecutive_errors == 0


def test_success_resets_errors_and_degraded_is_not_blocked():
    clock = _Clock()
    scheduler = HostScheduler(min_interval=1.0, error_threshold=2, cooldown=30.0, clock=clock, sleep=clock.sleep)
    scheduler.after("https://slow.test/a", ok=False, reason="timeout")
    scheduler.after("https://slow.test/a", ok=True)
    assert scheduler.state("https://slow.test/a").consecutive_errors == 0
    scheduler.after("https://slow.test/a", ok=False, reason="timeout")
    scheduler.after("https://slow.test/a", ok=False, reason="timeout")
    assert scheduler.state("https://slow.test/a").status == HOST_DEGRADED


def test_robots_cache_fetches_once_per_ttl():
    clock = _Clock()
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return "User-agent: *\nDisallow: /private/"

    cache = RobotsCache(fetch=fetch, ttl=600.0, clock=clock)
    first = cache.get("https://a.test/x")
    assert first and "/private/" in first
    assert calls == ["https://a.test/robots.txt"]
    cache.get("https://a.test/y")
    assert len(calls) == 1  # cached, no second request
    assert cache.allowed("https://a.test/public/") is True
    assert cache.allowed("https://a.test/private/x") is False
    assert ROBOTS_DENIED == "ROBOTS_DENIED"
    clock.now += 601.0
    cache.get("https://a.test/z")
    assert len(calls) == 2  # the TTL expired
    assert cache.state()["fetches"] == 2


def test_robots_cache_allows_everything_without_a_robots_file():
    cache = RobotsCache(fetch=lambda url: None, ttl=60.0, clock=_Clock())
    assert cache.allowed("https://a.test/anything") is True


def _fetcher(scheduler, *, status: int = 403, calls: list | None = None) -> AssetFetcher:
    log = calls if calls is not None else []

    def transport(url, headers, timeout):
        log.append(url)
        raise HTTPError(url, status, "forbidden", {}, None)

    return AssetFetcher(transport=transport, sleep=lambda _: None, cache=None, scheduler=scheduler)


def test_asset_fetcher_does_not_call_an_open_circuit():
    clock = _Clock()
    scheduler = HostScheduler(min_interval=1.0, error_threshold=1, cooldown=300.0, clock=clock, sleep=clock.sleep)
    scheduler.after("https://cdn.test/a.png", ok=False, blocked=True, reason="HTTP 403")
    calls: list[str] = []
    result = _fetcher(scheduler, calls=calls).fetch("https://cdn.test/b.png")
    assert result.status == HTTP_BLOCKED
    assert "circuit open" in (result.reason or "")
    assert calls == []


def test_repeated_403s_open_the_asset_host_circuit():
    clock = _Clock()
    scheduler = HostScheduler(min_interval=1.0, error_threshold=2, cooldown=300.0, clock=clock, sleep=clock.sleep)
    calls: list[str] = []
    fetcher = _fetcher(scheduler, calls=calls)
    assert fetcher.fetch("https://cdn.test/1.png").status == HTTP_BLOCKED
    assert fetcher.fetch("https://cdn.test/2.png").status == HTTP_BLOCKED
    assert scheduler.state("https://cdn.test/1.png").status == HOST_BLOCKED
    suppressed_before = len(calls)
    assert fetcher.fetch("https://cdn.test/3.png").status == HTTP_BLOCKED
    assert len(calls) == suppressed_before  # the third request never left
    assert scheduler.snapshot()["suppressed"] == 1
