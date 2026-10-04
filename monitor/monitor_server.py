#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hsrmap 实时监控台（只读，零依赖）。

单进程 = 采样线程 + 本地 HTTP 服务：
  GET /            -> index.html（页面每秒 fetch /api/status，原地更新，不整页 reload）
  GET /api/status  -> JSON 快照
  GET /framework   -> 框架台（超过 60s 自动重跑 framework/build.py 再吐，保证与监测台同刻）

只读保证：
  * 进程信息：PowerShell CIM 只读查询（每 2s 一次）
  * 文件信息：os.stat / os.scandir / os.walk
  * 数据库：sqlite3 以 mode=ro URI 在本进程内查询（不起子进程，避免污染进程观测）

不写入仓库任何数据。仅监听 127.0.0.1。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


def _runtime_paths() -> dict[str, Path]:
    """运行目录跟着 hsrmap 的解析走（--data-dir / HSRMAP_DATA_DIR / 仓库 data / 用户目录）。

    以前这里写死 `ROOT/data`：运行目录一旦外置，监控台就会指着一个空目录说「没有数据」。
    解析不出来（缺依赖、库没建）时退回仓库 data/，至少和以前一样能用。
    """
    fallback = {
        "db": ROOT / "data" / "guides" / "guide.db",
        "published": ROOT / "data" / "guides" / "published.db",
        "assets": ROOT / "data" / "guide-assets",
        "raw": ROOT / "data" / "guides" / "raw",
        "derived": ROOT / "data" / "guides" / "derived",
        "reports": ROOT / "data" / "guides" / "reports",
    }
    try:
        from hsrmap.runtime import get_runtime

        runtime = get_runtime()
        return {
            "db": runtime.guide_db,
            "published": runtime.published_db,
            "assets": runtime.guide_assets,
            "raw": runtime.guide_raw,
            "derived": runtime.guide_derived,
            "reports": runtime.guide_reports,
        }
    except Exception:  # noqa: BLE001 - 监控台是只读工具，解析失败也要能起来
        return fallback


_PATHS = _runtime_paths()
DB = _PATHS["db"]
PDB = _PATHS["published"]
ASSETS = _PATHS["assets"]
RAW = _PATHS["raw"]
DERIVED = _PATHS["derived"]
REPORTS = _PATHS["reports"]
SCAN_DIRS = [ROOT / "hsrmap", ROOT / "docs", ROOT / "tests"]

SELF_PID = os.getpid()
HOST = "127.0.0.1"
#: 8766 离线地图 / 8767 审核台（a1-8 的 S4 分家）——监控台是第三个，用 8768。
#: 曾经和审核台抢 8767：stop_monitor.bat 会把正在用的审核服务杀掉。
PORT = 8768
REFRESH_MS = 1000
PS_EVERY = 2.0
DB_EVERY = 20.0
COUNT_EVERY = 5.0
COMPLETE_EVERY = 90.0
DEV_EVERY = 1.0
RAW_EVERY = 5.0

_VIEWER = {"ctx": None}
#: 判定层一旦被改动，监控台必须重新导入它，否则会拿旧模块继续算（实测发生过：
#: stages.py 改了，页面数字却停在旧口径）。
#: (模块名, 监视路径)。路径可以是文件，也可以是目录（目录按其中最新的 *.yaml 算）——
#: 主题档案改一个 completion 块也要能触发重载，否则 loader 的 lru_cache 会一直拿旧分类。
_CODE_WATCH = (
    ("hsrmap.guides.stages", ROOT / "hsrmap" / "guides" / "stages.py"),
    ("hsrmap.guides.topics.loader", ROOT / "hsrmap" / "guides" / "topics" / "loader.py"),
    ("hsrmap.guides.topics.loader", ROOT / "hsrmap" / "guides" / "topics" / "profiles"),
    ("hsrmap.guides.official", ROOT / "hsrmap" / "guides" / "official.py"),
)
_CODE_MTIME: dict = {}
_RELOADED_AT = {"at": "-"}
_DEV_DIRS = [ROOT / "hsrmap", ROOT / "tests", ROOT / "docs"]
_WATCH: dict = {}
_DOC_CACHE: dict = {}
_BASELINE = {"done": False}

LOCK = threading.Lock()
STATE = {"started": time.time()}
HIST = deque(maxlen=300)
_CACHE: dict = {}

PS = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
PS_CMD = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object {"
    " $p = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue;"
    " [pscustomobject]@{ pid=$_.ProcessId; cmd=$_.CommandLine;"
    " start=$_.CreationDate.ToString('HH:mm:ss');"
    " cpu=$(if ($p) { [math]::Round($p.CPU,1) } else { 0 });"
    " ws=$(if ($p) { [math]::Round($p.WorkingSet64/1MB,1) } else { 0 }) }"
    "} | ConvertTo-Json -Compress"
)


def _count(path: Path) -> int:
    n = 0
    try:
        for _root, _dirs, files in os.walk(path):
            n += len(files)
    except OSError:
        pass
    return n


def _cached(key: str, ttl: float, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _stat(path: Path) -> dict:
    try:
        st = path.stat()
        return {
            "size": st.st_size,
            "mtime": time.strftime("%H:%M:%S", time.localtime(st.st_mtime)),
            "epoch": st.st_mtime,
        }
    except OSError:
        return {"size": 0, "mtime": "-", "epoch": 0}


def _walk_files(bases):
    for base in bases:
        if not base.exists():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if name.endswith(".pyc"):
                    continue
                yield Path(root) / name


def _newest_source() -> dict:
    best_m = -1.0
    best_p = None
    for p in _walk_files(SCAN_DIRS):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > best_m:
            best_m, best_p = m, p
    if best_p is None:
        return {"age": 99999, "name": "-", "at": "-"}
    return {
        "age": int(time.time() - best_m),
        "name": str(best_p.relative_to(ROOT)),
        "at": time.strftime("%H:%M:%S", time.localtime(best_m)),
    }


def _recent(minutes: int = 10, limit: int = 18) -> list:
    cut = time.time() - minutes * 60
    out = []
    for p in _walk_files(SCAN_DIRS + [REPORTS]):
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_mtime > cut:
            out.append({
                "t": time.strftime("%H:%M:%S", time.localtime(st.st_mtime)),
                "epoch": st.st_mtime,
                "name": str(p.relative_to(ROOT)),
                "size": st.st_size,
            })
    out.sort(key=lambda r: -r["epoch"])
    return out[:limit]


def _proc_list() -> list:
    try:
        raw = subprocess.run(
            [PS, "-NoProfile", "-NonInteractive", "-Command", PS_CMD],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
        txt = (raw.stdout or "").strip()
        if not txt:
            return []
        data = json.loads(txt)
        if isinstance(data, dict):
            data = [data]
        out = []
        for item in data:
            cmd = str(item.get("cmd") or "").strip()
            if cmd.startswith('"'):
                cmd = cmd.split('"', 2)[-1].strip()
            if len(cmd) > 96:
                cmd = cmd[:96] + "..."
            out.append({
                "pid": item.get("pid"),
                "self": item.get("pid") == SELF_PID,
                "cmd": cmd or "-",
                "start": item.get("start") or "-",
                "cpu": item.get("cpu") or 0,
                "ws": item.get("ws") or 0,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        return [{"pid": "-", "cmd": "PS 探测失败: " + type(exc).__name__, "start": "-", "cpu": 0, "ws": 0}]


def _q(con, sql):
    try:
        return con.execute(sql).fetchone()[0]
    except Exception:  # noqa: BLE001
        return None


def _db_counts() -> dict:
    out: dict = {}
    try:
        con = sqlite3.connect("file:" + DB.as_posix() + "?mode=ro", uri=True)
        pub = "WHERE status='published'"
        out["entries"] = _q(con, "SELECT COUNT(*) FROM guide_entry")
        out["published"] = _q(con, "SELECT COUNT(*) FROM guide_entry " + pub)
        out["official"] = _q(con, "SELECT COUNT(*) FROM guide_entry " + pub + " AND source_kind='Official'")
        out["community"] = _q(con, "SELECT COUNT(*) FROM guide_entry " + pub + " AND source_kind='Community'")
        out["covered"] = _q(con, "SELECT COUNT(DISTINCT source_point_id) FROM guide_entry " + pub)
        out["scope_entries"] = _q(con, "SELECT COUNT(*) FROM guide_entry " + pub
                                  + " AND (source_point_id LIKE 'set:%' OR source_point_id LIKE 'map:%' OR source_point_id LIKE 'global:%')")
        out["pages"] = _q(con, "SELECT COUNT(*) FROM guide_page")
        #: 步骤 / 图片与框架台首屏同一口径（那边也是全表 COUNT），两个台子才报同一组数字。
        out["steps"] = _q(con, "SELECT COUNT(*) FROM guide_steps")
        out["assets"] = _q(con, "SELECT COUNT(*) FROM guide_assets")
        out["review_pending"] = _q(con, "SELECT COUNT(*) FROM review_item WHERE status IN ('pending','NEEDS_REVIEW','AUTO_SUGGEST','NEW')")
        out["ledger"] = _q(con, "SELECT COUNT(*) FROM guide_target_status")
        con.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__ + ": " + str(exc)[:120]
    try:
        p = sqlite3.connect("file:" + PDB.as_posix() + "?mode=ro", uri=True)
        out["published_db"] = _q(p, "SELECT COUNT(*) FROM guide_entry")
        p.close()
    except Exception as exc:  # noqa: BLE001
        out["published_db"] = "err:" + type(exc).__name__
    return out


def _bump(bucket: dict, row: dict) -> None:
    bucket["points"] += 1
    status = str(row.get("status") or "")
    if row.get("done"):
        bucket["done"] += 1
    if status == "COMPLETE":
        bucket["complete"] += 1
    elif status == "LOCATE_COMPLETE":
        bucket["locate_complete"] += 1
    elif status == "SOLVE_MISSING":
        bucket["solve_missing"] += 1
    elif status == "LOCATE_MISSING":
        bucket["locate_missing"] += 1
    elif status == "SCOPE_ONLY":
        bucket["scope_only"] += 1
    else:
        bucket["no_evidence"] += 1


def _empty_bucket() -> dict:
    return {"points": 0, "done": 0, "complete": 0, "locate_complete": 0,
            "solve_missing": 0, "locate_missing": 0, "scope_only": 0, "no_evidence": 0}


def _watch_mtime(path: Path) -> float:
    """一个文件或一个目录（取其中最新 *.yaml）的 mtime。"""
    try:
        if path.is_dir():
            best = 0.0
            for item in path.glob("*.yaml"):
                try:
                    best = max(best, item.stat().st_mtime)
                except OSError:
                    pass
            return best
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _maybe_reload() -> None:
    """判定层源码变了就重新导入，避免监控台用旧口径继续算。"""
    import importlib

    changed = []
    for name, path in _CODE_WATCH:
        m = _watch_mtime(path)
        slot = name + "|" + str(path)
        if not m or _CODE_MTIME.get(slot) == m:
            continue
        _CODE_MTIME[slot] = m
        if name not in changed:
            changed.append(name)
    if not any(name in sys.modules for name in changed):
        return
    for name in changed:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        try:
            importlib.reload(mod)
        except Exception:  # noqa: BLE001 - 重载失败就继续用旧模块，不要打断采样
            pass
    if changed:
        _RELOADED_AT["at"] = time.strftime("%H:%M:%S")


def _completeness() -> dict:
    """两阶段 / solve_kind 维度的进度（复用 hsrmap.guides.stages，全程只读）。

    stages.completeness_report 只需要一个带 .conn 的对象，所以这里给它一个
    mode=ro 的 sqlite 连接壳，避免 GuideDatabase 以读写方式打开 guide.db
    （会和正在跑的 pytest / 抓取进程抢锁）。
    """
    try:
        _maybe_reload()
        from hsrmap.guides import stages
        from hsrmap.viewer_bind import bind_viewer

        ctx = _VIEWER["ctx"]
        if ctx is None:
            ctx = _VIEWER["ctx"] = bind_viewer()
        conn = sqlite3.connect("file:" + DB.as_posix() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        shim = type("ReadOnlyDb", (), {})()
        shim.conn = conn
        report = stages.completeness_report(shim, topics=None)
        conn.close()

        names: dict = {}
        try:
            from hsrmap.guides.topics.loader import list_topics

            for spec in list_topics():
                key = str(spec.get("topic_key") or "")
                labels = (spec.get("official_labels") or {}).get("names") or []
                names[key] = {
                    "display": str(spec.get("display_name") or key),
                    "kind": str(spec.get("guide_kind") or ""),
                    "label": str(labels[0]) if labels else "",
                    "enabled": bool(spec.get("enabled", True)),
                }
        except Exception:  # noqa: BLE001
            names = {}

        rows = report.get("rows") or []
        by_req: dict = {}
        by_kind: dict = {}
        req_topics: dict = {}
        kind_topics: dict = {}
        for row in rows:
            req = str(row.get("requirement"))
            kind = str(row.get("solve_kind"))
            topic = str(row.get("topic"))
            _bump(by_req.setdefault(req, _empty_bucket()), row)
            _bump(by_kind.setdefault(kind, _empty_bucket()), row)
            req_topics.setdefault(req, {})
            req_topics[req][topic] = int(req_topics[req].get(topic) or 0) + 1
            kind_topics.setdefault(kind, {})
            kind_topics[kind][topic] = int(kind_topics[kind].get(topic) or 0) + 1

        return {
            "at": time.strftime("%H:%M:%S"),
            "code_reloaded_at": _RELOADED_AT["at"],
            "points": report.get("points"),
            "done": report.get("done"),
            "complete": report.get("complete"),
            "locate_complete": report.get("locate_complete"),
            "solve_missing": report.get("solve_missing"),
            "locate_missing": report.get("locate_missing"),
            "scope_only": report.get("scope_only"),
            "no_evidence": report.get("no_evidence"),
            "statuses": report.get("statuses"),
            "by_requirement": by_req,
            "by_solve_kind": by_kind,
            "req_topics": req_topics,
            "kind_topics": kind_topics,
            "names": names,
            "topics": report.get("topics"),
            "solve_queue": [
                {"topic": r.get("topic"), "point": r.get("point"), "solve_kind": r.get("solve_kind"),
                 "title": r.get("title"), "guide_id": r.get("guide_id")}
                for r in (report.get("missing_solve") or [])
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__ + ": " + str(exc)[:200], "at": time.strftime("%H:%M:%S")}


def _subsys(rel: str) -> str:
    """这个文件属于哪一层——用来回答「在造什么轮子」。"""
    p = rel.replace("\\", "/")
    if p.startswith("tests/"):
        return "测试"
    if p.startswith("docs/"):
        return "文档"
    if "/vision/" in p or "/matching/" in p or "/layout/" in p or "/regions/" in p or "/extract/" in p:
        return "抽取／匹配／视觉"
    if "/crawler/" in p or "/sources/" in p:
        return "抓取"
    if "/publishing/" in p or "/review/" in p or p.endswith("closure.py") or p.endswith("publishing.py"):
        return "发布／审核／审计"
    if p.endswith(("stages.py", "ledger.py", "evidence.py", "nops.py", "planner.py", "coverage.py", "guide_db.py")):
        return "完整性判定／账本"
    if "/topics/" in p:
        return "主题档案"
    if p.startswith("hsrmap/"):
        return "其它源码"
    return "其它"


def _docline(path: Path) -> str:
    """文件自己声明的用途：Python 取首个 docstring 首行，其它取首个非空行。"""
    try:
        st = path.stat()
    except OSError:
        return ""
    key = str(path)
    hit = _DOC_CACHE.get(key)
    if hit and hit[0] == st.st_mtime:
        return hit[1]
    text = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(4096)
    except OSError:
        return ""
    out = ""
    lines = text.splitlines()
    if path.suffix == ".py":
        for i, raw in enumerate(lines):
            s = raw.strip()
            if s.startswith('"""') or s.startswith("'''"):
                quote = s[:3]
                body = s[3:]
                if body.endswith(quote) and len(body) > 3:
                    body = body[:-3]
                body = body.strip()
                if not body:
                    for nxt in lines[i + 1:]:
                        if nxt.strip():
                            body = nxt.strip()
                            break
                out = body
                break
            if s and not s.startswith("#") and not s.startswith("from ") and not s.startswith("import "):
                out = s
                break
    else:
        for raw in lines:
            s = raw.strip().lstrip("#").strip()
            if s:
                out = s
                break
    out = out[:110]
    _DOC_CACHE[key] = (st.st_mtime, out)
    return out


def _watch_update() -> None:
    for p in _walk_files(_DEV_DIRS):
        try:
            st = p.stat()
        except OSError:
            continue
        rel = str(p.relative_to(ROOT))
        w = _WATCH.get(rel)
        if w is None:
            _WATCH[rel] = {"rel": rel, "mtime": st.st_mtime, "size": st.st_size,
                           "edits": 0, "born": st.st_mtime, "new": _BASELINE["done"]}
        elif abs(st.st_mtime - w["mtime"]) > 1e-6:
            w["edits"] += 1
            w["mtime"] = st.st_mtime
            w["size"] = st.st_size
    _BASELINE["done"] = True


def _raw_recent() -> int:
    base = ROOT / "data" / "guides" / "raw"
    cut = time.time() - 600
    n = 0
    try:
        with os.scandir(base) as it:
            for e in it:
                try:
                    if e.is_dir() and e.stat().st_mtime > cut:
                        n += 1
                except OSError:
                    continue
    except OSError:
        pass
    return n


def _dev_snapshot(now: float) -> dict:
    cut5 = now - 300
    cut15 = now - 900
    items = []
    for w in _WATCH.values():
        if w["mtime"] > cut15:
            items.append(w)
    items.sort(key=lambda w: -w["mtime"])
    focus = []
    for w in items[:7]:
        p = ROOT / w["rel"]
        focus.append({
            "rel": w["rel"],
            "subsys": _subsys(w["rel"]),
            "edits": w["edits"],
            "age": int(now - w["mtime"]),
            "at": time.strftime("%H:%M:%S", time.localtime(w["mtime"])),
            "size": w["size"],
            "doc": _docline(p),
        })
    fresh = sorted([w for w in _WATCH.values() if w.get("new")], key=lambda w: -w["mtime"])[:15]
    sessions = {}
    for w in _WATCH.values():
        if w["mtime"] > cut5:
            sessions[_subsys(w["rel"])] = int(sessions.get(_subsys(w["rel"])) or 0) + 1
    dev_recent = sum(sessions.values())
    crawl_recent = _cached("rawr", RAW_EVERY, _raw_recent)
    if dev_recent and crawl_recent:
        mode = "造轮子 ＋ 抓取"
    elif dev_recent:
        mode = "造轮子"
    elif crawl_recent:
        mode = "抓取"
    else:
        mode = "空闲"
    return {
        "mode": mode,
        "dev_recent": dev_recent,
        "crawl_recent": crawl_recent,
        "subsystems": sessions,
        "focus": focus,
        "fresh": [{"rel": w["rel"], "subsys": _subsys(w["rel"]), "size": w["size"],
                   "at": time.strftime("%H:%M:%S", time.localtime(w["mtime"]))} for w in fresh],
        "tracked": len(_WATCH),
    }


def _spark(vals, color: str) -> str:
    if len(vals) < 2:
        return ""
    mn, mx = min(vals), max(vals)
    span = max(mx - mn, 1)
    w, h = 560.0, 48.0
    pts = []
    for i, v in enumerate(vals):
        x = round(w * i / max(len(vals) - 1, 1), 1)
        y = round(h - h * (v - mn) / span, 1)
        pts.append(str(x) + "," + str(y))
    return "<polyline points='" + " ".join(pts) + "' fill='none' stroke='" + color + "' stroke-width='2'/>"


def sampler() -> None:
    last_ps = 0.0
    last_db = 0.0
    last_cmp = 0.0
    while True:
        now = time.time()
        snap = {"now": time.strftime("%H:%M:%S"), "ts": now, "root": str(ROOT)}
        snap["guide_db"] = _stat(DB)
        snap["published_db_file"] = _stat(PDB)
        snap["assets"] = _cached("assets", COUNT_EVERY, lambda: _count(ASSETS))
        snap["raw"] = _cached("raw", COUNT_EVERY, lambda: _count(RAW))
        snap["derived"] = _cached("derived", COUNT_EVERY, lambda: _count(DERIVED))
        snap["newest"] = _cached("newest", 1.0, _newest_source)
        snap["recent"] = _cached("recent", 4.0, _recent)

        _watch_update()
        snap["dev"] = _dev_snapshot(now)

        if now - last_ps >= PS_EVERY:
            last_ps = now
            procs = _proc_list()
            _CACHE["procs"] = (now, procs)
        else:
            procs = _CACHE.get("procs", (0, []))[1]
        snap["procs"] = procs

        if now - last_db >= DB_EVERY:
            last_db = now
            counts = _db_counts()
            _CACHE["db"] = (now, counts)
        else:
            counts = _CACHE.get("db", (0, {}))[1]
        snap["db"] = counts

        if now - last_cmp >= COMPLETE_EVERY:
            last_cmp = now
            comp = _completeness()
            _CACHE["cmp"] = (now, comp)
        else:
            comp = _CACHE.get("cmp", (0, {}))[1]
        snap["completeness"] = comp

        HIST.append((now, float(snap["guide_db"]["size"]), float(snap["assets"])))
        snap["spark_guide"] = _spark([h[1] for h in HIST], "#8fd3c7")
        snap["spark_assets"] = _spark([h[2] for h in HIST], "#e0b84c")
        snap["hist_min"] = round(min(h[1] for h in HIST))
        snap["hist_max"] = round(max(h[1] for h in HIST))
        snap["uptime"] = int(now - STATE["started"])
        snap["refresh_ms"] = REFRESH_MS
        snap["alive"] = any(not p.get("self") for p in procs)
        snap["self_pid"] = SELF_PID
        with LOCK:
            STATE.update(snap)
        time.sleep(1.0)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            with LOCK:
                body = json.dumps(STATE, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
            return
        #: /framework 直接吐 framework/build.py 生成的框架台（分层架构总览）。
        #: 页面超过 _FW_TTL 就先重建再吐——数据与监测台同源同刻，不需要谁记得手动重跑。
        if self.path.startswith("/framework"):
            refresh_framework(force="force" in self.path)
            page = ROOT / "framework" / "index.html"
            try:
                body = page.read_bytes()
            except OSError:
                body = ("<h1>还没有框架台</h1><p>先跑：python framework/build.py</p>").encode("utf-8")
            self._send(body, "text/html; charset=utf-8")
            return
        page = HERE / "index.html"
        try:
            body = page.read_bytes()
        except OSError:
            body = "<h1>index.html 缺失</h1>".encode("utf-8")
        self._send(body, "text/html; charset=utf-8")


#: 框架台是 framework/build.py 生成的静态页。与其让人记得手动重跑，不如在请求时按需重建：
#: 这样监测台和框架台永远是同一时刻的同一批数字，不会一个实时、一个停在昨天。
_FW_TTL = 60.0
_fw_lock = threading.Lock()
_fw_status = {"at": 0.0, "ok": None, "error": ""}


def refresh_framework(force: bool = False) -> None:
    """超过 TTL 就重建框架台；失败也只记一次，不在每个请求上重试。"""
    now = time.time()
    if not force and now - float(_fw_status["at"] or 0.0) < _FW_TTL:
        return
    with _fw_lock:
        if not force and time.time() - float(_fw_status["at"] or 0.0) < _FW_TTL:
            return
        script = ROOT / "framework" / "build.py"
        if not script.exists():
            _fw_status.update({"at": time.time(), "ok": False, "error": "framework/build.py 不存在"})
            return
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240,
            )
            _fw_status.update({
                "at": time.time(),
                "ok": proc.returncode == 0,
                "error": "" if proc.returncode == 0 else (proc.stdout or b"")[-300:].decode("utf-8", "replace"),
            })
        except Exception as exc:  # noqa: BLE001 - 重建失败不该让页面 500
            _fw_status.update({"at": time.time(), "ok": False, "error": type(exc).__name__ + ": " + str(exc)[:200]})


def main() -> int:
    url = "http://" + HOST + ":" + str(PORT) + "/"
    threading.Thread(target=sampler, daemon=True).start()
    time.sleep(2.0)
    print("hsrmap 实时监控台: " + url)
    print("仓库根目录: " + str(ROOT))
    print("按 Ctrl+C 退出。")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退出。")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
