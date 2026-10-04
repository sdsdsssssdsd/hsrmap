"""The offline crawler fixture corpus (a1-6 §二十). Nothing here touches a network."""

import json
from pathlib import Path
from urllib.error import HTTPError

from hsrmap.guides.assets.fetcher import (
    FETCHED,
    HTTP_BLOCKED,
    HTTP_NOT_FOUND,
    NOT_IMAGE,
    AssetFetcher,
)
from hsrmap.guides.crawler.identity import ArticleFamilyResolver
from hsrmap.guides.crawler.pagination import sibling_pages
from hsrmap.guides.extract.blocks import html_to_blocks

FIXTURES = Path(__file__).parent / "fixtures" / "crawler"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
DESKTOP = "https://news.17173.com/z/xqtd/content/03032024/195341206.shtml"
MOBILE = "https://m.17173.com/z/xqtd/content/03032024/195341206.shtml"
PAGE2 = "https://news.17173.com/z/xqtd/content/03032024/195341206_2.shtml"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_manifest_lists_every_case_and_file():
    ids = {case["id"] for case in MANIFEST["cases"]}
    assert {
        "single_page",
        "pagination_page1",
        "pagination_page2",
        "mirror",
        "status_403",
        "status_404",
        "html_as_image",
    } <= ids
    for case in MANIFEST["cases"]:
        assert (FIXTURES / case["file"]).is_file(), case["file"]
        assert case["covers"]


def test_single_page_keeps_article_and_drops_noise():
    blocks = html_to_blocks(_read("3dm/single_page.html"))
    texts = [block.get("text") or "" for block in blocks]
    assert any(text == "朝露公馆全梦境迷钟解密攻略" for text in texts)
    assert any("第1个【梦境迷钟】在公馆一层" in text for text in texts)
    images = [block["src"] for block in blocks if block.get("type") == "image"]
    assert images[0].endswith("dream-1.png")  # data-original wins over the lazy placeholder
    assert sum(1 for src in images if src.endswith("dream-2.png")) == 2  # duplicate image
    assert not any("/ads/" in src for src in images)  # ("uploads/" also contains "ads/")
    assert blocks.dropped_empty_images == 1
    assert blocks.dropped_ads >= 1


def test_pagination_finds_only_the_same_article():
    first = sibling_pages(_read("17173/pagination.html"), DESKTOP)
    assert first == [PAGE2]
    assert sibling_pages(_read("17173/pagination_2.html"), PAGE2) == []


def test_mirror_page_is_the_same_article_and_declares_its_canonical():
    resolver = ArticleFamilyResolver()
    assert resolver.same_article(MOBILE, DESKTOP)
    html = _read("17173/mirror.html")
    assert resolver.declared_canonical(html, MOBILE) == DESKTOP
    assert resolver.reprint_of(MOBILE, html) is None  # same site, not a reprint
    assert resolver.mirror_host("m.17173.com") == "17173.com"


def _fetcher(case: str, *, status: int, content_type: str, raise_error: bool = False) -> AssetFetcher:
    body = (FIXTURES / case).read_bytes()

    def transport(url, headers, timeout):
        if raise_error:
            raise HTTPError(url, status, "boom", {}, None)
        return status, {"content-type": content_type}, body

    return AssetFetcher(transport=transport, sleep=lambda _: None, cache=None)


def test_403_fixture_is_a_typed_block():
    fetcher = _fetcher("9game/status_403.html", status=403, content_type="text/html", raise_error=True)
    result = fetcher.fetch("https://shouyou.9game.cn/img1.ali213.net/cover/4/10346053.jpg")
    assert result.status == HTTP_BLOCKED
    assert result.http_status == 403 and not result.ok
    assert result.reason == "HTTP 403"


def test_404_fixture_is_a_typed_miss():
    fetcher = _fetcher("9game/status_404.html", status=404, content_type="text/html", raise_error=True)
    result = fetcher.fetch("https://shouyou.9game.cn/missing.jpg")
    assert result.status == HTTP_NOT_FOUND
    assert result.http_status == 404


def test_html_served_as_an_image_is_not_an_image():
    fetcher = _fetcher("9game/html_as_image.html", status=200, content_type="image/jpeg")
    result = fetcher.fetch("https://shouyou.9game.cn/locked.jpg")
    assert result.status == NOT_IMAGE
    assert result.body is None


def test_a_real_image_fixture_is_fetched_and_hashed():
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (40, 30), (10, 20, 30)).save(buffer, "PNG")
    body = buffer.getvalue()

    def transport(url, headers, timeout):
        return 200, {"content-type": "image/png"}, body

    fetcher = AssetFetcher(transport=transport, sleep=lambda _: None, cache=None)
    result = fetcher.fetch("https://i.17173cdn.com/2fhnvk/YWxqaGBf/a1.png")
    assert result.status == FETCHED and result.sha256 and result.width == 40
