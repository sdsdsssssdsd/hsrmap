"""AssetFetcher / AssetCache: typed outcomes, no silent failures."""

import io
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest
from PIL import Image

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.assets import (
    CACHE_HIT,
    FETCHED,
    HTTP_BLOCKED,
    HTTP_NOT_FOUND,
    INVALID_IMAGE,
    NETWORK_ERROR,
    NOT_IMAGE,
    RATE_LIMITED,
    UNSUPPORTED,
    AssetCache,
    AssetFetcher,
    AssetPolicy,
    host_policy,
)

PNG_URL = "https://img.3dmgame.com/uploads/2026/guide-1.png"
PAGE_URL = "https://shouyou.3dmgame.com/gl/634580.html"


def png_bytes(size=(8, 6), color=(200, 30, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class Transport:
    """Deterministic transport: one queued reply per call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_fetcher(tmp_path, *replies, policies=None, cache=True, db=None, **kwargs):
    database = db if db is not None else GuideDatabase(tmp_path / "guide.db")
    store = AssetCache(tmp_path / "guide-cache", db=database) if cache else None
    fetcher = AssetFetcher(
        transport=Transport(*replies),
        cache=store,
        policies=policies or {},
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return fetcher, store, database


def test_fetch_decodes_image_and_caches_it(tmp_path):
    body = png_bytes()
    fetcher, cache, db = make_fetcher(
        tmp_path, (200, {"content-type": "image/png"}, body)
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == FETCHED
    assert result.ok is True
    assert (result.width, result.height, result.format) == (8, 6, "png")
    assert result.body == body and result.sha256
    assert result.path and cache.existing_file(result.sha256)
    assert fetcher.transport.calls[0]["headers"]["Referer"] == PAGE_URL
    row = db.conn.execute("SELECT * FROM guide_asset_cache WHERE source_url = ?", (PNG_URL,)).fetchone()
    assert row["sha256"] == result.sha256 and row["status"] == FETCHED
    db.close()


def test_second_fetch_is_a_cache_hit_without_a_request(tmp_path):
    fetcher, _cache, db = make_fetcher(tmp_path, (200, {"content-type": "image/png"}, png_bytes()))
    first = fetcher.fetch(PNG_URL, PAGE_URL)
    second = fetcher.fetch(PNG_URL, PAGE_URL)
    assert first.status == FETCHED
    assert second.status == CACHE_HIT and second.cache_hit is True
    assert second.body == first.body
    assert len(fetcher.transport.calls) == 1
    db.close()


def test_refresh_bypasses_the_cache(tmp_path):
    fetcher, _cache, db = make_fetcher(tmp_path, (200, {"content-type": "image/png"}, png_bytes()))
    fetcher.fetch(PNG_URL, PAGE_URL)
    again = fetcher.fetch(PNG_URL, PAGE_URL, refresh=True)
    assert again.status == FETCHED
    assert len(fetcher.transport.calls) == 2
    db.close()


@pytest.mark.parametrize(
    "code,expected",
    [(403, HTTP_BLOCKED), (404, HTTP_NOT_FOUND)],
)
def test_http_refusals_are_typed_and_not_retried(tmp_path, code, expected):
    fetcher, _cache, db = make_fetcher(tmp_path, HTTPError(PNG_URL, code, "nope", None, None))
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == expected
    assert result.http_status == code
    assert len(result.attempts) == 1 and len(fetcher.transport.calls) == 1
    db.close()


def test_rate_limited_is_retried_then_reported(tmp_path):
    fetcher, _cache, db = make_fetcher(
        tmp_path,
        HTTPError(PNG_URL, 429, "slow down", None, None),
        HTTPError(PNG_URL, 429, "slow down", None, None),
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == RATE_LIMITED
    assert len(result.attempts) == 2 and len(fetcher.transport.calls) == 2
    db.close()


def test_network_error_is_typed(tmp_path):
    fetcher, _cache, db = make_fetcher(tmp_path, URLError("dns"))
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == NETWORK_ERROR
    assert result.reason == "URLError"
    db.close()


def test_truncated_response_is_a_retryable_network_error(tmp_path):
    """A server that closes early raises IncompleteRead, which is not an OSError.

    It used to escape AssetFetcher entirely and kill the whole run that was
    seeding official point images.
    """
    fetcher, _cache, db = make_fetcher(
        tmp_path,
        IncompleteRead(b"half-a-png", 2048),
        IncompleteRead(b"half-a-png", 2048),
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == NETWORK_ERROR
    assert result.reason == "IncompleteRead"
    assert len(result.attempts) == 2 and len(fetcher.transport.calls) == 2
    db.close()


def test_truncated_response_recovers_on_the_retry(tmp_path):
    body = png_bytes()
    fetcher, _cache, db = make_fetcher(
        tmp_path,
        IncompleteRead(b"half-a-png", 2048),
        (200, {"content-type": "image/png"}, body),
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == FETCHED
    assert result.body == body and result.sha256
    db.close()


def test_html_body_is_not_an_image(tmp_path):
    fetcher, _cache, db = make_fetcher(
        tmp_path, (200, {"content-type": "text/html"}, b"<html>blocked</html>")
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == NOT_IMAGE
    assert result.body is None
    db.close()


def test_wrong_content_type_is_not_an_image(tmp_path):
    fetcher, _cache, db = make_fetcher(
        tmp_path, (200, {"content-type": "application/json"}, b'{"error":1}')
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == NOT_IMAGE
    assert "application/json" in (result.reason or "")
    db.close()


def test_undecodable_bytes_are_invalid_image(tmp_path):
    fetcher, _cache, db = make_fetcher(
        tmp_path, (200, {"content-type": "image/png"}, b"not-really-a-png")
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == INVALID_IMAGE
    assert result.body is None
    db.close()


def test_oversize_body_is_unsupported(tmp_path):
    fetcher, _cache, db = make_fetcher(
        tmp_path,
        (200, {"content-type": "image/png"}, png_bytes()),
        policies={"3dmgame.com": AssetPolicy(name="3dmgame", max_bytes=16)},
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == UNSUPPORTED
    assert "ceiling" in (result.reason or "")
    db.close()


def test_blocked_host_policy_short_circuits(tmp_path):
    fetcher, _cache, db = make_fetcher(
        tmp_path,
        (200, {"content-type": "image/png"}, png_bytes()),
        policies={"3dmgame.com": AssetPolicy(name="3dmgame", blocked=True, reason="Case C")},
    )
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    assert result.status == UNSUPPORTED
    assert result.reason == "Case C"
    assert fetcher.transport.calls == []
    db.close()


def test_non_http_url_is_unsupported(tmp_path):
    fetcher, _cache, db = make_fetcher(tmp_path, (200, {"content-type": "image/png"}, png_bytes()))
    result = fetcher.fetch("data:image/png;base64,AAAA", PAGE_URL)
    assert result.status == UNSUPPORTED
    assert fetcher.transport.calls == []
    db.close()


def test_host_policy_uses_registrable_suffix():
    policy = AssetPolicy(name="3dmgame")
    table = {"3dmgame.com": policy}
    assert host_policy("https://img.3dmgame.com/a.png", table) is policy
    assert host_policy("https://shouyou.3dmgame.com/gl/1.html", table) is policy
    assert host_policy("https://not3dmgame.com/a.png", table).name == "generic"
    assert host_policy("https://game.com/a.png", table).name == "generic"


def test_cache_lookup_survives_a_deleted_file(tmp_path):
    fetcher, cache, db = make_fetcher(tmp_path, (200, {"content-type": "image/png"}, png_bytes()))
    result = fetcher.fetch(PNG_URL, PAGE_URL)
    path = cache.existing_file(result.sha256)
    assert path is not None
    path.unlink()
    assert cache.lookup(PNG_URL) is None
    db.close()


def test_result_report_is_json_safe(tmp_path):
    import json

    fetcher, _cache, db = make_fetcher(tmp_path, (200, {"content-type": "image/png"}, png_bytes()))
    report = fetcher.fetch(PNG_URL, PAGE_URL).to_report()
    assert "body" not in report
    assert json.loads(json.dumps(report))["status"] == FETCHED
    db.close()
