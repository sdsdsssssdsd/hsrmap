from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


class GuideSourceAdapter:
    name = "generic"
    domain = ""
    source_kind = "OtherGuideSite"
    priority = 80
    adapter_name = "generic"

    def source_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "source_kind": self.source_kind,
            "priority": self.priority,
            "adapter_name": self.adapter_name,
        }

    def discover(self, seeds: dict[str, Any]) -> list[str]:
        return [url for url in seeds.get("urls") or [] if self.domain and self.domain in url]

    def fetch(self, url: str, client: Any, robots_txt: str | None = None) -> dict[str, Any]:
        from hsrmap.guides.crawler.fetch import fetch_page

        return fetch_page(url, client, robots_txt)

    def canonicalize(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def parse_page(self, html: str, url: str) -> dict[str, Any]:
        title = _between(html, "<title>", "</title>") or _between(html, "<h1>", "</h1>")
        return {
            "canonical_url": self.canonicalize(url),
            "title": _strip_tags(title or ""),
            "author": _guess_author(html),
            "source_claim": "米游社" if "米游社" in html else None,
        }

    def extract_assets(self, html: str) -> list[str]:
        """Image URLs only — never scripts, styles or other page chrome.

        A raw scan of every `src="` used to return `<script src=...>` and
        `<iframe src=...>` too, so the fetcher spent its budget downloading
        JavaScript and reported a wall of NOT_IMAGE statuses that looked like a
        host blocking us. Two passes, in order:

        1. attributes of `<img>` tags (src, data-src, data-original, ...);
        2. any absolute URL whose path ends in an image extension, which catches
           CSS backgrounds and linked screenshots.
        """
        text = html or ""
        urls: list[str] = []
        for tag in _IMG_TAG.findall(text):
            picked = pick_image_src(dict(_IMG_ATTR.findall(tag)))
            if picked:
                urls.append(picked)
        urls.extend(_IMAGE_URL.findall(text))
        cleaned: list[str] = []
        for raw in urls:
            src = _absolute_url(raw)
            if not src or _chrome_asset(src) or _non_image_url(src):
                continue
            cleaned.append(src)
        return list(dict.fromkeys(cleaned))


def _between(text: str, left: str, right: str) -> str | None:
    i = text.lower().find(left.lower())
    if i < 0:
        return None
    i += len(left)
    j = text.lower().find(right.lower(), i)
    if j < 0:
        return None
    return text[i:j]


def _prefer_author(current: str | None, candidate: str | None) -> str | None:
    if current and "祈鸢" in current:
        return current
    return candidate or current


def _guess_author(html: str) -> str | None:
    if "祈鸢" in html:
        return "祈鸢ya"
    match = re.search(r"作者[:：]\s*([^\s<]{1,24})", html)
    if match:
        return match.group(1).strip()
    named = _between(html, 'class="name">', "</")
    if named and 1 < len(_strip_tags(named)) <= 24:
        return _strip_tags(named)
    return None


#: Attribute priority for "which URL actually holds the picture". Chinese guide
#: sites lazy-load with `data-original` while `src` stays a placeholder, so the
#: extractor and the block builder must agree on this order or the fetched asset
#: never matches the block that needs it.
IMAGE_SRC_ATTRS = (
    "data-original",
    "data-src",
    "data-lazy-src",
    "data-lazy",
    "data-echo",
    "data-url",
    "data-img",
    "src",
)

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_IMG_ATTR = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*["']([^"']*)["']""")
_IMAGE_URL = re.compile(
    r"""https?://[^\s"'<>()\\]+?\.(?:jpe?g|png|gif|webp|bmp|avif)(?:[?#][^\s"'<>()\\]*)?""",
    re.I,
)
_NON_IMAGE_EXT = (
    ".js",
    ".css",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".svg",
    ".ico",
    ".mp4",
    ".webm",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
)


def pick_image_src(attrs: dict[str, str]) -> str:
    """The attribute that actually holds the picture, in priority order.

    Both the asset extractor and the block builder call this, so the URL that is
    fetched is exactly the URL a block looks up.
    """
    for name in IMAGE_SRC_ATTRS:
        value = str(attrs.get(name) or "").strip()
        if value and not value.startswith("data:"):
            return value
    return ""


def _absolute_url(src: str) -> str:
    value = (src or "").strip()
    if value.startswith("//"):
        return "https:" + value
    return value if value.lower().startswith(("http://", "https://")) else ""


def _non_image_url(src: str) -> bool:
    path = urlparse(src).path.lower()
    return path.endswith(_NON_IMAGE_EXT)


def _chrome_asset(src: str) -> bool:
    lower = src.lower()
    return any(
        token in lower
        for token in (
            "logo",
            "qrcode",
            "blank.png",
            "loading.gif",
            "!a-3-240x",
            "language-arrow",
            "/sao.png",
            "webimg13/zhuanti",
        )
    )


def _strip_tags(text: str) -> str:
    out = []
    skip = False
    for ch in text:
        if ch == "<":
            skip = True
            continue
        if ch == ">":
            skip = False
            continue
        if not skip:
            out.append(ch)
    return "".join(out).strip()


def adapter_for(url: str) -> GuideSourceAdapter:
    host = urlparse(url).netloc.lower()
    from hsrmap.guides.sources.gamersky import GamerSkyAdapter
    from hsrmap.guides.sources.miyoushe import MiyousheAdapter
    from hsrmap.guides.sources.site17173 import Site17173Adapter
    from hsrmap.guides.sources.taptap import TapTapAdapter
    from hsrmap.guides.sources.three_dm import ThreeDMAdapter

    for cls in (Site17173Adapter, TapTapAdapter, GamerSkyAdapter, ThreeDMAdapter, MiyousheAdapter):
        if cls.domain in host:
            return cls()
    return GuideSourceAdapter()
