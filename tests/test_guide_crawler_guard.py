"""URL admission (a1-6 §22): the crawler only talks to public HTTP(S)."""

import pytest

from hsrmap.guides.crawler.guard import (
    BLOCKED_NETWORKS,
    URLRejected,
    admit_redirect,
    admit_url,
    host_reason,
    is_private_ip,
)


def test_public_urls_pass():
    for url in (
        "https://www.gamersky.com/handbook/202404/1729233.shtml",
        "http://news.17173.com/z/xqtd/content/03032024/195341206.shtml",
        "https://cdn.example.test:8443/a.png",
    ):
        assert admit_url(url) == url


def test_non_http_schemes_are_refused():
    for url in ("file:///etc/passwd", "ftp://host/a", "data:text/html,x", "javascript:alert(1)"):
        with pytest.raises(URLRejected) as exc:
            admit_url(url)
        assert "scheme" in exc.value.reason


def test_local_and_private_targets_are_refused():
    cases = {
        "http://localhost:8000/admin": "local hostname",
        "http://127.0.0.1/x": "non-public",
        "http://10.1.2.3/x": "non-public",
        "http://192.168.1.1/x": "non-public",
        "http://169.254.169.254/latest/meta-data/": "non-public",
        "http://[::1]/x": "non-public",
        "http://metadata.google.internal/computeMetadata/v1/": "local hostname",
        "http://printer.local/x": "local hostname",
        "http://user:pw@example.test/x": "credentials",
    }
    for url, fragment in cases.items():
        with pytest.raises(URLRejected) as exc:
            admit_url(url)
        assert fragment in exc.value.reason, url


def test_a_redirect_is_validated_like_a_seed():
    assert admit_redirect("https://public.test/a", "https://cdn.public.test/b") == "https://cdn.public.test/b"
    with pytest.raises(URLRejected):
        admit_redirect("https://public.test/a", "http://127.0.0.1:8000/")
    with pytest.raises(URLRejected):
        admit_redirect("https://public.test/a", "file:///etc/passwd")


def test_dns_resolution_is_opt_in_and_checked():
    assert host_reason("public.test") is None
    assert host_reason("rebind.test", resolve=True, resolver=lambda host, port: [
        (2, 1, 6, "", ("93.184.216.34", 0)),
    ]) is None
    reason = host_reason("rebind.test", resolve=True, resolver=lambda host, port: [
        (2, 1, 6, "", ("127.0.0.1", 0)),
    ])
    assert reason and "non-public" in reason


def test_private_ip_helper_and_network_list():
    assert is_private_ip("127.0.0.1") and is_private_ip("10.0.0.1") and is_private_ip("::1")
    assert not is_private_ip("93.184.216.34") and not is_private_ip("not-an-ip")
    assert any(str(network) == "169.254.0.0/16" for network in BLOCKED_NETWORKS)
