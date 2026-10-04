from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
ENTRY_URL = "https://act.hoyolab.com/sr/app/interactive-map/index.html?lang=zh-cn"
ENTRY_BASE = "https://act.hoyolab.com/sr/app/interactive-map/"


@dataclass
class HttpResult:
    url: str
    status: int
    body: bytes
    json: Any | None


def http_get(url: str, timeout: int = 30) -> HttpResult:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        status = resp.status
    parsed = None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    return HttpResult(url=url, status=status, body=body, json=parsed)


def discover_frontend() -> dict[str, Any]:
    page = http_get(ENTRY_URL)
    html = page.body.decode("utf-8", "ignore")
    bundles = re.findall(r"(bundle_[A-Za-z0-9]+\.js)", html)
    if not bundles:
        raise RuntimeError("current HSR map page did not expose a bundle_*.js")
    bundle_url = urljoin(ENTRY_BASE, bundles[0])
    bundle = http_get(bundle_url)
    text = bundle.body.decode("utf-8", "ignore")
    app_versions = re.findall(r'app_version:"([a-z0-9]+)"', text)
    public_hosts = re.findall(
        r'apiBase="(https://[^"]+/common/srmap/sr_map)"', text
    )
    auth_hosts = re.findall(
        r'noCdnBase="(https://[^"]+/common/srmap/sr_map)"', text
    )
    return {
        "entry_url": ENTRY_URL,
        "bundle_url": bundle_url,
        "bundle_bytes": bundle.body,
        "bundle_text": text,
        "app_version": app_versions[0] if app_versions else None,
        "public_host": public_hosts[0] if public_hosts else None,
        "authenticated_host": auth_hosts[0] if auth_hosts else None,
    }


def api_get(host: str, path: str, params: dict[str, Any], retries: int = 3) -> HttpResult:
    query = {k: v for k, v in params.items() if v is not None}
    url = f"{host}{path}?{urlencode(query)}"
    last_error = None
    for attempt in range(retries):
        try:
            return http_get(url)
        except Exception as exc:
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last_error}")
