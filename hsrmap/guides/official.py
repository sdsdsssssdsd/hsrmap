"""官方点位详情：位置型主题最完整的攻略。

社区攻略对 3D 隐藏点（无名尘灵、若虫）和点对点挑战只能给出「该地图共 N 个」这类范围证据，
但官方地图里每个点位都自带一行位置说明和一张官方截图——「位于门旁的凳子上。」+ 图，那正是
玩家需要的东西。这个模块把官方点位详情做成正常的 guide entry：source_kind=Official、一张官方
截图、一行官方说明，与社区攻略走同一条发布与审计通道（不是绕过门槛，而是多一种证据来源）。

二次元 JUMP 是例外：它的官方详情状态是 EMPTY 且没有截图，所以那条路仍然只能等范围证据。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.assets.cache import AssetCache
from hsrmap.guides.assets.fetcher import CACHE_HIT, FETCHED, AssetFetcher
from hsrmap.guides.store import RawGuideStore
from hsrmap.guides.topics.loader import get_topic
from hsrmap.guides.topics.official import official_points_for_topic
from hsrmap.paths import DATA, GUIDE_ASSETS, GUIDE_CACHE, GUIDE_RAW

OFFICIAL_SOURCE_NAME = "HoYoLAB 官方地图"
OFFICIAL_SOURCE_KIND = "Official"
OFFICIAL_TITLE_SUFFIX = "官方点位"

#: 没有官方截图时，条目指向官方互动地图本身（registry 里记的就是这个地址）。
OFFICIAL_MAP_URL = "https://act.hoyolab.com/sr/app/interactive-map/index.html?lang=zh-cn"

#: Official detail states that carry something a reader can use.
USABLE_STATES = ("NONEMPTY",)


def latest_detail_db(root: Path | str | None = None) -> Path | None:
    """The newest enrichment detail database, or None when none was built."""
    base = Path(root or (DATA / "enrichments"))
    if not base.is_dir():
        return None
    found = [folder / "detail.db" for folder in base.iterdir() if (folder / "detail.db").exists()]
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def official_details(detail_db: Path | str | None, point_ids: list[str]) -> dict[str, dict[str, Any]]:
    """{point_id: {"text": ..., "assets": [{"sha256", "url"}]}} for the named points."""
    out: dict[str, dict[str, Any]] = {}
    wanted = [str(pid) for pid in point_ids if str(pid)]
    if not detail_db or not wanted or not Path(detail_db).exists():
        return out
    con = sqlite3.connect(Path(detail_db))
    con.row_factory = sqlite3.Row
    try:
        for start in range(0, len(wanted), 400):
            chunk = wanted[start : start + 400]
            marks = ",".join("?" * len(chunk))
            rows = con.execute(
                "SELECT id, source_point_id, plain_text, detail_state FROM point_details"
                f" WHERE source_point_id IN ({marks})",
                chunk,
            ).fetchall()
            for row in rows:
                assets = [
                    {
                        "sha256": str(asset["asset_sha256"] or ""),
                        "url": str(asset["remote_url"] or ""),
                        "state": str(asset["state"] or ""),
                    }
                    for asset in con.execute(
                        "SELECT asset_sha256, remote_url, state FROM point_detail_assets"
                        " WHERE detail_id = ? ORDER BY sort_order, id",
                        (row["id"],),
                    )
                ]
                out[str(row["source_point_id"])] = {
                    "text": str(row["plain_text"] or "").strip(),
                    "state": str(row["detail_state"] or ""),
                    "assets": assets,
                }
    finally:
        con.close()
    return out


def _needy_point_ids(
    db: GuideDatabase,
    topic: str,
    points: list[dict[str, Any]],
    *,
    all_points: bool = False,
) -> list[str]:
    """Points to seed: the ledger's gaps, or every point the completion model is not done with.

    `all_points=True` 以前用 `published_point_ids()` 判「已覆盖」，而那个函数会把 `set:` 键展开成
    成员点位——于是「被一条范围攻略提到过」就算覆盖，**这些点位自己的官方截图与说明再也不会被发出来**。
    2026-10 起改成问完成模型：只要这个点位还没有定位/解法证据（`missing_locate` / `missing_solve`），
    就该发它自己那一条。这也正是「下游从缺 LOCATE / 缺 SOLVE 派生」的最后一块。
    """
    if all_points:
        from hsrmap.guides.stages import completeness_report, is_done

        wanted = {str(point.get("source_point_id") or "") for point in points} - {""}
        if not wanted:
            return []
        try:
            report = completeness_report(db, topics=[topic])
        except Exception:  # noqa: BLE001 - 读不到完成模型时退回「按已发布条目判覆盖」
            from hsrmap.guides.ledger import published_point_ids

            covered = published_point_ids(db)
            return sorted(wanted - covered)
        return sorted(
            {
                str(row.get("point") or "")
                for row in report.get("rows") or []
                if str(row.get("point") or "") in wanted and not is_done(str(row.get("status") or ""))
            }
        )
    rows = [
        dict(row)
        for row in db.conn.execute(
            "SELECT target_key, source_point_id, map_id FROM guide_target_status"
            " WHERE topic_key = ? AND status IN ('NEEDS_SOURCE', 'NO_PUBLIC_SOURCE_FOUND')",
            (topic,),
        )
    ]
    want_points: set[str] = set()
    want_maps: set[str] = set()
    for row in rows:
        key = str(row.get("target_key") or "")
        if key.startswith("point:"):
            want_points.add(key.split(":", 1)[1])
        elif key.startswith("map:"):
            want_maps.add(str(row.get("map_id") or key.split(":", 1)[1]))
    if want_maps:
        for point in points:
            if str(point.get("map_id") or "") in want_maps:
                want_points.add(str(point.get("source_point_id") or ""))
    return sorted(pid for pid in want_points if pid)


def _ext(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix and len(suffix) <= 8 else ".png"


def _asset_sha_for_url(db: GuideDatabase, url: str) -> str:
    row = db.conn.execute(
        "SELECT sha256 FROM guide_asset_cache WHERE source_url = ? AND sha256 IS NOT NULL AND sha256 != ''",
        (url,),
    ).fetchone()
    return str(row["sha256"]) if row else ""


def ensure_official_asset(db: GuideDatabase, asset: dict[str, Any], *, fetcher: AssetFetcher | None = None) -> str:
    """Download (once) the official image and put it where published guides read it."""
    url = str(asset.get("url") or "")
    if not url:
        return ""
    store = RawGuideStore(GUIDE_RAW, GUIDE_ASSETS)
    known = _asset_sha_for_url(db, url)
    if known and list(store.assets_root.joinpath(known[:2]).glob(f"{known}*")):
        return known
    fetcher = fetcher or AssetFetcher(cache=AssetCache(root=GUIDE_CACHE, db=db))
    result = fetcher.fetch(url)
    if result.status not in {FETCHED, CACHE_HIT} or not result.body or not result.sha256:
        return ""
    digest = str(result.sha256)
    path = store.assets_root / digest[:2] / f"{digest}{_ext(url)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(result.body)
    AssetCache(root=GUIDE_CACHE, db=db).store(result)
    return digest

def seed_official_guides(
    db: GuideDatabase,
    topic: str,
    *,
    apply: bool = False,
    limit: int = 0,
    detail_db: Path | str | None = None,
    fetcher: AssetFetcher | None = None,
    points: list[dict[str, Any]] | None = None,
    all_points: bool = False,
) -> dict[str, Any]:
    """Publish one guide per needy point whose official detail carries a picture."""
    key = str(topic).replace("-", "_")
    spec = get_topic(key)
    display = str(spec.get("display_name") or key)
    points = list(points) if points is not None else (official_points_for_topic(key) or [])
    needy = _needy_point_ids(db, key, points, all_points=all_points)
    details = official_details(detail_db or latest_detail_db(), needy)
    by_id = {str(point.get("source_point_id") or ""): point for point in points}
    published: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for pid in needy:
        detail = details.get(pid) or {}
        text = str(detail.get("text") or "").strip()
        assets = [
            item
            for item in (detail.get("assets") or [])
            if item.get("sha256") and item.get("url") and str(item.get("state")) == "COMPLETE"
        ]
        if str(detail.get("state") or "") not in USABLE_STATES and not text:
            skipped.append({"point": pid, "reason": "OFFICIAL_DETAIL_EMPTY"})
            continue
        #: 官方说明本身就是 LOCATE 证据（「位于此处窗户上。」），有图更好、没图也发。
        #: 这类点位在官方地图上就是「一个标记 + 一行说明」，我们补不了图，但补得了那行说明。
        #: 例外：说明根本没写「在哪儿」（范围说明、设定文案）时不发文字条目——
        #: 「此处地图区域存在1个王下一桶」不是这个点的位置，发出来只会变成假证据。
        image = assets[0] if assets else None
        if image is None:
            from hsrmap.guides.stages import locating_text, official_action_text

            #: 说明没写「在哪儿」但写了「怎么做」（「击落空中气球获得。」）时同样发：这句话
            #: 正是把该点从「到点就行」上调成「要解法」的那句，官方地图上的标记负责「在哪儿」。
            if not locating_text(text) and not official_action_text(text):
                skipped.append({"point": pid, "reason": "OFFICIAL_TEXT_NOT_LOCATING"})
                continue
        if not apply:
            published.append({
                "point": pid,
                "sha": str(image["sha256"])[:12] if image else "",
                "text": text[:60],
                "text_only": image is None,
            })
            continue
        sha = ensure_official_asset(db, image, fetcher=fetcher) if image else ""
        if image and not sha:
            skipped.append({"point": pid, "reason": "ASSET_UNAVAILABLE"})
            continue
        point = by_id.get(pid) or {}
        entry = db.create_entry(
            {
                "source_point_id": pid,
                "title": f"{display}·{OFFICIAL_TITLE_SUFFIX}",
                "summary": text or f"{display} 官方地图点位截图",
                "source_name": OFFICIAL_SOURCE_NAME,
                "source_url": str(image["url"]) if image else OFFICIAL_MAP_URL,
                "source_kind": OFFICIAL_SOURCE_KIND,
                "status": "published",
                "steps": [
                    {
                        "text": text or ("官方地图点位：" + str(point.get("map_name") or "")),
                        "images": [sha] if sha else [],
                    }
                ],
            }
        )
        published.append({
            "point": pid,
            "entry_id": int(entry["id"]),
            "sha": sha,
            "text": text[:60],
            "text_only": not sha,
        })
    return {
        "topic": key,
        "applied": bool(apply),
        "scope": "all_missing" if all_points else "gaps",
        "needy": len(needy),
        "published": len(published),
        "text_only": sum(1 for item in published if item.get("text_only")),
        "skipped": len(skipped),
        "skipped_reasons": _reason_counts(skipped),
        "entries": published[: int(limit)] if limit else published,
        "skipped_detail": skipped[: int(limit)] if limit else skipped[:40],
    }


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("reason") or "")
        out[reason] = out.get(reason, 0) + 1
    return out

