"""米游社：SPA 拿不到正文，但公开 JSON API 带 Referer 就能拿到。"""

import json

from hsrmap.guides.crawler.miyoushe import fetch_post, post_html, search_posts


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, timeout=30, headers=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return _Response(self.payload)


def _full_payload():
    return {
        "retcode": 0,
        "data": {
            "post": {
                "post": {
                    "subject": "【V3.7攻略】「黄金替罪羊」系列全解密合集攻略",
                    "content": "<p>第1个：右右右右</p>",
                    "created_at": 1762000000,
                    "images": ["https://upload-bbs.miyoushe.com/a.png"],
                },
                "user": {"nickname": "赤夜"},
            }
        },
    }


def test_fetch_post_sends_the_referer_that_makes_the_api_work():
    client = _Client(_full_payload())
    post = fetch_post(70428611, client=client)
    call = client.calls[0]
    assert "post_id=70428611" in call["url"]
    #: 缺 Referer 就是 403 —— 这条头是米游社能不能拿到的分水岭
    assert call["headers"]["Referer"] == "https://www.miyoushe.com/sr/article/70428611"
    #: 短 UA 会被风险控制判成脚本（retcode 1034），必须是桌面 Chrome 的完整 UA
    assert "Chrome/" in call["headers"]["User-Agent"]
    assert post["subject"].startswith("【V3.7攻略】")
    assert post["content_html"] == "<p>第1个：右右右右</p>"
    assert post["author"] == "赤夜"
    assert post["images"] == ["https://upload-bbs.miyoushe.com/a.png"]


def test_a_non_zero_retcode_is_an_error_not_an_empty_post():
    client = _Client({"retcode": 10001, "message": "bad request"})
    try:
        fetch_post(1, client=client)
    except RuntimeError as exc:
        assert "retcode" in str(exc)
    else:
        raise AssertionError("retcode != 0 时必须报错")


def test_search_posts_lists_post_ids():
    payload = {
        "retcode": 0,
        "data": {
            "posts": [
                {"post": {"post_id": "70428611", "subject": "合集", "content": "预览"}},
                {"post": {"post_id": "1", "subject": "别的", "content": "预览"}},
                {"post": {"subject": "没有 id"}},
            ]
        },
    }
    rows = search_posts("黄金替罪羊", client=_Client(payload))
    assert [row["post_id"] for row in rows] == ["70428611", "1"]


def test_post_html_is_a_parseable_document():
    html = post_html({"subject": "标题", "author": "作者", "content_html": "<p>正文</p>"})
    assert "<title>标题</title>" in html
    assert "<p>正文</p>" in html
    assert "作者" in html
