from __future__ import annotations

from hashlib import sha256
from typing import Any


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "") if not ch.isspace())


def _simhash(text: str) -> int:
    digest = sha256(_norm(text).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def classify_duplicate(left: dict[str, Any], right: dict[str, Any]) -> str:
    if left.get("content_sha256") and left.get("content_sha256") == right.get("content_sha256"):
        return "DUPLICATE_EXACT"
    if _norm(left.get("text") or "") and _norm(left.get("text") or "") == _norm(right.get("text") or ""):
        return "DUPLICATE_EXACT"
    same_author = (left.get("author") or "") == (right.get("author") or "") and bool(left.get("author"))
    title_l = _norm(left.get("title") or "")
    title_r = _norm(right.get("title") or "")
    common = 0
    for a, b in zip(title_l, title_r):
        if a != b:
            break
        common += 1
    title_close = title_l and title_r and (title_l in title_r or title_r in title_l or common >= 6)
    text_close = _simhash(left.get("text") or "") >> 16 == _simhash(right.get("text") or "") >> 16
    text_l = _norm(left.get("text") or "")
    text_r = _norm(right.get("text") or "")
    text_overlap = bool(text_l and text_r and (text_l in text_r or text_r in text_l or text_close or _shared_span(text_l, text_r, 12)))
    if same_author and title_close and text_overlap:
        return "DUPLICATE_PROBABLE"
    if same_author and _shared_span(text_l, text_r, 12):
        return "DUPLICATE_PROBABLE"
    return "UNIQUE"


def _shared_span(left: str, right: str, n: int) -> bool:
    if not left or not right:
        return False
    if min(len(left), len(right)) >= 8 and (left in right or right in left):
        return True
    if len(left) < n or len(right) < n:
        return False
    if left in right or right in left:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    step = max(1, n // 2)
    for i in range(0, len(shorter) - n + 1, step):
        if shorter[i : i + n] in longer:
            return True
    return False
