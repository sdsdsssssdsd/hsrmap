from __future__ import annotations

from hsrmap.guides.crawler.base import GuideSourceAdapter, _between, _strip_tags


class Site17173Adapter(GuideSourceAdapter):
    name = "17173"
    domain = "17173.com"
    source_kind = "AttributedReprint"
    priority = 40
    adapter_name = "site17173"

    def parse_page(self, html: str, url: str) -> dict:
        meta = super().parse_page(html, url)
        if "米游社" in html:
            meta["source_claim"] = "米游社"
        return meta
