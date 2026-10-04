"""Image relevance rules and visual dedup (a1-6 §十)."""

from hsrmap.guides.assets.relevance import filter_images, judge_image


def test_small_and_extreme_aspect_images_are_dropped():
    assert judge_image(width=32, height=32, url="https://c.test/a.png").keep is False
    dropped = judge_image(width=1400, height=200, url="https://c.test/b.png")
    assert dropped.keep is False and "aspect" in dropped.reasons[0]
    kept = judge_image(width=1280, height=720, url="https://c.test/c.png")
    assert kept.keep is True and kept.score > 50


def test_url_patterns_and_chrome_regions_are_dropped():
    for url in ("https://c.test/logo.png", "https://c.test/qrcode.jpg", "https://c.test/ads/banner.png"):
        verdict = judge_image(width=800, height=600, url=url)
        assert verdict.keep is False and "url pattern" in verdict.reasons[0]
    assert judge_image(width=800, height=600, url="https://c.test/x.png", dom_class="sidebar").keep is False
    assert judge_image(width=800, height=600, url="https://c.test/x.png", dom_tag="footer").keep is False
    assert judge_image(width=800, height=600, url="https://c.test/x.png", alt="广告").keep is False


def test_exact_and_visual_duplicates_are_dropped():
    page = [
        {"url": "https://c.test/1.png", "width": 900, "height": 600, "sha256": "a" * 64, "phash": "44c42b3f913f36c4"},
        {"url": "https://c.test/1-copy.png", "width": 900, "height": 600, "sha256": "a" * 64, "phash": "44c42b3f913f36c4"},
        {"url": "https://c.test/1-small.jpg", "width": 900, "height": 600, "sha256": "b" * 64, "phash": "44c42b3f913f36c5"},
        {"url": "https://c.test/2.png", "width": 900, "height": 600, "sha256": "c" * 64, "phash": "704f60b01fec16ab"},
    ]
    result = filter_images(page)
    assert result["kept_count"] == 2
    assert result["dropped_count"] == 2
    assert "duplicate sha256" in result["reasons"]
    assert any("visual duplicate" in reason for reason in result["reasons"])


def test_filter_reports_what_it_kept_in_order():
    page = [
        {"url": "https://c.test/small.png", "width": 20, "height": 20},
        {"url": "https://c.test/big.png", "width": 1600, "height": 900, "alt": "点位图"},
    ]
    result = filter_images(page)
    assert [item["url"] for item in result["kept"]] == ["https://c.test/big.png"]
    assert result["kept"][0]["relevance"]["keep"] is True
    assert result["dropped"][0]["relevance"]["reasons"] == ["too small (20x20)"]
