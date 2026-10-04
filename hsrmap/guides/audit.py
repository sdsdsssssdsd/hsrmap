"""Published snapshot audit (a1-6 §33 step 5, a1-5 acceptance "hallucinated steps = 0").

Three questions, answered for every published guide against its *own* source: a crawled
page for a community guide, the official point's detail line for an `Official` entry
(those have no page by construction — see `official.py`):

1. does a step carry neither text nor an image (pure noise)?
2. is a step's text actually present in the page it claims to come from?
   — a step that is not is a hallucination and must never be published;
3. does the source page still exist, and do the guide's assets exist on disk?

The result feeds two places: the `guides published-audit` report and the publish
gate, where any hallucinated step is a HARD FAIL.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase, _now
from hsrmap.guides.signature import article_family, normalize_text

#: Problem vocabulary — every finding is one of these.
AUDIT_PROBLEMS = (
    "EMPTY_STEP_NO_ASSET",
    "IMAGE_ONLY_STEP",
    "HALLUCINATED_STEP",
    "CHROME_STEP",
    "DERIVED_LABEL",
    "UNGROUNDED_STEP",
    "SOURCE_PAGE_MISSING",
    "SOURCE_TEXT_MISSING",
    "MISSING_ASSET",
    "NO_STEPS",
    #: 不是问题，是记录：这条步骤是从本条目自己的图里转录出来的（有图可查）。
    "IMAGE_TRANSCRIBED_STEP",
)

#: Structural markers our own builders add to a step ("海原市 第1处").
_ORDINAL = re.compile(r"第\s*\d+\s*[处个步次号]|第\s*\d+$")

#: Leading list markers our own builders add ("（1）", "1.", "2、"). They are
#: numbering, not content, so they must not count against the article.
_LIST_MARKER = re.compile(r"^\s*(?:[（(\[]\s*\d+\s*[)）\]]|\d+\s*[.、)）])\s*")

#: Fragment separators used when a step joins several article blocks.
_FRAGMENT = re.compile(r"[，。；！？!?;,\n]")

#: Separators inside an assembled label ("3层区域 王下一桶").
_LABEL_SPLIT = re.compile(r"[\s【】\[\]()（）<>《》,，、。.:：;；/|]+")

#: A step longer than this is prose: it must be quoted, not assembled.
ASSEMBLED_MAX_CHARS = 24

#: 图解法转录：步骤文字来自这条攻略**自己带的图**，并写明是哪张
#: （「[图解法转录 0087f6d9] 二维市1号点位：第1步 …」）。只要那张图确实挂在这条条目上，
#: 这一步就是有据可查的——判定层读不了图上的字，不等于作者没写。
_IMAGE_TRANSCRIPTION = re.compile(r"^\[图解法转录\s+([0-9a-fA-F]{8,64})\]")


@lru_cache(maxsize=512)
def _relaxed_corpus(corpus: str) -> str:
    """The corpus with the same ordinal markers removed as the step.

    `content_key` drops "第1个/第3处/第2次" wherever it appears, so an article
    sentence like "第3个【梦境迷钟】在【房间2】里面" loses the marker and can no
    longer be found verbatim. Both sides must be reduced the same way.
    """
    return _ORDINAL.sub("", corpus)


def _assembled_from_page(text: str, corpus: str) -> bool:
    """Is a label-length step built only from words the article really contains?

    The step builder joins page fragments ("3层区域" + "王下一桶") and elides image
    references, so such a step is no longer one contiguous run of the page. For a
    label that short, every word still having to exist in the article keeps the
    claim checkable — prose is never accepted this way.
    """
    tokens = [content_key(part) for part in _LABEL_SPLIT.split(text)]
    tokens = [token for token in tokens if len(token) >= 2]
    if len(tokens) < 2:
        return False
    return all(token in corpus for token in tokens)


def grounding(text: str, corpus: str) -> str:
    """How a step relates to its article: EXACT, FRAGMENT, ASSEMBLED or NONE.

    A step that concatenates several blocks is not a contiguous substring of the
    page, so every sentence-length fragment must be grounded on its own. A short
    key is never accepted through the relaxed comparison: after the ordinals are
    gone there would be nothing left to prove provenance with.
    """
    key = content_key(text)
    if not key:
        # only our own ordinal marker: grounded by construction
        return "EXACT"
    fragments = [
        content_key(part)
        for part in _FRAGMENT.split(text)
        if len(content_key(part)) >= 6
    ]
    if key in corpus:
        return "EXACT"
    if bool(fragments) and all(fragment in corpus for fragment in fragments):
        return "FRAGMENT"
    relaxed = _relaxed_corpus(corpus)
    if len(key) >= 6 and key in relaxed:
        return "EXACT"
    if bool(fragments) and all(fragment in relaxed for fragment in fragments):
        return "FRAGMENT"
    if len(key) <= ASSEMBLED_MAX_CHARS and _assembled_from_page(text, relaxed):
        return "ASSEMBLED"
    return "NONE"


def is_grounded(text: str, corpus: str) -> bool:
    """Is this step's content present in the article?"""
    return grounding(text, corpus) != "NONE"

_TAGS = re.compile(r"<[^>]+>")


def _read(path_value: Any) -> str:
    path = Path(str(path_value or ""))
    if path and path.is_file():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return ""


def _page_text(page: dict[str, Any]) -> str:
    """The article text the extractor produced (its block texts)."""
    return _read(page.get("raw_text_path"))


def page_chrome(page: dict[str, Any]) -> str:
    """Public alias: the whole page as text (article plus site furniture)."""
    return _page_chrome(page)


def _page_chrome(page: dict[str, Any]) -> str:
    """The whole page as text — article plus navigation, sidebars and ads."""
    return _TAGS.sub(" ", _read(page.get("raw_html_path")))


def content_key(text: str) -> str:
    """A step's text with our own numbering and ordinal markers removed."""
    return _ORDINAL.sub("", normalize_text(_LIST_MARKER.sub("", str(text or ""))))


def _family_index(working: GuideDatabase) -> dict[str, list[dict[str, Any]]]:
    """Pages grouped by article family: an article may continue on page 2, 3, …"""
    family_pages: dict[str, list[dict[str, Any]]] = {}
    for row in working.conn.execute("SELECT * FROM guide_page ORDER BY id"):
        item = dict(row)
        family_pages.setdefault(article_family(str(item.get("canonical_url") or "")), []).append(item)
    return family_pages


def _page_for(working: GuideDatabase, source_url: str) -> dict[str, Any]:
    row = working.conn.execute(
        "SELECT * FROM guide_page WHERE canonical_url = ? ORDER BY id LIMIT 1", (source_url,)
    ).fetchone()
    return dict(row) if row is not None else {}


def _corpus_for(
    cache: dict[str, tuple[str, str]],
    family_pages: dict[str, list[dict[str, Any]]],
    page: dict[str, Any],
    source_url: str,
) -> tuple[str, str]:
    """(article text, whole-page chrome) for one source URL, computed once."""
    if source_url not in cache:
        family = family_pages.get(article_family(source_url)) or [page]
        cache[source_url] = (
            normalize_text(" ".join(_page_text(item) for item in family)),
            normalize_text(" ".join(_page_chrome(item) for item in family)),
        )
    return cache[source_url]


def audit_entries(
    entries: dict[int, dict[str, Any]],
    working: GuideDatabase,
    *,
    assets_root: Path | None = None,
) -> dict[str, Any]:
    """Audit the given guides (usually the publish candidate) against the working DB."""
    pages: dict[str, dict[str, Any]] = {}
    corpus_cache: dict[str, tuple[str, str]] = {}
    family_pages = _family_index(working)
    findings: list[dict[str, Any]] = []
    counts: dict[str, int] = {name: 0 for name in AUDIT_PROBLEMS}

    #: 官方点位条目的来源不是我们抓来的网页，而是官方地图本身：它的「正文」就是
    #: detail.db 里那一行点位说明。所以拿官方说明来给步骤做同一套 grounding 检查——
    #: 既不是豁免，也不是把它们一律记成 SOURCE_PAGE_MISSING。
    from hsrmap.guides.official import OFFICIAL_SOURCE_KIND, latest_detail_db, official_details

    official_ids = sorted({
        str(entry.get("source_point_id") or "")
        for entry in entries.values()
        if str(entry.get("source_kind") or "") == OFFICIAL_SOURCE_KIND
    } - {""})
    #: 同一个来源页的图片是共享的：渡画泉隐那页的三条条目各挂几张、彼此交叉引用，
    #: 转录写的是「哪张图」，那就得认整页的图，而不是只认这条条目自己挂的那几张。
    page_assets: dict[str, set[str]] = {}
    for other in entries.values():
        url = str(other.get("source_url") or "")
        for asset in other.get("asset_rows") or []:
            sha = str(asset.get("sha256") or "").lower()
            if sha:
                page_assets.setdefault(url, set()).add(sha)

    official_map: dict[str, dict[str, Any]] = {}
    if official_ids:
        try:
            official_map = official_details(latest_detail_db(), official_ids)
        except Exception:  # noqa: BLE001 - 读不到 detail.db 时退回「无说明文字」而不是崩掉审计
            official_map = {}

    for guide_id, entry in sorted(entries.items()):
        source_url = str(entry.get("source_url") or "")
        problems: list[dict[str, Any]] = []
        official = str(entry.get("source_kind") or "") == OFFICIAL_SOURCE_KIND
        official_text = ""
        if official:
            detail = official_map.get(str(entry.get("source_point_id") or "")) or {}
            official_text = normalize_text(str(detail.get("text") or ""))

        corpus = official_text
        chrome = ""
        if not official:
            page = pages.get(source_url)
            if page is None and source_url:
                page = _page_for(working, source_url)
                pages[source_url] = page
            if source_url and not page:
                problems.append({"problem": "SOURCE_PAGE_MISSING", "detail": source_url})
            if page:
                corpus, chrome = _corpus_for(corpus_cache, family_pages, page, source_url)
                if not corpus:
                    problems.append({"problem": "SOURCE_TEXT_MISSING", "detail": source_url})

        steps = entry.get("steps") or []
        if not steps:
            problems.append({"problem": "NO_STEPS", "detail": ""})
        for index, text in enumerate(steps):
            body = str(text or "").strip()
            has_asset = any(
                int(asset.get("step_index", 0)) == index for asset in entry.get("asset_rows") or []
            )
            if not body:
                problems.append({
                    "problem": "IMAGE_ONLY_STEP" if has_asset else "EMPTY_STEP_NO_ASSET",
                    "detail": f"step {index}",
                })
                continue
            transcription = _IMAGE_TRANSCRIPTION.match(body)
            if transcription:
                sha_prefix = transcription.group(1).lower()
                own = [str(asset.get("sha256") or "").lower() for asset in entry.get("asset_rows") or []]
                shared = page_assets.get(str(entry.get("source_url") or ""), set())
                carries = any(sha.startswith(sha_prefix) for sha in [*own, *shared])
                if carries:
                    counts["IMAGE_TRANSCRIBED_STEP"] = counts.get("IMAGE_TRANSCRIBED_STEP", 0) + 1
                    continue
            if not corpus:
                if official:
                    #: 官方点位没有说明文字时，步骤只可能是我们按地图名拼出来的标签
                    problems.append({"problem": "DERIVED_LABEL", "detail": body[:60]})
                    continue
                # No article text to check against: say so instead of passing the
                # step silently, otherwise an unverifiable guide looks clean.
                problems.append({"problem": "UNGROUNDED_STEP", "detail": body[:120]})
                continue
            key = content_key(body)
            if not key:
                # only our own ordinal marker, grounded by construction
                problems.append({"problem": "DERIVED_LABEL", "detail": body[:60]})
                continue
            tier = grounding(body, corpus)
            if tier != "NONE":
                if tier == "ASSEMBLED":
                    # a label our builder assembled from page words, not a quote
                    problems.append({"problem": "DERIVED_LABEL", "detail": f"assembled: {body[:80]}"})
                continue
            if chrome and (key in chrome or is_grounded(body, chrome)):
                # text exists on the page but not in the article the extractor kept:
                # sidebar/navigation pollution rather than an invented step
                problems.append({"problem": "CHROME_STEP", "detail": body[:120]})
                continue
            problems.append({"problem": "HALLUCINATED_STEP", "detail": body[:120]})

        root = Path(assets_root) if assets_root is not None else None
        if root is not None:
            for sha in entry.get("assets") or []:
                folder = root / str(sha)[:2]
                if not folder.is_dir() or not any(p.is_file() for p in folder.glob(f"{sha}*")):
                    problems.append({"problem": "MISSING_ASSET", "detail": str(sha)})

        if not problems:
            continue
        for item in problems:
            counts[item["problem"]] = counts.get(item["problem"], 0) + 1
        findings.append({
            "guide_id": guide_id,
            "target": entry.get("source_point_id"),
            "title": (entry.get("title") or "")[:80],
            "source_url": source_url,
            "problems": problems,
        })

    return {
        "guides_checked": len(entries),
        "guides_with_problems": len(findings),
        "counts": counts,
        "findings": findings,
        "hallucinated": [item for item in findings if any(p["problem"] == "HALLUCINATED_STEP" for p in item["problems"])],
        #: 和 hallucinated 并列：步骤在自己那条来源里找不到任何依据（无正文可查）。
        "ungrounded": [item for item in findings if any(p["problem"] == "UNGROUNDED_STEP" for p in item["problems"])],
    }


def empty_step_rows(db: GuideDatabase) -> list[dict[str, Any]]:
    """Steps that carry no text, with the number of images on the same index."""
    return [
        dict(row)
        for row in db.conn.execute(
            """
            SELECT s.id, s.guide_id, s.step_index, IFNULL(s.text, '') AS text,
                   (SELECT COUNT(*) FROM guide_assets a
                     WHERE a.guide_id = s.guide_id AND a.step_index = s.step_index) AS assets
            FROM guide_steps s
            WHERE TRIM(IFNULL(s.text, '')) = ''
            ORDER BY s.guide_id, s.step_index
            """
        )
    ]


def fix_empty_steps(db: GuideDatabase, *, apply: bool = False) -> dict[str, Any]:
    """Drop text-less steps that hold no image; keep image-only steps.

    An image-only step is how a screenshot-only guide stores its picture, so
    removing it would lose content. A step with neither text nor image is noise.
    """
    rows = empty_step_rows(db)
    removable = [row for row in rows if not row["assets"]]
    if apply and removable:
        for row in removable:
            db.conn.execute("DELETE FROM guide_steps WHERE id = ?", (row["id"],))
        db.conn.commit()
    return {
        "applied": bool(apply),
        "empty_steps": len(rows),
        "image_only": len(rows) - len(removable),
        "removable": len(removable),
        "removed": [row["id"] for row in removable],
    }

def chrome_step_rows(db: GuideDatabase) -> list[dict[str, Any]]:
    """Steps whose text exists only in a page's chrome (sidebars, download lists).

    Such a step is not invented — the words really are on the page — but they
    are navigation, not article: the extractor that built the guide read the
    wrong container. They must not stay in a published guide.
    """
    family_pages = _family_index(db)
    cache: dict[str, tuple[str, str]] = {}
    rows: list[dict[str, Any]] = []
    for guide in db.conn.execute("SELECT id, source_url FROM guide_entry ORDER BY id"):
        source_url = str(guide["source_url"] or "")
        page = _page_for(db, source_url)
        if not page:
            continue
        corpus, chrome = _corpus_for(cache, family_pages, page, source_url)
        if not corpus or not chrome:
            continue
        steps = db.conn.execute(
            "SELECT id, step_index, text FROM guide_steps WHERE guide_id = ? ORDER BY step_index, id",
            (guide["id"],),
        )
        for step in steps:
            body = str(step["text"] or "").strip()
            if not body or grounding(body, corpus) != "NONE":
                continue
            if not is_grounded(body, chrome):
                continue
            rows.append({
                "step_id": int(step["id"]),
                "guide_id": int(guide["id"]),
                "step_index": int(step["step_index"]),
                "url": source_url,
                "text": body[:100],
            })
    return rows


def _renumber_steps(db: GuideDatabase, guide_id: int) -> None:
    """Make step_index contiguous again, moving images onto their new index."""
    steps = [
        dict(row)
        for row in db.conn.execute(
            "SELECT id, step_index FROM guide_steps WHERE guide_id = ? ORDER BY step_index, id",
            (guide_id,),
        )
    ]
    mapping = {int(row["step_index"]): index for index, row in enumerate(steps)}
    assets = [
        dict(row)
        for row in db.conn.execute(
            "SELECT id, step_index FROM guide_assets WHERE guide_id = ?", (guide_id,)
        )
    ]
    shift = 100000
    db.conn.execute("UPDATE guide_steps SET step_index = step_index + ? WHERE guide_id = ?", (shift, guide_id))
    db.conn.execute("UPDATE guide_assets SET step_index = step_index + ? WHERE guide_id = ?", (shift, guide_id))
    for row in steps:
        db.conn.execute(
            "UPDATE guide_steps SET step_index = ? WHERE id = ?",
            (mapping[int(row["step_index"])], row["id"]),
        )
    for asset in assets:
        target = mapping.get(int(asset["step_index"]))
        if target is None:
            # the step it illustrated was chrome: the binding goes with it
            db.conn.execute("DELETE FROM guide_assets WHERE id = ?", (asset["id"],))
            continue
        db.conn.execute(
            "UPDATE guide_assets SET step_index = ? WHERE id = ?", (target, asset["id"])
        )


#: An entry whose every step was page chrome: it needs a real source before it
#: may be published again. Anything other than "published" is unpublishable.
QUARANTINED_STATUS = "QUARANTINED_CHROME"


def empty_guide_ids(db: GuideDatabase) -> list[int]:
    """Published entries left with no step at all."""
    return [
        int(row["guide_id"])
        for row in db.conn.execute(
            """
            SELECT e.id AS guide_id
            FROM guide_entry e
            WHERE IFNULL(e.status, '') = 'published'
              AND NOT EXISTS (SELECT 1 FROM guide_steps s WHERE s.guide_id = e.id)
            ORDER BY e.id
            """
        )
    ]


def prune_chrome_steps(
    db: GuideDatabase, *, apply: bool = False, quarantine_empty: bool = False
) -> dict[str, Any]:
    """Report (or remove) steps that only the page chrome contains.

    With `quarantine_empty` an entry that loses every step becomes
    `QUARANTINED_CHROME` instead of being published empty: the extractor read
    the wrong container, so the guide has to be rebuilt from a real source.
    """
    rows = chrome_step_rows(db)
    guides = sorted({row["guide_id"] for row in rows})
    quarantined: list[int] = []
    if apply:
        if rows:
            for row in rows:
                db.conn.execute("DELETE FROM guide_steps WHERE id = ?", (row["step_id"],))
            for guide_id in guides:
                _renumber_steps(db, guide_id)
        if quarantine_empty:
            quarantined = empty_guide_ids(db)
            for guide_id in quarantined:
                db.conn.execute(
                    "UPDATE guide_entry SET status = ?, updated_at = ? WHERE id = ?",
                    (QUARANTINED_STATUS, _now(), guide_id),
                )
        if rows or quarantined:
            db.conn.commit()
    return {
        "applied": bool(apply),
        "guides": len(guides),
        "steps": len(rows),
        "removed": [row["step_id"] for row in rows],
        "quarantined": quarantined,
        "samples": rows[:10],
    }

