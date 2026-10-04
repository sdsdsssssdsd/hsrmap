from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from hsrmap.database import CoreDatabase
from hsrmap.detail_db import DetailDatabase
from hsrmap.detail_enrich import build_detail_statistics, run_enrichment
from hsrmap.detail_offline import validate_details_offline
from hsrmap.detail_queue import canary_source_ids
from hsrmap.inspect_map import inspect
class _LazyPaths:
    """模块级路径代理（a1-8 四.2）：属性访问时才向 hsrmap.paths 要运行态路径。

    运行目录必须在 import 任何运行态模块之前定好，所以这里不能在模块顶层把路径取出来。
    """

    def __getattr__(self, name: str):
        from hsrmap import paths

        return getattr(paths, name)


_P = _LazyPaths()


def _prescan_data_dir(argv: list[str]) -> str:
    """正式 parse 之前先捞出 --data-dir：运行目录要先于任何运行态 import 定下来。"""
    for index, item in enumerate(argv):
        if item == "--data-dir" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--data-dir="):
            return item.split("=", 1)[1]
    return ""


from hsrmap.reports import build_statistics, write_json
from hsrmap.runtime import ENV_DATA_DIR, prescan_data_dir, resolve_runtime, set_runtime
from hsrmap.sync import run_smoke_overlays, run_sync
from hsrmap.validate import golden_point_errors, validate_offline


def _load_current() -> dict:
    if not _P.CURRENT_PATH.exists():
        raise SystemExit("no current snapshot")
    return json.loads(_P.CURRENT_PATH.read_text(encoding="utf-8"))


def cmd_status(_: argparse.Namespace) -> int:
    current = _load_current()
    db = CoreDatabase(_P.DATA / current["core_db"])
    stats = build_statistics(db)
    print(json.dumps({"current": current, "counts": db.counts(), "stats_summary": {
        "maps": stats["maps"],
        "labels": stats["labels"],
        "points": {k: stats["points"][k] for k in ("total", "maps_with_points", "maps_with_zero_points")},
        "raster": {k: stats["raster"][k] for k in ("total_fragments", "single_fragment_maps", "multi_fragment_maps")},
        "floating_grease": stats["floating_grease"],
        "floating_grease_notes": stats["floating_grease_notes"],
    }}, ensure_ascii=False, indent=2))
    db.close()
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    current = run_sync(resume=args.resume)
    print(json.dumps(current, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    if args.snapshot:
        db_path = _P.SNAPSHOTS / args.snapshot / "core.db"
    else:
        db_path = _P.DATA / _load_current()["core_db"]
    db = CoreDatabase(db_path)
    golden = json.loads(_P.GOLDEN_PATH.read_text(encoding="utf-8"))
    errors = golden_point_errors(db, golden)
    result = {"golden": errors, "counts": db.counts()}
    ok = errors["mean"] == 0 and errors["max"] == 0
    if args.offline:
        snapshot_root = db_path.parent
        smoke = run_smoke_overlays(db, snapshot_root / "debug")
        offline = validate_offline(db)
        result["offline"] = offline
        result["smoke"] = smoke
        ok = ok and offline["passed"] and all(item.get("ok") for item in smoke)
        report_path = snapshot_root / "reports" / "validation-report.json"
        if report_path.exists():
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            existing["golden"] = errors
            existing["smoke"] = smoke
            existing["offline"] = offline
            existing["passed"] = ok and existing.get("passed", True)
            write_json(report_path, existing)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    db.close()
    return 0 if ok else 1


def cmd_inspect(args: argparse.Namespace) -> int:
    path = inspect(args.map)
    print(path)
    return 0


def cmd_staging(args: argparse.Namespace) -> int:
    _P.STAGING.mkdir(parents=True, exist_ok=True)
    if args.staging_cmd == "list":
        for path in sorted(_P.STAGING.iterdir()):
            print(path.name)
        return 0
    if args.staging_cmd == "clean":
        for path in _P.STAGING.iterdir():
            if path.name == "sync.lock":
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        print("staging cleaned")
        return 0
    raise SystemExit("unknown staging command")


def _snapshot_id(args) -> str:
    if getattr(args, "snapshot", None):
        return args.snapshot
    return _load_current()["snapshot_id"]


def cmd_enrich_details(args: argparse.Namespace) -> int:
    result = run_enrichment(
        snapshot_id=_snapshot_id(args),
        resume=args.resume,
        retry_failed=args.retry_failed,
        canary=args.canary,
        queue_only=args.queue_only,
        semantic_key=args.semantic_key,
        map_id=args.map_id,
        point_id=args.point_id,
    )
    reports = result.get("reports") or {}
    summary = {
        "status": result.get("status"),
        "counts": result.get("counts") or reports.get("requests"),
        "queue": result.get("queue_summary") or reports.get("queue"),
        "empty_fixture_5260": reports.get("empty_fixture_5260"),
        "stats": None if "stats" not in reports else {
            key: reports["stats"][key]
            for key in (
                "unique_detail_requests",
                "nonempty_details",
                "empty_details",
                "details_with_text",
                "details_with_image",
                "unique_image_assets",
                "image_bytes",
                "missing_images",
                "source_broken",
            )
            if key in reports["stats"]
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    status = result.get("status")
    return 0 if status in {"READY", "READY_WITH_SOURCE_WARNINGS", "QUEUE_ONLY"} else 1


def cmd_detail_status(args: argparse.Namespace) -> int:
    snapshot = _snapshot_id(args)
    core = CoreDatabase(_P.SNAPSHOTS / snapshot / "core.db", readonly=True)
    detail_path = _P.ENRICHMENTS / snapshot / "detail.db"
    if not detail_path.exists():
        print(json.dumps({"core_snapshot": snapshot, "detail_db": None}, ensure_ascii=False, indent=2))
        core.close()
        return 1
    detail = DetailDatabase(detail_path, readonly=True)
    stats = build_detail_statistics(core, detail)
    print(json.dumps({
        "core_snapshot": snapshot,
        "core_points": stats["total_core_points"],
        "detail_requests": detail.request_counts(),
        "details": {
            "with_text": stats["details_with_text"],
            "with_image": stats["details_with_image"],
            "empty": stats["empty_details"],
        },
        "assets": {
            "total": stats["unique_image_assets"],
            "bytes": stats["image_bytes"],
            "missing": stats["missing_images"],
            "source_broken": stats["source_broken"],
        },
    }, ensure_ascii=False, indent=2))
    detail.close()
    core.close()
    return 0


def cmd_validate_details(args: argparse.Namespace) -> int:
    snapshot = _snapshot_id(args)
    core = CoreDatabase(_P.SNAPSHOTS / snapshot / "core.db", readonly=True)
    detail = DetailDatabase(_P.ENRICHMENTS / snapshot / "detail.db", readonly=True)
    extra = canary_source_ids(core, json.loads(_P.GOLDEN_PATH.read_text(encoding="utf-8")))
    result = validate_details_offline(core, detail, extra_source_ids=extra, sample_size=100)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    core.close()
    detail.close()
    return 0 if result["passed"] else 1


def cmd_serve(args: argparse.Namespace) -> int:
    """起本地服务。审核台与离线地图是两个分区（--app map|review|all），互不依赖。"""
    from hsrmap.serve import LANDING, resolve_kind, resolve_port, run_server

    kind = resolve_kind(getattr(args, "app", None))
    port = resolve_port(getattr(args, "port", None), kind)
    #: 起飞前检查在 run_server 里做（缺库/缺前端产物要给一条人话，而不是一段栈）。
    #: --no-browser：给无头/CI/自动化验证用（默认仍然是打开浏览器，双击 .bat 的人要的就是它）。
    return int(
        run_server(
            port=port,
            kind=kind,
            open_browser=not bool(getattr(args, "no_browser", False)),
        )
        or 0
    )


def _guide_paths(args: argparse.Namespace):
    from hsrmap.paths import GUIDE_ASSETS, GUIDE_DB, GUIDE_RAW

    db_path = Path(getattr(args, "db", None) or GUIDE_DB)
    raw = Path(getattr(args, "raw", None) or GUIDE_RAW)
    assets = Path(getattr(args, "assets", None) or GUIDE_ASSETS)
    return db_path, raw, assets


def _optional_viewer():
    """A viewer binding when the core DB is available, otherwise None."""
    try:
        from hsrmap.viewer_bind import bind_viewer

        return bind_viewer()
    except Exception:
        return None


#: 只读命令：打开 guide.db 时既不许建库也不许写（a1-8 四.2 的 read/write/create 三分）。
#: 其余命令允许在库不存在时建库（首次 ingest / import-page 之类的引导路径）。
READ_ONLY_GUIDES_COMMANDS = frozenset({
    "completeness", "closure-check", "published-audit", "atlas", "targets", "status",
    "ledger", "coverage", "topics", "search-log", "search-plan", "job-list", "job-status",
    "evidence-forms", "yield", "frontier", "asset-smoke", "identity", "review",
})


def _open_guide_db(path, *, write: bool):
    """按命令性质打开 guide.db（a1-8 四.2）。

    * 库在：readwrite / readonly 各自打开，都不建库；
    * 库不在且这是写入型命令：create（唯一允许建库的路径）；
    * 库不在且这是只读命令：报错退出（rc=1），绝不偷偷建一个空库让报告看起来像 0/0。
    """
    from hsrmap.guide_db import GuideDatabase as _DB

    target = Path(path)
    if target.is_file():
        return _DB.open_readwrite(target) if write else _DB.open_readonly(target)
    if write:
        return _DB.create(target)
    raise SystemExit(
        "guide database missing: %s（只读命令不建库：先用写入型命令建库，"
        "或用 --data-dir / HSRMAP_DATA_DIR 指向已有的数据目录）" % target
    )

def cmd_release(args: argparse.Namespace) -> int:
    """构建交付目录与交付包（a1-8 八；DoD 6）。"""
    from hsrmap.hygiene import DEFAULT_MAX_BYTES
    from hsrmap.paths import ROOT
    from hsrmap.release import build, render_report

    root = Path(getattr(args, "root", None) or ROOT)
    out = Path(getattr(args, "out", None) or (root / "submit"))
    zip_path = Path(getattr(args, "zip", None) or (root / "submit.zip"))
    report = build(
        root, out, zip_path,
        clean=not bool(getattr(args, "no_clean", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
        max_bytes=int(getattr(args, "max_bytes", 0) or 0) or DEFAULT_MAX_BYTES,
    )
    print(render_report(report))
    target = getattr(args, "json", None)
    if target:
        out_json = Path(target)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print("json:", out_json)
    return 0

def cmd_doctor(args: argparse.Namespace) -> int:
    """闭环自检：启动入口、端口、起飞前检查、首屏请求与载荷预算。"""
    from hsrmap.doctor import render_doctor, run_checks

    report = run_checks()
    if bool(getattr(args, "json_out", False)):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_doctor(report))
    target = getattr(args, "json", None)
    if target:
        out = Path(target)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print("json:", out)
    return 0 if report["ok"] else 2


def cmd_dod(args: argparse.Namespace) -> int:
    """Definition of Done（a1-8 十六）：12 项卫生化验收，逐项机器判定。"""
    from hsrmap.dod import render_dod, run_checks

    report = run_checks(
        require_data=bool(getattr(args, "require_data", False)),
        stamp=bool(getattr(args, "stamp", False)),
    )
    if bool(getattr(args, "json_out", False)):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_dod(report))
    target = getattr(args, "json", None)
    if target:
        out = Path(target)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print("json:", out)
    return 0 if report["ok"] else 2


def cmd_repo_hygiene(args: argparse.Namespace) -> int:
    """仓库卫生检查（a1-8 九）：只对**新增**违规失败，已知违规登记在基线里。"""
    from hsrmap.hygiene import (
        BASELINE_REL,
        DEFAULT_MAX_BYTES,
        check_tree,
        load_baseline,
        render_report,
        write_baseline,
    )
    from hsrmap.paths import ROOT

    baseline_path = Path(getattr(args, "baseline", None) or (ROOT / BASELINE_REL))
    max_bytes = int(getattr(args, "max_bytes", 0) or 0) or DEFAULT_MAX_BYTES
    if getattr(args, "update_baseline", False):
        report = check_tree(ROOT, baseline={}, max_bytes=max_bytes)
        payload = write_baseline(baseline_path, report["findings"], max_bytes=max_bytes)
        print(json.dumps({"ok": True, "baseline": str(baseline_path),
                          "entries": len(payload["entries"])}, ensure_ascii=False, indent=2))
        return 0
    report = check_tree(ROOT, baseline=load_baseline(baseline_path), max_bytes=max_bytes)
    print(render_report(report))
    target = getattr(args, "json", None)
    if target:
        out = Path(target)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "root": report["root"], "scanned_files": report["scanned_files"], "ok": report["ok"],
            "findings": [item.as_dict() for item in report["findings"]],
            "new": [item.as_dict() for item in report["new"]],
            "baselined": [item.as_dict() for item in report["baselined"]],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("json:", out)
    return 0 if report["ok"] else 2

def cmd_runtime(args: argparse.Namespace) -> int:
    """打印当前运行时根目录与来源（a1-8 四.2 的可观测入口）。"""
    from hsrmap.paths import runtime_report

    print(json.dumps(runtime_report(), ensure_ascii=False, indent=2))
    return 0


def cmd_guides(args: argparse.Namespace) -> int:
    from hsrmap.guide_db import GuideDatabase
    from hsrmap.guides.discover import describe, discover_urls, load_seeds
    from hsrmap.guides.pipeline import import_page
    from hsrmap.guides.review.queue import list_pending
    from hsrmap.guides.store import RawGuideStore

    db_path, raw, assets = _guide_paths(args)
    db = _open_guide_db(db_path, write=args.guides_cmd not in READ_ONLY_GUIDES_COMMANDS)
    store = RawGuideStore(raw, assets)
    cmd = args.guides_cmd
    #: 需要主题的命令默认 floating-grease（历史行为）；完成度报告是例外——
    #: 它默认看**全部启用主题**（a1-8 四.1：默认要回答整个问题，而不是回答一个子集）。
    topic = getattr(args, "topic", None) or ("" if cmd == "completeness" else "floating-grease")
    try:
        if cmd == "inventory":
            from hsrmap.guides.inventory.reports import build_inventory, load_selectable_labels, write_inventory_reports
            from hsrmap.viewer_bind import bind_viewer

            ctx = bind_viewer()
            try:
                labels = load_selectable_labels(ctx.core)
            finally:
                ctx.close()
            inventory = build_inventory(labels)
            out = write_inventory_reports(_P.DATA / "guides" / "reports", inventory)
            from hsrmap.guides.topics.seed import seed_topics

            seeded = seed_topics(db)
            migrated = db.migrate_point_entries_to_targets("floating_grease")
            print(
                json.dumps(
                    {k: inventory[k] for k in inventory if k != "labels"}
                    | {"reports": out, "topics_seeded": len(seeded), "targets_migrated": migrated},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if cmd == "topics":
            from hsrmap.guides.topics.loader import list_topics

            print(json.dumps({"topics": list_topics()}, ensure_ascii=False, indent=2))
            return 0
        if cmd == "discover":
            seeds = load_seeds(topic=topic)
            urls = discover_urls(seeds)
            print(json.dumps({
                "topic": topic,
                "queries": seeds.get("queries") or [],
                "urls": urls,
                "sources": [describe(url) for url in urls],
            }, ensure_ascii=False, indent=2))
            return 0
        if cmd == "status":
            from hsrmap.guides.topics.official import topic_status

            print(json.dumps(topic_status(db, topic.replace("-", "_")), ensure_ascii=False, indent=2))
            return 0
        if cmd == "targets":
            from hsrmap.guides.topics.seed import seed_official_targets, seed_topics

            seed_topics(db)
            n = seed_official_targets(db, topic.replace("-", "_"))
            print(json.dumps({"topic": topic, "targets": n, "publish": "skipped"}, ensure_ascii=False, indent=2))
            return 0
        if cmd == "atlas":
            from hsrmap.guides.topics.loader import list_topics
            from hsrmap.guides.topics.official import topic_status
            from hsrmap.viewer_bind import bind_viewer

            ctx = bind_viewer()
            try:
                topics = [topic_status(db, item["topic_key"], ctx=ctx) for item in list_topics()]
            finally:
                ctx.close()
            print(json.dumps({"topics": topics, "publish": "never_auto"}, ensure_ascii=False, indent=2))
            return 0
        if cmd == "import-page":
            html = Path(args.html).read_text(encoding="utf-8")
            result = import_page(html, args.url, db, store, topic=topic.replace("-", "_"))
            print(json.dumps({"page": result["page"], "blocks": len(result["blocks"]), "status": result["page"]["crawl_status"]}, ensure_ascii=False, indent=2, default=str))
            return 0
        if cmd == "fetch":
            print(json.dumps({"status": "NEEDS_MANUAL_IMPORT", "hint": "use guides import-page for local HTML"}, ensure_ascii=False))
            return 0
        if cmd == "parse":
            pages = [dict(row) for row in db.conn.execute("SELECT id, canonical_url, crawl_status FROM guide_page")]
            print(json.dumps({"pages": pages}, ensure_ascii=False, indent=2))
            return 0
        if cmd == "extract":
            from hsrmap.guides.derived import DerivedGuideStore
            from hsrmap.guides.extract.blocks import html_to_blocks
            from hsrmap.guides.llm.provider import build_provider, hallucinated_steps
            from hsrmap.paths import GUIDE_DERIVED

            page_id = getattr(args, "page", None)
            if not page_id:
                print(json.dumps({"error": "--page required"}, ensure_ascii=False))
                return 1
            provider = build_provider(getattr(args, "provider", None) or "fake")
            row = db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page_id,)).fetchone()
            if row is None:
                print(json.dumps({"error": "page not found", "page_id": page_id}, ensure_ascii=False))
                return 1
            page = dict(row)
            html = Path(page["raw_html_path"]).read_text(encoding="utf-8") if page.get("raw_html_path") else ""
            blocks = html_to_blocks(html)
            extracted = provider.extract_sections(blocks, page_id=page_id, topic=topic.replace("-", "_"))
            derived = DerivedGuideStore(GUIDE_DERIVED if not getattr(args, "raw", None) else Path(args.raw) / "derived")
            derived.save_section_extraction(page_id, extracted)
            invented = hallucinated_steps(extracted, blocks)
            print(json.dumps({
                "page_id": page_id,
                "provider": getattr(args, "provider", None) or "fake",
                "section_count": len(extracted.get("sections") or []),
                "hallucinated_steps": len(invented),
                "json_valid": True,
                "publish": "skipped",
            }, ensure_ascii=False, indent=2))
            return 0
        if cmd == "reingest-page":
            from hsrmap.guides.reingest import reingest_page as run_reingest

            page_id = getattr(args, "page", None)
            if not page_id:
                print(json.dumps({"error": "--page required"}, ensure_ascii=False))
                return 1
            result = run_reingest(
                db,
                int(page_id),
                topic=topic,
                store=store,
                cache_root=getattr(args, "cache", None),
                ctx=_optional_viewer(),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if "error" not in result else 1
        if cmd == "image-filter":
            from hsrmap.guides.assets.relevance import filter_images
            from hsrmap.guides.extract.blocks import html_to_blocks

            page_id = getattr(args, "page", None)
            if not page_id:
                print(json.dumps({"error": "--page required"}, ensure_ascii=False))
                return 1
            row = db.conn.execute("SELECT * FROM guide_page WHERE id = ?", (page_id,)).fetchone()
            if row is None:
                print(json.dumps({"error": "page not found", "page_id": page_id}, ensure_ascii=False))
                return 1
            page = dict(row)
            html = Path(page["raw_html_path"]).read_text(encoding="utf-8", errors="replace") if page.get("raw_html_path") else ""
            blocks = html_to_blocks(html)
            assets = {
                str(item["source_url"]): dict(item)
                for item in db.conn.execute(
                    "SELECT source_url, width, height, sha256, phash FROM guide_asset_cache"
                )
            }
            images = []
            for block in blocks:
                if block.get("type") != "image":
                    continue
                cached = assets.get(str(block.get("src") or "")) or {}
                images.append({
                    "url": block.get("src"),
                    "alt": block.get("alt"),
                    "sha256": cached.get("sha256") or block.get("asset") or "",
                    "phash": cached.get("phash") or "",
                    "width": cached.get("width"),
                    "height": cached.get("height"),
                })
            result = filter_images(images)
            print(json.dumps({
                "page_id": page_id,
                "images": len(images),
                "kept": result["kept_count"],
                "dropped": result["dropped_count"],
                "reasons": result["reasons"],
                "kept_sample": [item["url"] for item in result["kept"][:5]],
                "dropped_sample": [
                    {"url": item["url"], "why": item["relevance"]["reasons"]}
                    for item in result["dropped"][:5]
                ],
            }, ensure_ascii=False, indent=2))
            return 0
        if cmd == "ai-cache":
            from hsrmap.guides.ai_cache import AICache

            cache = AICache(db=db)
            prompt_version = getattr(args, "invalidate_prompt", None)
            model = getattr(args, "invalidate_model", None)
            removed = 0
            if prompt_version or model:
                removed = cache.invalidate(prompt_version=prompt_version, model=model)
            payload = {"stats": cache.stats(), "invalidated": removed}
            if prompt_version:
                payload["prompt_version"] = prompt_version
            if model:
                payload["model"] = model
            json_path = getattr(args, "json", None)
            if json_path:
                Path(json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                payload["json"] = str(json_path)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if cmd == "no-public-source":
            from hsrmap.guides.nops import mark_no_public_source

            result = mark_no_public_source(
                db,
                topic=topic.replace("-", "_"),
                min_runs=int(getattr(args, "min_runs", 1) or 1),
                apply=bool(getattr(args, "apply", False)),
                limit=int(getattr(args, "limit", 0) or 0) or None,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if cmd == "review-approve":
            from hsrmap.guides.review.service import approve_anchored, approve_grounded
            from hsrmap.guides.topics.official import official_maps_for_topic, official_points_for_topic

            key = topic.replace("-", "_")
            ctx = _optional_viewer()
            try:
                points = list(official_points_for_topic(key, ctx=ctx) or [])
            except Exception:
                points = []
            apply_flag = bool(getattr(args, "apply", False))
            limit = int(getattr(args, "limit", 0) or 0) or None
            if bool(getattr(args, "text_only", False)):
                try:
                    maps = list(official_maps_for_topic(key, ctx=ctx) or [])
                except Exception:
                    maps = []
                result = approve_grounded(
                    db,
                    topic=key,
                    official_points=points,
                    maps=maps,
                    apply=apply_flag,
                    limit=limit,
                )
            else:
                result = approve_anchored(
                    db,
                    topic=key,
                    official_points=points,
                    apply=apply_flag,
                    limit=limit,
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if cmd == "revive-thin":
            from hsrmap.guides.revive import revive_thin
            from hsrmap.paths import GUIDE_PUBLISHED_DB

            published = GuideDatabase(Path(GUIDE_PUBLISHED_DB))
            try:
                result = revive_thin(
                    db,
                    apply=bool(getattr(args, "apply", False)),
                    limit=int(getattr(args, "limit", 0) or 0) or None,
                    store=store,
                    ctx=_optional_viewer(),
                    published=published,
                )
            finally:
                published.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if cmd == "dedupe-entries":
            from hsrmap.guides.dedupe import dedupe_entries

            result = dedupe_entries(
                db,
                apply=bool(getattr(args, "apply", False)),
                limit=int(getattr(args, "limit", 0) or 0) or None,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if cmd == "rebuild-quarantined":
            from hsrmap.guides.rebuild import rebuild_quarantined
            from hsrmap.guides.topics.loader import list_topics
            from hsrmap.guides.topics.official import official_maps_for_topic, official_points_for_topic

            ctx = _optional_viewer()
            points: dict[str, list[dict]] = {}
            maps: dict[str, list[dict]] = {}
            displays: dict[str, str] = {}
            point_topics: dict[str, str] = {}
            for item in list_topics(enabled_only=True):
                topic_key = str(item["topic_key"])
                displays[topic_key] = str(item.get("display_name") or topic_key)
                try:
                    points[topic_key] = list(official_points_for_topic(topic_key, ctx=ctx) or [])
                except Exception:
                    points[topic_key] = []
                for point in points[topic_key]:
                    pid = str(point.get("source_point_id") or "")
                    if pid:
                        point_topics.setdefault(pid, topic_key)
                try:
                    maps[topic_key] = list(official_maps_for_topic(topic_key, ctx=ctx) or [])
                except Exception:
                    maps[topic_key] = []
            result = rebuild_quarantined(
                db,
                store=store,
                apply=bool(getattr(args, "apply", False)),
                limit=int(getattr(args, "limit", 0) or 0) or None,
                points=points,
                maps=maps,
                displays=displays,
                point_topics=point_topics,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if cmd == "job-status":
            from hsrmap.guides.jobs import job_status, list_jobs

            job_id = getattr(args, "job", None)
            if job_id is None:
                rows = list_jobs(
                    db,
                    topic=topic.replace("-", "_") if topic not in {"all", "*"} else None,
                    state=getattr(args, "state", None) or None,
                    limit=int(getattr(args, "limit", 0) or 10),
                )
                print(json.dumps({"jobs": rows}, ensure_ascii=False, indent=2, default=str))
                return 0
            print(json.dumps(job_status(db, int(job_id)), ensure_ascii=False, indent=2, default=str))
            return 0
        if cmd == "job-list":
            from hsrmap.guides.jobs import list_jobs

            rows = list_jobs(
                db,
                topic=topic.replace("-", "_") if topic not in {"all", "*"} else None,
                state=getattr(args, "state", None) or None,
                limit=int(getattr(args, "limit", 0) or 20),
            )
            print(json.dumps(
                {
                    "jobs": [
                        {
                            "job_id": row["job_id"],
                            "job_type": row["job_type"],
                            "topic": row["topic"],
                            "state": row["state"],
                            "counters": row.get("counters") or {},
                            "error": row.get("error") or "",
                            "updated_at": row.get("updated_at"),
                        }
                        for row in rows
                    ]
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ))
            return 0
        if cmd == "claims":
            from hsrmap.guides.claims import backfill, claim_digest, summary as claims_summary

            if getattr(args, "backfill", False):
                out = backfill(db, apply=bool(getattr(args, "apply", False)))
                out["digest"] = claim_digest(db) if getattr(args, "apply", False) else None
            else:
                out = {"summary": claims_summary(db), "digest": claim_digest(db)}
            json_path = getattr(args, "json", None)
            if json_path:
                Path(json_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                out["json"] = str(json_path)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if cmd == "search-record":
            from hsrmap.guides.workflows import WorkflowError, record_search_payload

            spec_path = getattr(args, "json", None)
            if not spec_path:
                raise SystemExit("search-record 需要 --json <载荷文件>（schema_version/topic/query/results）")
            try:
                payload = json.loads(Path(spec_path).read_text(encoding="utf-8"))
                out = record_search_payload(db, payload, dry_run=bool(getattr(args, "dry_run", False)))
            except WorkflowError as exc:
                raise SystemExit(f"search-record 载荷不合法：{exc}") from exc
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if cmd == "evidence-transcribe":
            from hsrmap.guides.topics.official import official_points_for_topic
            from hsrmap.guides.workflows import WorkflowError, transcribe_payload
            from hsrmap.paths import GUIDE_ASSETS

            spec_path = getattr(args, "spec", None) or getattr(args, "json", None)
            if not spec_path:
                raise SystemExit("evidence-transcribe 需要 --spec <载荷文件>")
            try:
                payload = json.loads(Path(spec_path).read_text(encoding="utf-8"))
                #: 先校验（不需要快照/官方点位），确认没问题再取官方点位并真正发布。
                checked = transcribe_payload(
                    db, payload,
                    assets_root=Path(getattr(args, "assets", None) or GUIDE_ASSETS),
                    dry_run=True,
                )
                if not getattr(args, "apply", False):
                    out = checked
                else:
                    out = transcribe_payload(
                        db, payload,
                        assets_root=Path(getattr(args, "assets", None) or GUIDE_ASSETS),
                        dry_run=False,
                        official_points=official_points_for_topic(str(payload.get("topic_key") or "")),
                    )
            except WorkflowError as exc:
                raise SystemExit(f"evidence-transcribe 载荷不合法：{exc}") from exc
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if cmd == "search-log":
            from hsrmap.guides.evidence import searches_for
            from hsrmap.guides.evidence import summary as evidence_summary

            key = None if topic in {"all", "*"} else topic.replace("-", "_")
            payload = {
                "summary": evidence_summary(db, topic=key),
                "searches": searches_for(
                    db,
                    topic=key,
                    target_key=getattr(args, "target", None) or None,
                    limit=int(getattr(args, "limit", 0) or 20),
                ),
            }
            json_path = getattr(args, "json", None)
            if json_path:
                Path(json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                payload["json"] = str(json_path)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if cmd == "search-plan":
            from hsrmap.guides.planner import discovery_plan
            from hsrmap.guides.topics.official import official_maps_for_topic

            ctx = _optional_viewer()
            key = topic.replace("-", "_")
            try:
                maps = list(official_maps_for_topic(key, ctx=ctx) or [])
            except Exception:
                maps = []
            plan = discovery_plan(
                db,
                key,
                ctx=ctx,
                official_maps=maps,
                limit=int(getattr(args, "limit", 0) or 10),
                include_searched=not bool(getattr(args, "no_searched", False)),
            )
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        if cmd == "completeness":
            from hsrmap.guides.stages import completeness_markdown, completeness_report

            key = topic.replace("-", "_")
            #: --stats：把「这次算花了多少条 SQL / 多少毫秒」一起报出来（a1-8 十二的性能验收）。
            report = completeness_report(
                db,
                topics=[key] if topic else None,
                profile=bool(getattr(args, "stats", False)),
                lookup=str(getattr(args, "target_lookup", "") or "auto"),
            )
            rows = report.pop("rows", [])
            #: 工作队列视图：--missing solve / locate 直接给出「还缺哪一种证据」的点位。
            want = str(getattr(args, "missing", "") or "")
            if want == "solve":
                rows = [row for row in rows if str(row["status"]) == "SOLVE_MISSING"]
            elif want == "locate":
                rows = [row for row in rows if str(row["status"]) in {"LOCATE_MISSING", "SCOPE_ONLY", "NO_EVIDENCE"}]
            elif want == "any":
                rows = [row for row in rows if not row["done"]]
            if getattr(args, "markdown", False):
                print(completeness_markdown(report))
                return 0
            if want:
                #: 过滤视图只给工作队列：两张清单换成条数，明细在 rows 里。
                report["missing_locate"] = len(report.get("missing_locate") or [])
                report["missing_solve"] = len(report.get("missing_solve") or [])
            if want or getattr(args, "limit", 0):
                limit = int(getattr(args, "limit", 0) or 0)
                report["rows"] = rows[:limit] if limit else rows
                report["rows_filtered"] = len(rows)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if cmd == "target-shadow":
            from hsrmap.guides.targets import relations_in_sync, shadow_compare, sync_relations
            from hsrmap.guides.topics.loader import list_topics
            from hsrmap.guides.topics.official import official_points_for_topic

            apply_relations = bool(getattr(args, "apply", False))
            plan = sync_relations(db, apply=apply_relations)
            state = relations_in_sync(db)
            points: list[str] = []
            if not bool(getattr(args, "no_points", False)):
                for item in list_topics(enabled_only=True):
                    try:
                        found = official_points_for_topic(str(item["topic_key"]), ctx=_optional_viewer()) or []
                    except Exception:  # noqa: BLE001 - 一个主题读不到不该让对比失败
                        continue
                    points += [
                        str(point["source_point_id"])
                        for point in found
                        if point.get("source_point_id")
                    ]
            compare = shadow_compare(db, sorted(set(points)))
            print(json.dumps(
                {"plan": plan, "relations": state, "shadow": compare},
                ensure_ascii=False, indent=2,
            ))
            #: 退出码契约：0 = 报告出来了；2 = 关系表还没资格切（缺绑定或对比不一致）。
            if not state["in_sync"] or not compare["equal"]:
                return 2 if apply_relations else 0
            return 0
        if cmd == "official-seed":
            from hsrmap.guides.official import seed_official_guides

            report = seed_official_guides(
                db,
                topic.replace("-", "_"),
                apply=bool(getattr(args, "apply", False)),
                limit=int(getattr(args, "limit", 0) or 0),
                all_points=bool(getattr(args, "all_points", False)),
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if cmd == "evidence-forms":
            from hsrmap.guides.ledger import topic_ledger
            from hsrmap.guides.topics.evidence import profile_table
            from hsrmap.guides.topics.official import official_points_for_topic

            rows = []
            for row in profile_table():
                key = row["topic_key"]
                try:
                    points = official_points_for_topic(key, ctx=_optional_viewer()) or []
                    ledger = topic_ledger(db, key, official_points=points)
                    gaps = int(ledger.get("no_source_yet") or 0)
                except Exception:  # noqa: BLE001 - a report must not fail on one topic
                    gaps = -1
                rows.append({**row, "needs_source": gaps})
            print(json.dumps({"topics": rows}, ensure_ascii=False, indent=2))
            return 0
        if cmd == "frontier":
            from hsrmap.guides.planner import candidates_from_evidence, candidates_from_seeds, frontier

            key = topic.replace("-", "_")
            candidates = candidates_from_evidence(db, topic=key)
            if getattr(args, "include_seeds", False):
                candidates += candidates_from_seeds(key)
            rows = frontier(db, candidates, limit=int(getattr(args, "limit", 0) or 20))
            print(json.dumps(
                {"topic": key, "candidates": len(candidates), "frontier": rows},
                ensure_ascii=False,
                indent=2,
            ))
            return 0
        if cmd == "yield":
            from hsrmap.guides.planner import source_yield, target_yield

            key = topic.replace("-", "_")
            print(json.dumps(
                {
                    "sources": source_yield(db),
                    "target": target_yield(db, key, ctx=_optional_viewer()),
                },
                ensure_ascii=False,
                indent=2,
            ))
            return 0
        if cmd == "identity":
            from hsrmap.guides.crawler.identity import ArticleFamilyResolver

            urls = [str(url) for url in (getattr(args, "url", None) or [])]
            resolver = ArticleFamilyResolver()
            print(json.dumps({
                "articles": [resolver.resolve(url).as_dict() for url in urls],
                "families": resolver.group(urls),
                "unique": resolver.unique(urls),
                "duplicates": resolver.duplicates(urls),
            }, ensure_ascii=False, indent=2))
            return 0
        if cmd == "asset-dedup":
            from hsrmap.guides.assets.cache import AssetCache
            from hsrmap.guides.assets.phash import SIMILARITY_THRESHOLD
            from hsrmap.paths import GUIDE_CACHE

            root = Path(args.cache) if getattr(args, "cache", None) else GUIDE_CACHE
            threshold = int(getattr(args, "threshold", 0) or SIMILARITY_THRESHOLD)
            groups = AssetCache(root, db=db).visual_duplicates(threshold=threshold)
            limit = int(getattr(args, "limit", 0) or 10)
            print(json.dumps({
                "threshold": threshold,
                "groups": len(groups),
                "assets": sum(len(group) for group in groups),
                "sample": groups[:limit],
            }, ensure_ascii=False, indent=2))
            return 0
        if cmd == "chrome-prune":
            from hsrmap.guides.audit import prune_chrome_steps

            result = prune_chrome_steps(
                db,
                apply=bool(getattr(args, "apply", False)),
                quarantine_empty=bool(getattr(args, "quarantine_empty", False)),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if cmd == "refresh-text":
            from hsrmap.guides.extract.refresh import refresh_page_text

            result = refresh_page_text(
                db,
                store,
                page_id=getattr(args, "page", None),
                apply=bool(getattr(args, "apply", False)),
                topic=topic.replace("-", "_"),
            )
            changes = result.pop("changes", [])
            result["change_preview"] = changes[:20]
            result["change_count"] = len(changes)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if cmd == "ingest":
            from hsrmap.guides.ingest import ingest_page
            from hsrmap.guides.llm.provider import build_provider
            from hsrmap.guides.topics.official import official_maps_for_topic, official_points_for_topic

            html = Path(args.html).read_text(encoding="utf-8")
            key = topic.replace("-", "_")
            result = ingest_page(
                html,
                args.url,
                db,
                store,
                build_provider(getattr(args, "provider", None) or "deterministic"),
                topic=key,
                fetch_asset=lambda src: b"PNG-" + src.encode(),
                official_points=official_points_for_topic(key),
                official_maps=official_maps_for_topic(key),
            )
            print(json.dumps({
                "page_id": result["page"]["id"],
                "qa": {k: result["qa"][k] for k in result["qa"] if k != "blocks"},
                "review_status": [item["status"] for item in result["review"]],
                "hallucinated_steps": result["hallucinated_steps"],
                "publish": result["publish"],
            }, ensure_ascii=False, indent=2, default=str))
            return 0
        if cmd == "corpus":
            from hsrmap.guides.corpus import import_real_urls
            from hsrmap.guides.discover import discover_urls, load_seeds
            from hsrmap.guides.ledger import WaveLocked, assert_wave_unlocked, atlas_gates
            from hsrmap.paths import GUIDE_PUBLISHED_DB

            if str(getattr(args, "wave", None) or "") == "all":
                published = GuideDatabase(Path(getattr(args, "published", None) or GUIDE_PUBLISHED_DB))
                try:
                    assert_wave_unlocked(atlas_gates(db, published), force=bool(getattr(args, "force_development", False)))
                except WaveLocked as exc:
                    print(json.dumps({"error": str(exc), "hint": "use --force-development"}, ensure_ascii=False, indent=2))
                    return 2

            seeds = load_seeds(topic=topic)
            urls = discover_urls(seeds)
            # curated/discovered candidates come first: the frontier already
            # decided they are worth fetching (a1-6 §六/§八)
            urls = [str(url) for url in (getattr(args, "url", None) or [])] + urls
            if not urls:
                print(json.dumps({"topic": topic, "imported": [], "reason": "no seed urls", "publish": "skipped"}, ensure_ascii=False, indent=2))
                return 0
            budget = {
                key: value
                for key, value in (
                    ("max_pages", getattr(args, "max_pages", None)),
                    ("max_assets", getattr(args, "max_assets", None)),
                    ("max_bytes", getattr(args, "max_bytes", None)),
                    ("max_runtime", getattr(args, "max_runtime", None)),
                    ("max_failures", getattr(args, "max_failures", None)),
                )
                if value is not None
            }
            reports = import_real_urls(
                urls,
                db=db,
                store=store,
                topic=topic,
                skip_known=not bool(getattr(args, "refresh", False)),
                job_id=getattr(args, "resume", None),
                budget=budget or None,
                report_dir=Path(args.report_dir) if getattr(args, "report_dir", None) else None,
            )
            counts: dict[str, int] = {}
            for item in reports:
                key = str(item.get("status") or "?")
                counts[key] = counts.get(key, 0) + 1
            print(json.dumps({
                "topic": topic,
                "counts": counts,
                "imported": reports,
                "publish": "skipped",
            }, ensure_ascii=False, indent=2, default=str))
            return 0
        if cmd == "process":
            from hsrmap.guides.ledger import WaveLocked, assert_wave_unlocked, atlas_gates
            from hsrmap.paths import GUIDE_PUBLISHED_DB

            force = bool(getattr(args, "force_development", False))
            published = GuideDatabase(Path(getattr(args, "published", None) or GUIDE_PUBLISHED_DB))
            try:
                assert_wave_unlocked(atlas_gates(db, published), force=force)
            except WaveLocked as exc:
                print(json.dumps({"error": str(exc), "hint": "use --force-development"}, ensure_ascii=False, indent=2))
                return 2
            print(json.dumps({"wave": getattr(args, "wave", None), "status": "unlocked"}, ensure_ascii=False))
            return 0
        if cmd == "ledger":
            from hsrmap.guides.ledger import atlas_gates, materialize_ledger, topic_ledger
            from hsrmap.guides.topics.official import official_points_for_topic
            from hsrmap.paths import GUIDE_PUBLISHED_DB

            published = GuideDatabase(Path(getattr(args, "published", None) or GUIDE_PUBLISHED_DB))
            key = topic.replace("-", "_")
            report = topic_ledger(db, key, official_points=official_points_for_topic(key), published_db=published)
            materialize_ledger(db, report)
            print(json.dumps({"ledger": report, "gates": atlas_gates(db, published)}, ensure_ascii=False, indent=2))
            return 0
        if cmd == "coverage":
            from hsrmap.guides.coverage import build_coverage
            from hsrmap.guides.topics.loader import get_topic
            from hsrmap.guides.topics.official import official_points_for_topic

            spec = get_topic(topic.replace("-", "_"))
            points = official_points_for_topic(spec["topic_key"])
            official = [row["source_point_id"] for row in points]
            report = build_coverage(db, official, [{"source_point_id": pid} for pid in official])
            report["topic"] = spec["topic_key"]
            report["display_name"] = spec.get("display_name")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if cmd == "match":
            from hsrmap.guides.matching.matcher import match_sections
            from hsrmap.guides.topics.loader import get_topic
            from hsrmap.guides.topics.official import official_maps_for_topic

            spec = get_topic(topic.replace("-", "_"))
            maps = official_maps_for_topic(spec["topic_key"])
            label = spec.get("display_name") or spec["topic_key"]
            tokens = list((spec.get("official_labels") or {}).get("names") or [label])
            points = []
            blocks = []
            official_n = 0
            for item in maps:
                heading = item["name"] or item.get("path") or item["map_id"]
                blocks.append({"type": "heading", "text": heading})
                for index, point in enumerate(item["points"], start=1):
                    official_n += 1
                    points.append(
                        {
                            "source_point_id": point["source_id"],
                            "map_name": item["name"],
                            "map_id": item["map_id"],
                            "path": item.get("path") or item["name"] or item["map_id"],
                            "name": point.get("label") or label,
                            "label": point.get("label") or label,
                        }
                    )
                    blocks.append({"type": "paragraph", "text": f"第{index}处 {label}"})
            bindings = match_sections(blocks, points, label_tokens=tokens)
            print(json.dumps({
                "topic": spec["topic_key"],
                "official_points": official_n,
                "matched": len({item["source_point_id"] for item in bindings}),
                "unresolved": official_n - len({item["source_point_id"] for item in bindings}),
                "publish": "skipped",
            }, ensure_ascii=False, indent=2))
            return 0
        if cmd == "review":
            print(json.dumps({"pending": list_pending(db)}, ensure_ascii=False, indent=2, default=str))
            return 0
        if cmd == "ticker-bind":
            from hsrmap.guides.llm.provider import build_provider
            from hsrmap.guides.topics.dream_ticker.bind import bind_dream_ticker
            from hsrmap.guides.topics.official import official_points_for_topic

            provider_name = getattr(args, "provider", None)
            provider = None
            if provider_name:
                provider = build_provider(provider_name, missing_ok=True)
            points = official_points_for_topic("dream_ticker")
            loader = None
            try:
                from hsrmap.guides.topics.official import official_image_bytes
                from hsrmap.viewer_bind import bind_viewer

                ctx = bind_viewer()

                def loader(source_id: str) -> bytes:
                    return official_image_bytes(ctx, source_id)
            except Exception:
                ctx = None
            try:
                report = bind_dream_ticker(
                    db,
                    official_points=points,
                    provider=provider,
                    canary_only=not getattr(args, "all_items", False),
                    official_image_loader=loader,
                )
            finally:
                if ctx is not None:
                    ctx.close()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if cmd == "rematch":
            from hsrmap.guides.matching.rematch import rematch_topic
            from hsrmap.guides.topics.official import official_points_for_topic

            key = topic.replace("-", "_")
            points = official_points_for_topic(key)
            print(json.dumps(rematch_topic(db, key, official_points=points), ensure_ascii=False, indent=2))
            return 0
        if cmd == "backfill-topics":
            from hsrmap.guides.topics.backfill import backfill_page_topics

            print(json.dumps(backfill_page_topics(db), ensure_ascii=False, indent=2))
            return 0
        if cmd == "closure-check":
            from hsrmap.guide_db import GuideDatabase as GuideDB
            from hsrmap.guides.closure import closure_check, render_closure
            from hsrmap.guides.publishing.diff import load_waivers
            from hsrmap.paths import GUIDE_PUBLISHED_DB

            dest = Path(getattr(args, "published", None) or GUIDE_PUBLISHED_DB)
            ctx = None
            if not getattr(args, "no_core_check", False):
                try:
                    from hsrmap.viewer_bind import bind_viewer

                    ctx = bind_viewer()
                except Exception:
                    ctx = None
            published = GuideDB(dest)
            try:
                report = closure_check(
                    db,
                    published,
                    ctx=ctx,
                    assets_root=assets,
                    waivers=load_waivers(
                        Path(args.waivers) if getattr(args, "waivers", None) else None
                    ),
                    e2e=not bool(getattr(args, "no_e2e", False)),
                )
            finally:
                if ctx is not None:
                    ctx.close()
                published.close()
            print(render_closure(report))
            target = getattr(args, "json", None)
            if target:
                out = Path(target)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                print("json:", out)
            return 0 if report["closure"] == "PASS" else 2
        if cmd == "published-audit":
            from hsrmap.guide_db import GuideDatabase as GuideDB
            from hsrmap.guides.audit import audit_entries, fix_empty_steps
            from hsrmap.guides.publishing.diff import entry_snapshot
            from hsrmap.paths import GUIDE_PUBLISHED_DB

            dest = Path(getattr(args, "published", None) or GUIDE_PUBLISHED_DB)
            published = GuideDB(dest)
            try:
                entries = entry_snapshot(published)
            finally:
                published.close()
            report = audit_entries(entries, db, assets_root=assets)
            report["empty_steps"] = fix_empty_steps(
                db, apply=bool(getattr(args, "fix_empty_steps", False))
            )
            report["published_db"] = str(dest)
            target = getattr(args, "json", None)
            if target:
                out = Path(target)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                report["json"] = str(out)
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return 0
        if cmd == "signature-merge":
            from hsrmap.guides.review.merge import merge_duplicates

            report = merge_duplicates(db, apply=bool(getattr(args, "apply", False)))
            # a1-6 §14 asks for the metrics, not for "signature implemented"
            metrics = {
                key: report[key]
                for key in (
                    "applied",
                    "review_items_before",
                    "review_items_after",
                    "merged_items",
                    "unique_signatures",
                    "unique_target_candidates",
                    "duplicate_groups",
                    "duplicate_ratio",
                    "merge_ratio",
                )
                if key in report
            }
            path = Path(_P.DATA) / "guides" / "reports" / "signature-merge.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
            report["metrics"] = metrics
            report["report"] = str(path)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if cmd == "qa-rescan":
            from pathlib import Path as _Path

            from hsrmap.guides.assets import AssetCache, AssetFetcher
            from hsrmap.guides.qa import evaluate_import, inspect_import, record_qa
            from hsrmap.guides.pipeline import import_page
            from hsrmap.paths import GUIDE_CACHE

            hosts = [item.strip() for item in str(getattr(args, "hosts", "") or "").split(",") if item.strip()]
            limit = int(getattr(args, "limit", 0) or 0)
            apply_changes = bool(getattr(args, "apply", False))
            where = ["raw_html_path IS NOT NULL"]
            params: list[Any] = []
            if hosts:
                where.append("(" + " OR ".join("canonical_url LIKE ?" for _ in hosts) + ")")
                params.extend(f"%//%{host}/%" for host in hosts)
            sql = (
                "SELECT id, canonical_url, raw_html_path, qa_status FROM guide_page WHERE "
                + " AND ".join(where)
                + " ORDER BY id"
                + (" LIMIT ?" if limit else "")
            )
            if limit:
                params.append(limit)
            cache = AssetCache(_Path(GUIDE_CACHE), db=db)
            fetcher = AssetFetcher(cache=cache)
            rows: list[dict[str, Any]] = []
            for page in [dict(row) for row in db.conn.execute(sql, params)]:
                html_file = _Path(page["raw_html_path"])
                if not html_file.exists():
                    rows.append({"page_id": page["id"], "url": page["canonical_url"], "before": page["qa_status"], "after": "QA_FAIL", "reason": "PARSER_ERROR", "note": "raw html missing"})
                    continue
                html = html_file.read_text(encoding="utf-8", errors="replace")
                asset_log: list[dict[str, Any]] = []

                def fetch_asset(src: str, _page=page["canonical_url"]) -> bytes:
                    outcome = fetcher.fetch(src, _page)
                    asset_log.append(outcome.to_report())
                    return outcome.body if outcome.ok else b""

                imported = import_page(html, page["canonical_url"], db, store, topic=topic, fetch_asset=fetch_asset)
                qa = inspect_import(imported, store)
                status, reason = evaluate_import(qa)
                if apply_changes:
                    record_qa(db, page["id"], status, reason)
                rows.append({
                    "page_id": page["id"],
                    "url": page["canonical_url"],
                    "before": page["qa_status"],
                    "after": status,
                    "reason": reason,
                    "blocks": len(qa.get("blocks") or []),
                    "image_blocks": qa.get("image_blocks"),
                    "assets_ok": qa.get("images_in_guide_assets"),
                    "assets_fetched": sum(1 for item in asset_log if item["status"] in {"FETCHED", "CACHE_HIT"}),
                })
            summary: dict[str, int] = {}
            for row in rows:
                key = f"{row.get('before')} -> {row['after']}"
                summary[key] = summary.get(key, 0) + 1
            print(json.dumps({"applied": apply_changes, "pages": len(rows), "transitions": summary, "rows": rows}, ensure_ascii=False, indent=2))
            return 0
        if cmd == "qa-quarantine":
            apply_changes = bool(getattr(args, "apply", False))
            rows = [
                dict(row)
                for row in db.conn.execute(
                    """
                    SELECT ri.id, ri.page_id, ri.status, p.qa_status, p.qa_reason, p.canonical_url
                    FROM review_item ri JOIN guide_page p ON p.id = ri.page_id
                    WHERE p.qa_status = 'QA_FAIL'
                      AND IFNULL(p.qa_override_by, '') = ''
                      AND ri.status <> 'SOURCE_REJECTED'
                    ORDER BY ri.id
                    """
                )
            ]
            by_reason: dict[str, int] = {}
            for row in rows:
                key = str(row["qa_reason"] or "QA_FAIL")
                by_reason[key] = by_reason.get(key, 0) + 1
            if apply_changes and rows:
                for row in rows:
                    db.conn.execute(
                        "UPDATE review_item SET status = 'SOURCE_REJECTED', reason = ? WHERE id = ?",
                        (f"upstream_qa_failed: {row['qa_reason'] or 'QA_FAIL'}", row["id"]),
                    )
                db.conn.commit()
            print(json.dumps({
                "applied": apply_changes,
                "items": len(rows),
                "by_reason": by_reason,
                "sample": [{"id": row["id"], "page": row["page_id"], "status": row["status"], "qa_reason": row["qa_reason"]} for row in rows[:8]],
            }, ensure_ascii=False, indent=2))
            return 0
        if cmd == "asset-smoke":
            from hsrmap.guides.assets import AssetCache, AssetFetcher
            from hsrmap.guides.assets.smoke import render_matrix, smoke_matrix, verdict
            from hsrmap.paths import GUIDE_CACHE

            cache = AssetCache(Path(getattr(args, "cache", None) or GUIDE_CACHE), db=db)
            fetcher = AssetFetcher(cache=cache)
            hosts = [item.strip() for item in str(getattr(args, "hosts", "")).split(",") if item.strip()]
            matrix = smoke_matrix(
                db,
                hosts,
                limit=int(getattr(args, "limit", 8)),
                fetcher=fetcher,
                refresh=bool(getattr(args, "refresh", False)),
            )
            matrix["verdict"] = verdict(matrix)
            print(render_matrix(matrix))
            print()
            print(json.dumps(matrix, ensure_ascii=False, indent=2))
            return 0
        if cmd == "publish-snapshot":
            from hsrmap.guide_db import GuideDatabase as GuideDB
            from hsrmap.guides.publishing.diff import (
                PublishBlocked,
                assert_publishable,
                load_waivers,
                snapshot_diff,
                snapshot_manifest,
                write_diff_reports,
            )
            from hsrmap.guides.publishing.sync import sync_published
            from hsrmap.paths import GUIDE_CACHE, GUIDE_PUBLISHED_DB

            dest = Path(getattr(args, "published", None) or GUIDE_PUBLISHED_DB)
            report_path = Path(args.report) if getattr(args, "report", None) else None
            #: 报告跟着这次发布的库走（生产 = data/guides/reports，测试 = tmp_path/reports）。
            #: 以前用的是模块导入时算出的常量，测试里的 monkeypatch 拦不住，会把真实报告目录写花。
            reports_dir = dest.parent / "reports"
            manifest_path = reports_dir / "snapshot-manifest.json"
            markdown_path = reports_dir / "snapshot-diff.md"
            allow_drop = bool(getattr(args, "allow_coverage_drop", False))
            force = bool(getattr(args, "force", False))
            ctx = None
            if not getattr(args, "no_core_check", False):
                try:
                    from hsrmap.viewer_bind import bind_viewer

                    ctx = bind_viewer()
                except Exception:
                    ctx = None
            waivers = load_waivers(
                Path(args.waivers) if getattr(args, "waivers", None) else None
            )
            published = GuideDB(dest)
            try:
                report = snapshot_diff(db, published, ctx=ctx, assets_root=assets, waivers=waivers)
                try:
                    assert_publishable(report, allow_coverage_drop=allow_drop, force=force)
                except PublishBlocked as exc:
                    out = write_diff_reports({**exc.report, "blocked": True}, json_path=report_path, md_path=markdown_path)
                    print(json.dumps({
                        "ok": False,
                        "blocked": True,
                        "reasons": exc.reasons,
                        "gate": report.get("gate"),
                        "change_counts": report.get("change_counts"),
                        "entries": report["entries"]["counts"],
                        "coverage": report["coverage"]["regressions"],
                        "report": out["json"],
                        "markdown": out["markdown"],
                        "published_db": str(dest),
                        "auto_approve": False,
                    }, ensure_ascii=False, indent=2))
                    return 2
                if getattr(args, "dry_run", False):
                    out = write_diff_reports({**report, "dry_run": True}, json_path=report_path, md_path=markdown_path)
                    Path(manifest_path).write_text(
                        json.dumps(
                            snapshot_manifest(published, ctx=ctx), ensure_ascii=False, indent=1, default=str
                        ),
                        encoding="utf-8",
                    )
                    print(json.dumps({
                        "ok": True,
                        "dry_run": True,
                        "copied": 0,
                        "gate": report.get("gate"),
                        "change_counts": report.get("change_counts"),
                        "entries": report["entries"]["counts"],
                        "coverage": report["coverage"]["regressions"],
                        "report": out["json"],
                        "markdown": out["markdown"],
                        "manifest": str(manifest_path),
                        "published_db": str(dest),
                        "auto_approve": False,
                    }, ensure_ascii=False, indent=2))
                    return 0
                if not getattr(args, "in_place", False):
                    from hsrmap.guides.publishing.atomic import publish_atomic

                    out = write_diff_reports(report, json_path=report_path, md_path=markdown_path)
                    published.close()
                    result = publish_atomic(
                        db,
                        target=dest,
                        ctx=ctx,
                        assets_root=assets,
                        waivers=waivers,
                        allow_coverage_drop=allow_drop,
                        force=force,
                    )
                    result["report"] = out["json"]
                    result["markdown"] = out["markdown"]
                    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                    return 0 if result.get("ok") else 2
                copied = sync_published(db, published)
                after = snapshot_diff(
                    db, published, ctx=ctx, assets_root=assets, coverage=False, gates=False
                )
                verified = {
                    "entries_match": after["entries"]["counts"] == {"added": 0, "removed": 0, "changed": 0},
                    "missing_assets": after["assets"]["missing_files"],
                }
                out = write_diff_reports({**report, "copied": copied, "verified": verified}, json_path=report_path, md_path=markdown_path)
                Path(manifest_path).write_text(
                    json.dumps(
                        snapshot_manifest(published, ctx=ctx), ensure_ascii=False, indent=1, default=str
                    ),
                    encoding="utf-8",
                )
                print(json.dumps({
                    "ok": bool(verified["entries_match"]),
                    "copied": copied,
                    "gate": report.get("gate"),
                    "change_counts": report.get("change_counts"),
                    "entries": report["entries"]["counts"],
                    "coverage": report["coverage"]["regressions"],
                    "verified": verified,
                    "report": out["json"],
                    "markdown": out["markdown"],
                    "manifest": str(manifest_path),
                    "published_db": str(dest),
                    "auto_approve": False,
                }, ensure_ascii=False, indent=2))
                return 0
            finally:
                if ctx is not None:
                    ctx.close()
                published.close()
        if cmd == "publish":
            print(json.dumps({"ok": False, "reason": "publish requires review confirmation"}, ensure_ascii=False))
            return 0
        if cmd == "sync":
            urls = discover_urls(load_seeds(topic=topic))
            print(json.dumps({
                "topic": topic,
                "discovered": urls,
                "offline": bool(getattr(args, "offline", False)),
                "publish": "skipped",
            }, ensure_ascii=False, indent=2))
            return 0
        raise SystemExit(f"unknown guides command: {cmd}")
    finally:
        db.close()


def use_utf8_output() -> None:
    """把标准输出切到 UTF-8（Windows 控制台默认 GBK）。

    中文提示在 GBK 控制台上会变成乱码——用户看到的就是「入口打不开、只有一堆问号」。
    顺手把 PYTHONIOENCODING 写进环境，子进程（审计、pytest、build.py）跟着一致。
    """
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 切不动就照旧输出，不因为编码让命令失败
            pass


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    data_dir = prescan_data_dir(argv)
    if data_dir:
        #: 运行目录要在任何运行态 import 之前生效：先写环境变量，懒加载的 paths 会看到它。
        os.environ[ENV_DATA_DIR] = data_dir
    parser = argparse.ArgumentParser(prog="hsrmap")
    parser.add_argument("--data-dir", default=None, help="运行时数据目录（默认：HSRMAP_DATA_DIR → 仓库 data/ → 用户数据目录）")
    parser.add_argument("--quiet", action="store_true", help="成功也不打印（退出码仍然表达结果）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    runtime_cmd = sub.add_parser("runtime")
    runtime_cmd.set_defaults(func=cmd_runtime)
    release_cmd = sub.add_parser("release")
    release_cmd.add_argument("--root", help="源码树根（默认仓库根；测试用）")
    release_cmd.add_argument("--out", help="交付目录（默认 <root>/submit）")
    release_cmd.add_argument("--zip", help="交付包（默认 <root>/submit.zip）")
    release_cmd.add_argument("--dry-run", action="store_true", help="只列出会收录的文件")
    release_cmd.add_argument("--no-clean", action="store_true", help="不先清空交付目录")
    release_cmd.add_argument("--max-bytes", type=int, default=0, help="超大文件阈值（默认 2 MB）")
    release_cmd.add_argument("--json", help="把报告写成 JSON")
    release_cmd.set_defaults(func=cmd_release)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", help="把完整报告写成 JSON")
    doctor.add_argument("--json-out", action="store_true", help="直接把 JSON 打到标准输出")
    doctor.set_defaults(func=cmd_doctor)
    dod = sub.add_parser("dod")
    dod.add_argument("--require-data", action="store_true", help="没有数据的项算失败，而不是跳过")
    dod.add_argument("--stamp", action="store_true", help="先给认得出 schema 的旧库补上 user_version")
    dod.add_argument("--json", help="把完整报告写成 JSON")
    dod.add_argument("--json-out", action="store_true", help="直接把 JSON 打到标准输出")
    dod.set_defaults(func=cmd_dod)
    hygiene = sub.add_parser("repo-hygiene")
    hygiene.add_argument("--update-baseline", action="store_true", help="把当前违规写成新的基线")
    hygiene.add_argument("--baseline", help="基线文件路径（默认 tools/hygiene_baseline.json）")
    hygiene.add_argument("--max-bytes", type=int, default=0, help="oversized 阈值（默认 2 MB）")
    hygiene.add_argument("--json", help="把完整报告写成 JSON")
    hygiene.set_defaults(func=cmd_repo_hygiene)
    sync = sub.add_parser("sync")
    sync.add_argument("--resume", action="store_true")
    sync.set_defaults(func=cmd_sync)
    validate = sub.add_parser("validate")
    validate.add_argument("snapshot", nargs="?")
    validate.add_argument("--offline", action="store_true")
    validate.set_defaults(func=cmd_validate)
    inspect_p = sub.add_parser("inspect")
    inspect_p.add_argument("map")
    inspect_p.set_defaults(func=cmd_inspect)
    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)
    staging = sub.add_parser("staging")
    staging.add_argument("staging_cmd", choices=["list", "clean"])
    staging.set_defaults(func=cmd_staging)
    enrich = sub.add_parser("enrich-details")
    enrich.add_argument("--snapshot")
    enrich.add_argument("--canary", action="store_true")
    enrich.add_argument("--resume", action="store_true")
    enrich.add_argument("--retry-failed", action="store_true")
    enrich.add_argument("--queue-only", action="store_true")
    enrich.add_argument("--semantic-key")
    enrich.add_argument("--map-id")
    enrich.add_argument("--point-id")
    enrich.set_defaults(func=cmd_enrich_details)
    detail_status = sub.add_parser("detail-status")
    detail_status.add_argument("--snapshot")
    detail_status.set_defaults(func=cmd_detail_status)
    validate_details = sub.add_parser("validate-details")
    validate_details.add_argument("--snapshot")
    validate_details.add_argument("--offline", action="store_true")
    validate_details.set_defaults(func=cmd_validate_details)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--app", default="map", choices=("map", "review", "all"),
                       help="map=离线地图（8766）/ review=审核台（8767）/ all=同一个进程")
    serve.add_argument("--no-browser", action="store_true", help="只起服务，不打开浏览器")
    serve.set_defaults(func=cmd_serve)
    guides = sub.add_parser("guides")
    guides.add_argument("--data-dir", default=None, help="运行时数据目录（同顶层开关）")
    guides.add_argument("--quiet", action="store_true", help="成功也不打印")
    gsub = guides.add_subparsers(dest="guides_cmd", required=True)
    for name in ("inventory", "topics", "discover", "fetch", "parse", "extract", "match", "rematch", "ticker-bind", "review", "publish", "publish-snapshot", "backfill-topics", "sync", "coverage", "corpus", "status", "targets", "atlas", "ledger", "process", "asset-smoke", "qa-rescan", "qa-quarantine", "signature-merge", "refresh-text", "chrome-prune", "identity", "asset-dedup", "search-log", "search-plan", "search-record", "evidence-transcribe", "claims", "frontier", "yield", "evidence-forms", "official-seed", "completeness", "target-shadow", "no-public-source", "job-status", "job-list", "rebuild-quarantined", "review-approve", "ai-cache", "image-filter", "reingest-page", "published-audit", "closure-check", "dedupe-entries", "revive-thin"):
        item = gsub.add_parser(name)
        item.add_argument("--topic", default="floating-grease")
        item.add_argument("--db")
        item.add_argument("--raw")
        item.add_argument("--assets")
        item.add_argument("--offline", action="store_true")
        item.add_argument("--provider")
        item.add_argument("--published")
        if name == "asset-smoke":
            item.add_argument("--cache")
        if name == "extract":
            item.add_argument("--page", type=int)
        if name == "ticker-bind":
            item.add_argument("--all-items", action="store_true")
        if name in {"process", "corpus"}:
            item.add_argument("--wave")
            item.add_argument("--force-development", action="store_true")
        if name == "corpus":
            item.add_argument("--url", action="append", default=[])
            item.add_argument("--resume", type=int)
            item.add_argument("--max-pages", type=int)
            item.add_argument("--max-assets", type=int)
            item.add_argument("--max-bytes", type=int)
            item.add_argument("--max-runtime", type=float)
            item.add_argument("--max-failures", type=int)
            item.add_argument("--report-dir")
        if name in {"job-status", "job-list"}:
            item.add_argument("--job", type=int)
            item.add_argument("--state")
            item.add_argument("--limit", type=int, default=0)
        if name == "official-seed":
            item.add_argument("--all", dest="all_points", action="store_true")
        if name == "completeness":
            #: 完成度报告默认看**全部启用主题**：不给 --topic 时不该只剩 floating-grease 的 48 个点位
            #: （a1-8 四.1 命令契约：默认要回答整个问题，而不是回答一个子集）。
            item.set_defaults(topic=None)
            item.add_argument("--missing", choices=("locate", "solve", "any"))
            item.add_argument("--markdown", action="store_true")
            item.add_argument("--limit", type=int, default=0)
        if name in {"rebuild-quarantined", "dedupe-entries", "revive-thin", "official-seed"}:
            item.add_argument("--apply", action="store_true")
            item.add_argument("--limit", type=int, default=0)
        if name == "review-approve":
            item.add_argument("--apply", action="store_true")
            item.add_argument("--text-only", action="store_true")
            item.add_argument("--limit", type=int, default=0)
        if name == "no-public-source":
            item.add_argument("--apply", action="store_true")
            item.add_argument("--limit", type=int, default=0)
            item.add_argument("--min-runs", type=int, default=1)
        if name in {"image-filter", "reingest-page"}:
            item.add_argument("--page", type=int)
        if name == "ai-cache":
            item.add_argument("--invalidate-prompt")
            item.add_argument("--invalidate-model")
            item.add_argument("--json")
        if name == "corpus":
            item.add_argument("--refresh", action="store_true")
        if name == "asset-smoke":
            item.add_argument("--hosts", default="3dmgame.com,9game.cn,17173.com")
            item.add_argument("--limit", type=int, default=8)
            item.add_argument("--refresh", action="store_true")
        if name == "qa-rescan":
            item.add_argument("--hosts", default="")
            item.add_argument("--limit", type=int, default=0)
            item.add_argument("--apply", action="store_true")
        if name == "qa-quarantine":
            item.add_argument("--apply", action="store_true")
        if name == "signature-merge":
            item.add_argument("--apply", action="store_true")
        if name == "refresh-text":
            item.add_argument("--page", type=int)
            item.add_argument("--apply", action="store_true")
        if name == "chrome-prune":
            item.add_argument("--apply", action="store_true")
            item.add_argument("--quarantine-empty", action="store_true")
        if name == "identity":
            item.add_argument("--url", action="append", default=[])
        if name in {"search-log", "search-plan", "frontier", "yield"}:
            item.add_argument("--limit", type=int, default=0)
        if name == "reingest-page":
            item.add_argument("--cache")
        if name == "claims":
            item.add_argument("--backfill", action="store_true", help="按当前语料重建证据声明")
            item.add_argument("--apply", action="store_true", help="backfill 时不加只干跑")
            item.add_argument("--json", help="把结果写成 JSON")
        if name == "search-record":
            item.add_argument("--json", help="检索载荷（schema_version/topic/query/results）")
            item.add_argument("--dry-run", action="store_true")
        if name == "evidence-transcribe":
            item.add_argument("--spec", help="转录载荷（schema_version/topic_key/target_key/page/steps）")
            item.add_argument("--apply", action="store_true", help="不写 --apply 时只做校验（dry-run）")
        if name == "search-log":
            item.add_argument("--target")
            item.add_argument("--json")
        if name == "search-plan":
            item.add_argument("--no-searched", action="store_true")
        if name == "frontier":
            item.add_argument("--include-seeds", action="store_true")
        if name == "asset-dedup":
            item.add_argument("--cache")
            item.add_argument("--threshold", type=int, default=0)
            item.add_argument("--limit", type=int, default=0)
        if name == "published-audit":
            item.add_argument("--fix-empty-steps", action="store_true")
            item.add_argument("--json")
        if name == "closure-check":
            item.add_argument("--json")
            item.add_argument("--waivers")
            item.add_argument("--no-e2e", action="store_true")
            item.add_argument("--no-core-check", action="store_true")
        if name == "target-shadow":
            item.add_argument("--apply", action="store_true")
            item.add_argument("--no-points", action="store_true")
        if name == "completeness":
            item.add_argument("--stats", action="store_true")
            #: auto = 关系表齐就走关系表，否则走字符串键（两条路 shadow 对比见 target-shadow）。
            item.add_argument("--target-lookup", choices=("auto", "string", "relation"), default="auto")
        if name == "publish-snapshot":
            item.add_argument("--dry-run", action="store_true")
            item.add_argument("--allow-coverage-drop", action="store_true")
            item.add_argument("--force", action="store_true")
            item.add_argument("--report")
            item.add_argument("--waivers")
            item.add_argument("--in-place", action="store_true")
            item.add_argument("--no-core-check", action="store_true")
        item.set_defaults(func=cmd_guides)
    ingest = gsub.add_parser("ingest")
    ingest.add_argument("html")
    ingest.add_argument("--url", required=True)
    ingest.add_argument("--topic", default="floating-grease")
    ingest.add_argument("--provider", default="deterministic")
    ingest.add_argument("--db")
    ingest.add_argument("--raw")
    ingest.add_argument("--assets")
    ingest.set_defaults(func=cmd_guides)
    imp = gsub.add_parser("import-page")
    imp.add_argument("html")
    imp.add_argument("--url", required=True)
    imp.add_argument("--topic", default="floating-grease")
    imp.add_argument("--db")
    imp.add_argument("--raw")
    imp.add_argument("--assets")
    imp.set_defaults(func=cmd_guides)
    args = parser.parse_args(argv)
    #: 子解析器的同名默认值会覆盖顶层参数，所以两个开关都以 argv 为准（a1-8 四.1）。
    chosen_dir = getattr(args, "data_dir", None) or data_dir
    if chosen_dir:
        set_runtime(resolve_runtime(chosen_dir))
    if bool(getattr(args, "quiet", False)) or "--quiet" in argv:
        #: 显式 --quiet 才允许「rc=0 且无输出」，其它情况必须打印结果。
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - module entry point (a1-8 四.1)
    #: 没有这个守卫时 python -m hsrmap.cli ... 会「成功」但什么也不做（rc=0、无输出、无副作用），
    #: 是最危险的一种静默失败。有它之后两个入口（-m hsrmap / -m hsrmap.cli）行为一致。
    raise SystemExit(main())
