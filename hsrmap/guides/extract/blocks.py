from __future__ import annotations

import html as html_module
import re
from html.parser import HTMLParser
from typing import Any

#: <meta> tags and their attributes, for the page's own description.
_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR = re.compile(r"""([A-Za-z:_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


#: Bumped whenever a parser change alters which text a page yields, so a page
#: can be re-derived and the reason is visible in `guide_page.parser_version`.
BLOCK_PARSER_VERSION = "blocks/2026-10-04"

SKIP = {"script", "style", "noscript", "svg"}
SKIP_HEADINGS = {
    "相关推荐",
    "热门推荐",
    "广告",
    "17173 新闻导语",
    "崩坏：星穹铁道",
    "崩坏星穹铁道",
}

#: Container classes that hold site furniture. Everything inside them is chrome,
#: no matter which tag it uses — navigation lists were being stored as article
#: text ("网页游戏", "热门单机") and then audited as if the article said them.
CHROME_CLASSES = frozenset(
    {
        "nav",
        "navbar",
        "navigation",
        "menu",
        "submenu",
        "breadcrumb",
        "crumbs",
        "footer",
        "sidebar",
        "side",
        "related",
        "related-rec",
        "recommend",
        "rank",
        "ranking",
        "advert",
        "ad",
        "ads",
    }
)


def is_chrome_class(class_name: str) -> bool:
    tokens = {token.strip().lower() for token in str(class_name or "").replace("_", "-").split()}
    return bool(tokens & CHROME_CLASSES)


#: Headings after which nothing belongs to the article any more. Only these end
#: the body — a "17173 新闻导语" or a game-name heading is just skipped, it must
#: never suppress the paragraphs that follow it.
TAIL_HEADINGS = {"相关推荐", "热门推荐", "广告"}
CHROME_TEXTS = {
    "主站",
    "商城",
    "论坛",
    "首页",
    "手游",
    "网游",
    "单机",
    "软件",
    "自运营",
    "推荐",
    "安卓",
    "苹果",
    "简体中文",
    "English",
    "扫描二维码",
    "下载17173APP",
    "手机浏览",
}
AD_HINTS = (
    "广告",
    "/ads/",
    "/ads."
    "ad-banner",
    "adbanner",
    "sponsor",
    "related-rec",
    "logo",
    "qrcode",
    "blank.png",
    "loading.gif",
    "!a-3-240x",
    "language-arrow",
    "/sao.png",
    "new_preview",
    "pe_u_thumb",
    "/upimg/new",
    "webgame_oss",
    "webimg13/webgame",
)


def is_ad_image(src: str = "", alt: str = "", class_name: str = "") -> bool:
    hay = " ".join([src or "", alt or "", class_name or ""]).lower()
    return any(hint.lower() in hay for hint in AD_HINTS) or "广告" in (alt or "") or "相关推荐" in (alt or "")


class _BlockParser(HTMLParser):
    def __init__(self, images: dict[str, str]):
        super().__init__(convert_charrefs=True)
        self.images = images
        self.blocks: list[dict[str, Any]] = []
        self.dropped_ads = 0
        self.dropped_empty_images = 0
        self._skip = 0
        self._omit = False
        self._buf: list[str] = []
        self._kind: str | None = None
        self._img_attrs: dict[str, str] = {}
        self._div_is_fold_title: list[bool] = []
        self._div_is_chrome: list[bool] = []
        self.dropped_chrome = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            self._kind = "heading"
            return
        if tag == "div":
            class_name = " ".join((v or "") for k, v in attrs if k == "class")
            self._div_is_fold_title.append("ql-fold-title" in class_name)
            self._div_is_chrome.append(is_chrome_class(class_name))
            if "GsWeTxt" in class_name:
                self._flush()
                self._kind = "heading"
                return
        if tag == "p":
            self._flush()
            self._kind = "heading" if any(self._div_is_fold_title) else "paragraph"
            return
        if tag in {"li"}:
            self._flush()
            self._kind = "list"
            return
        if tag == "blockquote":
            self._flush()
            self._kind = "quote"
            return
        if tag == "hr":
            self._flush()
            if not self._omit:
                self._append({"type": "separator"})
            return
        if tag == "img":
            data = {k: (v or "") for k, v in attrs}
            from hsrmap.guides.crawler.base import pick_image_src

            src = pick_image_src(data)
            if not src:
                # An <img> without a usable source carries no information; it must
                # not become a block that later fails the asset check.
                self.dropped_empty_images += 1
                return
            if src.startswith("//"):
                src = "https:" + src
            alt = data.get("alt") or ""
            class_name = data.get("class") or ""
            if self._omit or is_ad_image(src, alt, class_name):
                self.dropped_ads += 1
                return
            asset = self.images.get(src)
            block: dict[str, Any] = {"type": "image", "src": src, "alt": alt}
            if asset:
                block["asset"] = asset
            self._append(block)
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if "youtube" in href or "bilibili" in href:
                self._append({"type": "video_link", "url": href})

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP and self._skip:
            self._skip -= 1
            return
        if tag == "div" and self._div_is_fold_title:
            self._div_is_fold_title.pop()
        if tag == "div" and self._div_is_chrome:
            self._div_is_chrome.pop()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "div"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip or self._kind is None:
            return
        self._buf.append(data)

    def _append(self, block: dict[str, Any]) -> None:
        block["id"] = f"b{len(self.blocks) + 1}"
        self.blocks.append(block)

    def _flush(self) -> None:
        if self._kind is None:
            return
        text = "".join(self._buf).strip()
        self._buf = []
        kind = self._kind
        self._kind = None
        if not text:
            return
        if any(self._div_is_chrome):
            self.dropped_chrome += 1
            return
        if text in CHROME_TEXTS or text.startswith("本文内容来源于互联网"):
            return
        if kind == "heading":
            tail = text in TAIL_HEADINGS or text.startswith("关于崩坏") or text.startswith("更多相关")
            if text in SKIP_HEADINGS or tail:
                # A recommendation tail really does end the article; the other
                # skipped headings are only dropped themselves.
                if tail:
                    self._omit = True
                return
            self._omit = False
        if self._omit:
            return
        self._append({"type": kind, "text": text})


class BlockList(list):
    dropped_ads = 0
    dropped_empty_images = 0
    dropped_chrome = 0


#: A description shorter than this is a site slogan, not the page's own text.
MIN_DESCRIPTION_CHARS = 40


def _meta_description(html: str) -> str:
    """The page's own summary line ("description" / "og:description").

    Mobile-first sites render the post with JavaScript and keep the post text in
    the description meta. Without it a taptap guide arrives as 27 characters of UI
    labels even though the sentence the guide is *about* — "『观览云岛站』共4处" —
    sits in the page source.
    """
    best = ""
    for tag in _META_TAG.findall(html or ""):
        attrs: dict[str, str] = {}
        for match in _META_ATTR.finditer(tag):
            value = match.group(2) if match.group(2) is not None else (match.group(3) or "")
            attrs[match.group(1).lower()] = value
        key = (attrs.get("name") or attrs.get("property") or "").strip().lower()
        if key not in {"description", "og:description"}:
            continue
        value = html_module.unescape(attrs.get("content") or "").strip()
        if len(value) > len(best):
            best = value
    return best


def html_to_blocks(html: str, images: dict[str, str] | None = None) -> list[dict[str, Any]]:
    parser = _BlockParser(images or {})
    parser.feed(html)
    parser._flush()
    description = _meta_description(html)
    if (
        len(description) >= MIN_DESCRIPTION_CHARS
        and any("一" <= char <= "鿿" for char in description)
        and not any(description in str(block.get("text") or "") for block in parser.blocks)
    ):
        #: kept as the first block: it is the uploader's own summary of the page
        parser.blocks.insert(0, {"type": "paragraph", "text": description, "source": "meta"})
    blocks = BlockList(parser.blocks)
    blocks.dropped_ads = parser.dropped_ads
    blocks.dropped_empty_images = parser.dropped_empty_images
    blocks.dropped_chrome = parser.dropped_chrome
    return blocks
