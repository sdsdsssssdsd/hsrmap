"""Rebuild quarantined guides from the article text we really stored (a1-6 §30).

A guide that lost every step to the chrome-prune is not broken content — it was
built by an extractor that read the wrong container. Before publishing it again,
the rebuild pass asks one question: *does the stored article actually talk about
this target?* Only then are steps written, and every step is a verbatim sentence
of that article, which is what keeps `Hallucinated steps = 0` true by
construction rather than by luck.

Entries whose article says nothing about the target stay quarantined and are
reported with the reason, so the gap shows up as "needs a different source"
instead of as invented content.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from hsrmap.guide_db import GuideDatabase, _now
from hsrmap.guides.extract.blocks import html_to_blocks
from hsrmap.guides.signature import article_family, normalize_text

QUARANTINED_STATUS = "QUARANTINED_CHROME"

#: Hard caps so a rebuild cannot turn one sentence into a wall of text.
MAX_STEPS = 12
MAX_STEP_CHARS = 200
MAX_IMAGES = 4
#: A map-name list entry ("2，筑梦边境") is not an instruction, so a sentence
#: has to be this long before it counts as evidence that the article covers it.
MIN_SENTENCE_CHARS = 12

#: The topic name ("折纸小鸟") appears on every page about the topic, so it can
#: never be the only evidence: evidence needs two matched sentences, or one that
#: is clearly an instruction rather than a label.
MIN_EVIDENCE_STEPS = 2
MIN_EVIDENCE_CHARS = 24

#: The topic name ("折纸小鸟") appears on every page about the topic, so it can
#: never be the only evidence. Evidence needs two matched sentences, or one that
#: is clearly an instruction rather than a label.
MIN_EVIDENCE_STEPS = 2
MIN_EVIDENCE_CHARS = 24

_SENTENCE = re.compile(r"[。！？!?\n]+")

#: Numbered sub-instructions ("（1）将1蓝色模块点击旋转2次") that belong to a heading.
_INSTRUCTION_START = re.compile(r"^\s*(?:[（(]\s*\d+\s*[)）]|\d+\s*[.、)）]|第\s*\d+\s*步)")

#: Sentences that never become a step: page intros, sign-offs, site furniture.
BOILERPLATE = (
    "请看",
    "希望可以",
    "希望能够",
    "希望大家",
    "大家好",
    "本篇",
    "本期",
    "以上就是",
    "更多相关",
    "本文",
    "责任编辑",
)


def _family_text(pages: dict[str, list[dict[str, Any]]], source_url: str) -> str:
    """The stored article of the entry's family, first page first."""
    chunks: list[str] = []
    for page in pages.get(article_family(source_url or ""), []):
        path = page.get("raw_text_path")
        if path and Path(str(path)).is_file():
            chunks.append(Path(str(path)).read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def article_index(db: GuideDatabase) -> dict[str, list[dict[str, Any]]]:
    """Public alias: pages grouped by article family (shared with the approver)."""
    return _family_pages(db)


def article_text(pages: dict[str, list[dict[str, Any]]], source_url: str) -> str:
    """Public alias: the stored article of one family (shared with the approver)."""
    return _family_text(pages, source_url)


def _family_title(pages: dict[str, list[dict[str, Any]]], source_url: str) -> str:
    """The stored page title of the entry's family (the headline to skip)."""
    for page in pages.get(article_family(source_url or ""), []):
        if page.get("title"):
            return str(page["title"])
    return ""


def _family_pages(db: GuideDatabase) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in db.conn.execute("SELECT * FROM guide_page ORDER BY id"):
        item = dict(row)
        out.setdefault(article_family(str(item.get("canonical_url") or "")), []).append(item)
    return out


def target_keys(
    entry: dict[str, Any],
    *,

    display: str = "",
    points: Iterable[dict[str, Any]] = (),
    maps: Iterable[dict[str, Any]] = (),
) -> dict[str, list[str]]:
    """The names that would identify this entry's target inside an article.

    `strong` keys name the target itself (its map, region or point label);
    `weak` keys only prove the article is about the topic, so they can never
    carry evidence on their own.
    """
    target = str(entry.get("source_point_id") or "")
    topic = target.split(":topic:")[-1] if ":topic:" in target else ""
    point_id = target
    map_id = ""
    if target.startswith("map:"):
        map_id, point_id = target.split(":")[1], ""
    elif target.startswith(("set:",)):
        point_id = ""
    strong: list[str] = []
    if map_id:
        # a MAP_LABEL target is identified by its map and by nothing else: taking
        # every point of the topic would make all map targets look identical.
        for amap in maps:
            if str(amap.get("map_id") or "") != map_id:
                continue
            value = str(amap.get("name") or "").strip()
            if value and value not in strong:
                strong.append(value)
    elif point_id:
        for point in points:
            if str(point.get("source_point_id") or "") != point_id:
                continue
            for value in (point.get("map_name"), point.get("region"), point.get("label")):
                value = str(value or "").strip()
                if value and value not in strong:
                    strong.append(value)

    def usable(names: Iterable[str]) -> list[str]:
        out: list[str] = []
        for name in names:
            # a one-character map name ("1") is not evidence of anything
            if len(normalize_text(name)) < 2 or name in out:
                continue
            out.append(name)
        return out

    return {"strong": usable(strong), "weak": usable([display] if display else [])}


def matched_steps(text: str, keys: Iterable[str], *, limit: int = MAX_STEPS) -> list[str]:
    """Verbatim article sentences that name one of the keys, in document order."""
    wanted = [normalize_text(key) for key in keys if normalize_text(key)]
    if not wanted or not text:
        return []
    steps: list[str] = []
    for chunk in _SENTENCE.split(text):
        body = chunk.strip()
        if len(body) < MIN_SENTENCE_CHARS:
            continue
        norm = normalize_text(body)
        if not norm or not any(key in norm for key in wanted):
            continue
        if len(body) > MAX_STEP_CHARS:
            body = body[:MAX_STEP_CHARS].rstrip()
        if body in steps:
            continue
        steps.append(body)
        if len(steps) >= limit:
            break
    return steps


def article_steps(
    text: str, keys: Iterable[str], *, limit: int = MAX_STEPS, title: str = ""
) -> list[str]:
    """Heading-anchored steps: the matched line plus the instructions under it.

    A guide page is usually "第N个【目标】…" followed by numbered instructions that
    never repeat the target name. Matching single sentences would keep the heading
    and throw the actual solution away, so a unit is kept whole: the heading and
    the numbered lines that follow, up to the next heading.
    """
    wanted = [normalize_text(key) for key in keys if normalize_text(key)]
    if not wanted or not text:
        return []
    lines = [line.strip() for line in text.split("\n")]
    # the page headline is not a step, however often the page repeats it
    headline = normalize_text(title)
    steps: list[str] = []
    index = 0
    while index < len(lines) and len(steps) < limit:
        line = lines[index]
        index += 1
        if not line:
            continue
        if headline and normalize_text(line) and normalize_text(line) in headline:
            continue
        if len(line) < MIN_SENTENCE_CHARS and not _INSTRUCTION_START.match(line):
            continue
        if any(marker in line for marker in BOILERPLATE):
            continue
        if not any(key in normalize_text(line) for key in wanted):
            continue
        steps.append(_clip(line))
        while index < len(lines) and len(steps) < limit:
            nxt = lines[index].strip()
            if not nxt:
                index += 1
                continue
            if any(marker in nxt for marker in BOILERPLATE):
                index += 1
                continue
            if _INSTRUCTION_START.match(nxt):
                steps.append(_clip(nxt))
                index += 1
                continue
            break
    return steps


def _clip(line: str) -> str:
    return line[:MAX_STEP_CHARS].rstrip() if len(line) > MAX_STEP_CHARS else line


def image_candidates(text: str, keys: Iterable[str], blocks: list[dict[str, Any]], *, limit: int = MAX_IMAGES) -> list[str]:
    """Images that sit next to a matched sentence (the picture of that step)."""
    wanted = [normalize_text(key) for key in keys if normalize_text(key)]
    hits = 0
    out: list[str] = []
    for block in blocks:
        body = str(block.get("text") or "")
        if body and wanted and any(key in normalize_text(body) for key in wanted):
            hits += 1
            continue
        src = str(block.get("src") or "")
        if block.get("type") == "image" and src and (hits or not wanted):
            if src not in out:
                out.append(src)
            if len(out) >= limit:
                break
    return out


def _strong_enough(steps: list[str]) -> bool:
    """Two matched sentences, or one that is long enough to be an instruction."""
    if len(steps) >= MIN_EVIDENCE_STEPS:
        return True
    return bool(steps) and len(normalize_text(steps[0])) >= MIN_EVIDENCE_CHARS


def rebuild_quarantined(
    db: GuideDatabase,
    *,
    store: Any = None,
    apply: bool = False,
    limit: int | None = None,
    points: dict[str, list[dict[str, Any]]] | None = None,
    maps: dict[str, list[dict[str, Any]]] | None = None,
    displays: dict[str, str] | None = None,
    point_topics: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Rebuild every quarantined entry the stored article can actually source."""
    pages = _family_pages(db)
    rows = [
        dict(row)
        for row in db.conn.execute(
            "SELECT * FROM guide_entry WHERE status = ? ORDER BY id", (QUARANTINED_STATUS,)
        )
    ]
    if limit:
        rows = rows[: int(limit)]
    rebuilt: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for entry in rows:
        target = str(entry.get("source_point_id") or "")
        if ":topic:" in target:
            topic = target.split(":topic:")[-1]
        else:
            # a plain point key does not name its topic: the caller's index knows,
            # and failing that the official points themselves do
            topic = str((point_topics or {}).get(target) or "")
            if not topic and not target.startswith(("map:", "set:", "global:")):
                for key, values in (points or {}).items():
                    if any(str(point.get("source_point_id") or "") == target for point in values):
                        topic = key
                        break
        display = str((displays or {}).get(topic) or "")
        key_sets = target_keys(
            entry,
            display=display,
            points=(points or {}).get(topic, []),
            maps=(maps or {}).get(topic, []),
        )
        # only target-specific names may carry evidence: the topic name alone
        # would "prove" that an overview page covers every one of its targets
        keys = key_sets["strong"]
        text = _family_text(pages, str(entry.get("source_url") or ""))
        steps = article_steps(text, keys, title=_family_title(pages, str(entry.get("source_url") or "")))
        if steps and not _strong_enough(steps):
            steps = []
        if not steps:
            if not keys:
                reason = "NO_TARGET_KEYS"
            elif len(normalize_text(text)) <= 60:
                reason = "NO_ARTICLE_TEXT"
            else:
                reason = "NO_ARTICLE_EVIDENCE"
            skipped.append({
                "guide_id": int(entry["id"]),
                "target": target,
                "topic": topic,
                "reason": reason,
                "keys": keys[:4],
            })
            continue
        record = {
            "guide_id": int(entry["id"]),
            "target": target,
            "topic": topic,
            "steps": len(steps),
            "keys": [key for key in keys if normalize_text(key) in normalize_text(text)][:4],
            "strong_keys": len(key_sets["strong"]),
        }
        if apply:
            images = _attached_images(db, pages, entry, keys, store)
            _replace_steps(db, int(entry["id"]), steps, images)
            db.conn.execute(
                "UPDATE guide_entry SET status = 'published', updated_at = ? WHERE id = ?",
                (_now(), int(entry["id"])),
            )
            db.conn.commit()
            record["images"] = len(images)
        rebuilt.append(record)
    return {
        "applied": bool(apply),
        "checked": len(rows),
        "rebuilt": len(rebuilt),
        "skipped": len(skipped),
        "entries": rebuilt,
        "unresolved": skipped,
    }


def _attached_images(db, pages, entry, keys, store) -> list[dict[str, Any]]:
    """Image URLs next to the matched text, resolved to a stored asset sha256."""
    family = pages.get(article_family(str(entry.get("source_url") or ""))) or []
    out: list[dict[str, Any]] = []
    for page in family:
        html_path = str(page.get("raw_html_path") or "")
        if not html_path or not Path(html_path).is_file():
            continue
        html = Path(html_path).read_text(encoding="utf-8", errors="replace")
        for src in image_candidates(_family_text(pages, str(entry.get("source_url") or "")), keys, html_to_blocks(html)):
            sha = _asset_sha(db, src, store)
            if sha and all(item["sha256"] != sha for item in out):
                out.append({"src": src, "sha256": sha})
        break
    return out


def _asset_sha(db: GuideDatabase, src: str, store: Any) -> str:
    """The sha256 of a cached image, copied into the guide asset store if needed."""
    from hsrmap.paths import GUIDE_ASSETS

    row = db.conn.execute(
        "SELECT sha256 FROM guide_asset_cache WHERE source_url = ? AND sha256 IS NOT NULL LIMIT 1", (src,)
    ).fetchone()
    if row is None or not row["sha256"]:
        return ""
    sha = str(row["sha256"])
    root = Path(GUIDE_ASSETS)
    folder = root / sha[:2]
    if folder.is_dir() and any(path.is_file() for path in folder.glob(f"{sha}*")):
        return sha
    from hsrmap.paths import GUIDE_CACHE

    cached = Path(GUIDE_CACHE) / "assets" / "sha256" / sha[:2]
    if not cached.is_dir():
        return ""
    for path in cached.glob(f"{sha}*"):
        if path.is_file():
            if store is not None:
                store.save_asset(path.read_bytes(), src)
                return sha
            return ""
    return ""


def _replace_steps(db: GuideDatabase, guide_id: int, steps: list[str], images: list[dict[str, Any]]) -> None:
    """Swap in the rebuilt steps and put the images on the last step they follow."""
    db.conn.execute("DELETE FROM guide_steps WHERE guide_id = ?", (guide_id,))
    db.conn.execute("DELETE FROM guide_assets WHERE guide_id = ?", (guide_id,))
    for index, text in enumerate(steps):
        db.conn.execute(
            "INSERT INTO guide_steps(guide_id, step_index, text) VALUES (?, ?, ?)",
            (guide_id, index, text),
        )
    target_index = max(0, len(steps) - 1)
    for image in images:
        db.conn.execute(
            "INSERT INTO guide_assets(guide_id, step_index, asset_sha256) VALUES (?, ?, ?)",
            (guide_id, target_index, image["sha256"]),
        )
