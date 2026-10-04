from __future__ import annotations

from hsrmap.guides.crawler.base import GuideSourceAdapter, _between, _prefer_author, _strip_tags


class GamerSkyAdapter(GuideSourceAdapter):
    name = "GamerSky"
    domain = "gamersky.com"
    source_kind = "AttributedReprint"
    priority = 50
    adapter_name = "gamersky"

    def parse_page(self, html: str, url: str) -> dict:
        meta = super().parse_page(html, url)
        author = _strip_tags(_between(html, 'class="author">', "</") or "")
        if author:
            meta["author"] = _prefer_author(meta.get("author"), author)
        if "米游社" in html:
            meta["source_claim"] = "米游社"
        return meta
