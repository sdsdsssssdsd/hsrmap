from __future__ import annotations

from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.derived import DerivedGuideStore
from hsrmap.guides.llm.provider import hallucinated_steps
from hsrmap.guides.matching.candidates import query_candidates
from hsrmap.guides.matching.matcher import match_sections
from hsrmap.guides.matching.registry import match_for_topic
from hsrmap.guides.semantic.classifier import classify_topics
from hsrmap.guides.topics.loader import get_topic
from hsrmap.guides.pipeline import import_page
from hsrmap.guides.qa import admits_review, evaluate_import, inspect_import, record_qa
from hsrmap.guides.regions.resolver import apply_page_anchor, resolve_map, resolve_page_map
from hsrmap.guides.regions.sections import build_region_sections, region_aliases
from hsrmap.guides.units.registry import build_units_for_topic
from hsrmap.guides.review.service import create_item
from hsrmap.guides.store import RawGuideStore
from hsrmap.guides.vision.region_reader import read_region
from hsrmap.guides.assets.phash import phash
from hsrmap.guides.assets.relevance import judge_image
from hsrmap.guides.vision.roles import USEFUL, classify_role
from hsrmap.guides.vision.slicer import slice_long_image


def ingest_page(
    html: str,
    url: str,
    db: GuideDatabase,
    store: RawGuideStore,
    provider,
    *,
    derived: DerivedGuideStore | None = None,
    topic: str = "floating_grease",
    fetch_asset=None,
    official_points: list[dict[str, Any]] | None = None,
    official_maps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    imported = import_page(html, url, db, store, topic=topic, fetch_asset=fetch_asset)
    try:
        topic_spec = get_topic(str(topic).replace("-", "_"))
        topic_key = topic_spec["topic_key"]
    except KeyError:
        topic_key = str(topic).replace("-", "_")
    db.bind_page_topic(imported["page"]["id"], topic_key, 1.0)
    for extra in classify_topics(imported["blocks"]).get("topics") or []:
        db.bind_page_topic(imported["page"]["id"], extra["topic_key"], float(extra.get("confidence") or 0))
    qa = inspect_import(imported, store)
    qa_status, qa_reason = evaluate_import(qa)
    record_qa(db, imported["page"]["id"], qa_status, qa_reason)
    page_row = dict(db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (imported["page"]["id"],)).fetchone())
    admitted = admits_review(page_row)
    blocks = imported["blocks"]
    extracted = provider.extract_sections(blocks, page_id=imported["page"]["id"], topic=topic)
    maps = official_maps or _maps_from_points(official_points or [])
    relevance: dict[str, Any] = {}
    observations = observe_images(
        blocks,
        provider,
        store,
        maps,
        page_title=str(imported["page"].get("title") or ""),
        report=relevance,
    )
    page_title = str(imported["page"].get("title") or "")
    sections = apply_page_anchor(
        #: 官方区域名交给切片器：正文里那一行「永恒圣城奥赫玛」就是边界（九游/游侠的写法），
        #: 而几个区域共享的尾巴（无名泰坦大墓）命中不唯一，不会乱切。
        build_region_sections(
            blocks,
            observations,
            title=page_title,
            regions=region_aliases(official_points or []),
        ),
        title=page_title,
        headings=[str(block.get("text") or "") for block in blocks if block.get("type") == "heading"],
        maps=maps,
    )
    units = build_units_for_topic(topic_key, sections, observations)
    if derived:
        derived.save_section_extraction(imported["page"]["id"], extracted)
        derived.save_article_classification(imported["page"]["id"], provider.classify_article(blocks))
        derived.save(
            imported["page"]["id"],
            "image-roles.json",
            {"observations": observations, "relevance": relevance},
        )
        derived.save(imported["page"]["id"], "guide-units.json", {"units": units})
    points = official_points or []
    try:
        topic_spec = get_topic(topic_key)
        label_names = list((topic_spec.get("official_labels") or {}).get("names") or [])
        topic_key = topic_spec["topic_key"]
    except KeyError:
        label_names = []
    items = []
    for unit in (units if admitted else []):
        cands = query_candidates(unit.get("map_name"), points, semantic=None, label_names=label_names)
        if not cands:
            #: 标题/小标题没写区域名，但正文自己说了「穹顶关塞二层」「晨昏之眼负二层」时，
            #: 用正文里的区域 + 楼层去找唯一点位。规则很紧（见 query_candidates_by_text）：
            #: 至少两个证据、楼层必须一致、限定词必须落在胜出点位里，否则宁可不绑。
            from hsrmap.guides.matching.candidates import query_candidates_by_text

            unit_text = " ".join(
                [
                    str(unit.get("map_name") or ""),
                    str(unit.get("location_text") or ""),
                    str(unit.get("spatial_anchor") or ""),
                    *[
                        str((step or {}).get("text") or "")
                        for step in (unit.get("steps") or [])
                        if isinstance(step, dict)
                    ],
                ]
            )
            cands = query_candidates_by_text(unit_text, points, label_names=label_names)
        bind = match_for_topic(topic_key, unit, cands)
        pid = str(bind.get("source_point_id") or "")
        if pid == "pending":
            pid = ""
        items.append(
            create_item(
                db,
                {
                    "page_id": imported["page"]["id"],
                    "source_point_id": pid,
                    "status": "AUTO_SUGGEST" if bind.get("status") == "auto" else "NEEDS_REVIEW",
                    "draft": {
                        "schema_version": 2,
                        "topic_key": topic_key,
                        "target_type": bind.get("target_type"),
                        "target_key": bind.get("target_key"),
                        "map_name": unit.get("map_name"),
                        "map_id": unit.get("map_id"),
                        "article_ordinal": unit.get("article_ordinal"),
                        "spatial_anchor": unit.get("spatial_anchor"),
                        "floor_label": unit.get("floor_label"),
                        "steps": unit.get("steps") or [],
                        "images": unit.get("images") or [],
                        "source_block_ids": unit.get("source_block_ids") or [],
                        "candidate_points": bind.get("candidates") or [],
                        "evidence": bind.get("evidence") or {},
                    },
                },
            )
        )
    if admitted and not items:
        items.extend(_fallback_items(db, imported["page"]["id"], blocks, extracted, points, topic_key))
    return {
        "page": imported["page"],
        "qa": qa,
        "qa_status": qa_status,
        "qa_reason": qa_reason,
        "admitted": admitted,
        "quarantined": not admitted,
        "extracted": extracted,
        "hallucinated_steps": hallucinated_steps(extracted, blocks),
        "review": items,
        "publish": "skipped",
        "observations": observations,
        "units": units,
    }


def observe_images(
    blocks: list[dict[str, Any]],
    provider,
    store: RawGuideStore,
    maps: list[dict[str, Any]],
    *,
    page_title: str = "",
    report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    nearby = " ".join(block.get("text") or "" for block in blocks if block.get("type") in {"heading", "paragraph"})
    whitelist = _whitelist_from_maps(maps)
    headings = [str(block.get("text") or "") for block in blocks if block.get("type") == "heading"]
    anchor = resolve_page_map(page_title, headings, maps)
    out = []
    dropped: list[dict[str, Any]] = []
    seen_sha: list[str] = []
    seen_phash: list[str] = []
    for block in blocks:
        if block.get("type") != "image":
            continue
        sha = block.get("asset") or ""
        blob = _asset_bytes(store, sha) or b""
        digest = phash(blob) if blob else ""
        # cheap rules first: furniture, ads, duplicates never reach the model (§十)
        verdict = judge_image(
            url=str(block.get("src") or ""),
            alt=str(block.get("alt") or ""),
            sha256=str(sha or ""),
            seen_sha=seen_sha,
            phash=digest,
            seen_phash=seen_phash,
        )
        if not verdict.keep:
            dropped.append({"block_id": block.get("id"), "sha256": sha, **verdict.as_dict()})
            continue
        if sha:
            seen_sha.append(str(sha))
        if digest:
            seen_phash.append(digest)
        sliced = slice_long_image(blob)
        role = classify_role(blob, sha256=sha, alt=block.get("alt") or "", src=block.get("src") or "", provider=provider)
        obs = {
            "block_id": block.get("id"),
            "sha256": sha,
            "role": role["role"],
            "role_confidence": role.get("confidence"),
            "map_name_raw": None,
            "resolved_map": {"status": "NO_MATCH"},
            "article_ordinal": None,
            "instruction_text": [],
            "grounded": False,
            "long_image": sliced.get("sliced"),
            "segments": sliced.get("segments") or [],
            "relevance": verdict.as_dict(),
        }
        if role["role"] in USEFUL:
            try:
                region = read_region(
                    blob,
                    sha256=sha,
                    provider=provider,
                    whitelist=whitelist,
                    alt=block.get("alt") or "",
                    src=block.get("src") or "",
                    nearby=nearby,
                )
                obs.update(region)
                resolved = resolve_map(region.get("map_name_raw"), maps or _default_maps())
                if resolved.get("status") == "NO_MATCH" and anchor.get("map_id"):
                    # the picture is labelled "1号小鸟"; the page says which map
                    resolved = {**anchor, "anchored_by": "page_title"}
                obs["resolved_map"] = resolved
            except Exception:
                obs["role"] = obs.get("role") or "unknown"
        out.append(obs)
    if report is not None:
        report["observations"] = out
        report["dropped"] = dropped
        report["kept"] = len(out)
    return out


def _fallback_items(db, page_id: int, blocks, extracted, points, topic_key: str = "floating_grease") -> list[dict[str, Any]]:
    items = []
    bindings = match_sections(blocks, points) if points else []
    sections = extracted.get("sections") or []
    if not bindings and sections:
        for section in sections:
            items.append(
                create_item(
                    db,
                    {
                        "page_id": page_id,
                        "source_point_id": "",
                        "status": "NEEDS_REVIEW",
                        "draft": {
                            "schema_version": 2,
                            "topic_key": topic_key,
                            "map_name": section.get("map_name") or "",
                            "steps": [
                                {"text": step.get("text_original") or step.get("text"), "images": []}
                                for step in (section.get("steps") or [])
                            ],
                            "candidate_points": [],
                        },
                    },
                )
            )
        return items
    for bind in bindings:
        pid = str(bind.get("source_point_id") or "")
        if pid == "pending":
            pid = ""
        heading = bind.get("heading")
        section = next((item for item in sections if item.get("map_name") == heading), {})
        items.append(
            create_item(
                db,
                {
                    "page_id": page_id,
                    "source_point_id": pid,
                    "status": "AUTO_SUGGEST" if bind.get("status") == "auto" else "NEEDS_REVIEW",
                    "draft": {
                        "schema_version": 2,
                        "topic_key": topic_key,
                        "map_name": heading,
                        "steps": [
                            {"text": step.get("text_original") or step.get("text"), "images": []}
                            for step in (section.get("steps") or [])
                        ],
                        "evidence": bind.get("evidence") or {},
                    },
                },
            )
        )
    return items


def _asset_bytes(store: RawGuideStore, sha: str) -> bytes | None:
    if not sha:
        return None
    folder = store.assets_root / sha[:2]
    if not folder.exists():
        return None
    for path in folder.glob(f"{sha}.*"):
        return path.read_bytes()
    return None


def _whitelist_from_maps(maps: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in maps or []:
        for raw in (item.get("name"), item.get("path"), *(str(item.get("path") or "").split("/"))):
            text = str(raw or "").strip()
            if text and text not in names:
                names.append(text)
    return names


def _maps_from_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {}
    for point in points:
        path = point.get("map_path") or point.get("path")
        for name in (point.get("map_name"), point.get("region")):
            if name and name not in seen:
                seen[name] = {"map_id": point.get("map_id"), "name": name, "path": path, "renderable": True}
    return list(seen.values())


def _default_maps() -> list[dict[str, Any]]:
    return [
        {"map_id": "842", "name": "海原市", "path": "海原市", "renderable": True},
        {"map_id": "tv", "name": "海原电视塔", "path": "海原电视塔", "renderable": True},
        {"map_id": "2d", "name": "二维市", "path": "二维市", "renderable": True},
        {"map_id": "paint", "name": "绘世学院", "path": "绘世学院", "renderable": True},
        {"map_id": "dove", "name": "鸽川区", "path": "鸽川区", "renderable": True},
        {"map_id": "end", "name": "世界尽头", "path": "世界尽头", "renderable": True},
        {"map_id": "682", "name": "珠星大厦", "path": "珠星大厦", "renderable": True},
        {"map_id": "683", "name": "观览云岛站", "path": "观览云岛站", "aliases": ["观觉云岛站"], "renderable": True},
        {"map_id": "844", "name": "渡画泉隐", "path": "渡画泉隐", "renderable": True},
        {"map_id": "880", "name": "寂灭空飨妖都", "path": "寂灭空飨妖都", "aliases": ["寂灭空飨妖"], "renderable": True},
        {"map_id": "879", "name": "坠星的摇篮", "path": "坠星的摇篮", "renderable": True},
        {"map_id": "942", "name": "千星城中心城区", "path": "千星城中心城区", "aliases": ["千星城"], "renderable": True},
        {"map_id": "945", "name": "指针塔", "path": "指针塔", "renderable": True},
        {"map_id": "1014", "name": "生研院", "path": "生研院", "renderable": True},
        {"map_id": "997", "name": "特殊房间", "path": "特殊房间", "renderable": True},
        {"map_id": "root", "name": "二相乐园", "path": "二相乐园", "renderable": False},
    ]
