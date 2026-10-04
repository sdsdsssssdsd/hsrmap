"""Rule-based image relevance, then perceptual dedup (a1-6 §十).

A guide page can carry 55 images and only a handful matter. Before any model
looks at them, cheap rules answer "is this even a candidate":

```text
min width / min height / aspect ratio
URL pattern (logo, icon, avatar, sprite, banner, qr, loading, blank)
DOM location (header / footer / sidebar / nav)
duplicate SHA256            -> exact duplicate
duplicate pHash (<= 6 bit)  -> visual duplicate (recompressed, resized, watermarked)
```

The second layer is what makes multi-site reprints tractable: the same guide
picture rarely survives a re-upload byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from hsrmap.guides.assets.phash import SIMILARITY_THRESHOLD, hamming

#: Below this an image is decoration (icons, spacers, avatars).
MIN_WIDTH = 200
MIN_HEIGHT = 150

#: Extreme aspect ratios are banners, dividers or single lines of text.
MIN_ASPECT = 0.2
MAX_ASPECT = 5.0

#: URL fragments that never carry guide content.
URL_HINTS = (
    "logo",
    "icon",
    "avatar",
    "sprite",
    "banner",
    "qrcode",
    "qr_code",
    "loading",
    "blank.",
    "placeholder",
    "spacer",
    "share_",
    "/ads/",
    "advert",
)

#: Class names that mean "this image sits in the page furniture".
CHROME_REGIONS = ("header", "footer", "sidebar", "side-bar", "nav", "menu", "breadcrumb", "related", "recommend")

#: Chinese alt text that always means "not article content".
AD_ALT_HINTS = ("广告", "相关推荐")


@dataclass
class ImageVerdict:
    """Keep or drop, with the reason and a rough usefulness score."""

    keep: bool
    score: int
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"keep": self.keep, "score": self.score, "reasons": list(self.reasons)}


def _fragment_hits(haystack: str, needles: Iterable[str]) -> list[str]:
    value = str(haystack or "").lower()
    return [needle for needle in needles if needle in value]


def judge_image(
    *,
    width: int | None = None,
    height: int | None = None,
    url: str = "",
    alt: str = "",
    dom_class: str = "",
    dom_tag: str = "",
    sha256: str = "",
    seen_sha: Iterable[str] = (),
    phash: str = "",
    seen_phash: Iterable[str] = (),
    threshold: int = SIMILARITY_THRESHOLD,
    min_width: int = MIN_WIDTH,
    min_height: int = MIN_HEIGHT,
) -> ImageVerdict:
    """Decide whether one image is worth keeping, and say why."""
    reasons: list[str] = []
    score = 50
    if width is not None and height is not None:
        if width < min_width or height < min_height:
            return ImageVerdict(False, 0, [f"too small ({width}x{height})"])
        ratio = (width / height) if height else 0.0
        if ratio < MIN_ASPECT or ratio > MAX_ASPECT:
            return ImageVerdict(False, 0, [f"aspect ratio {ratio:.2f}"])
        # a big picture in the article body is the most likely to be useful
        score += min(30, int(width * height / 200000))
    region = f"{dom_tag} {dom_class}".lower()
    hits = _fragment_hits(region, CHROME_REGIONS)
    if hits:
        return ImageVerdict(False, 0, [f"chrome region ({hits[0]})"])
    url_hits = _fragment_hits(f"{url} {alt}", URL_HINTS)
    if url_hits:
        return ImageVerdict(False, 0, [f"url pattern ({url_hits[0]})"])
    ad_hits = [hint for hint in AD_ALT_HINTS if hint in str(alt or "")]
    if ad_hits:
        return ImageVerdict(False, 0, [f"advertisement ({ad_hits[0]})"])
    if sha256 and sha256 in set(seen_sha):
        return ImageVerdict(False, 0, ["duplicate sha256"])
    if phash:
        for other in seen_phash:
            if other and hamming(phash, str(other)) <= threshold:
                return ImageVerdict(False, 0, [f"visual duplicate (pHash <= {threshold})"])
    score += 10 if alt else 0
    return ImageVerdict(True, min(100, score), reasons or ["kept"])


def filter_images(images: Iterable[dict[str, Any]], *, threshold: int = SIMILARITY_THRESHOLD) -> dict[str, Any]:
    """Apply the rules to a page's images, deduplicating across the page."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    seen_sha: list[str] = []
    seen_phash: list[str] = []
    for image in images:
        verdict = judge_image(
            width=image.get("width"),
            height=image.get("height"),
            url=str(image.get("url") or image.get("src") or ""),
            alt=str(image.get("alt") or ""),
            dom_class=str(image.get("dom_class") or image.get("class") or ""),
            dom_tag=str(image.get("dom_tag") or ""),
            sha256=str(image.get("sha256") or ""),
            seen_sha=seen_sha,
            phash=str(image.get("phash") or ""),
            seen_phash=seen_phash,
            threshold=threshold,
        )
        record = {**image, "relevance": verdict.as_dict()}
        if verdict.keep:
            kept.append(record)
            if image.get("sha256"):
                seen_sha.append(str(image["sha256"]))
            if image.get("phash"):
                seen_phash.append(str(image["phash"]))
        else:
            dropped.append(record)
    return {
        "kept": kept,
        "dropped": dropped,
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "reasons": sorted({reason for item in dropped for reason in item["relevance"]["reasons"]}),
    }
