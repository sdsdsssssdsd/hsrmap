from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.layout.planner import plan_layout
from hsrmap.guides.layout.render import render_layout
from hsrmap.guides.matching.candidates import query_candidates
from hsrmap.guides.publish.publisher import publish_page
from hsrmap.guides.publishing.sync import copy_entry
from hsrmap.guides.review.canary import CANARY_POINT_IDS
from hsrmap.guides.ledger import published_point_ids


def _row(row) -> dict[str, Any]:
    item = dict(row)
    if item.get("draft_json"):
        item["draft"] = json.loads(item["draft_json"])
    else:
        item["draft"] = {}
    if item.get("evidence_json"):
        item["evidence"] = json.loads(item["evidence_json"])
    return item


def _observation_index(page_id: int, html_path: Path) -> dict[str, dict[str, Any]]:
    from hsrmap.paths import GUIDE_DERIVED, GUIDE_RAW

    candidates = []
    try:
        candidates.append(html_path.parents[3] / "derived" / str(page_id) / "image-roles.json")
    except IndexError:
        pass
    if GUIDE_RAW in html_path.parents:
        candidates.append(GUIDE_DERIVED / str(page_id) / "image-roles.json")
    payload = None
    for path in candidates:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            break
    index: dict[str, dict[str, Any]] = {}
    for obs in (payload or {}).get("observations") or []:
        name = (obs.get("resolved_map") or {}).get("map_name") or obs.get("map_name_raw") or ""
        rec = {**obs, "map_name": name}
        if obs.get("block_id"):
            index[str(obs["block_id"])] = rec
        if obs.get("sha256"):
            index[str(obs["sha256"])] = rec
    return index


def _file_stamp(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "-"
    return f"{int(stat.st_mtime)}:{stat.st_size}"


def _cached_page_images(db: GuideDatabase, page_id: int, fingerprint: str) -> list[dict[str, Any]] | None:
    """缓存命中就返回一份**拷贝**（调用方会往 img 里写 role，不能共享同一批 dict）。"""
    row = _image_cache_rows(db, [page_id]).get(int(page_id))
    if row is None or row[0] != fingerprint:
        return None
    return _decode_cached(row[1])


def _image_cache_rows(db: GuideDatabase, page_ids: list[int]) -> dict[int, tuple[str, str]]:
    """一次取回多页的缓存行（逐条查询会让 2551 条队列又变成 N+1）。"""
    ids = sorted({int(item) for item in page_ids})
    out: dict[int, tuple[str, str]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        placeholders = ",".join("?" * len(chunk))
        try:
            rows = db.conn.execute(
                "SELECT page_id, fingerprint, images_json FROM page_image_cache"
                f" WHERE page_id IN ({placeholders})",
                chunk,
            ).fetchall()
        except Exception:  # noqa: BLE001 - 老库还没迁移过这张表：当作没缓存
            return {}
        for row in rows:
            out[int(row["page_id"])] = (str(row["fingerprint"]), str(row["images_json"]))
    return out


def _decode_cached(images_json: str) -> list[dict[str, Any]] | None:
    try:
        payload = json.loads(images_json)
    except ValueError:
        return None
    return [dict(item) for item in payload if isinstance(item, dict)]


def _raw_html_paths(db: GuideDatabase, page_ids: list[int]) -> dict[int, str]:
    """一次取回多页的 raw_html_path（同上）。"""
    ids = sorted({int(item) for item in page_ids})
    out: dict[int, str] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        placeholders = ",".join("?" * len(chunk))
        for row in db.conn.execute(
            f"SELECT id, IFNULL(raw_html_path, '') AS raw_html_path FROM guide_page"
            f" WHERE id IN ({placeholders})",
            chunk,
        ):
            out[int(row["id"])] = str(row["raw_html_path"] or "")
    return out


def _store_page_images(db: GuideDatabase, page_id: int, fingerprint: str, images: list[dict[str, Any]]) -> None:
    from hsrmap.guide_db import DB_READ_ONLY, _now

    if getattr(db, "mode", None) == DB_READ_ONLY:
        #: 只读打开（viewer 的 published 库）不许写：算得出结果就返回，别偷偷建表。
        return
    try:
        db.conn.execute(
            "INSERT OR REPLACE INTO page_image_cache(page_id, fingerprint, images_json, cached_at)"
            " VALUES (?, ?, ?, ?)",
            (int(page_id), fingerprint, json.dumps(images, ensure_ascii=False), _now()),
        )
        db.conn.commit()
    except Exception:  # noqa: BLE001 - 缓存写失败不该影响审核队列
        pass


def page_images(
    db: GuideDatabase,
    page_id: int,
    *,
    raw_html_path: str | None = None,
    cache_row: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """一页的图片清单（带指纹缓存）。

    `raw_html_path` / `cache_row` 可由调用方批量预取：审核队列一次要 2551 页，
    逐页查库就是又一处 N+1。
    """
    if raw_html_path is None:
        row = db.conn.execute("SELECT raw_html_path FROM guide_page WHERE id = ?", (page_id,)).fetchone()
        raw_html_path = str(row["raw_html_path"] or "") if row is not None else ""
    if not raw_html_path:
        return []
    html_path = Path(raw_html_path)
    extracted = html_path.parent.parent / "extracted" / f"{html_path.stem}.json"
    if not extracted.exists():
        return []
    roles = html_path.parents[3] / "derived" / str(page_id) / "image-roles.json" if len(html_path.parents) > 3 else None
    fingerprint = f"{_file_stamp(extracted)}|{_file_stamp(html_path)}|{_file_stamp(roles) if roles else '-'}"
    if cache_row is not None and cache_row[0] == fingerprint:
        cached = _decode_cached(cache_row[1])
    else:
        cached = _cached_page_images(db, page_id, fingerprint)
    if cached is not None:
        return cached
    blocks = json.loads(extracted.read_text(encoding="utf-8"))
    observed = _observation_index(page_id, html_path)
    images = []
    seen: set[str] = set()
    for block in blocks:
        if block.get("type") != "image":
            continue
        sha = block.get("asset")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        hit = observed.get(str(block.get("id"))) or observed.get(sha) or {}
        images.append(
            {
                "sha": sha,
                "url": f"/guide-assets/{sha}",
                "id": block.get("id"),
                "alt": block.get("alt") or "",
                "src": block.get("src") or "",
                "role": hit.get("role") or block.get("role") or "",
                "map_name": hit.get("map_name") or block.get("map_name") or "",
            }
        )
    _store_page_images(db, page_id, fingerprint, images)
    return [dict(item) for item in images]


def image_groups(images: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    from hsrmap.guides.vision.roles import WASTE, prefilter

    groups: dict[str, list[dict[str, Any]]] = {}
    for img in images:
        role = str(img.get("role") or "")
        if role not in WASTE:
            cheap = prefilter(alt=img.get("alt") or "", src=img.get("src") or "")
            if cheap:
                role = cheap["role"]
                img["role"] = role
        if role in WASTE:
            key = "废图"
        else:
            key = img.get("map_name") or "未识别地区"
        groups.setdefault(key, []).append(img)
    return groups


def _with_images(db: GuideDatabase, item: dict[str, Any]) -> dict[str, Any]:
    images = page_images(db, int(item["page_id"]))
    item["page_images"] = images
    item["image_groups"] = image_groups(images)
    item["regions"] = [name for name in item["image_groups"] if name not in {"废图", "未识别地区"}]
    item["layout"] = plan_layout(item.get("draft") or {})
    item["layout_html"] = render_layout(item["layout"])
    return item


def create_item(db: GuideDatabase, body: dict[str, Any]) -> dict[str, Any]:
    page = body.get("page") or {}
    page_id = body.get("page_id")
    if page.get("canonical_url"):
        source = db.upsert_source({"name": page.get("author") or "review", "domain": "review.local"})
        stored = db.add_page(source["id"], page)
        page_id = stored["id"]
        page = {**page, **stored}
    if page_id is None:
        raise ValueError("page_id or page.canonical_url required")
    draft = body.get("draft") or {}
    from hsrmap.guides.signature import signature_for_draft

    signature = str(
        body.get("signature")
        or signature_for_draft(draft, page_url=str(page.get("canonical_url") or ""))
    )
    cur = db.conn.execute(
        """
        INSERT INTO review_item(page_id, reason, status, created_at, source_point_id, draft_json, evidence_json, signature)
        VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?)
        """,
        (
            page_id,
            body.get("reason") or "ingest",
            body.get("status") or "NEEDS_REVIEW",
            str(body.get("source_point_id") or draft.get("source_point_id") or ""),
            json.dumps(draft, ensure_ascii=False),
            json.dumps(draft.get("evidence") or body.get("evidence") or {}, ensure_ascii=False),
            signature,
        ),
    )
    db.conn.commit()
    return get_item(db, int(cur.lastrowid))


def list_items(db: GuideDatabase, *, with_layout: bool = True) -> list[dict[str, Any]]:
    """队列里的全部条目（图片清单与布局一次算好）。

    图片、缓存、页面路径都是**批量**取的：2551 条记录逐条查库 + 逐条读文件，
    会让审核台第一次打开要等 7 秒以上（实测）。
    """
    rows = db.conn.execute(
        """
        SELECT r.*, p.title AS page_title, p.author AS page_author, p.canonical_url AS page_url
        FROM review_item r
        LEFT JOIN guide_page p ON p.id = r.page_id
        ORDER BY r.id
        """
    ).fetchall()
    page_ids = [int(row["page_id"]) for row in rows if row["page_id"]]
    html_by_page = _raw_html_paths(db, page_ids)
    cache = _image_cache_rows(db, page_ids)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = _row(row)
        page_id = int(item["page_id"]) if item.get("page_id") else 0
        images = page_images(
            db,
            page_id,
            raw_html_path=html_by_page.get(page_id, ""),
            cache_row=cache.get(page_id),
        ) if page_id else []
        item["page_images"] = images
        item["image_groups"] = image_groups(images)
        item["regions"] = [name for name in item["image_groups"] if name not in {"废图", "未识别地区"}]
        if with_layout:
            item["layout"] = plan_layout(item.get("draft") or {})
            item["layout_html"] = render_layout(item["layout"])
        out.append(item)
    return out


_CHROME_TITLES = {
    "17173 新闻导语",
    "崩坏：星穹铁道",
    "崩坏星穹铁道",
}


def _is_chrome_title(name: str) -> bool:
    text = str(name or "").strip()
    if text in _CHROME_TITLES:
        return True
    if text.startswith("关于崩坏") or text.startswith("更多相关"):
        return True
    if text.startswith("《崩坏") or text.startswith("【崩坏"):
        return True
    return False


def _enrich_candidate(cand: Any, official_points: list[dict[str, Any]] | None) -> dict[str, Any]:
    row = dict(cand) if isinstance(cand, dict) else {"source_point_id": cand}
    pid = str(row.get("source_point_id") or "")
    for point in official_points or []:
        if str(point.get("source_point_id") or "") != pid:
            continue
        url = point.get("official_image_url") or point.get("image_url")
        if not url:
            images = point.get("images") or []
            if images and isinstance(images[0], dict):
                url = images[0].get("url")
        if url:
            row["official_image_url"] = url
        row["map_path"] = row.get("map_path") or point.get("map_path")
        row["map_name"] = row.get("map_name") or point.get("map_name") or point.get("region")
        break
    return row


def attach_official_thumbs(payload: dict[str, Any], ctx=None) -> dict[str, Any]:
    if ctx is None:
        return payload
    from hsrmap.guides.topics.official import official_image_url

    def enrich(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        out = []
        for row in rows or []:
            item = dict(row)
            pid = str(item.get("source_point_id") or "")
            if pid and not item.get("official_image_url"):
                try:
                    item["official_image_url"] = official_image_url(ctx, pid)
                except Exception:
                    item.setdefault("official_image_url", "")
            out.append(item)
        return out

    for item in payload.get("items") or []:
        draft = item.get("draft") or {}
        draft["candidate_points"] = enrich(draft.get("candidate_points") or [])
        item["draft"] = draft
    for amap in payload.get("maps") or []:
        amap["candidate_points"] = enrich(amap.get("candidate_points") or [])
    return payload


def collapse_by_map(
    items: list[dict[str, Any]],
    official_points: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    by_map: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups = item.get("image_groups") or {}
        recognized = False
        for name, images in groups.items():
            if name in {"废图", "未识别地区"} or not images:
                continue
            recognized = True
            by_map.setdefault(name, []).append({"item": item, "images": images})
        if not recognized:
            draft = item.get("draft") or {}
            name = str(draft.get("resolved_map_name") or "").split(" / ")[0].strip()
            if not name:
                name = str(draft.get("map_name") or item.get("page_title") or f"page {item.get('page_id')}")
            if _is_chrome_title(name):
                continue
            images = list(groups.get("未识别地区") or [])
            by_map.setdefault(name, []).append({"item": item, "images": images})
    maps = []
    for name, rows in by_map.items():
        pages: dict[int, dict[str, Any]] = {}
        for row in rows:
            page_id = int(row["item"]["page_id"])
            draft_name = ((row["item"].get("draft") or {}).get("map_name") or "")
            score = (len(row["images"]), 1 if draft_name == name else 0, -int(row["item"]["id"]))
            prev = pages.get(page_id)
            if prev is None or score > prev["score"]:
                pages[page_id] = {**row, "score": score, "page_id": page_id}
        ranked = sorted(pages.values(), key=lambda row: (-len(row["images"]), row["page_id"]))
        winner = ranked[0]
        hidden = ranked[1:]
        item = winner["item"]
        draft_cands = (item.get("draft") or {}).get("candidate_points") or []
        labels = _topic_label_names(item)
        cands = query_candidates(
            name.split(" / ")[0],
            official_points or [],
            semantic=None,
            label_names=labels,
        ) or draft_cands
        cands = [_enrich_candidate(row, official_points) for row in cands]
        maps.append(
            {
                "map_name": name,
                "page_id": winner["page_id"],
                "item_id": item["id"],
                "images": winner["images"],
                "hidden": len(hidden),
                "hidden_page_ids": [row["page_id"] for row in hidden],
                "status": item.get("status"),
                "page_title": item.get("page_title"),
                "source_point_id": item.get("source_point_id") or "",
                "draft": {**(item.get("draft") or {}), "candidate_points": cands or (item.get("draft") or {}).get("candidate_points") or []},
                "evidence": item.get("evidence") or item.get("draft", {}).get("evidence") or {},
                "layout": plan_layout(item.get("draft") or {}),
                "layout_html": render_layout(plan_layout(item.get("draft") or {})),
                "candidate_points": cands,
                "waste": list((item.get("image_groups") or {}).get("废图") or []),
                "slots": [
                    {
                        "source_point_id": row.get("source_point_id"),
                        "map_id": row.get("map_id"),
                        "canary": str(row.get("source_point_id")) in CANARY_POINT_IDS,
                    }
                    for row in cands
                ],
            }
        )
    maps.sort(key=lambda row: row["map_name"])
    return maps


def _topic_label_names(item: dict[str, Any]) -> list[str]:
    from hsrmap.guides.topics.loader import get_topic

    key = str((item.get("draft") or {}).get("topic_key") or "floating_grease")
    try:
        spec = get_topic(key)
    except KeyError:
        return ["浮脂溯源"]
    names = list((spec.get("official_labels") or {}).get("names") or [])
    if spec.get("display_name"):
        names.append(str(spec["display_name"]))
    return [name for name in names if name]


def _item_topic(item: dict[str, Any], db: GuideDatabase | None = None) -> str:
    draft = item.get("draft") or {}
    key = draft.get("topic_key") or draft.get("topic")
    if key:
        return str(key).replace("-", "_")
    if db is not None and item.get("page_id"):
        topics = db.topics_for_page(int(item["page_id"]))
        if topics:
            return str(topics[0]["topic_key"]).replace("-", "_")
    return "floating_grease"


def list_review_payload(
    db: GuideDatabase,
    official_points: list[dict[str, Any]] | None = None,
    topic: str | None = None,
    *,
    include_items: bool = False,
    slim: bool = False,
) -> dict[str, Any]:
    """审核队列列表。

    控制台渲染的是 `maps`（按地图折叠）。两个「别把整库塞进首屏」的开关：

    * `items` 是逐页原始记录——带上 `draft_json` / `image_groups` 之后 2551 条 = **127 MB**，
      列表页从来不看它；默认只在 `maps` 为空时一起返回（兼容老前端），
      要原始记录就显式 `include_items=True`（HTTP 是 `?include_items=1`）；
    * `slim` 把每一行里的 `draft` / `layout` / `layout_html` / `evidence` 和逐张图片数组
      也去掉（只留计数）：那些是**选中某一行**才需要的，由
      `review/maps/{item_id}` 单行取回。整包因此从 9.8 MB 降到 ~1 MB。
    """
    items = list_items(db, with_layout=not slim)
    if topic:
        key = topic.replace("-", "_")
        items = [item for item in items if _item_topic(item, db) == key]
    maps = collapse_by_map(items, official_points)
    if slim:
        maps = slim_map_rows(maps)
    payload: dict[str, Any] = {
        "maps": maps,
        "canary_total": 10,
        "topic": (topic or "").replace("-", "_") or None,
        "slim": bool(slim),
    }
    if include_items or not maps:
        payload["items"] = items
    else:
        payload["items"] = []
        #: 如实说「没带」和「一条都没有」的区别，免得前端把「省略」当成「空队列」。
        payload["items_omitted"] = len(items)
    return payload


#: 只有「选中某一行」才需要的重字段（列表页把它们换成计数）。
_HEAVY_ROW_KEYS = ("draft", "layout", "layout_html", "evidence", "images", "waste")


def slim_map_rows(maps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把队列行压成「索引」：留名字、计数、候选点，去掉 draft / 布局 / 图片数组。

    完整的一行用 `map_row_for_item()` 单独取——列表页只负责让人找到那一行。
    """
    out: list[dict[str, Any]] = []
    for row in maps:
        slim = {key: value for key, value in row.items() if key not in _HEAVY_ROW_KEYS}
        draft = row.get("draft") or {}
        slim["image_count"] = len(row.get("images") or [])
        slim["waste_count"] = len(row.get("waste") or [])
        slim["slot_count"] = len(row.get("slots") or [])
        slim["draft_brief"] = {
            "topic_key": draft.get("topic_key"),
            "target_type": draft.get("target_type"),
            "target_key": draft.get("target_key"),
            "map_name": draft.get("map_name"),
            "resolved_map_name": draft.get("resolved_map_name"),
            "steps": len(draft.get("steps") or []),
        }
        out.append(slim)
    return out


def map_row_for_item(
    db: GuideDatabase,
    item_id: int,
    official_points: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """单行详情：把一条 review_item 折成列表页那一行的**完整**形状。

    列表页只带索引，选中时用这个补齐draft / 布局 / 图片数组。
    """
    item = get_item(db, int(item_id))
    rows = collapse_by_map([item], official_points)
    for row in rows:
        if int(row.get("item_id") or 0) == int(item_id):
            return row
    return rows[0] if rows else None


def get_item(db: GuideDatabase, item_id: int) -> dict[str, Any]:
    row = db.conn.execute("SELECT * FROM review_item WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    return _with_images(db, _row(row))


def patch_item(db: GuideDatabase, item_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    item = get_item(db, item_id)
    draft = item.get("draft") or {}
    if "draft" in patch and isinstance(patch["draft"], dict):
        draft.update(patch["draft"])
    point = patch.get("source_point_id", item.get("source_point_id"))
    status = patch.get("status", item.get("status"))
    method = str(patch.get("binding_method") or draft.get("binding_method") or "").strip()
    if method:
        draft["binding_method"] = method
    elif str(point or "") and str(point or "") != str(item.get("source_point_id") or ""):
        draft["binding_method"] = "HUMAN_REVIEW"
    db.conn.execute(
        "UPDATE review_item SET source_point_id=?, draft_json=?, status=? WHERE id=?",
        (str(point or ""), json.dumps(draft, ensure_ascii=False), status, item_id),
    )
    db.conn.commit()
    return get_item(db, item_id)


def approve_item(
    db: GuideDatabase,
    item_id: int,
    *,
    official_points: list[dict[str, Any]] | None = None,
    published_db: GuideDatabase | None = None,
) -> dict[str, Any]:
    item = get_item(db, item_id)
    draft = item.get("draft") or {}
    target_type = str(draft.get("target_type") or "").upper()
    if target_type in {"MAP_LABEL", "POINT_SET", "GLOBAL"}:
        _assert_approvable(item, official_points)
        target_key = str(draft.get("target_key") or "").strip()
        if not target_key:
            raise ValueError("target_key required")
        db.conn.execute("UPDATE review_item SET source_point_id=? WHERE id=?", (target_key, item_id))
        db.conn.commit()
        item = get_item(db, item_id)
    else:
        _assert_approvable(item, official_points)
    page_row = db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (item["page_id"],)).fetchone()
    page = dict(page_row) if page_row else {"id": item["page_id"], "title": (item.get("draft") or {}).get("map_name")}
    steps = (item.get("draft") or {}).get("steps") or []
    published = publish_page(
        db,
        page,
        [{"source_point_id": item["source_point_id"], "heading": page.get("title"), "confidence": 1.0, "status": "APPROVED"}],
        steps,
    )
    db.conn.execute("UPDATE review_item SET status='APPROVED' WHERE id=?", (item_id,))
    db.conn.commit()
    if published_db is not None:
        for entry in published or []:
            copy_entry(db, published_db, int(entry["id"]))
    out = get_item(db, item_id)
    out["entry"] = published[0] if published else None
    return out


def approve_anchored(
    db: GuideDatabase,
    *,
    topic: str = '',
    official_points: list[dict[str, Any]] | None = None,
    apply: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Approve the richest page-anchored draft per map target (operator step).

    A draft only qualifies when the page itself named its map (`PAGE_ANCHOR`),
    so the target comes from evidence rather than from a guess. One guide per
    target is approved — the one with the most steps — and its images are
    attached to the last step.
    """
    rows = [
        dict(row)
        for row in db.conn.execute(
            "SELECT * FROM review_item WHERE status IN ('NEEDS_REVIEW','AUTO_SUGGEST') ORDER BY id"
        )
    ]
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            draft = json.loads(row.get("draft_json") or "{}")
        except Exception:
            continue
        if topic and str(draft.get("topic_key") or "") != topic.replace("-", "_"):
            continue
        if str(draft.get("target_type") or "").upper() != "MAP_LABEL":
            continue
        target_key = str(draft.get("target_key") or "").strip()
        if not target_key:
            continue
        anchored = any(
            (image.get("resolved_map") or {}).get("status") == "PAGE_ANCHOR"
            for image in (draft.get("images") or [])
        )
        if not anchored:
            continue
        current = best.get(target_key)
        if current is None or len(draft.get("steps") or []) > len(current["draft"].get("steps") or []):
            best[target_key] = {"item": row, "draft": draft}
    chosen = list(best.values())
    if limit:
        chosen = chosen[: int(limit)]
    approved: list[dict[str, Any]] = []
    for entry in chosen:
        item = entry["item"]
        draft = dict(entry["draft"])
        shas = [str(image.get("sha256")) for image in (draft.get("images") or []) if image.get("sha256")]
        steps = [dict(step) for step in (draft.get("steps") or [])]
        if steps:
            steps[-1] = {**steps[-1], "images": shas}
        else:
            steps = [{"text": "", "images": shas}]
        record = {
            "item_id": int(item["id"]),
            "target_key": draft.get("target_key"),
            "map_name": draft.get("map_name"),
            "steps": len(steps),
            "images": len(shas),
        }
        if apply:
            draft["steps"] = steps
            draft["binding_method"] = "PAGE_ANCHOR"
            db.conn.execute(
                "UPDATE review_item SET draft_json = ? WHERE id = ?",
                (json.dumps(draft, ensure_ascii=False), int(item["id"])),
            )
            db.conn.commit()
            published = approve_item(db, int(item["id"]), official_points=official_points)
            record["entry_id"] = (published.get("entry") or {}).get("id")
        approved.append(record)
    return {
        "applied": bool(apply),
        "targets": len(best),
        "approved": approved,
    }


#: Step text that is site furniture rather than guide content: bylines, "latest
#: articles" strips, download prompts. A guide made only of these is not a guide.
CHROME_STEP_HINTS = (
    "来源：",
    "作者：",
    "编辑：",
    "责任编辑",
    "最新文章",
    "最新资讯",
    "相关攻略",
    "相关推荐",
    "更多",
    "点击查看",
    "扫描二维码",
    "下载",
    "关注",
    "责任编辑",
)

#: Page-navigation lines ("第1页：") are furniture, not steps.
_PAGE_LABEL = re.compile(r"^第\s*\d+\s*页\s*[:：]?$")

#: Route data the site embeds in the page and the text extractor cannot tell from
#: prose: 九游's articles carry their interactive map's edge list as lines
#: ("t2627_2_2627_1:5.0", "t52-t0:947.0"). The rows *are* in the article, so the
#: grounding check keeps them, but they instruct nobody — a step a reader can
#: follow never looks like "<symbol>-<symbol>:<number>".
_MACHINE_STEP = re.compile(r"^[A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+)*:[0-9]+(?:\.[0-9]+)?$")

#: A step shorter than this carries no instruction ("铁卫禁区", "最新").
MIN_SUBSTANTIVE_CHARS = 8

#: A guide whose whole text is one short line is a stub, not a guide.
MIN_STEP_CHARS_TOTAL = 30

#: Binding a whole region still has to *say* something: a page whose only line is
#: "共10只若虫" states its scope but walks nobody anywhere, so a set needs a body.
MIN_REGION_SET_STEPS = 3

#: Image roles that carry guide content. A collection post often renders its
#: walkthrough as screenshots and keeps only the scope line as text; the pictures
#: are the body then, and the relevance pass already labels which ones they are.
CONTENT_IMAGE_ROLES = frozenset(
    {"puzzle_step", "location_map", "map", "screenshot", "step", "gameplay", "guide"}
)

#: A picture smaller than this is a thumbnail or an icon, not a step. Phone
#: screenshots are commonly 560 wide, so the floor is well below that.
GALLERY_MIN_SIZE = (400, 300)


def gallery_images(
    db: GuideDatabase, page_id: int, html_path: Any, *, want: int
) -> list[str]:
    """Up to a number of content screenshots of a page, in page order."""
    if not page_id:
        return []
    try:
        index = _observation_index(int(page_id), Path(str(html_path or "")))
    except Exception:  # noqa: BLE001 - a missing derivation is not an error here
        return []
    out: list[str] = []
    for observation in index.values():
        sha = str(observation.get("sha256") or "")
        if not sha or sha in out:
            continue
        row = db.conn.execute(
            "SELECT width, height, final_url, source_url FROM guide_asset_cache WHERE sha256 = ?", (sha,)
        ).fetchone()
        if row is None:
            continue
        width, height = int(row["width"] or 0), int(row["height"] or 0)
        if width < GALLERY_MIN_SIZE[0] or height < GALLERY_MIN_SIZE[1]:
            continue
        role = str(observation.get("role") or "")
        if role not in CONTENT_IMAGE_ROLES:
            #: most pages were never classified by a model (role "unknown"): fall
            #: back to the rule-based relevance judge, which drops logos, icons,
            #: banners, QR codes and duplicates by URL and shape
            if role not in {"", "unknown"}:
                continue
            from hsrmap.guides.assets.relevance import judge_image

            verdict = judge_image(
                width=width,
                height=height,
                url=str(row["final_url"] or row["source_url"] or ""),
                sha256=sha,
            )
            if not verdict.keep:
                continue
        out.append(sha)
        if len(out) >= int(want):
            break
    return out


#: "一共3个" / "共有 6 个" / "共10只" / "共两个" — how many targets a region guide
#: covers. Pages rarely write the bare copula ("共10只若虫"); "共有"/"共为" is the
#: common phrasing, and collectables are counted in 只/处/座/根 as often as in 个.
_DECLARED_COUNT = re.compile(
    r"(?:一共|共|总计|合计)\s*(?:有|为|是)?\s*([0-9一二三四五六七八九十两]+)\s*[个只处张座根]"
)

#: "王下一桶(20星琼+120金表钞)*2" / "梦境迷钟 ×3" / "(2个)" — a list marker count.
_TIMES_COUNT = re.compile(r"[*×]\s*([0-9]+)|[（(]\s*(?:一共|共)?\s*([0-9]+)\s*[个只处张座根]\s*[)）]")

#: Segment boundaries: list markers, sentence ends, blank runs.
_SEGMENT = re.compile(r"[①-⑳]|[\n。；;]|\s{2,}")


#: "…，3个黄金替罪羊解谜" — a number written at the name itself, opening a list
#: entry (after a separator) so that it cannot be the previous entry's number
#: ("甲城中心城区2个，指针塔1个" says nothing about 指针塔's own count).
#: The ordinal form ("第一个黄金替罪羊") is a numbering, not a count, hence (?<!第).
_NEAR_COUNT = re.compile(
    r"(?:^|[，,。；;、：:和与及（(]\s*)(?<!第)"
    r"([0-9一二三四五六七八九十两]+)\s*[个只处张座根]\s*%s"
)

#: "『观览云岛站』共4处" — a count written right after the region it belongs to.
#: One sentence can state several regions' counts, which the segment rule alone
#: reads as "two numbers, no answer"; the anchor says which number is whose.
_ANCHORED_COUNT = re.compile(
    r"%s[^0-9一二三四五六七八九十两]{0,12}(?:一共|共|总计|合计)\s*(?:有|为|是)?\s*"
    r"([0-9一二三四五六七八九十两]+)\s*[个只处张座根]"
)

#: The same anchor without "共": "数一下渡画泉隐jump点位是否为9个" is a page stating
#: the area's count as a fact to check. Only the copulas are accepted — a bare "有"
#: would read any "「甲区」有3个宝箱" as a count of the topic's items.
_ANCHORED_STATE_COUNT = re.compile(
    r"%s[^0-9一二三四五六七八九十两]{0,12}(?:是否|应该)?\s*(?:为|是)\s*"
    r"([0-9一二三四五六七八九十两]+)\s*[个只处张座根]"
)

#: "每个区域各有3个谜题" — one number the page states for *several* regions at
#: once. The number is written once, so the anchor rule finds nothing and the
#: segment rule sees one number for two regions; the distributive wording is the
#: page saying it counts that many in each of the regions it names.
_DISTRIBUTIVE_COUNT = re.compile(
    r"每[^0-9一二三四五六七八九十两]{0,8}?各\s*(?:有|为|是)?\s*"
    r"([0-9一二三四五六七八九十两]+)\s*[个只处张座根]"
)


#: 「【稚子的梦】2个位置」「3.【稚子的梦】2处:」——区域名带括号、数字紧跟着它，
#: 中间没有「共」。括号是这条规则的安全带：它把数字绑定到被点名的区域身上，
#: 而页面的总数（「…中全部的【王下一桶】(共8个)」）说的是四个区域合起来。
#: 「【稚子的梦】2个位置」「3.【稚子的梦】2处:」——区域名带括号、数字紧跟着它，中间没有「共」。
#: 括号是这条规则的安全带：它把数字绑定到被点名的区域身上，而页面总数
#: （「…中全部的【王下一桶】(共8个)」）说的是四个区域合起来。
_BRACKET_OPEN = r"[【「『\[]"
_BRACKET_CLOSE = r"[】」』\]]"
#: 名字里可能夹着标点（官方写「白日梦」酒店-梦境，页面写【「白日梦」酒店-梦境】），
#: 所以逐字拼正则、字与字之间允许任意标点；但不能跨过数字。
_BETWEEN = r"[^0-9a-z\u4e00-\u9fff]*"


def _semi_normal(text: Any) -> str:
    """NFKC + casefold，但**保留标点**：括号是这条规则要看的信号。"""
    import unicodedata

    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def bracketed_count(text: str, label: str) -> int:
    """A count written right after the *bracketed* region name, or 0.

    「【稚子的梦】2个位置」是这一页在说这个区域有几个；页面的总数（「共8个」）是四个
    区域加起来的，不能拿来当这个区域的。只在名字带括号时成立，避免把「甲区有3个宝箱」
    这种别的主题的句子读进来。
    """
    from hsrmap.guides.signature import normalize_text

    name = normalize_text(str(label or ""))
    corpus = _semi_normal(text)
    if not name or not corpus:
        return 0
    body = _BETWEEN.join(re.escape(char) for char in name)
    pattern = re.compile(
        _BRACKET_OPEN + body + _BRACKET_CLOSE + "?" + _BETWEEN
        + r"(?:共|一共|总计|合计)?" + _BETWEEN + r"(?:有|为|是)?" + _BETWEEN
        + r"([0-9一二三四五六七八九十两]+)s*[个只处张座根]"
    )
    values = {_count_value(match.group(1)) for match in pattern.finditer(corpus)}
    values.discard(0)
    return values.pop() if len(values) == 1 else 0


def named_topic_count(text: str, region: str, labels: list[str]) -> int:
    """「晖长石号中有4个梦境迷钟」——区域名 + 有/共 + 数字 + 单位 + **主题名**。

    数字后面必须紧跟主题名：这就是它和「甲区有3个宝箱」的区别——后者说的是宝箱，
    不能拿来当这一区迷钟的数量。
    """
    from hsrmap.guides.signature import normalize_text

    name = normalize_text(str(region or ""))
    corpus = _semi_normal(text)
    names = [item for item in (normalize_text(label) for label in labels or []) if item]
    if not name or not corpus or not names:
        return 0
    body = _BETWEEN.join(re.escape(char) for char in name)
    values: set[int] = set()
    for label in names:
        pattern = re.compile(
            body + _BETWEEN + r"(?:共|一共|总计|合计|中)?" + _BETWEEN + r"(?:有|为|是)?" + _BETWEEN
            + r"([0-9一二三四五六七八九十两]+)\s*[个只处张座根]" + _BETWEEN + re.escape(label)
        )
        for match in pattern.finditer(corpus):
            value = _count_value(match.group(1))
            if value:
                values.add(value)
    return values.pop() if len(values) == 1 else 0


def anchored_count(text: str, label: str) -> int:
    """The count a page writes immediately after a region name, or 0.

    Compared on normalized text: a page writes 『甲区』一处 where the official
    region string is 「甲区」一处, and the brackets are not the evidence.
    """
    from hsrmap.guides.signature import normalize_text

    name = normalize_text(str(label or ""))
    corpus = normalize_text(str(text or ""))
    if not name or not corpus:
        return 0
    values = set()
    for template in (_ANCHORED_COUNT, _ANCHORED_STATE_COUNT):
        pattern = re.compile(template.pattern % re.escape(name))
        for match in pattern.finditer(corpus):
            value = _count_value(match.group(1))
            if value:
                values.add(value)
    return values.pop() if len(values) == 1 else 0


#: Ordinal markers a walkthrough uses to number the items it covers.
_ORDINAL_MARKER = re.compile(r"第\s*([0-9一二三四五六七八九十两]+)\s*[个只处]|([①-⑳])")
_CIRCLED_VALUES = {chr(0x2460 + index): index + 1 for index in range(20)}


#: A line stating where one of the page's subjects sits: "1、位置：半神议院黎明云崖地图的左下方。"
_LOCATION_LINE = re.compile(r"(?:位置|地点|坐标)\s*[:：]")
_LOCATION_MARKERS = ("位置", "地点", "坐标")


def location_statement_count(text: str, names: list[str]) -> int:
    """How many subjects a page places inside the area, or 0.

    A walkthrough that gives every puzzle its own line — "1、位置：半神议院黎明云崖地图的
    左下方。" ×3 — has listed exactly three puzzles there, even though it never states a
    total. Only lines that name the area count, so the chest and chest-location lines
    of the same page stay out; a page that places two of a three-point area still
    fails the equality check in the caller.
    """
    from hsrmap.guides.signature import normalize_text

    wanted = [name for name in (normalize_text(item) for item in names or []) if len(name) >= 2]
    if not wanted:
        return 0
    chunks: list[str] = []
    for raw in text if isinstance(text, (list, tuple)) else str(text or "").split("\n"):
        chunks.extend(re.split(r"[。\n]", str(raw or "")))
    seen: set[str] = set()
    for raw in chunks:
        line = raw.strip()
        if not line or not _LOCATION_LINE.search(line):
            continue
        normalized = normalize_text(line)
        if not any(name in normalized for name in wanted):
            continue
        seen.add(normalized)
    return len(seen) if len(seen) >= 2 else 0


def enumeration_count(
    text: str,
    labels: list[str],
    *,
    require_label_adjacent: bool = True,
    allow_run: bool = False,
) -> int:
    """How many items a walkthrough numbers against the topic, or 0.

    A page that writes "第一个黄金替罪羊 … 第二个 … 第三个" has counted its subjects
    itself, and that count is evidence just like "共3个" — many walkthroughs never
    state a total. The numbers have to be exactly 1..N (a page that numbers its
    own screenshots, or skips one, says nothing), and every marker has to sit next
    to the topic's name: "①普通战利品" is not a numbered 梦境迷钟.

    `require_label_adjacent=False` 只给「小标题已经点明区域」的分节用：正文用
    「一、第1个 … 四、第4个」把这一区的谜题逐个编号时，主题名在标题里而不在每条旁边，
    这时编号本身（1..N 完整）就是这一页自己的计数——调用方还要用「等于该区域点位数」
    来把关，所以放宽的只是「每条紧贴主题名」这一条。
    """
    from hsrmap.guides.signature import normalize_text

    body = str(text or "")
    names = [name for name in (normalize_text(label) for label in labels or []) if name]
    if not body or not names:
        return 0
    values: set[int] = set()
    for match in _ORDINAL_MARKER.finditer(body):
        if match.group(2):
            value = _CIRCLED_VALUES.get(match.group(2), 0)
        else:
            value = _count_value(match.group(1))
        if not value:
            continue
        window = normalize_text(body[max(0, match.start() - 8) : match.end() + 8])
        if require_label_adjacent and not any(name in window for name in names):
            continue
        values.add(value)
    if not values:
        return 0
    top = max(values)
    if top >= 2 and values == set(range(1, top + 1)):
        return top
    if allow_run:
        #: 分节的一页：全文把这一区的谜题编号成 1..10，而这一节只走「第4个…第6个」。
        #: 连续的一段就是这一节自己的计数——中间缺号或重复都不算（上面那条 1..N 才是
        #: 完整编号），调用方仍要求这个数等于该区域的点位数。
        ordered = sorted(values)
        if len(ordered) >= 2 and ordered == list(range(ordered[0], ordered[0] + len(ordered))):
            return len(ordered)
    return 0


def distributive_count(text: str) -> int:
    """The per-region number a page states for several regions at once, or 0.

    "呓语密林-神悟树庭和神谕圣地-雅努萨波利斯两大区域，每个区域各有3个谜题"
    claims three in each of the two areas it names — one number, two regions. Two
    different numbers in distributive wording mean the sentence is not saying one
    count per region, so the answer is 0 (the same "one number or nothing" rule
    the other count readers use).
    """
    values = {
        _count_value(match.group(1))
        for match in _DISTRIBUTIVE_COUNT.finditer(str(text or ""))
    }
    values.discard(0)
    return values.pop() if len(values) == 1 else 0


def _segments(text: str) -> list[str]:
    bounds = [0] + [match.end() for match in _SEGMENT.finditer(text)] + [len(text)]
    return [text[bounds[index] : bounds[index + 1]] for index in range(len(bounds) - 1)]

#: Chinese numerals 1-99 as pages write them ("两个", "十二个", "二十三个").
_CN_DIGITS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _count_value(value: str) -> int:
    raw = str(value or "").strip()
    if raw.isdigit():
        return int(raw)
    if not raw or any(char not in _CN_DIGITS for char in raw):
        return 0
    if "十" in raw:
        head, _, tail = raw.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_DIGITS[raw]


def near_count(text: str, label: str) -> int:
    """The number written at the name itself ("3个黄金替罪羊解谜"), or 0.

    A sentence that lists several kinds ("一共有29个宝箱，2个贼灵和3个黄金替罪羊解谜")
    states the topic's own count next to the topic's name; the "一共29个" belongs to
    the chests. The ordinal form ("第一个黄金替罪羊") is not a count and is skipped.
    """
    from hsrmap.guides.signature import normalize_text

    body = str(text or "")
    if not body or not str(label or "").strip():
        return 0
    values = set()
    for name in [str(label or "").strip(), *_loose_region_names(str(label or ""))]:
        if not name:
            continue
        pattern = re.compile(_NEAR_COUNT.pattern % re.escape(name))
        for match in pattern.finditer(body):
            value = _count_value(match.group(1))
            if value:
                values.add(value)
    return values.pop() if len(values) == 1 else 0



#: 行首序号：「1、集市左侧解谜」「一、灾梦余温」「1.位置：…」。九游/游侠用这种方式
#: 给一区里的谜题编号，主题名在小标题上而不是每条旁边——和「第1个」一样是计数证据。
_LINE_ORDINAL = re.compile(r"(?:^|[\s。；;、])([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*[、.．)）]\s*")


#: 序号的**行**里出现这些字，说明它编的是「一个谜题内部的步骤」（位置→影子出现前→影子出现后），
#: 不是「这一区的第几个谜题」——两者的形状一模一样，靠位置行区分。
_LOCATION_MARKER = re.compile(r"(位置|地点|坐标)")


def line_ordinal_count(text: str, *, allow_run: bool = False) -> int:
    """页面用「1、」「一、」编号时，编号本身就是计数（完整 1..N，或连续一段）。

    编谜题（「1、集市左侧解谜 右2步」）与编步骤（「1、位置：… 2、影子出现前：…」）写法相同；
    后者一旦被当成计数，就会把一个谜题的 3 步读成「这一区有 3 个谜题」，正好和点位数撞上。
    所以只要序号行里出现过位置字眼，这一串编号就按**步骤模板**处理，返回 0。
    """
    body = str(text or "")
    hits = list(_LINE_ORDINAL.finditer(body))
    values: set[int] = set()
    for index, match in enumerate(hits):
        #: 一个序号管到下一个序号为止：那一段就是它编的那一条（步骤或谜题）。
        end = hits[index + 1].start() if index + 1 < len(hits) else len(body)
        segment = body[match.start():end]
        if _LOCATION_MARKER.search(segment):
            return 0
        value = _count_value(match.group(1))
        if value:
            values.add(value)
    if len(values) < 2:
        return 0
    ordered = sorted(values)
    if ordered == list(range(1, len(ordered) + 1)):
        return len(ordered)
    if allow_run and ordered == list(range(ordered[0], ordered[0] + len(ordered))):
        return len(ordered)
    return 0


def declared_count(text: str, *, label: str = "", labels: list[str] | None = None) -> int:
    """How many targets the page says it covers (0 when it is not clear).

    With a `label` (or several `labels`) only the lines naming the topic count, and
    the answer has to be a single number: a page listing "战利品×12 … 王下一桶×2"
    states two different counts, and guessing which one is the guide's scope is how
    wrong bindings happen. Several distinct numbers therefore mean "no answer".
    """
    source = str(text or "")
    names = [str(item).strip() for item in (labels if labels is not None else [label]) if str(item or "").strip()]
    if names:
        #: a number written at one of the names is that name's own count, and it
        #: beats the sentence's "一共": "一共有29个宝箱，2个贼灵和3个黄金替罪羊解谜"
        near = {value for value in (near_count(source, name) for name in names) if value}
        if len(near) == 1:
            return near.pop()
        # only what the page says *about this topic* may decide its scope: a list
        # that mentions "战利品×12" next to "王下一桶×2" states two different counts
        relevant = [segment for segment in _segments(source) if any(name in segment for name in names)]
        if not relevant:
            return 0
        source = " ".join(relevant)
    values: set[int] = set()
    for match in _DECLARED_COUNT.finditer(source):
        value = _count_value(match.group(1))
        if value:
            values.add(value)
    for match in _TIMES_COUNT.finditer(source):
        raw = match.group(1) or match.group(2) or ""
        if str(raw).isdigit():
            value = int(raw)
            if value:
                values.add(value)
    if len(values) == 1:
        return values.pop()
    return 0


def _loose_region_names(region: str) -> list[str]:
    """The halves of a bracketed region name that a page may write instead.

    "「世界尽头」酒馆" is written as "世界尽头地图共有3个浮脂溯源解密", and
    "「半神议院」黎明云崖" as "半神议院黎明云崖" or "黎明云崖".
    """
    text = str(region or "")
    inside = ""
    for opener, closer in (("「", "」"), ("[", "]")):
        if opener in text and closer in text:
            inside = text.split(opener, 1)[1].split(closer, 1)[0].strip()
            if inside:
                break
    out: list[str] = []
    for value in (
        text.split("」")[-1].split("]")[-1],  # 「白日梦」酒店-梦境 -> 酒店-梦境
        inside,  # 「世界尽头」酒馆 -> 世界尽头
        text.split("「")[0].split("[")[0],  # 无名客「阿哈」的债务清单 -> 无名客
    ):
        value = str(value or "").strip()
        if value and value != text and value not in out:
            out.append(value)
    return out


#: The bracketed head of an official region ("「灾梦余温」无名泰坦大墓" -> 灾梦余温).
_BRACKET_HEAD = re.compile(r"[「『\[](?P<inside>[^」』\]]+)[」』\]]")


def _region_variant(region: str) -> tuple[str, str]:
    """(distinctive head, shared tail) of a bracketed region name, or ("", "")."""
    text = str(region or "").strip()
    match = _BRACKET_HEAD.search(text)
    if not match:
        return "", ""
    inside = match.group("inside").strip()
    tail = text[match.end() :].strip()
    return inside, tail


def _names_a_sibling_instead(
    regions: dict[str, list[str]],
    article: str,
    official_points: list[dict[str, Any]] | None,
) -> bool:
    """Does the page name the claimed region's twin instead of the region itself?

    Official data keeps two variants of one area as separate regions
    ("「全世矩阵」无名泰坦大墓" and "「灾梦余温」无名泰坦大墓"), and a page about one
    of them names only that one. When the item claims one variant while the page
    names the other, a matching number is a coincidence of equal counts, not
    evidence: 3dmgame's 全世矩阵 page counts ten nymphs, and it is the 灾梦余温
    region — not the 全世矩阵 one — that happens to hold ten points.
    """
    from hsrmap.guides.signature import normalize_text

    corpus = normalize_text(str(article or ""))
    if not corpus:
        return False
    for region in regions:
        inside, tail = _region_variant(str(region))
        if not inside or not tail:
            continue
        if normalize_text(inside) in corpus:
            #: the page names this very variant, so nothing contradicts the claim
            continue
        for point in official_points or []:
            other = str(point.get("region") or "").strip()
            if not other or other == str(region):
                continue
            other_inside, other_tail = _region_variant(other)
            if not other_inside or other_tail != tail:
                continue
            if normalize_text(other_inside) in corpus:
                return True
    return False


#: "千星城中心城区4个，指针塔3个，空声院1个" — a page's own partition of an area.
#: An entry starts at the text's start or right after a separator and ends at the
#: unit, so a title like "【浮脂溯源】指针塔（共三个）" is not a partition entry
#: (its number sits inside brackets, which the scope readers handle instead).
_PART_ENTRY = re.compile(
    r"(?:^|[，,。；;、|/\n])\s*"
    r"([\u4e00-\u9fffA-Za-z0-9·\-—（）()「」『』【】]{2,16}?)\s*"
    #: the number must not be the tail of a longer one ("共48个" is a topic-wide
    #: scope note, not the pair ("共4", 8))
    r"(?<![0-9一二三四五六七八九十两])([0-9一二三四五六七八九十两]+)\s*[个处]"
    r"(?=[，,。；;、|/\n]|\s|$)"
)

#: Regions official data leaves unnamed: the room exists as a map, but the map and
#: its node are called "特殊房间" (or nothing at all).
_GENERIC_REGIONS = {"特殊房间", ""}


def partition_counts(text: str) -> list[tuple[str, int]]:
    """The ("<area>", N) pairs a page writes when it partitions an area."""
    out: list[tuple[str, int]] = []
    for match in _PART_ENTRY.finditer(str(text or "")):
        name = match.group(1).strip()
        value = _count_value(match.group(2))
        if name and value:
            out.append((name, value))
    return out


def _map_root(point: dict[str, Any]) -> str:
    return str(point.get("map_path") or "").split(" / ")[0].strip()


def residual_region_binding(
    draft: dict[str, Any],
    topic_key: str,
    official_points: list[dict[str, Any]] | None,
    article: str,
    item_text: str,
) -> tuple[str, str]:
    """("REGION_SET", target) for the unnamed leftover of an area the page partitions.

    A page may account for a whole area part by part ("千星城中心城区4个，指针塔3个，
    空声院1个"). Official data has no name for some interior rooms — the map and its
    node are called "特殊房间" — so a part the official regions cannot match may be
    exactly the point official data cannot name. Nothing is guessed: the arithmetic
    has to close on both sides

    * every part that *does* match an official region states that region's exact
      point count (a part that states a different number voids the whole reading);
    * those regions all sit under one parent area, which the item names;
    * exactly one part matches no official region;
    * the points of that area that no matched part covers and that official data
      leaves unnamed are exactly as many as that part claims.

    A part that merely *looks like* an official region (a shortened or decorated
    name) is not accepted as the unnamed one — it is a name the engine failed to
    resolve, not a room without a name — so those pages stay refused.
    """
    parts = partition_counts(item_text)
    if len(parts) < 2:
        return "", ""
    topic = str(draft.get("topic_key") or topic_key or "").replace("-", "_")
    if not topic:
        return "", ""
    from hsrmap.guides.signature import normalize_text

    regions = [str(point.get("region") or "").strip() for point in (official_points or [])]
    matched_ids: set[str] = set()
    roots: set[str] = set()
    unmatched: list[int] = []
    for name, count in parts:
        found = region_candidates(official_points, name)
        ids = {pid for group in found.values() for pid in group}
        if not ids:
            norm = normalize_text(name)
            if any(norm and norm in normalize_text(region) for region in regions if region):
                #: the official data knows this area under a longer name; the page
                #: simply named it loosely, so it is not the unnamed room
                return "", ""
            unmatched.append(count)
            continue
        if count != len(ids):
            return "", ""
        matched_ids |= ids
        for point in official_points or []:
            if str(point.get("source_point_id") or "") in ids:
                root = _map_root(point)
                if root:
                    roots.add(root)
    if not matched_ids or len(roots) != 1 or len(unmatched) != 1:
        return "", ""
    root = next(iter(roots))
    if normalize_text(root) not in normalize_text(item_text):
        return "", ""
    leftover = sorted(
        {
            str(point.get("source_point_id") or "")
            for point in (official_points or [])
            if _map_root(point) == root
            and str(point.get("source_point_id") or "") not in matched_ids
            and str(point.get("region") or "").strip() in _GENERIC_REGIONS
            and not str(point.get("map_name") or "").strip()
        }
        - {""}
    )
    if not leftover or len(leftover) != unmatched[0]:
        return "", ""
    return "REGION_SET", "set:" + "-".join(leftover) + f":topic:{topic}"


#: A map name that is really an area name. Official data uses the same "1层"
#: everywhere, and those two characters match any page; only names with three
#: characters of their own ("渡画泉隐", "珠星大厦") may stand for an area.
_FLOOR_NAME = re.compile(r"^-?\d+\s*层$|^\d+$|^\s*$")


def _named_area(name: str) -> bool:
    from hsrmap.guides.signature import normalize_text

    text = str(name or "").strip()
    if not text or _FLOOR_NAME.match(text):
        return False
    return len(normalize_text(text)) >= 3


def _room_base(region: str) -> str:
    """The area a sub-room belongs to: "朝露公馆-2" -> "朝露公馆".

    Official data keeps interior rooms as their own regions ("朝露公馆-1", "苏乐达-1号-左",
    "「龙骸古城」斯缇科西亚-1层房间（黎明）"), while the page names the area and counts
    every bird in it. The base is only used additively: a page that says "朝露公馆"
    covered the area, so its rooms come along — but only if the numbers add up.
    """
    text = str(region or "")
    parts = re.split(r"[-_](?=[0-9])", text, maxsplit=1)
    base = parts[0].strip() if len(parts) > 1 else ""
    return base if base and base != text else ""


def region_candidates(
    points: list[dict[str, Any]] | None, text: str
) -> dict[str, list[str]]:
    """Official regions named by the page's own words, with their point ids.

    A region string like "「半神议院」黎明云崖" is matched whole, and also by its
    tail after the bracket ("黎明云崖") — pages rarely repeat the bracket prefix.
    Rooms of an area the page names ("朝露公馆-2" when the page says "朝露公馆")
    are added on top of that, because the page counted the whole area.
    """
    from hsrmap.guides.signature import normalize_text

    corpus = normalize_text(text)
    if not corpus:
        return {}
    full: dict[str, list[str]] = {}
    tail: dict[str, list[str]] = {}
    rooms: dict[str, list[str]] = {}
    for point in points or []:
        region = str(point.get("region") or "").strip()
        point_id = str(point.get("source_point_id") or "")
        if not region or not point_id:
            continue
        #: a page names the area the player walks through, which official data
        #: splits into a region and a map ("「无名客「阿哈」的债务清单」" on the map
        #: "渡画泉隐", "珠星大厦" as its own map). Either name is the area.
        names = [region]
        map_name = str(point.get("map_name") or "").strip()
        if map_name and map_name != region and _named_area(map_name):
            names.append(map_name)
        #: 页面写区域名时会省掉世界名（官方「匹诺康尼折纸大学学院」，攻略只写「折纸大学学院」）。
        #: 世界名不用硬编码——它就是这个点位自己 map_path 的根。剥掉后剩下的必须是**够长的专名**
        #: （≥5 字）：「中心城区」这类通名谁都能叫，认了就会把甲城/千星城的点位互相绑串。
        root = _map_root(point)
        if root and region.startswith(root):
            shortened = region[len(root):].lstrip("-·_ ")
            if len(normalize_text(shortened)) >= 5 and shortened not in names:
                names.append(shortened)
        for name in names:
            norm_region = normalize_text(name)
            if norm_region and norm_region in corpus:
                full.setdefault(name, []).append(point_id)
                break
            # the loose name is only a fallback: a page that says "无晖祈堂黎明云崖"
            # named one sub-region, not both of the ones ending in "黎明云崖". Pages
            # drop either half of a bracketed name ("「世界尽头」酒馆" is written as
            # "世界尽头地图共有3个…"), so both halves are tried, and the >= 3 char
            # floor keeps "1层" or "酒馆" from matching everywhere.
            matched = False
            for loose in _loose_region_names(name):
                norm_loose = normalize_text(loose)
                if len(norm_loose) >= 3 and norm_loose in corpus:
                    tail.setdefault(name, []).append(point_id)
                    matched = True
                    break
            if matched:
                break
        else:
            base = _room_base(region)
            norm_base = normalize_text(base)
            if base and len(norm_base) >= 3 and norm_base in corpus:
                rooms.setdefault(region, []).append(point_id)
    #: the rooms of a named area are additive (the page counted the whole area);
    #: the bracket halves are not, because they are shared between siblings
    out = dict(full or tail)
    for region, ids in rooms.items():
        out.setdefault(region, []).extend(ids)
    return out


def label_variants(label: str) -> list[str]:
    """The names a page may use for the topic: the label and its undecorated head.

    A topic's official label carries its own decorations ("浮脂溯源·二次元ROTATE！"),
    which no article repeats; the head before the separator is what pages write.
    """
    text = str(label or "").strip()
    out: list[str] = []
    for value in [text, re.split(r"[·！!：:（(]", text)[0].strip()]:
        if len(value) >= 2 and value not in out:
            out.append(value)
    return out


def region_scope_count(
    text: str, regions: dict[str, list[str]], labels: list[str] | None = None
) -> tuple[int, str]:
    """(count, how) — how many targets the page claims *within the regions it names*.

    One region: the number may be attached to the region ("海原市地图共有3个") or to
    the topic as a whole ("共10只，有的若虫…"), and either has to be the single
    number its own lines state. Several regions: the page's own arithmetic decides —
    the numbers it states for the regions it names have to add up to exactly those
    regions' points. Official data splits an area the page counts as one ("「白日梦」
    酒店-梦境" plus its "-5" sub-room), so a number needs to be traceable to *a* named
    region, not necessarily to the one it finally lands on.
    """
    if not regions:
        return 0, ""
    if len(regions) == 1:
        region = next(iter(regions))
        #: 先看「这个区域自己有几个」（「【稚子的梦】2个位置」）。页面的总数
        #: （「…中全部的【王下一桶】(共8个)」）是几个区域加起来的，只有在区域自己
        #: 没写数字时才轮到它，否则 2 个点位的区域会被 8 挡住、一条也绑不上。
        count = (
            bracketed_count(text, region)
            or anchored_count(text, region)
            #: 「晖长石号中有4个梦境迷钟」：区域名 + 数字 + 单位 + 主题名
            or named_topic_count(text, region, list(labels or []))
        )
        if count:
            return count, "anchored"
        for names, how in ((list(labels or []) + [region], "topic"), ([region], "region")):
            count = declared_count(text, labels=names)
            if count:
                return count, how
        return 0, ""
    stated = [
        count
        for count in (declared_count(text, labels=[region]) for region in regions)
        if count
    ]
    if stated:
        return sum(stated), "sum"
    #: one number for every named region ("每个区域各有3个"): the page counts that
    #: many in each of them, so its scope is the number times the regions it named.
    #: Equality against the official points still decides whether it binds.
    shared = distributive_count(text)
    if shared:
        return shared * len(regions), "each"
    #: several regions in one sentence: take each count that sits right after its
    #: own region name ("『珠星大厦』共3处，『观览云岛站』共4处")
    anchored = [count for count in (anchored_count(text, region) for region in regions) if count]
    if anchored:
        return sum(anchored), "anchored"
    #: the page may state one number for the whole topic instead of one per area
    #: ("（1）共10只，有的折纸小鸟需要交互多次"). It still has to equal the named
    #: regions' points exactly, so a page wide enough to name them all is the
    #: only one that can pass.
    total = declared_count(text, labels=list(labels or []))
    if total:
        return total, "topic"
    return 0, ""


def substantive_steps(steps: list[Any]) -> list[str]:
    """The steps that could actually instruct someone (a1-6 §10/§30 in spirit)."""
    from hsrmap.guides.signature import normalize_text

    out: list[str] = []
    for step in steps or []:
        text = str((step or {}).get("text") if isinstance(step, dict) else step or "").strip()
        if not text:
            continue
        if any(hint in text for hint in CHROME_STEP_HINTS):
            continue
        if _PAGE_LABEL.match(text):
            continue
        if _MACHINE_STEP.match(text):
            continue
        if len(normalize_text(text)) < MIN_SUBSTANTIVE_CHARS:
            continue
        if text not in out:
            out.append(text)
    return out


def grounded_steps(steps: list[str], article: str) -> tuple[list[str], list[str]]:
    """(grounded, invented) — every step must be traceable to the article text."""
    from hsrmap.guides.audit import grounding
    from hsrmap.guides.signature import normalize_text

    corpus = normalize_text(article)
    grounded: list[str] = []
    invented: list[str] = []
    for text in steps:
        if corpus and grounding(text, corpus) != "NONE":
            grounded.append(text)
        else:
            invented.append(text)
    return grounded, invented


def region_set_target_for(
    draft: dict[str, Any],
    topic_key: str,
    official_points: list[dict[str, Any]] | None,
    article: str,
    item_text: str,
) -> str:
    """The region-set target this draft would bind, or "" (public wrapper).

    Kept next to its rule so callers that only want to *look* — reporting, the
    thin-draft revival pass — cannot drift from what approval actually does.
    """
    return _region_set_binding(draft, topic_key, official_points, article, item_text)[1]


def _region_set_binding(
    draft: dict[str, Any],
    topic_key: str,
    official_points: list[dict[str, Any]] | None,
    article: str,
    item_text: str,
) -> tuple[str, str]:
    """("REGION_SET", target) when the page's own scope statement binds a region.

    The page names the region ("海原市地图共有3个浮脂溯源解密") and states how many
    targets it covers there; that number has to equal the region's official point
    count exactly, because a page covering "两个" of a ten-point region would
    otherwise publish the eight points it never talked about.

    Membership comes from the *item*, the numbers from the *page*: one review item
    is one slice of a page ("3、匹诺康尼大剧院"), so its steps are the guide for the
    region it walks through, while the count it has to match is the page's own.
    """
    #: the item's own heading is the strongest scope signal ("生研院"): a body that
    #: says "从千星城中心城区传送到生研院" mentions another area in passing, and
    #: taking it along would put four of its points into this region's set
    heading = str(draft.get("map_name") or "")
    #: membership comes from the item — its heading or its own steps. The page is
    #: only asked for the number: an item that never names the region is a slice of
    #: the page's furniture ("最新专题" and its link list), and letting the article
    #: name the region for it published that furniture as a guide.
    from_heading = region_candidates(official_points, heading)
    regions = from_heading or region_candidates(official_points, item_text)
    if not regions:
        return "", ""
    if _names_a_sibling_instead(regions, article, official_points):
        #: the page talks about the other variant of the area: whatever the
        #: numbers say, this item is not evidence for the region it claims
        return "", ""
    labels = {
        str(point.get("label") or "").strip()
        for point in (official_points or [])
        if str(point.get("label") or "").strip()
    }
    topic_label = labels.pop() if len(labels) == 1 else ""
    count, _how = region_scope_count(article, regions, label_variants(topic_label))
    if not count:
        #: no total anywhere: a walkthrough that numbers its items ("第一个…第三个")
        #: has counted them itself, and the equality below still has to hold
        count = enumeration_count(item_text, label_variants(topic_label))
    if not count and from_heading:
        #: 小标题已经点明区域（「全世矩阵无名泰坦大墓黄金替罪羊」），正文再把它逐个编号
        #: （「一、第1个 … 四、第4个」）：主题名在标题里、不在每条旁边，
        #: 但编号完整 1..N，而下面仍要求 N 恰好等于这个区域的点位数。
        #: 同一页连着几个区域时（游民星空 2.2 那篇把 10 个迷钟编号到「第10个」），
        #: 分节只拿到连续的一段（苏乐达热砂海选会场=第4..6个），所以这里也认连续段。
        count = enumeration_count(
            item_text,
            label_variants(topic_label) or list(regions),
            require_label_adjacent=False,
            allow_run=True,
        )
    if not count:
        #: no numbering either: a page that gives every puzzle its own location line
        #: inside the area ("位置：半神议院黎明云崖地图的左下方" ×3) has listed three
        count = location_statement_count(item_text, [*regions, *label_variants(topic_label)])
    if not count:
        #: 最后才看行首序号（九游「1、集市左侧解谜」、游侠「一、灾梦余温」）：位置行比编号更硬，
        #: 编号则允许连续段（一区里的编号是 4、5、6、7），段长仍须等于点位数。
        count = line_ordinal_count(item_text, allow_run=True)
    members = sorted({point_id for ids in regions.values() for point_id in ids})
    if count and len(members) != count and len(regions) > 1:
        #: 页面写的名字同时属于两个区域时（「雅努萨波利斯」既是「神谕圣地」的也是「命运重渊」的），
        #: 用它自己说的数量去分辨：官方点位数正好等于这个数的那个区域才可能是它讲的——
        #: 只有一个对得上才收窄，两个都对得上说明分不出来，照旧拒绝。
        fits = {key: ids for key, ids in regions.items() if len(ids) == count}
        if len(fits) == 1:
            regions = fits
            members = sorted({point_id for ids in regions.values() for point_id in ids})
    if not count or len(members) != count:
        return "", ""
    #: Areas that exist *only* as a map name: official data keeps the region under
    #: another name ("渡画泉隐" lives under 「无名客「阿哈」的债务清单」). Matching by
    #: such a name is the finer match, so the item has to state the count itself —
    #: a slice that only mentions the map in passing ("…渡画泉隐地图。共计34个计数
    #: 战利品") is not a guide for the map's items. The sibling item that does state
    #: it ("可以在地图上数一下渡画泉隐jump点位是否为9个") binds.
    region_names = {
        str(point.get("region") or "").strip()
        for point in (official_points or [])
        if str(point.get("region") or "").strip()
    }
    map_only = {
        str(point.get("map_name") or "").strip()
        for point in (official_points or [])
        if _named_area(str(point.get("map_name") or ""))
    } - region_names
    for key in regions:
        if str(key) not in map_only:
            continue
        stated = anchored_count(item_text, str(key)) or declared_count(item_text, labels=[str(key)])
        if stated != count:
            return "", ""
    draft_topic = str(draft.get("topic_key") or topic_key or "").replace("-", "_")
    if not draft_topic:
        return "", ""
    return "REGION_SET", "set:" + "-".join(members) + f":topic:{draft_topic}"


def _solve_missing_points(db: GuideDatabase, topic_key: str) -> set[str]:
    """完成模型说「位置有了、缺解法」的点位；读不到时返回空集合。"""
    if not topic_key:
        return set()
    from hsrmap.guides.stages import completeness_report

    try:
        report = completeness_report(db, topics=[topic_key])
    except Exception:  # noqa: BLE001 - 评审不该因为完成模型读不到数据而失败
        return set()
    return {
        str(row.get("point") or "")
        for row in report.get("rows") or []
        if str(row.get("status")) == "SOLVE_MISSING"
    }


def approve_grounded(
    db: GuideDatabase,
    *,
    topic: str = "",
    official_points: list[dict[str, Any]] | None = None,
    maps: list[dict[str, Any]] | None = None,
    apply: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Approve text-only drafts whose steps are verbatim article text (§27/§31).

    Relaxed on purpose (the corpus may be plain text), strict where it matters:

    - the target must be evidence-based: a MAP_LABEL key whose map the draft names
      (or a PAGE_ANCHOR image), a single candidate point, or — when nothing anchors
      the draft — a region the page names together with the number it states for
      it, matching that region's official point count exactly;
    - at least one *substantive* step (no bylines, no furniture, no bare map name);
    - every substantive step must be grounded in the stored article, so nothing
      invented can be published;
    - one guide per target: the draft with the most substantive steps wins.
    """
    from hsrmap.guides.audit import grounding, page_chrome
    from hsrmap.guides.stages import has_solution_steps
    from hsrmap.guides.rebuild import article_index, article_text
    from hsrmap.guides.regions.resolver import resolve_map
    from hsrmap.guides.signature import article_family, normalize_text

    index = article_index(db)
    #: targets that already have a published guide, with how many steps it has:
    #: a duplicate draft is only worth approving when it is richer than what is
    #: already published, otherwise the corpus just grows sideways.
    published_steps: dict[str, int] = {}
    for row in db.conn.execute(
        "SELECT e.source_point_id AS key, COUNT(s.id) AS steps FROM guide_entry e"
        " LEFT JOIN guide_steps s ON s.guide_id = e.id"
        " WHERE IFNULL(e.status, '') = 'published' AND e.source_point_id IS NOT NULL"
        " GROUP BY e.source_point_id"
    ):
        key = str(row["key"] or "")
        if key:
            published_steps[key] = max(published_steps.get(key, 0), int(row["steps"] or 0))
    #: 「已发布」也可能是 20 条视频标题那种空壳：条目在、解法没有。这样的目标再发一条带解法的
    #: 草稿是**升级**，不该被「步数没它多」挡住（哀丽秘榭 2 个点位就卡在这里）。
    published_solve: set[str] = set()
    for row in db.conn.execute(
        "SELECT e.source_point_id AS key, s.text AS text FROM guide_entry e"
        " JOIN guide_steps s ON s.guide_id = e.id"
        " WHERE IFNULL(e.status, '') = 'published' AND e.source_point_id IS NOT NULL"
    ):
        key = str(row["key"] or "")
        if key and key not in published_solve and has_solution_steps([row["text"]]):
            published_solve.add(key)
    rows = [
        dict(row)
        for row in db.conn.execute(
            "SELECT * FROM review_item WHERE status IN ('NEEDS_REVIEW','AUTO_SUGGEST') ORDER BY id"
        )
    ]
    topic_key = str(topic).replace("-", "_") if topic else ""
    best: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []

    def _consider(
        row: dict[str, Any],
        draft: dict[str, Any],
        page_row: dict[str, Any],
        article: str,
        binding: str,
        target: str,
        *,
        residual: bool = False,
    ) -> None:
        """Validate one (item, target) pair and keep it when it is the best so far."""
        steps = substantive_steps(draft.get("steps") or [])
        if not steps:
            rejected.append({"item_id": int(row["id"]), "target": target, "reason": "NO_SUBSTANTIVE_STEPS"})
            return
        gallery: list[str] = []
        if binding == "REGION_SET" and len(steps) < MIN_REGION_SET_STEPS:
            #: a gallery guide is acceptable when the pictures themselves cover the
            #: targets: at least one content screenshot per point, and the count
            #: evidence above still had to be exact
            members = len([item for item in target.split(":")[1].split("-") if item])
            gallery = gallery_images(
                db, int(row.get("page_id") or 0), page_row.get("raw_html_path"), want=members * 3
            )
            if len(gallery) < members:
                rejected.append({
                    "item_id": int(row["id"]),
                    "target": target,
                    "reason": "REGION_SET_TOO_THIN",
                    "detail": [
                        f"{len(steps)} steps and {len(gallery)} pictures for {members} points"
                    ],
                })
                return
        if not gallery and sum(len(normalize_text(text)) for text in steps) < MIN_STEP_CHARS_TOTAL:
            rejected.append({"item_id": int(row["id"]), "target": target, "reason": "STEPS_TOO_THIN"})
            return
        grounded, invented = grounded_steps(steps, article)
        # a step that is missing from the article but present in the page chrome
        # is site furniture ("latest news"), not an invention: drop it and keep
        # the guide. Only a step found nowhere can be invented.
        dropped_chrome: list[str] = []
        if invented:
            chrome = normalize_text(page_chrome(page_row))
            still_invented: list[str] = []
            for text in invented:
                if chrome and grounding(text, chrome) != "NONE":
                    dropped_chrome.append(text)
                else:
                    still_invented.append(text)
            invented = still_invented
        if not grounded:
            rejected.append({"item_id": int(row["id"]), "target": target, "reason": "NO_SUBSTANTIVE_STEPS"})
            return
        if invented:
            rejected.append({
                "item_id": int(row["id"]),
                "target": target,
                "reason": "UNGROUNDED_STEPS",
                "detail": invented[:1],
            })
            return
        #: 重复发布只在两种情况下值得拒：已发布的那条**自己就有解法**且不比这条瘦，
        #: 或者两边都没有解法、这条还更瘦。带解法的草稿去替换没有解法的空壳，是升级。
        upgrade = has_solution_steps(grounded) and target not in published_solve
        if not upgrade and published_steps.get(target, 0) >= len(grounded):
            rejected.append({
                "item_id": int(row["id"]),
                "target": target,
                "reason": "ALREADY_PUBLISHED",
                "detail": [f"{published_steps[target]} steps published"],
            })
            return
        current = best.get(target)
        if current is None or len(grounded) > len(current["steps"]):
            best[target] = {
                "item": row,
                "draft": draft,
                "target": target,
                "binding": binding,
                "steps": grounded,
                "gallery": gallery,
                "members": target.split(":")[1].split("-") if binding == "REGION_SET" else [],
                "dropped_chrome": dropped_chrome,
                "residual": bool(residual),
                "page_url": str((dict(page_row) if page_row else {}).get("canonical_url") or ""),
            }

    for row in rows:
        try:
            draft = json.loads(row.get("draft_json") or "{}")
        except Exception:
            continue
        if topic_key and str(draft.get("topic_key") or "") != topic_key:
            continue
        kind = str(draft.get("target_type") or "").upper()
        target_key = str(draft.get("target_key") or "").strip()
        candidates = draft.get("candidate_points") or []
        page = db.conn.execute(
            "SELECT canonical_url, raw_html_path FROM guide_page WHERE id = ?", (row.get("page_id"),)
        ).fetchone()
        page_row = dict(page) if page else {}
        article = article_text(index, str(page_row.get("canonical_url") or ""))
        raw_steps = [
            str((step or {}).get("text") or "")
            for step in (draft.get("steps") or [])
            if isinstance(step, dict)
        ]
        #: what the *item* walks through: its own steps plus the map it names. The
        #: article is the page — feeding both would state every number twice (the
        #: same "共有5个" would be attributed to two regions and their sum would
        #: double), so the page text is only used for the counts themselves.
        item_text = " ".join([str(draft.get("map_name") or ""), *raw_steps])
        binding = ""
        target = ""
        if kind == "MAP_LABEL" and target_key:
            map_id = target_key.split(":")[1] if target_key.startswith("map:") else ""
            resolved = resolve_map(draft.get("map_name"), maps or [])
            anchored = any(
                (image.get("resolved_map") or {}).get("status") == "PAGE_ANCHOR"
                for image in (draft.get("images") or [])
            )
            # the map list is often unnamed; the points of that map still know it
            point_names = {
                str(point.get("source_point_id") or ""): str(point.get("map_name") or "")
                for point in (official_points or [])
                if str(point.get("map_name") or "")
            }
            point_map_names = {
                str(point.get("map_id") or ""): str(point.get("map_name") or "")
                for point in (official_points or [])
                if str(point.get("map_name") or "")
            }
            named_by_point = False
            if map_id and map_id in point_map_names:
                wanted = normalize_text(point_map_names[map_id])
                candidate = normalize_text(str(draft.get("map_name") or ""))
                # a two-character floor name ("1层") matches everywhere; require
                # at least three normalized characters of real agreement
                named_by_point = bool(wanted) and len(wanted) >= 3 and (wanted in candidate or candidate in wanted)
            if anchored:
                binding, target = "PAGE_ANCHOR", target_key
            elif map_id and str(resolved.get("map_id") or "") == map_id:
                binding, target = "MAP_NAME", target_key
            elif named_by_point:
                binding, target = "MAP_NAME", target_key
        elif kind == "POINT" and len(candidates) == 1:
            point = candidates[0]
            candidate_id = str((point or {}).get("source_point_id") or "")
            if not candidate_id:
                continue
            binding, target = "CANDIDATE_POINT", candidate_id
        if not binding:
            # nothing anchored this draft to a single point or map, so the last
            # resort is the page's own scope statement: the regions the draft walks
            # through plus the number the page states for them
            binding, target = _region_set_binding(
                draft, topic_key, official_points, article, item_text
            )
        #: the page may also be the evidence for the points official data cannot
        #: name (see the function); the same item is only credited once per target
        residual = residual_region_binding(draft, topic_key, official_points, article, item_text)[1]
        if residual and residual == target:
            residual = ""
        if not binding and not residual:
            continue
        if binding:
            _consider(row, draft, page_row, article, binding, target)
        if residual:
            #: when the item has a target of its own, the complementary one gets its
            #: own review item instead of overwriting this item's record
            _consider(row, draft, page_row, article, "REGION_SET", residual, residual=bool(binding))
    chosen = list(best.values())
    if limit:
        chosen = chosen[: int(limit)]
    #: one guide per point: a page that covers three areas yields one set per area,
    #: and those sets must not overlap or the viewer shows three guides for the same
    #: point. The richest set wins a point; smaller overlapping sets step aside.
    chosen.sort(key=lambda entry: (-len(entry["steps"]), -len(entry.get("members") or []), entry["target"]))
    covered = published_point_ids(db)
    #: 「已发布」不等于「做完了」：点位可能只挂着一条官方点位条目（有位置、没解法）。
    #: 这时一条带解法的攻略是**新覆盖**（把 SOLVE_MISSING 变成 COMPLETE），不是重复卡片。
    incomplete = _solve_missing_points(db, topic_key)
    taken: set[str] = set()
    disjoint: list[dict[str, Any]] = []
    for entry in chosen:
        members = set(entry.get("members") or [])
        if members and not (members - covered) and not (members & incomplete):
            #: every point of this set already has a published guide: approving it
            #: would add a second card for the same points and no coverage at all
            rejected.append({
                "item_id": int(entry["item"]["id"]),
                "target": entry["target"],
                "reason": "NO_NEW_COVERAGE",
                "detail": [f"all {len(members)} points are already published"],
            })
            continue
        if members and members & taken:
            rejected.append({
                "item_id": int(entry["item"]["id"]),
                "target": entry["target"],
                "reason": "OVERLAPS_RICHER_SET",
                "detail": [f"{len(members)} points already covered by a richer set"],
            })
            continue
        taken |= members
        disjoint.append(entry)
    chosen = disjoint
    approved: list[dict[str, Any]] = []
    for entry in chosen:
        item = entry["item"]
        draft = dict(entry["draft"])
        if entry.get("residual") and str(entry["item"].get("source_point_id") or "").strip() != entry["target"]:
            #: this item already published its own target from the same page; the
            #: complementary target gets its own review item, so the record stays
            #: "one item, one target" while the page is credited for both. An item
            #: that already *is* the complementary target is approved as it stands.
            item = create_item(
                db,
                {
                    "page_id": int(entry["item"]["page_id"]),
                    "status": "NEEDS_REVIEW",
                    "source_point_id": entry["target"],
                    "draft": {
                        **draft,
                        "target_type": "POINT_SET",
                        "target_key": entry["target"],
                    },
                },
            )
        shas = [str(image.get("sha256")) for image in (draft.get("images") or []) if image.get("sha256")]
        image_source = "draft"
        if not shas and entry.get("gallery"):
            #: the body is the page's own screenshots (a gallery guide)
            shas = list(entry["gallery"])
            image_source = "gallery"
        steps = [{"text": text, "images": []} for text in entry["steps"]]
        if shas:
            steps[-1] = {**steps[-1], "images": shas}
        record = {
            "item_id": int(item["id"]),
            "target_key": entry["target"],
            "binding": entry["binding"],
            "steps": len(steps),
            "images": len(shas),
            "image_source": image_source,
            "page": entry["page_url"],
        }
        if apply:
            draft["steps"] = steps
            draft["binding_method"] = entry["binding"]
            if entry["binding"] == "REGION_SET":
                # a set is a POINT_SET target: the key names its member points
                draft["target_type"] = "POINT_SET"
                draft["target_key"] = entry["target"]
                draft["member_points"] = entry["target"].split(":")[1].split("-")
            db.conn.execute(
                "UPDATE review_item SET draft_json = ? WHERE id = ?",
                (json.dumps(draft, ensure_ascii=False), int(item["id"])),
            )
            if entry["binding"] == "CANDIDATE_POINT":
                # a POINT draft carries its point in the candidate list; the item
                # itself has to name it before approval can publish anything
                db.conn.execute(
                    "UPDATE review_item SET source_point_id = ? WHERE id = ?",
                    (entry["target"], int(item["id"])),
                )
            db.conn.commit()
            published = approve_item(db, int(item["id"]), official_points=official_points)
            record["entry_id"] = (published.get("entry") or {}).get("id")
        approved.append(record)
    return {
        "applied": bool(apply),
        "targets": len(chosen),
        "candidates": len(best),
        "approved": approved,
        "rejected": rejected[:20],
        "rejected_count": len(rejected),
    }


def _assert_approvable(item: dict[str, Any], official_points: list[dict[str, Any]] | None) -> None:
    target_type = str((item.get("draft") or {}).get("target_type") or "").upper()
    if target_type in {"MAP_LABEL", "POINT_SET", "GLOBAL"}:
        if not str((item.get("draft") or {}).get("target_key") or "").strip():
            raise ValueError("target_key required")
        if target_type == "POINT_SET" and official_points is not None:
            official_ids = {str(row.get("source_point_id")) for row in official_points}
            cands = (item.get("draft") or {}).get("candidate_points") or []
            cand_ids = {str(row.get("source_point_id")) for row in cands if isinstance(row, dict)}
            if cand_ids - official_ids:
                raise ValueError("unknown source_point_id")
        return
    pid = str(item.get("source_point_id") or "").strip()
    if pid in {"", "pending", "null", "None"}:
        raise ValueError("source_point_id required")
    draft = item.get("draft") or {}
    topic = str(draft.get("topic_key") or draft.get("topic") or "").replace("-", "_")
    cands = draft.get("candidate_points") or []
    cand_ids = {str(row.get("source_point_id")) for row in cands if isinstance(row, dict)}
    spec: dict[str, Any] = {}
    tokens: list[str] = []
    profile = ""
    if topic:
        try:
            from hsrmap.guides.topics.loader import get_topic

            spec = get_topic(topic)
            profile = str((spec.get("matcher") or {}).get("profile") or "")
            tokens = [str(name) for name in ((spec.get("official_labels") or {}).get("names") or []) if name]
            if spec.get("display_name"):
                tokens.append(str(spec["display_name"]))
        except KeyError:
            spec = {}
    require_cands = profile == "ticker_point_v1" or bool((spec.get("review") or {}).get("require_candidates"))
    if require_cands and not cand_ids:
        raise ValueError("candidate list required")
    if cands and pid not in cand_ids:
        raise ValueError("source_point_id not in candidates")
    if official_points is None:
        return
    hit = next((row for row in official_points if str(row.get("source_point_id")) == pid), None)
    if hit is None:
        raise ValueError("unknown source_point_id")
    if draft.get("map_id") and hit.get("map_id") and str(draft["map_id"]) != str(hit["map_id"]):
        raise ValueError("source_point_id map mismatch")
    wanted = draft.get("semantic_key") or draft.get("label")
    have = hit.get("label") or hit.get("name") or hit.get("semantic_key")
    if tokens:
        if have and not any(tok in str(have) for tok in tokens):
            raise ValueError("source_point_id label mismatch")
        if wanted and not any(tok in str(wanted) for tok in tokens) and wanted != have:
            raise ValueError("source_point_id label mismatch")
        return
    if wanted and have and wanted != have:
        raise ValueError("source_point_id label mismatch")


def reject_item(db: GuideDatabase, item_id: int) -> dict[str, Any]:
    db.conn.execute("UPDATE review_item SET status='REJECTED' WHERE id=?", (item_id,))
    db.conn.commit()
    return get_item(db, item_id)
