from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

_ARTICLE = re.compile(r"/(\d+)(?:_\d+)?\.s?html", re.I)
#: Pagination links are often relative ("1729233_2.shtml"), so every href is
#: resolved against the page URL before the article-id check.
_HREF = re.compile(r"""href=['"]([^'"]+)['"]""", re.I)


def same_site(left: str, right: str) -> bool:
    """Same host, tolerating the `www.` spelling difference.

    A mobile or app mirror (`m.3dmgame.com`, `app.ali213.net`, `a.9game.cn`) is
    a different host and therefore a different page, not a continuation of the
    article the crawler is already reading.
    """
    return left.lower().removeprefix("www.") == right.lower().removeprefix("www.")


def sibling_pages(html: str, url: str) -> list[str]:
    hit = _ARTICLE.search(urlparse(url).path)
    if not hit:
        return []
    article_id = hit.group(1)
    same = re.compile(rf"/{re.escape(article_id)}(?:_\d+)?\.s?html(?:$|[?#])", re.I)
    seen: list[str] = []
    self_url = url.split("#")[0].split("?")[0]
    host = urlparse(url).netloc
    for href in _HREF.findall(html or ""):
        href = (href or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "#", "data:")):
            continue
        clean = urljoin(self_url, href).split("#")[0].split("?")[0]
        if not same_site(urlparse(clean).netloc, host):
            continue
        if not same.search(urlparse(clean).path):
            continue
        if clean == self_url or clean in seen:
            continue
        seen.append(clean)
    return seen
