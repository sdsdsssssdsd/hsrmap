"""URL identity: one article, however many URLs point at it (Sprint 2)."""

from hsrmap.guides.crawler.identity import (
    ArticleFamilyResolver,
    first_page_url,
    page_index,
)

RESOLVER = ArticleFamilyResolver()


def test_mirror_host_and_pagination_collapse_to_one_article():
    desktop = "https://www.gamersky.com/handbook/202404/1729233.shtml"
    mobile = "https://m.gamersky.com/handbook/202404/1729233_2.shtml?utm_source=share"
    assert RESOLVER.same_article(desktop, mobile)
    ref = RESOLVER.resolve(mobile)
    assert ref.family == RESOLVER.family(desktop)
    assert ref.page_index == 2 and ref.is_paginated
    assert ref.canonical_url == "gamersky.com/handbook/202404/1729233.shtml"
    assert ref.site == "gamersky.com"
    assert ref.query == ""


def test_distinct_articles_stay_distinct():
    a = "https://www.gamersky.com/handbook/202404/1729233.shtml"
    b = "https://www.gamersky.com/handbook/202404/1728961.shtml"
    assert not RESOLVER.same_article(a, b)
    assert len(RESOLVER.group([a, b])) == 2


def test_group_and_unique_prefer_the_first_page():
    page1 = "https://news.17173.com/content/05112024/152401625.shtml"
    page2 = "https://news.17173.com/content/05112024/152401625_2.shtml"
    mirror = "https://m.17173.com/content/05112024/152401625.shtml"
    groups = RESOLVER.group([page2, page1, mirror])
    assert len(groups) == 1
    assert len(next(iter(groups.values()))) == 3
    assert RESOLVER.unique([page2, page1, mirror]) == [page1]
    # page 1 and page 2 are the same article seen twice
    assert list(RESOLVER.duplicates([page2, page1]).values()) == [[page2, page1]]


def test_page_index_reads_query_pagination():
    assert page_index("https://t.test/a.html") == 1
    assert page_index("https://t.test/a_3.shtml") == 3
    assert page_index("https://t.test/a.html?page=4") == 4
    assert page_index("https://t.test/a.html?p=2&utm_source=x") == 2
    assert first_page_url("https://t.test/a_3.shtml") == "https://t.test/a.shtml"


def test_mirror_of_known_reports_the_known_view():
    known = ["https://www.gamersky.com/handbook/202404/1729233.shtml"]
    mirror = RESOLVER.mirror_of_known("https://m.gamersky.com/handbook/202404/1729233.shtml", known)
    assert mirror == known[0]
    assert RESOLVER.mirror_of_known("https://www.gamersky.com/handbook/202404/1728961.shtml", known) is None


def test_declared_canonical_reads_link_then_og_url():
    link = '<html><head><link rel="canonical" href="https://www.gamersky.com/handbook/202404/1729233.shtml" /></head></html>'
    assert RESOLVER.declared_canonical(link) == "https://www.gamersky.com/handbook/202404/1729233.shtml"
    og = '<meta property="og:url" content="/handbook/202404/1729233.shtml" />'
    assert (
        RESOLVER.declared_canonical(og, "https://www.gamersky.com/x/")
        == "https://www.gamersky.com/handbook/202404/1729233.shtml"
    )
    assert RESOLVER.declared_canonical("<html></html>") == ""


def test_reprint_is_when_the_page_claims_another_site():
    html = '<link rel="canonical" href="https://www.gamersky.com/handbook/202404/1729233.shtml">'
    assert RESOLVER.reprint_of("https://www.9game.cn/news/123456.html", html) == (
        "https://www.gamersky.com/handbook/202404/1729233.shtml"
    )
    # same article, same site: not a reprint
    assert RESOLVER.reprint_of("https://m.gamersky.com/handbook/202404/1729233_2.shtml", html) is None
    # no claim at all
    assert RESOLVER.reprint_of("https://www.9game.cn/news/123456.html", "<html></html>") is None


def test_site_adapter_can_declare_extra_mirrors():
    resolver = ArticleFamilyResolver(mirrors={"legacy.test": "t.test"})
    assert resolver.mirror_host("legacy.test") == "t.test"
    assert resolver.mirror_host("m.t.test") == "t.test"
    assert resolver.mirror_host("www.t.test") is None

def test_cli_identity_groups_urls(tmp_path, capsys):
    import json

    from hsrmap.cli import main
    from hsrmap.guide_db import GuideDatabase

    #: identity 是只读命令：库不存在就报错（a1-8 四.2），测试自己把它建出来。
    GuideDatabase.create(tmp_path / "guide.db").close()
    code = main([
        "guides", "identity",
        "--db", str(tmp_path / "guide.db"),
        "--url", "https://www.gamersky.com/handbook/202404/1729233.shtml",
        "--url", "https://m.gamersky.com/handbook/202404/1729233_2.shtml?utm_source=x",
    ])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert len(body["families"]) == 1
    assert body["unique"] == ["https://www.gamersky.com/handbook/202404/1729233.shtml"]
    assert body["articles"][1]["page_index"] == 2

