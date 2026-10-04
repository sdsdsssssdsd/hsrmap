from hsrmap.guides.crawler.pagination import sibling_pages


HTML = """
<div class="page_css">
 <b><a href="https://www.gamersky.com/handbook/202304/1592527.shtml">1</a></b>
 <a href="https://www.gamersky.com/handbook/202304/1592527_2.shtml">2</a>
 <a href="https://www.gamersky.com/handbook/202304/1592527_3.shtml">3</a>
 <a href="https://www.gamersky.com/handbook/202304/1592527_2.shtml">下一页</a>
</div>
<li><a href="https://www.gamersky.com/handbook/202304/1590833.shtml">宝箱收集攻略</a></li>
"""


def test_sibling_pages_only_same_article_id():
    url = "https://www.gamersky.com/handbook/202304/1592527.shtml"
    pages = sibling_pages(HTML, url)
    assert "https://www.gamersky.com/handbook/202304/1592527_2.shtml" in pages
    assert "https://www.gamersky.com/handbook/202304/1592527_3.shtml" in pages
    assert all("1590833" not in item for item in pages)
    assert all("1592527" in item for item in pages)


def test_sibling_pages_empty_without_pager():
    assert sibling_pages("<p>第1页：</p>", "https://www.gamersky.com/handbook/202511/2040531.shtml") == []


MIRROR_HTML = """
<a href="https://m.3dmgame.com/ol/gl/354807.html">手机版</a>
<a href="https://app.3dmgame.com/gl/354807.html">APP版</a>
<a href="https://ol.3dmgame.com/gl/354807_2.html">下一页</a>
"""


def test_sibling_pages_ignores_mirror_hosts():
    url = "https://ol.3dmgame.com/gl/354807.html"
    pages = sibling_pages(MIRROR_HTML, url)
    assert pages == ["https://ol.3dmgame.com/gl/354807_2.html"]


def test_sibling_pages_tolerates_www_spelling():
    html = '<a href="https://news.17173.com/content/04022026/111240224.shtml">首页</a>'
    pages = sibling_pages(html, "https://www.news.17173.com/content/04022026/111240224.shtml")
    assert pages == ["https://news.17173.com/content/04022026/111240224.shtml"]
