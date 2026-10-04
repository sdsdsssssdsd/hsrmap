from __future__ import annotations

import mimetypes
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from hsrmap.guide_db import GuideDatabase
from hsrmap.live_assets import LiveAssetStore
from hsrmap.paths import GUIDE_ASSETS, GUIDE_DB, GUIDE_PUBLISHED_DB, LIVE_CACHE, ROOT, USER_DB
from hsrmap.user_db import UserDatabase
from hsrmap.providers.factory import build_provider
from hsrmap.providers.hybrid import LIVE_ERRORS, NETWORK_MESSAGE
from hsrmap.providers.live import live_asset_key
from hsrmap.viewer_bind import SnapshotMismatchError, ViewerContext, bind_viewer
from hsrmap.viewer_repo import (
    asset_path,
    meta_payload,
    search_payload,
    settings_payload,
    update_check_payload,
)

WEB_DIST = ROOT / "web" / "dist"
CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'"


def _default_http():
    from hsrmap.http import RateLimitedClient

    return RateLimitedClient(min_interval=0.3)


#: 可挂载的分区名。
SECTIONS = ("map", "review")

#: 四个路由组的说明（**实例在工厂里创建**，见 `create_app`）：
#:
#: * `map_router`：离线地图；
#: * `guide_router`：**只读**攻略查询面——地图页的点位抽屉要显示「有没有攻略 / 为什么算完成」，
#:   那是读，不是审核。它跟着两边走，但只读，永远不会让地图进程拿到写权限（a1-8 十三）；
#: * `review_router`：审核台（唯一有写端点的一组）；
#: * `shared_router`：健康检查、用户标记、设置、更新检查。
#:
#: 路由组**必须**是每个 app 自己的一份：模块级的 APIRouter 会在第二次 create_app 时
#: 累积重复路由，而先注册的那条闭包指向上一个 app 的 state——一个进程里建两个 app
#: （测试、offline E2E 都是这样）时，第二个 app 会拿着第一个 app 的数据库句柄。


def create_app(
    *,
    user_path: Path | None = None,
    guide_path: Path | None = None,
    published_path: Path | None = None,
    guide_assets: Path | None = None,
    data_mode: str = "offline",
    live_client=None,
    live_session=None,
    live_cache: Path | None = None,
    sections: tuple[str, ...] = SECTIONS,
) -> FastAPI:
    unknown = [item for item in sections if item not in SECTIONS]
    if unknown:
        raise ValueError(f"unknown section(s): {unknown} (expected any of {SECTIONS})")
    want_map = "map" in sections
    want_review = "review" in sections
    #: 每个 app 一份路由组（见文件头说明）：共享实例会让第二个 app 用第一个 app 的 state。
    map_router = APIRouter()
    guide_router = APIRouter()
    review_router = APIRouter()
    shared_router = APIRouter()
    app = FastAPI(title="hsrmap viewer kernel", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    app.state.user = UserDatabase(Path(user_path or USER_DB))
    working = Path(guide_path or GUIDE_DB)
    if published_path is not None:
        published = Path(published_path)
    elif guide_path is not None:
        published = Path(guide_path).with_name("published.db")
    else:
        published = GUIDE_PUBLISHED_DB
    #: 审核台要写 working 库（approve/create_item），但**不许建库**；published 库只读（a1-8 四.2）。
    #: 只跑离线地图时不碰 working 库：map-only 进程没有 guide.db 也能起来。
    #: 但**发布快照是只读的**，地图页的点位抽屉要用它显示攻略与证据；没有快照时如实报
    #: `available: false`，而不是让地图起不来。
    app.state.guide_assets = Path(guide_assets or GUIDE_ASSETS)
    if want_review:
        app.state.guide = GuideDatabase.open_readwrite(working)
    else:
        app.state.guide = None
    try:
        app.state.published = GuideDatabase.open_readonly(published)
    except FileNotFoundError:
        app.state.published = None
    app.state.data_mode = data_mode
    if want_map:
        app.state.live_store = LiveAssetStore(Path(live_cache or LIVE_CACHE) / "assets")
        app.state.live_client = live_client
        app.state.live_session = live_session
    else:
        app.state.live_store = None
        app.state.live_client = None
        app.state.live_session = None
    app.state.live_index: dict[str, str] = {}
    app.state.bind_lock = threading.Lock()

    def raster_fetch(url: str):
        client = app.state.live_client or _default_http()
        path = app.state.live_store.fetch(client, url)
        app.state.live_index[live_asset_key(url)] = url
        if path.stat().st_size == 0:
            raise RuntimeError("empty raster")
        return path

    def make_provider(ctx: ViewerContext):
        return build_provider(
            ctx,
            mode=app.state.data_mode,
            live_client=app.state.live_client,
            live_session=app.state.live_session,
            raster_fetch=raster_fetch,
        )

    def get_ctx() -> ViewerContext:
        with app.state.bind_lock:
            ctx = getattr(app.state, "ctx", None)
            if ctx is None:
                try:
                    ctx = bind_viewer()
                except FileNotFoundError as exc:
                    #: 运行目录里还没有 current.json / core.db：这是「快照未就绪」，不是 500。
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "message": "快照未就绪：先跑 python -m hsrmap sync（或用 --data-dir 指向已有数据目录）",
                            "error": str(exc),
                        },
                    ) from exc
                app.state.ctx = ctx
            if getattr(app.state, "provider", None) is None:
                app.state.provider = make_provider(ctx)
            return ctx

    def get_provider():
        get_ctx()
        return app.state.provider

    def call_provider(fn):
        try:
            return fn()
        except LIVE_ERRORS as exc:
            raise HTTPException(status_code=503, detail={"message": NETWORK_MESSAGE, "error": str(exc)}) from exc

    def data_source_payload() -> dict:
        get_ctx()
        prov = app.state.provider.provenance()
        offline = app.state.data_mode == "offline"
        return {
            "mode": app.state.data_mode,
            "source": prov.get("source"),
            "fetched_at": prov.get("fetched_at"),
            "snapshot_id": get_ctx().snapshot_id,
            "stale": bool(prov.get("stale")),
            "bundle_sha256": prov.get("bundle_sha256"),
            "bundle_changed": bool(prov.get("bundle_changed")),
            "remote_enabled": not offline,
            "message": "离线模式，不访问外网" if offline else prov.get("message"),
            "snapshot_command": "python -m hsrmap sync",
        }

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @shared_router.get("/health")
    def health():
        #: 健康检查不许因为「快照还没同步」而 500：报状态就行（审核台进程甚至没有快照）。
        ctx = getattr(app.state, "ctx", None)
        if ctx is None:
            try:
                ctx = get_ctx()
            except Exception:
                ctx = None
        return {
            "status": "ok",
            "core": "READY" if ctx is not None else "MISSING",
            "detail": getattr(ctx, "detail_state", None),
            "user": "READY",
            "guide": "READY" if app.state.guide is not None else "NOT_MOUNTED",
        }

    @map_router.get("/api/v1/meta")
    def meta():
        return meta_payload(get_ctx())

    @map_router.get("/api/v1/debug/golden-20")
    def golden_20():
        from hsrmap.paths import GOLDEN_PATH
        import json as json_lib

        return json_lib.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    @map_router.get("/api/v1/search")
    def search(q: str = ""):
        return search_payload(get_ctx(), q)

    @map_router.get("/api/v1/data-source")
    def data_source():
        return data_source_payload()

    @map_router.put("/api/v1/data-source")
    def set_data_source(body: dict):
        mode = body.get("mode") or "offline"
        if mode not in {"offline", "hybrid", "live"}:
            raise HTTPException(400, "invalid mode")
        app.state.data_mode = mode
        app.state.user.set_meta("data_mode", mode)
        ctx = get_ctx()
        app.state.provider = make_provider(ctx)
        return data_source_payload()

    @map_router.get("/api/v1/maps/{map_id}/revision")
    def map_revision(map_id: str):
        provider = get_provider()
        hasher = getattr(provider, "point_list_hash", None)
        if hasher is None and hasattr(provider, "live"):
            hasher = getattr(provider.live, "point_list_hash", None)
        digest = hasher(map_id) if callable(hasher) else None
        return {"map_id": map_id, "hash": digest, "source": provider.provenance().get("source")}

    @map_router.get("/api/v1/live-assets/{key}")
    def live_asset(key: str):
        url = app.state.live_index.get(key)
        if not url:
            raise HTTPException(404, "asset not cached")
        path = app.state.live_store.fetch(app.state.live_client or _default_http(), url)
        mime, _ = mimetypes.guess_type(str(path))
        return FileResponse(path, media_type=mime or "application/octet-stream")

    @review_router.get("/api/v1/topics/{topic}")
    def topic_by_key(topic: str):
        key = topic.replace("-", "_")
        from hsrmap.guides.topics.official import official_payload_for_topic

        try:
            return official_payload_for_topic(key, ctx=get_ctx())
        except KeyError as exc:
            raise HTTPException(404, "unknown topic") from exc

    @shared_router.get("/api/v1/settings")
    def settings():
        return settings_payload(get_ctx())

    @shared_router.get("/api/v1/updates/check")
    def updates_check():
        return update_check_payload()

    @shared_router.get("/api/v1/user/export")
    def user_export():
        return app.state.user.export_payload()

    @shared_router.post("/api/v1/user/import")
    def user_import(body: dict):
        return app.state.user.import_payload(body)

    @shared_router.get("/api/v1/user/points")
    def user_points():
        return {"points": app.state.user.list_points()}

    @shared_router.get("/api/v1/user/points/{source_point_id}")
    def user_point(source_point_id: str):
        return app.state.user.get_point(source_point_id)

    @shared_router.put("/api/v1/user/points/{source_point_id}")
    def save_user_point(source_point_id: str, body: dict):
        return app.state.user.upsert_point(source_point_id, body)

    @guide_router.get("/api/v1/guides/by-point/{source_point_id}")
    def guides_for_point(source_point_id: str):
        from hsrmap.guides.publish.lookup import resolve_guide_keys

        if app.state.published is None:
            return {"entries": [], "available": False}
        keys = resolve_guide_keys(get_ctx(), source_point_id)
        return {"entries": app.state.published.list_for_keys(keys), "available": True}

    @guide_router.get("/api/v1/guides/index")
    def guides_index():
        from hsrmap.guides.publish.lookup import expand_guide_index

        if app.state.published is None:
            return {"points": {}, "available": False}
        return {
            "points": expand_guide_index(get_ctx(), app.state.published.list_published_index()),
            "available": True,
        }

    @guide_router.get("/api/v1/guides/atlas")
    def guides_atlas():
        from hsrmap.guides.topics.official import atlas_payload

        if app.state.published is None:
            return {"topics": [], "available": False}
        return atlas_payload(app.state.published, ctx=get_ctx())

    @guide_router.get("/api/v1/guides/evidence")
    def guides_evidence(topic: str | None = None):
        """证据总览（a1-8 十三）：完成度分层 + 声明等级 + digest。"""
        from hsrmap.guides.claims import overview

        if app.state.published is None:
            return {"available": False}
        return {
            "available": True,
            **overview(app.state.published, topics=[topic.replace("-", "_")] if topic else None),
        }

    @guide_router.get("/api/v1/guides/evidence/{source_point_id}")
    def point_evidence(source_point_id: str):
        """点位详情要的「为什么算完成」：完成状态 + 每条攻略的逐步证据（a1-8 十三）。"""
        from hsrmap.guides.claims import point_evidence_view
        from hsrmap.guides.stages import point_report

        from hsrmap.guides.stages import EntryIndex

        if app.state.published is None:
            return {"point": source_point_id, "available": False, "status": None, "entries": []}
        #: 判定行可能为空（这个点位不在官方点位表里，例如本地攻略），但**攻略本身还是要显示**。
        status = point_report(app.state.published, source_point_id)
        #: 用**判定层同一个索引**挑条目：set 键点名了这个点位的攻略也算数，
        #: 否则详情页会漏掉真正提供解法的那条（而完成度却说它算完成）。
        index = EntryIndex(app.state.published, lookup="auto")
        entries = app.state.published.entries_by_ids(
            [int(row["id"]) for row in index.matching(source_point_id)]
        )
        problems: dict[int, list[dict[str, Any]]] = {}
        if app.state.guide is not None and entries:
            #: 审计要读来源页正文，只有审核进程手里有 working 库；地图进程如实说「没有审计状态」。
            from hsrmap.guides.audit import audit_entries
            from hsrmap.guides.publishing.diff import entry_snapshot

            candidate = entry_snapshot(app.state.published)
            keep = {int(row["id"]): candidate[int(row["id"])] for row in entries if int(row["id"]) in candidate}
            if keep:
                report = audit_entries(keep, app.state.guide, assets_root=app.state.guide_assets)
                for finding in report.get("findings") or []:
                    problems.setdefault(int(finding["guide_id"]), []).extend(finding.get("problems") or [])
        return {
            "point": source_point_id,
            "available": True,
            "status": status,
            "audit_available": app.state.guide is not None,
            "entries": point_evidence_view(app.state.published, entries=entries, problems=problems),
        }

    @review_router.get("/api/v1/atlas/gates")
    def atlas_gates():
        from hsrmap.guides.ledger import atlas_gates as build

        return build(app.state.guide, app.state.published, ctx=get_ctx())

    @review_router.get("/api/v1/atlas/topics")
    def atlas_topics():
        from hsrmap.guides.admin import topic_admin_rows

        return {"topics": topic_admin_rows(app.state.guide, app.state.published, ctx=get_ctx())}

    @review_router.get("/api/v1/atlas/coverage")
    def atlas_coverage(topic: str | None = None):
        from hsrmap.guides.ledger import topic_ledger
        from hsrmap.guides.topics.loader import list_topics
        from hsrmap.guides.topics.official import official_points_for_topic

        keys = (
            [topic.replace("-", "_")]
            if topic
            else [str(item["topic_key"]) for item in list_topics(enabled_only=True)]
        )
        rows = []
        for key in keys:
            try:
                points = official_points_for_topic(key, ctx=get_ctx())
            except Exception:
                points = []
            rows.append(topic_ledger(app.state.guide, key, official_points=points, published_db=app.state.published))
        return {"topics": rows}

    @review_router.get("/api/v1/atlas/review")
    def atlas_review():
        from hsrmap.guides.admin import review_admin

        return review_admin(app.state.guide)

    @review_router.get("/api/v1/atlas/sources")
    def atlas_sources():
        from hsrmap.guides.admin import sources_admin

        return sources_admin(app.state.guide)

    @review_router.get("/api/v1/atlas/snapshots")
    def atlas_snapshots():
        from hsrmap.guides.admin import snapshots_admin
        from hsrmap.paths import DATA

        return snapshots_admin(DATA / "guides" / "reports")

    @review_router.get("/api/v1/atlas/ledger")
    def atlas_ledger(topic: str):
        from hsrmap.guides.ledger import topic_ledger
        from hsrmap.guides.topics.official import official_points_for_topic

        key = topic.replace("-", "_")
        return topic_ledger(
            app.state.guide,
            key,
            official_points=official_points_for_topic(key, ctx=get_ctx()),
            published_db=app.state.published,
        )

    @review_router.post("/api/v1/guides")
    def create_guide(body: dict):
        if not body.get("source_point_id"):
            raise HTTPException(400, "source_point_id required")
        entry = app.state.guide.create_entry(body)
        if (body.get("status") or "") == "published":
            from hsrmap.guides.publishing.sync import copy_entry

            copy_entry(app.state.guide, app.state.published, int(entry["id"]))
        return entry

    def _official_catalog(topic: str | None = None):
        from hsrmap.guides.topics.loader import list_topics
        from hsrmap.guides.topics.official import official_points_for_topic

        ctx = get_ctx()
        keys = [topic.replace("-", "_")] if topic else [item["topic_key"] for item in list_topics(enabled_only=True)]
        out = []
        for key in keys:
            try:
                out.extend(official_points_for_topic(key, ctx=ctx))
            except Exception:
                continue
        return out

    @review_router.get("/api/v1/guides/topics")
    def guides_topics():
        from hsrmap.guides.topics.loader import list_topics

        return {"topics": list_topics()}

    @review_router.get("/api/v1/review/items")
    def review_items(topic: str | None = None, include_items: int = 0, full: int = 0):
        """审核队列（默认 slim）。

        `include_items=1` 带上逐页原始记录（127 MB，默认不带）；
        `full=1` 让每一行都带上 draft/布局/图片数组（9.8 MB，默认由
        `/api/v1/review/maps/{item_id}` 按行取）。
        """
        from hsrmap.guides.review.service import attach_official_thumbs, list_review_payload

        #: 没有快照时也要能看队列：官方候选图 / 缩略图是加分项，不是列表的前提。
        try:
            catalog = _official_catalog(topic)
        except Exception:  # noqa: BLE001 - 快照未就绪
            catalog = []
        try:
            ctx = get_ctx()
        except Exception:  # noqa: BLE001 - 快照未就绪
            ctx = None
        payload = list_review_payload(
            app.state.guide,
            catalog,
            topic=topic,
            include_items=bool(include_items),
            slim=not bool(full),
        )
        return attach_official_thumbs(payload, ctx)

    @review_router.get("/api/v1/review/maps/{item_id}")
    def review_map_row(item_id: int):
        """单行详情：列表页选中某一行时补齐 draft / 布局 / 图片数组。"""
        from hsrmap.guides.review.service import attach_official_thumbs, map_row_for_item

        try:
            catalog = _official_catalog(None)
        except Exception:  # noqa: BLE001 - 快照未就绪
            catalog = []
        try:
            ctx = get_ctx()
        except Exception:  # noqa: BLE001 - 快照未就绪
            ctx = None
        try:
            row = map_row_for_item(app.state.guide, item_id, catalog)
        except KeyError as exc:
            raise HTTPException(404, "review item not found") from exc
        if row is None:
            raise HTTPException(404, "review item not found")
        return attach_official_thumbs({"maps": [row]}, ctx)["maps"][0]

    @review_router.post("/api/v1/review/items")
    def review_create(body: dict):
        from hsrmap.guides.review.service import create_item

        return create_item(app.state.guide, body)

    @review_router.get("/api/v1/review/items/{item_id}")
    def review_get(item_id: int):
        from hsrmap.guides.review.service import get_item

        try:
            return get_item(app.state.guide, item_id)
        except KeyError as exc:
            raise HTTPException(404, "review item not found") from exc

    @review_router.patch("/api/v1/review/items/{item_id}")
    def review_patch(item_id: int, body: dict):
        from hsrmap.guides.review.service import patch_item

        return patch_item(app.state.guide, item_id, body)

    @review_router.post("/api/v1/review/items/{item_id}/approve")
    def review_approve(item_id: int):
        from hsrmap.guides.review.service import approve_item

        try:
            return approve_item(
                app.state.guide,
                item_id,
                official_points=_official_catalog() or None,
                published_db=app.state.published,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @review_router.post("/api/v1/review/items/{item_id}/reject")
    def review_reject(item_id: int):
        from hsrmap.guides.review.service import reject_item

        return reject_item(app.state.guide, item_id)

    @review_router.get("/review")
    def review_console():
        path = ROOT / "hsrmap" / "guides" / "review" / "console.html"
        if not path.exists():
            return HTMLResponse("<!doctype html><title>review</title><p>missing console</p>")
        return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})

    @review_router.get("/review.js")
    def review_console_js():
        path = ROOT / "hsrmap" / "guides" / "review" / "console.js"
        if not path.exists():
            raise HTTPException(404, "review script missing")
        return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": "no-store"})

    @review_router.get("/guide-assets/{sha256}")
    def get_guide_asset(sha256: str):
        if ".." in sha256 or "/" in sha256 or "\\" in sha256 or len(sha256) < 4:
            raise HTTPException(400, "invalid asset id")
        folder = app.state.guide_assets / sha256[:2]
        path = None
        if folder.exists():
            for item in folder.iterdir():
                if item.name.startswith(sha256):
                    path = item
                    break
        if path is None:
            raise HTTPException(404, "guide asset not found")
        mime, _ = mimetypes.guess_type(str(path))
        return FileResponse(path, media_type=mime or "application/octet-stream")

    @map_router.get("/api/v1/maps/tree")
    def map_tree(refresh: bool = False):
        return call_provider(lambda: get_provider().get_tree(refresh=refresh))

    @map_router.get("/api/v1/maps/{map_id}/labels")
    def map_labels(map_id: str, refresh: bool = False):
        payload = call_provider(lambda: get_provider().get_labels(map_id, refresh=refresh))
        if payload is None:
            raise HTTPException(404, "map not found")
        return payload

    @map_router.get("/api/v1/maps/{map_id}")
    def map_detail(map_id: str, refresh: bool = False):
        payload = call_provider(lambda: get_provider().get_map(map_id, refresh=refresh))
        if payload is None:
            raise HTTPException(404, "map not found")
        raster = payload.get("raster") or {}
        if raster.get("live_url") and raster.get("asset"):
            app.state.live_index[raster["asset"]] = raster["live_url"]
        return payload

    @map_router.get("/api/v1/maps/{map_id}/points")
    def map_points(map_id: str, semantic_key: str | None = None, label_id: str | None = None, refresh: bool = False):
        return call_provider(lambda: get_provider().get_points(map_id, semantic_key=semantic_key, label_id=label_id, refresh=refresh))

    @map_router.get("/api/v1/points/{core_point_id}")
    def get_point(core_point_id: int, refresh: bool = False):
        payload = call_provider(lambda: get_provider().get_point(core_point_id, refresh=refresh))
        if payload is None:
            raise HTTPException(404, "point not found")
        for image in (payload.get("detail") or {}).get("images") or []:
            live_url = image.get("live_url")
            if live_url:
                app.state.live_index[live_asset_key(live_url)] = live_url
        return payload

    @map_router.get("/assets/{sha256}")
    def get_asset(sha256: str):
        if ".." in sha256 or "/" in sha256 or "\\" in sha256:
            raise HTTPException(400, "invalid asset id")
        path = asset_path(sha256)
        if path is None:
            raise HTTPException(404, "asset not found")
        mime, _ = mimetypes.guess_type(str(path))
        return FileResponse(
            path,
            media_type=mime or "application/octet-stream",
            headers={
                "ETag": f'"{sha256.lower()}"',
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )

    @map_router.get("/")
    def index():
        index_path = WEB_DIST / "index.html"
        if index_path.exists():
            return FileResponse(index_path, media_type="text/html")
        return HTMLResponse("<!doctype html><title>hsrmap kernel</title><p>web/dist missing</p>")

    if WEB_DIST.exists():
        app_dir = WEB_DIST / "app"
        if app_dir.exists() and want_map:
            app.mount("/app", StaticFiles(directory=app_dir), name="app")

    #: 分区挂载：审核台与离线地图各拿自己那组路由，共用组两边都挂。
    app.include_router(shared_router)
    if want_map:
        app.include_router(map_router)
    if want_map or want_review:
        #: 只读攻略面：地图页要它显示证据，审核台要它显示状态；它不含任何写端点。
        app.include_router(guide_router)
    if want_review:
        app.include_router(review_router)
        if not want_map:
            #: 只跑审核台时，根路径直接进审核页（不然 404 会让人以为服务没起来）。
            @app.get("/")
            def review_index() -> Response:
                return RedirectResponse("/review")

    @app.on_event("shutdown")
    def shutdown():
        #: 各分区只管自己开出来的东西：map-only 的进程没有 guide/published 句柄。
        ctx = getattr(app.state, "ctx", None)
        if ctx is not None:
            ctx.close()
        app.state.user.close()
        if app.state.guide is not None:
            app.state.guide.close()
        if app.state.published is not None:
            app.state.published.close()

    return app


def create_map_app(**kwargs) -> FastAPI:
    """只离线地图（+ live 数据源、用户标记、assets）。

    不需要 guide.db / published.db：审核台的数据不在这个进程里。
    """
    kwargs.pop("sections", None)
    return create_app(sections=("map",), **kwargs)


def create_review_app(**kwargs) -> FastAPI:
    """只审核台（guides / atlas / review / topics / 设置 / 用户标记）。

    不需要 core.db 与快照：离线地图的数据不在这个进程里。
    """
    kwargs.pop("sections", None)
    return create_app(sections=("review",), **kwargs)
