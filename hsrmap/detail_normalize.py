from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif")
HTML_TAG = re.compile(r"<[^>]+>")
NOT_FOUND_MARKERS = ("not exist", "not found", "不存在", "找不到")


def classify_retcode(retcode: Any, message: str | None) -> str:
    text = (message or "").lower()
    if retcode == 0:
        return "OK"
    if any(marker in text for marker in NOT_FOUND_MARKERS):
        return "SOURCE_NOT_FOUND"
    return "FAILED_PERMANENT"


def _looks_like_image(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in IMAGE_EXT)


def _plain_text(content: str) -> str:
    return HTML_TAG.sub("", content).strip()


def _content_format(content: str) -> str:
    return "html" if "<" in content and ">" in content else "plain"


def _walk_urls(obj: Any, found: list[str]) -> None:
    if isinstance(obj, str) and obj.startswith(("http://", "https://")):
        found.append(obj)
        return
    if isinstance(obj, dict):
        for value in obj.values():
            _walk_urls(value, found)
        return
    if isinstance(obj, list):
        for value in obj:
            _walk_urls(value, found)


def normalize_point_info(payload: dict[str, Any]) -> dict[str, Any]:
    classification = classify_retcode(payload.get("retcode"), payload.get("message"))
    if classification == "SOURCE_NOT_FOUND":
        return {
            "job_state": "SOURCE_NOT_FOUND",
            "detail_state": "SOURCE_NOT_FOUND",
            "is_empty": False,
            "source_point_id": None,
            "title": None,
            "subtitle": None,
            "plain_text": "",
            "content_format": "plain",
            "content_raw": "",
            "images": [],
            "unclassified_asset_candidates": [],
            "retcode": payload.get("retcode"),
        }
    if classification != "OK":
        return {
            "job_state": "FAILED_PERMANENT",
            "detail_state": "FAILED",
            "is_empty": False,
            "source_point_id": None,
            "title": None,
            "subtitle": None,
            "plain_text": "",
            "content_format": "plain",
            "content_raw": "",
            "images": [],
            "unclassified_asset_candidates": [],
            "retcode": payload.get("retcode"),
        }

    info = ((payload.get("data") or {}).get("info") or {})
    content = info.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    img = (info.get("img") or "").strip()
    url_list = info.get("url_list") or []
    images: list[dict[str, Any]] = []
    known: set[str] = set()
    if img:
        images.append({"role": "image", "remote_url": img, "sort_order": 0, "alt_text": None})
        known.add(img)
    for index, item in enumerate(url_list):
        url = item if isinstance(item, str) else (item or {}).get("url")
        if not url or not str(url).startswith(("http://", "https://")):
            continue
        url = str(url)
        if _looks_like_image(url) or True:
            if url not in known:
                images.append({"role": "image", "remote_url": url, "sort_order": len(images), "alt_text": None})
                known.add(url)

    all_urls: list[str] = []
    _walk_urls(payload, all_urls)
    unclassified = []
    seen = set(known)
    for url in all_urls:
        if url in seen or not _looks_like_image(url):
            continue
        seen.add(url)
        unclassified.append({"remote_url": url, "reason": "unknown_field"})

    is_empty = not _plain_text(content) and not images
    return {
        "job_state": "COMPLETE_EMPTY" if is_empty else "COMPLETE",
        "detail_state": "EMPTY" if is_empty else "NONEMPTY",
        "is_empty": is_empty,
        "source_point_id": None if info.get("id") is None else str(info.get("id")),
        "title": None,
        "subtitle": None,
        "plain_text": _plain_text(content),
        "content_format": _content_format(content),
        "content_raw": content,
        "images": images,
        "unclassified_asset_candidates": unclassified,
        "retcode": payload.get("retcode"),
    }
