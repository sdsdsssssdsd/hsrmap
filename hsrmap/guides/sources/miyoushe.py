from hsrmap.guides.crawler.base import GuideSourceAdapter


class MiyousheAdapter(GuideSourceAdapter):
    name = "miyoushe"
    domain = "miyoushe.com"
    source_kind = "OriginalAuthorPage"
    priority = 10
    adapter_name = "miyoushe"
