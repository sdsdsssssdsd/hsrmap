"""Canonicalization and the Review Unit Signature (a1-6 §10–14).

A single official target can be reached from many articles, their paginated
continuations, their mobile/APP mirrors and several extractor units, so the
number of Review items stops tracking the number of targets. The fix is a
deterministic identity for one *review unit*:

```text
Layer 1  target identity   topic | target_key
Layer 2  source identity   canonical article family | normalized heading
Layer 3  unit signature    sha256(topic + target_key + normalized instruction + asset shas)
```

The signature is computed here, never by a model, and both the ingest path and
the merge pass use this one implementation.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse

#: Host prefixes that publish the same article as the desktop site.
MIRROR_HOST_PREFIXES = ("m.", "app.", "a.", "mip.", "3g.", "mob.", "wap.")

#: Query parameters that never change which article a URL points at.
TRACKING_PARAMS = frozenset(
    {
        "spm_id_from",
        "spm",
        "from",
        "from_source",
        "share_source",
        "share_medium",
        "share_plat",
        "share_tag",
        "share_token",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "_x_tr_sch",
        "ref",
        "refer",
        "channel",
    }
)

_PAGE_SUFFIX = re.compile(r"_(\d+)\.s?html?$", re.I)
_HTML_SUFFIX = re.compile(r"\.s?html?$", re.I)
_ARTICLE_ID = re.compile(r"/(\d{5,})(?:_\d+)?\.s?html?$", re.I)
_NON_WORD = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff]+")


def canonical_host(host: str) -> str:
    """Lower-case host without a mirror prefix or a `www.`."""
    value = (host or "").lower().split(":")[0]
    value = value.removeprefix("www.")
    for prefix in MIRROR_HOST_PREFIXES:
        if value.startswith(prefix) and value.count(".") >= 2:
            return value[len(prefix) :]
    return value


def canonical_url(url: str) -> str:
    """Host + path, without scheme, query, fragment or tracking parameters."""
    parsed = urlparse(str(url or ""))
    return f"{canonical_host(parsed.netloc)}{parsed.path}"


def query_signature(url: str) -> str:
    """Meaningful query pairs only, sorted — used when a site has no clean path."""
    pairs = [
        (key.lower(), value)
        for key, value in parse_qsl(urlparse(str(url or "")).query, keep_blank_values=False)
        if key.lower() not in TRACKING_PARAMS
    ]
    return "&".join(f"{key}={value}" for key, value in sorted(pairs))


#: Two-label public suffixes where the registrable domain is three labels.
_MULTI_LABEL_SUFFIXES = ("com.cn", "net.cn", "org.cn", "gov.cn", "com.hk", "com.tw", "co.jp", "co.kr")


def registrable_domain(host: str) -> str:
    """The site a host belongs to, mirror subdomains included."""
    value = canonical_host(host)
    labels = value.split(".")
    if len(labels) <= 2:
        return value
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def article_family(url: str) -> str:
    """The canonical article this URL is a view of.

    Pagination (`_2.shtml`), mirror hosts/subdomains and tracking parameters
    collapse onto one family id; distinct articles keep distinct ids. Sites that
    number their articles (`/319072.html`) are keyed by that number, the rest by
    their canonical host and normalized path.
    """
    parsed = urlparse(str(url or ""))
    path = parsed.path or ""
    hit = _ARTICLE_ID.search(path)
    if hit:
        return f"{registrable_domain(parsed.netloc)}/{hit.group(1)}"
    stem = _PAGE_SUFFIX.sub("_1.html", path)
    stem = stem.rstrip("/") or "/"
    if not _HTML_SUFFIX.search(stem):
        stem = f"{stem}.html"
    query = query_signature(url)
    return f"{canonical_host(parsed.netloc)}{stem}" + (f"?{query}" if query else "")


def normalize_text(text: Any) -> str:
    """Width-, case-, space- and punctuation-insensitive text key."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return _NON_WORD.sub("", value)


def instruction_key(steps: Iterable[Any] | None) -> str:
    """Normalized instruction text of one unit (its steps joined)."""
    parts: list[str] = []
    for step in steps or []:
        if isinstance(step, dict):
            parts.append(str(step.get("text") or ""))
        else:
            parts.append(str(step))
    return normalize_text("".join(parts))


def asset_key(images: Iterable[Any] | None) -> str:
    """Sorted, de-duplicated canonical asset hashes of one unit."""
    shas: set[str] = set()
    for image in images or []:
        if isinstance(image, dict):
            sha = image.get("asset") or image.get("sha256") or ""
        else:
            sha = str(image or "")
        sha = str(sha).strip()
        if sha:
            shas.add(sha)
    return ",".join(sorted(shas))


def target_identity(topic_key: str, target_key: str) -> str:
    """Layer 1: which official target a unit claims."""
    return f"{str(topic_key or '').strip().lower()}|{str(target_key or '').strip()}"


def source_identity(page_url: str, heading: str = "") -> str:
    """Layer 2: which article unit a candidate came from."""
    return f"{article_family(page_url)}|{normalize_text(heading)}"


def unit_signature(
    topic_key: str,
    target_key: str,
    steps: Iterable[Any] | None,
    images: Iterable[Any] | None,
) -> str:
    """Layer 3: the deterministic Review Unit Signature."""
    payload = "\x1f".join(
        [
            target_identity(topic_key, target_key),
            instruction_key(steps),
            asset_key(images),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def signature_for_draft(draft: dict[str, Any] | None, *, page_url: str = "") -> str:
    """Signature of a Review item's draft (target + instruction + assets)."""
    body = draft or {}
    topic = body.get("topic_key") or body.get("topic") or ""
    target = body.get("target_key") or ""
    if not target:
        pid = str(body.get("source_point_id") or "")
        target = f"point:{pid}" if pid else "unresolved"
    steps = list(body.get("steps") or [])
    images = list(body.get("images") or [])
    for step in steps:
        if isinstance(step, dict):
            images.extend(step.get("images") or [])
    return unit_signature(topic, str(target), steps, images)
