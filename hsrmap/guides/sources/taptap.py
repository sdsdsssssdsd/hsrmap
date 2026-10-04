from __future__ import annotations

from hsrmap.guides.crawler.base import GuideSourceAdapter, _between, _prefer_author, _strip_tags


class TapTapAdapter(GuideSourceAdapter):
    name = "TapTap"
    domain = "taptap.cn"
    source_kind = "OriginalCommunityPost"
    priority = 20
    adapter_name = "taptap"

    def parse_page(self, html: str, url: str) -> dict:
        meta = super().parse_page(html, url)
        author = _strip_tags(_between(html, 'class="author">', "</") or "")
        if author:
            meta["author"] = _prefer_author(meta.get("author"), author)
        return meta
