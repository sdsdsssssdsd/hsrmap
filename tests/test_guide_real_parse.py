"""Real-site markup: protocol-relative images and 作者 attribution."""

from hsrmap.guides.crawler.base import adapter_for


HTML = """
<html><head><title>星铁4.2版本，6个浮脂溯源解密攻略</title></head>
<body>
<p>来源：米游社 作者：祈鸢ya</p>
<p>这里是祈鸢，海原市共有3个浮脂溯源</p>
<img src="//cdn.example.test/guide-1.png" alt="海原市">
<img src="https://cdn.example.test/logo.png" alt="logo">
<img data-src="https://cdn.example.test/guide-2.png" alt="电视塔">
</body></html>
"""


def test_real_markup_author_and_https_assets():
    adapter = adapter_for("https://news.17173.com/content/x.shtml")
    meta = adapter.parse_page(HTML, "https://news.17173.com/content/x.shtml")
    assert meta["author"] == "祈鸢ya"
    assert meta["source_claim"] == "米游社"
    assets = adapter.extract_assets(HTML)
    assert "https://cdn.example.test/guide-1.png" in assets
    assert "https://cdn.example.test/guide-2.png" in assets
    assert all("logo" not in src for src in assets)


def test_gamersky_sidebar_author_does_not_overwrite_qi_yuan():
    html = HTML.replace("<body>", '<body><p class="author">游民星空</p>')
    meta = adapter_for("https://www.gamersky.com/handbook/x.shtml").parse_page(
        html, "https://www.gamersky.com/handbook/x.shtml"
    )
    assert meta["author"] == "祈鸢ya"

SCRIPT_HTML = """
<html><body>
<script src="https://shouyou.3dmgame.com/page/js/jq1.9.js"></script>
<link rel="stylesheet" href="https://x.test/a.css">
<iframe src="https://x.test/frame.html"></iframe>
<img src="https://x.test/real-1.jpg" alt="步骤1">
<img data-original="//x.test/real-2.jpg" alt="步骤2">
<div style="background:url(https://x.test/bg-3.png)"></div>
</body></html>
"""


def test_extract_assets_skips_scripts_and_page_chrome():
    adapter = adapter_for("https://shouyou.3dmgame.com/gl/634580.html")
    urls = adapter.extract_assets(SCRIPT_HTML)
    assert urls == [
        "https://x.test/real-1.jpg",
        "https://x.test/real-2.jpg",
        "https://x.test/bg-3.png",
    ]


def test_extract_assets_keeps_img_and_drops_non_images():
    adapter = adapter_for("https://a.9game.cn/bhxqtd/11837470.html")
    html = '<img src="https://cdn.9game.cn/a.png"><img src="https://cdn.9game.cn/b.js">'
    assert adapter.extract_assets(html) == ["https://cdn.9game.cn/a.png"]

def test_data_original_is_the_url_both_sides_agree_on():
    """Lazy-loaded pages: the extractor fetches data-original, the block looks it up."""
    from hsrmap.guides.extract.blocks import html_to_blocks

    html = '<img src="/static/blank.gif" data-original="https://cdn.test/real.jpg" alt="步骤">'
    adapter = adapter_for("https://news.17173.com/content/x.shtml")
    assert adapter.extract_assets(html) == ["https://cdn.test/real.jpg"]
    blocks = html_to_blocks(html, {"https://cdn.test/real.jpg": "b" * 64})
    image = [block for block in blocks if block["type"] == "image"][0]
    assert image["src"] == "https://cdn.test/real.jpg"
    assert image["asset"] == "b" * 64
