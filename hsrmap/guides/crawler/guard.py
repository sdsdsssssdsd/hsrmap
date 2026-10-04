"""URL admission: the crawler only ever talks to public HTTP(S) hosts (a1-6 §22).

Once URLs are discovered instead of typed by hand, an attacker-controlled page
can point the crawler at `http://127.0.0.1:8000/`, at a cloud metadata endpoint
or at `file:///etc/passwd`. The rule is simple and absolute:

```text
public HTTP URL -> request -> redirect -> validate the target again
```

Nothing here needs the network: numeric hosts and hostnames are checked first,
and DNS resolution is an explicit opt-in (`resolve=True`) so tests stay offline.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

#: Schemes the crawler may use. Everything else is refused outright.
ALLOWED_SCHEMES = ("http", "https")

#: Hostnames that always mean "this machine or the local network".
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "metadata.google.internal",
        "metadata",
    }
)

#: Ranges that are not public internet (RFC1918, loopback, link-local, CGNAT…).
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


class URLRejected(ValueError):
    """The URL is not something a crawler is allowed to request."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"URL rejected ({reason}): {url}")
        self.url = url
        self.reason = reason


def is_private_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return False
    if address.is_private or address.is_loopback or address.is_link_local:
        return True
    return any(address in network for network in BLOCKED_NETWORKS)


def host_reason(host: str, *, resolve: bool = False, resolver: Any = None) -> str | None:
    """Why this host may not be fetched, or None when it is fine."""
    name = (host or "").strip().lower().strip(".")
    if not name:
        return "missing host"
    if name in BLOCKED_HOSTNAMES or name.endswith((".local", ".internal", ".localhost")):
        return f"local hostname {name}"
    if is_private_ip(name):
        return f"non-public address {name}"
    bracketless = name.strip("[]")
    if is_private_ip(bracketless):
        return f"non-public address {bracketless}"
    if not resolve:
        return None
    lookup = resolver or socket.getaddrinfo
    try:
        infos = lookup(name, None)
    except Exception:
        return f"cannot resolve {name}"
    for info in infos or []:
        address = info[4][0] if len(info) > 4 else ""
        if is_private_ip(str(address)):
            return f"{name} resolves to non-public {address}"
    return None


def admit_url(url: str, *, resolve: bool = False, resolver: Any = None) -> str:
    """Return the URL when it is a public HTTP(S) URL, else raise URLRejected."""
    raw = str(url or "").strip()
    if not raw:
        raise URLRejected(raw, "empty url")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise URLRejected(raw, f"scheme {scheme or '(none)'} is not http(s)")
    if parsed.username or parsed.password:
        raise URLRejected(raw, "credentials in url")
    reason = host_reason(parsed.hostname or "", resolve=resolve, resolver=resolver)
    if reason:
        raise URLRejected(raw, reason)
    return raw


def admit_redirect(original: str, target: str, *, resolve: bool = False, resolver: Any = None) -> str:
    """A redirect target is validated exactly like a seed (a1-6 §22)."""
    del original  # kept in the signature because the caller knows both sides
    return admit_url(target, resolve=resolve, resolver=resolver)
