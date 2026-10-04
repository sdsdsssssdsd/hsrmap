"""DocumentBlocks keep heading / paragraph / image order from HTML."""

from pathlib import Path

from hsrmap.guides.extract.blocks import html_to_blocks

FIXTURE = Path(__file__).parent / "fixtures" / "guides" / "sample_17173.html"


def test_chrome_nav_is_not_kept_as_guide_blocks():
    html = """
    <html><body>
    <ul><li>主站</li><li>商城</li><li>论坛</li></ul>
    <p>扫描二维码</p>
    <h2>海原市</h2>
    <p>海原市地图共有3个浮脂溯源解密</p>
    <img src="https://cdn.example.test/haiyuan-1.png" alt="海原市">
    </body></html>
    """
    blocks = html_to_blocks(html)
    texts = [b.get("text") or "" for b in blocks]
    assert "主站" not in texts
    assert "商城" not in texts
    assert "扫描二维码" not in texts
    assert any(b.get("type") == "heading" and b.get("text") == "海原市" for b in blocks)
    assert any("海原市地图共有3个" in (b.get("text") or "") for b in blocks)


def test_17173_fixture_becomes_ordered_blocks():
    html = FIXTURE.read_text(encoding="utf-8")
    blocks = html_to_blocks(html, images={"https://cdn.example.test/haiyuan-1.png": "aaa", "https://cdn.example.test/tower-1.png": "bbb"})
    types = [b["type"] for b in blocks]
    assert "heading" in types
    assert "paragraph" in types
    assert "image" in types
    assert blocks[0]["type"] == "heading"
    assert "海原市" in " ".join(b.get("text") or "" for b in blocks)
    images = [b for b in blocks if b["type"] == "image"]
    assert images[0]["asset"] == "aaa"
    assert images[1]["asset"] == "bbb"


def test_3dm_upload_guide_images_are_not_dropped_as_ads():
    html = """
    <html><body>
    <h3>寂灭空飨妖都地图共有4个浮脂溯源解密</h3>
    <p align="center">
      <img src="https://olimg.3dmgame.com/uploads/images/xiaz/2026/0716/1784171173323_png_r.webp">
    </p>
    <img src="https://cdn.example.test/ads/banner.png" alt="广告">
    </body></html>
    """
    src = "https://olimg.3dmgame.com/uploads/images/xiaz/2026/0716/1784171173323_png_r.webp"
    blocks = html_to_blocks(html, images={src: "grease-sha"})
    images = [b for b in blocks if b.get("type") == "image"]
    assert [b.get("asset") for b in images] == ["grease-sha"]


def test_gamersky_section_divs_are_headings():
    html = """
    <html><body>
    <div class="GsWeTxt2">1层区域</div>
    <p>【梦境迷钟】解谜宝箱</p>
    <div class="GsWeTxt3">点位3：（解谜）</div>
    </body></html>
    """
    blocks = html_to_blocks(html)
    headings = [b.get("text") for b in blocks if b.get("type") == "heading"]
    assert "1层区域" in headings
    assert "点位3：（解谜）" in headings
    assert any(b.get("type") == "paragraph" and "梦境迷钟" in (b.get("text") or "") for b in blocks)


def test_17173_fold_titles_are_headings():
    html = """
    <html><body>
    <h2>1、黄金的时刻（4个）</h2>
    <div class="ql-fold-title">
      <div class="ql-fold-title-content">
        <p>第1个【梦境迷钟】修复解密</p>
      </div>
    </div>
    <div class="ql-fold-content">
      <p>将1黄色模块移到左下</p>
    </div>
    </body></html>
    """
    blocks = html_to_blocks(html)
    headings = [b.get("text") for b in blocks if b.get("type") == "heading"]
    assert "1、黄金的时刻（4个）" in headings
    assert "第1个【梦境迷钟】修复解密" in headings
    assert any(b.get("type") == "paragraph" and "黄色模块" in (b.get("text") or "") for b in blocks)


def test_17173_chrome_lead_is_not_a_heading():
    html = """
    <html><body>
    <h1>17173 新闻导语</h1>
    <p>导语广告</p>
    <h2>1、黄金的时刻（4个）</h2>
    <p>第1个【梦境迷钟】</p>
    </body></html>
    """
    blocks = html_to_blocks(html)
    texts = [b.get("text") or "" for b in blocks]
    assert "17173 新闻导语" not in texts
    assert any(b.get("type") == "heading" and "黄金的时刻" in (b.get("text") or "") for b in blocks)

def test_skip_heading_does_not_swallow_the_article():
    """A skipped chrome heading must be dropped on its own; the body survives."""
    html = """
    <html><body>
    <div class="gb-final-mod-summary"><h2>17173 新闻导语</h2><p>导语</p></div>
    <div class="gb-final-mod-article" id="mod_article">
      <p>大家好，本期整理了2.0版本新增地图点位。</p>
      <p>下图为匹诺康尼-稚子的梦点位图。</p>
    </div>
    </body></html>
    """
    blocks = html_to_blocks(html)
    texts = [b.get("text") or "" for b in blocks]
    assert "17173 新闻导语" not in texts
    assert any("大家好，本期整理了" in text for text in texts)
    assert any("下图为匹诺康尼" in text for text in texts)


def test_tail_heading_ends_the_article():
    html = """
    <html><body>
    <p>正文最后一段</p>
    <h2>相关推荐</h2>
    <p>推荐文章甲</p>
    <p>推荐文章乙</p>
    </body></html>
    """
    blocks = html_to_blocks(html)
    texts = [b.get("text") or "" for b in blocks]
    assert any("正文最后一段" in text for text in texts)
    assert "推荐文章甲" not in texts
    assert "推荐文章乙" not in texts

def test_chrome_containers_are_not_article_blocks():
    """A navigation list is site furniture, not article text (a1-6 §十八)."""
    html = """
    <html><body>
    <div class="nav"><ul><li>网页游戏</li><li>热门单机</li></ul></div>
    <div class="side related-rec"><p>相关推荐：别的攻略</p></div>
    <div class="content"><p>真正的正文</p></div>
    </body></html>
    """
    blocks = html_to_blocks(html)
    texts = [b.get("text") or "" for b in blocks]
    assert texts == ["真正的正文"]
    assert blocks.dropped_chrome == 3



def test_the_page_description_becomes_article_text():
    """A mobile post keeps its text in the description meta; the blocks keep it too."""
    from hsrmap.guides.extract.blocks import html_to_blocks, MIN_DESCRIPTION_CHARS

    description = "『珠星大厦』共3处，『观览云岛站』共4处，具体位置标序看图2，查缺补漏直接对号查找即可。"
    html = (
        "<html><head>"
        '<meta name="description" content="' + description + '">'
        "</head><body><p>正文</p></body></html>"
    )
    html = html.replace("&", "&amp;").replace("＆", "&amp;")
    blocks = html_to_blocks(html)
    texts = [str(block.get("text") or "") for block in blocks]
    assert any(description[:20] in text for text in texts), texts
    assert len(description) >= MIN_DESCRIPTION_CHARS


def test_a_short_or_repeated_description_is_not_added():
    from hsrmap.guides.extract.blocks import html_to_blocks

    short = "<html><head><meta name='description' content='攻略'></head><body><p>正文</p></body></html>"
    assert all("攻略" != str(block.get("text") or "") for block in html_to_blocks(short))

    long_text = "崩坏星穹铁道二相乐园的收集攻略，本文整理了全部点位与路线，供大家查缺补漏使用。"
    body = "<html><head><meta name='description' content='" + long_text + "'></head><body><p>" + long_text + "</p></body></html>"
    blocks = html_to_blocks(body)
    # already in the page body: not added a second time
    assert sum(1 for block in blocks if long_text in str(block.get("text") or "")) == 1


def test_an_english_only_description_is_left_out():
    from hsrmap.guides.extract.blocks import html_to_blocks

    english = "Honkai Star Rail guide for the Anime Jump stickers in the Second Dimension City."
    html = "<html><head><meta property='og:description' content='" + english + "'></head><body><p>正文</p></body></html>"
    assert all(english not in str(block.get("text") or "") for block in html_to_blocks(html))
