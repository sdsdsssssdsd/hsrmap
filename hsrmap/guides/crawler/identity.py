"""URL identity for the crawler: which article, which site, which mirror.

Crawler Sprint 2 (a1-6 §三/§四/§二十四). One article is reachable as a desktop
page, a mobile mirror, an APP view, a `_2.shtml` continuation and a dozen URLs
carrying share parameters. The corpus must treat all of those as *one* article,
otherwise the same guide is fetched, imported and reviewed again and again.

```text
resolve(url)        -> ArticleRef(host, site, article_id, page_index, family)
same_article(a, b)  -> bool                     (mirror / pagination aware)
group(urls)         -> {family: [urls]}         (what the corpus should fetch)
declared_canonical  -> the URL the page itself claims (`<link rel=canonical>`)
reprint_of          -> that claim when it names a *different* site
```

The identity rules themselves live in `guides/signature.py` so the audit, the
review merge and the crawler cannot drift apart; this module is the crawler-side
class the ADR asked for, and adds explicit mirror/``canonical`` handling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlparse

from hsrmap.guides.signature import (
    MIRROR_HOST_PREFIXES,
    article_family,
    canonical_host,
    canonical_url,
    query_signature,
    registrable_domain,
)

#: Hosts that serve another host's article under a different name. Anything a
#: site adapter declares goes here too; the prefix rule covers the rest.
KNOWN_MIRROR_HOSTS: dict[str, str] = {
    "m.gamersky.com": "gamersky.com",
    "m.17173.com": "17173.com",
    "app.17173.com": "17173.com",
    "m.miyoushe.com": "miyoushe.com",
    "m.taptap.cn": "taptap.cn",
    "m.3dmgame.com": "3dmgame.com",
}

#: Query keys that address a page *within* an article rather than an article.
PAGE_PARAMS = ("page", "p", "pn", "pageno", "pagenum", "pageindex")

_PAGE_SUFFIX = re.compile(r"_(\d+)\.s?html?$", re.I)
_CANONICAL_LINK = re.compile(r"<link[^>]+rel=[\"\']?canonical[\"\']?[^>]*>", re.I)
_OG_URL = re.compile(r"<meta[^>]+property=[\"\']og:url[\"\']?[^>]*>", re.I)
_HREF = re.compile(r"href=[\"\']([^\"\']+)[\"\']", re.I)
_CONTENT = re.compile(r"content=[\"\']([^\"\']+)[\"\']", re.I)


def page_index(url: str) -> int:
    """Which page of the article this URL is (1 when it is the first)."""
    parsed = urlparse(str(url or ""))
    hit = _PAGE_SUFFIX.search(parsed.path or "")
    if hit:
        return max(1, int(hit.group(1)))
    for key, value in (pair.split("=", 1) for pair in (parsed.query or "").split("&") if "=" in pair):
        if key.lower() in PAGE_PARAMS and value.isdigit():
            return max(1, int(value))
    return 1


def first_page_url(url: str) -> str:
    """The same article without its pagination suffix."""
    parsed = urlparse(str(url or ""))
    path = _PAGE_SUFFIX.sub(lambda hit: ".shtml" if hit.group(0).lower().endswith(".shtml") else ".html", parsed.path or "")
    return parsed._replace(path=path, query="", fragment="").geturl()


@dataclass(frozen=True)
class ArticleRef:
    """One URL, resolved to the article it belongs to."""

    url: str
    family: str
    canonical_url: str
    host: str
    raw_host: str
    site: str
    article_id: str
    page_index: int
    query: str

    @property
    def is_paginated(self) -> bool:
        return self.page_index > 1

    def same_article(self, other: "ArticleRef") -> bool:
        return self.family == other.family

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "family": self.family,
            "canonical_url": self.canonical_url,
            "host": self.host,
            "raw_host": self.raw_host,
            "site": self.site,
            "article_id": self.article_id,
            "page_index": self.page_index,
            "query": self.query,
        }


class ArticleFamilyResolver:
    """Groups URLs into articles, across mirrors and pagination."""

    def __init__(self, mirrors: Mapping[str, str] | None = None) -> None:
        table = dict(KNOWN_MIRROR_HOSTS)
        table.update({str(k).lower(): str(v).lower() for k, v in (mirrors or {}).items()})
        self.mirrors = table

    # -- identity ------------------------------------------------------ #

    def mirror_host(self, host: str) -> str | None:
        """The canonical host when this host is a known mirror, else None."""
        value = (host or "").lower().split(":")[0]
        if value in self.mirrors:
            return self.mirrors[value]
        if canonical_host(value) != value.removeprefix("www."):
            return canonical_host(value)
        return None

    def resolve(self, url: str) -> ArticleRef:
        parsed = urlparse(str(url or ""))
        host = (parsed.netloc or "").lower()
        clean = canonical_host(host)
        return ArticleRef(
            url=str(url or ""),
            family=article_family(str(url or "")),
            canonical_url=canonical_url(first_page_url(str(url or ""))),
            host=clean,
            raw_host=_raw_host(host),
            site=registrable_domain(host),
            article_id=_article_id(str(url or "")),
            page_index=page_index(str(url or "")),
            query=query_signature(str(url or "")),
        )

    def family(self, url: str) -> str:
        return article_family(str(url or ""))

    def same_article(self, left: str, right: str) -> bool:
        return article_family(str(left or "")) == article_family(str(right or ""))

    def group(self, urls: Iterable[str]) -> dict[str, list[str]]:
        """Every URL of one article under that article's family id."""
        out: dict[str, list[str]] = {}
        for url in urls:
            out.setdefault(article_family(str(url or "")), []).append(str(url or ""))
        return out

    def unique(self, urls: Iterable[str]) -> list[str]:
        """One URL per article, page 1 preferred, order preserved."""
        chosen: dict[str, ArticleRef] = {}
        for url in urls:
            ref = self.resolve(str(url or ""))
            current = chosen.get(ref.family)
            if current is None or ref.page_index < current.page_index:
                chosen[ref.family] = ref
        return [ref.url for ref in chosen.values()]

    def mirror_of_known(self, url: str, known: Iterable[str]) -> str | None:
        """A known URL for the same article on a different host (a mirror view)."""
        ref = self.resolve(url)
        for other in known:
            other_ref = self.resolve(str(other or ""))
            if (
                other_ref.raw_host
                and other_ref.raw_host != ref.raw_host
                and other_ref.family == ref.family
            ):
                return other_ref.url
        return None

    def duplicates(self, urls: Iterable[str]) -> dict[str, list[str]]:
        """Only the families that were offered more than once."""
        return {family: group for family, group in self.group(urls).items() if len(group) > 1}

    # -- what the page itself claims ----------------------------------- #

    def declared_canonical(self, html: str, base_url: str = "") -> str:
        """The URL the page declares as canonical (`<link>` first, then og:url)."""
        body = str(html or "")
        for pattern, attr in ((_CANONICAL_LINK, _HREF), (_OG_URL, _CONTENT)):
            match = pattern.search(body)
            if not match:
                continue
            found = attr.search(match.group(0))
            if found and found.group(1).strip():
                return urljoin(base_url, found.group(1).strip())
        return ""

    def reprint_of(self, url: str, html: str) -> str | None:
        """The canonical URL when the page is a reprint of another site."""
        declared = self.declared_canonical(html, url)
        if not declared:
            return None
        if self.same_article(url, declared):
            return None
        here = registrable_domain(urlparse(str(url or "")).netloc)
        there = registrable_domain(urlparse(declared).netloc)
        if not there or there == here:
            return None
        return declared


def _raw_host(host: str) -> str:
    """The host exactly as the URL spells it (no port), mirrors included."""
    return (host or "").lower().split(":")[0]


def _article_id(url: str) -> str:
    from hsrmap.guides.signature import _ARTICLE_ID

    hit = _ARTICLE_ID.search(urlparse(str(url or "")).path or "")
    return hit.group(1) if hit else ""


DEFAULT_RESOLVER = ArticleFamilyResolver()


def same_article(left: str, right: str) -> bool:
    """Module-level convenience for callers that do not need a resolver."""
    return DEFAULT_RESOLVER.same_article(left, right)


def families(urls: Iterable[str]) -> dict[str, list[str]]:
    return DEFAULT_RESOLVER.group(urls)
