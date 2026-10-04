"""米游社（miyoushe）文章的 JSON API —— SPA 页面之外的那条路。

米游社文章页是 SPA：`crawler/render.py` 渲染出来只有 1.5 MB 的空壳（正文进不了 DOM），
于是这个站一直是「已知有料、拿不到」的黑洞。其实它有公开 JSON API，**缺的只是一个 Referer 头**
（不带就是 403）：

  GET https://bbs-api.miyoushe.com/post/wapi/getPostFull?post_id=<id>&read=1
      Referer: https://www.miyoushe.com/sr/article/<id>
  -> data.post.post.content 就是正文 HTML（等于页面里那篇攻略的正文）

搜索接口不需要 Referer：

  GET https://bbs-api.miyoushe.com/post/wapi/searchPosts?keyword=<kw>&size=20&offset=0
  -> data.posts[].post.{post_id, subject, content(预览)}

这个模块只做「取回 + 变成能被 import_page 解析的 HTML」，发布仍然走同一条审计/评审通道。
"""

from __future__ import annotations

import time
from html import escape
from typing import Any
from urllib.parse import quote

from hsrmap.http import RateLimitedClient

#: 米游社的风险控制认桌面 Chrome 的完整 UA——只有 "Mozilla/5.0 (...)" 的短 UA 会被判成脚本，
#: 接口回 `retcode=1034`（内容为空）。这条和 Referer 一样是必要头。
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

API_HOST = "https://bbs-api.miyoushe.com"
POST_FULL = API_HOST + "/post/wapi/getPostFull"
SEARCH_POSTS = API_HOST + "/post/wapi/searchPosts"
ARTICLE_URL = "https://www.miyoushe.com/sr/article/{post_id}"
#: 风控的「稍后再试」：同一条链接隔几秒再取通常就好了。
RISK_CONTROL_RETCODE = 1034


def article_url(post_id: int | str) -> str:
    return ARTICLE_URL.format(post_id=post_id)


def _client(client: Any = None) -> Any:
    return client or RateLimitedClient(min_interval=0.5)


def fetch_post(post_id: int | str, *, client: Any = None) -> dict[str, Any]:
    """一篇米游社文章：正文 HTML + 标题/作者/时间。失败抛 RuntimeError/HTTPError。"""
    url = f"{POST_FULL}?post_id={int(post_id)}&read=1"
    #: 关键是 Referer：没有它这个接口一律 403（实测）。
    headers = {"Referer": article_url(post_id), "User-Agent": _DESKTOP_UA}
    #: 风控会间歇性地回 retcode=1034（同一条链接隔几秒再取就正常），所以退避重试几次；
    #: 其它 retcode（比如文章不存在）不重试。
    payload: dict[str, Any] = {}
    for attempt, delay in enumerate((0, 3, 6, 12)):
        if delay:
            time.sleep(delay)
        payload = _client(client).get(url, headers=headers).json()
        if int(payload.get("retcode") or 0) == 0:
            break
        if int(payload.get("retcode") or 0) != RISK_CONTROL_RETCODE:
            break
    if int(payload.get("retcode") or 0) != 0:
        raise RuntimeError(f"miyoushe retcode={payload.get('retcode')} message={payload.get('message')}")
    data = payload.get("data") or {}
    wrapper = data.get("post") or {}
    post = wrapper.get("post") if isinstance(wrapper.get("post"), dict) else wrapper
    user = (wrapper.get("user") or {}) if isinstance(wrapper.get("user"), dict) else {}
    return {
        "post_id": str(post_id),
        "subject": str(post.get("subject") or ""),
        "content_html": str(post.get("content") or ""),
        "images": [str(item) for item in (post.get("images") or []) if item],
        "created_at": post.get("created_at") or post.get("created") or None,
        "author": str(user.get("nickname") or ""),
        "url": article_url(post_id),
    }


def search_posts(keyword: str, *, size: int = 20, offset: int = 0, client: Any = None) -> list[dict[str, Any]]:
    """按关键词搜文章（不需要 Referer）。返回 [{post_id, subject, preview}]。"""
    url = f"{SEARCH_POSTS}?keyword={quote(str(keyword))}&size={int(size)}&offset={int(offset)}"
    payload = _client(client).get(url, headers={"User-Agent": _DESKTOP_UA}).json()
    if int(payload.get("retcode") or 0) != 0:
        raise RuntimeError(f"miyoushe search retcode={payload.get('retcode')}")
    out: list[dict[str, Any]] = []
    for row in ((payload.get("data") or {}).get("posts") or []):
        post = (row or {}).get("post") or row or {}
        post_id = post.get("post_id")
        if not post_id:
            continue
        out.append({
            "post_id": str(post_id),
            "subject": str(post.get("subject") or ""),
            "preview": str(post.get("content") or "")[:200],
            "url": article_url(post_id),
        })
    return out


def post_html(post: dict[str, Any]) -> str:
    """把正文包成一篇可被 `import_page` 解析的 HTML（标题、正文、来源都留痕）。"""
    subject = escape(str(post.get("subject") or "米游社文章"))
    author = escape(str(post.get("author") or ""))
    body = str(post.get("content_html") or "")
    meta = f"<p>作者：{author}｜来源：米游社</p>" if author else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{subject}</title></head><body>"
        f"<h1>{subject}</h1>{meta}<div id='miyoushe-content'>{body}</div>"
        "</body></html>"
    )
