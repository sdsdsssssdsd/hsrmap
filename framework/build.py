#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hsrmap 项目框架台 —— 从源码自动生成分层架构总览（只读）。

数据全部从源码提取：每个模块自己声明的 docstring、行数、最近改动时间；
判定层常量直接 import 读取；主题档案走 topics.loader；数据库表扫 CREATE TABLE；
CLI 命令树从 cli.py 抽。展示层（分层配色、人话解释）写在本文件里。

重跑即可刷新：python framework/build.py
"""
from __future__ import annotations

import ast
import json
import re
import sqlite3
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_HTML = HERE / "index.html"
OUT_JSON = HERE / "architecture.json"

# 层名 -> (图标, 主题色, 人话一句, 模块路径前缀/文件)
L = OrderedDict()
L["存储层"] = ("DB", "#8b9dc3", "所有攻略数据都装在一个 sqlite 文件里，这里定义表长什么样。",
    ["hsrmap/guide_db.py"])
L["数据底座"] = ("SAT", "#6fb3d9", "官方那一侧：地图快照、点位详情、图片资源，还有「离线 / 混合 / 联网」三种跑法。",
    ["hsrmap/sync.py", "hsrmap/database.py", "hsrmap/detail_db.py", "hsrmap/detail_normalize.py",
     "hsrmap/detail_lock.py", "hsrmap/detail_rebuild.py", "hsrmap/detail_offline.py",
     "hsrmap/detail_enrich.py", "hsrmap/detail_queue.py", "hsrmap/assets.py",
     "hsrmap/live_assets.py", "hsrmap/providers/", "hsrmap/viewer_repo.py",
     "hsrmap/viewer_bind.py", "hsrmap/viewer_crs.py", "hsrmap/user_db.py",
     "hsrmap/schema.py", "hsrmap/request_key.py", "hsrmap/http.py", "hsrmap/paths.py",
     "hsrmap/jobs.py", "hsrmap/reports.py", "hsrmap/validate.py", "hsrmap/preflight.py",
     "hsrmap/normalize.py", "hsrmap/transform.py", "hsrmap/inspect_map.py",
     "hsrmap/rebuild.py"])
L["完整性判定层"] = ("SCL", "#7ec86a", "全项目最有话语权的一层，而且只管一件事：玩家照着现有材料，到底能不能把东西拿到手。",
    ["hsrmap/guides/stages.py"])
L["主题档案层"] = ("CFG", "#e0b84c", "16 个攻略主题（若虫、浮脂溯源、黄金替罪羊……）各自的规矩：要不要第二阶段、怎么匹配、怎么排版。",
    ["hsrmap/guides/topics/"])
L["证据与账本层"] = ("LED", "#c99ae0", "记账用的：这个目标搜过几次、搜到过什么、现在算「已发布」还是「确实没有公开来源」。",
    ["hsrmap/guides/evidence.py", "hsrmap/guides/nops.py", "hsrmap/guides/ledger.py",
     "hsrmap/guides/coverage.py", "hsrmap/guides/planner.py", "hsrmap/guides/golden.py"])
L["抽取与视觉层"] = ("CV", "#8fd3c7", "把网页变成能用的东西：文字切成块、图里分成镜、图和点位对上号。",
    ["hsrmap/guides/extract/", "hsrmap/guides/vision/", "hsrmap/guides/matching/",
     "hsrmap/guides/layout/", "hsrmap/guides/regions/", "hsrmap/guides/llm/",
     "hsrmap/guides/semantic/", "hsrmap/guides/units/", "hsrmap/guides/derived.py"])
L["抓取与语料层"] = ("NET", "#e08a5f", "出门找东西：适配各家网站、渲染需要跑 JS 的页面、去重、质检、把图存下来。",
    ["hsrmap/guides/crawler/", "hsrmap/guides/sources/", "hsrmap/guides/corpus.py",
     "hsrmap/guides/discover.py", "hsrmap/guides/ingest.py", "hsrmap/guides/reingest.py",
     "hsrmap/guides/assets/", "hsrmap/guides/dedup.py", "hsrmap/guides/signature.py",
     "hsrmap/guides/qa.py", "hsrmap/guides/rebuild.py", "hsrmap/guides/revive.py"])
L["审核层"] = ("REV", "#d98cb3", "人工闸门：机器觉得可以发的，也还是要人过一眼才准发。",
    ["hsrmap/guides/review/"])
L["发布与审计层"] = ("PUB", "#b8c46a", "发出去之前比一比、发的时候别写坏、发完回头审计有没有编造的内容。",
    ["hsrmap/guides/publishing/", "hsrmap/guides/audit.py", "hsrmap/guides/closure.py",
     "hsrmap/guides/admin.py", "hsrmap/guides/ai_cache.py", "hsrmap/guides/publish/"])
L["接口层"] = ("API", "#7fa8d9", "对外的两个口子：命令行工具，和本机网页服务。",
    ["hsrmap/cli.py", "hsrmap/serve.py", "hsrmap/viewer_app.py"])
LORDER = list(L.keys())

MOD_PLAIN = [
    ("stages.py", "判定层本体：两个维度 × 三种证据，算出六个状态。全项目只有它说「算不算完成」。"),
    ("guide_db.py", "攻略库的表结构 + 读写入口，guide.db 和 published.db 共用同一套表。"),
    ("ledger.py", "台账：每个目标现在处于哪个状态（已发布 / 待审 / 确实没来源）。"),
    ("nops.py", "把「查过了，公开来源确实没有」变成有记录、可复核的一条结论。"),
    ("planner.py", "根据缺什么，排出下一轮该用什么关键词去搜。"),
    ("evidence.py", "搜索证据账本：每次搜了什么、搜到哪些候选、为什么留为什么扔。"),
    ("guides/official.py", "官方点位详情：对「走到就能拿」的点位，官方那一行说明加截图本身就是完整攻略。"),
    ("audit.py", "发布后回头查：每一步文字是不是真的出现在它声称的来源页里，不在就是编造。"),
    ("closure.py", "收官判定：整个项目现在算不算做完了。"),
    ("publishing/diff.py", "发布前比对：这次发布会删掉什么、覆盖率会不会倒退。"),
    ("publishing/atomic.py", "原子发布：中途失败不会把 published.db 写坏一半。"),
    ("crawler/render.py", "用本机已装的 Chrome 把需要跑 JS 的页面渲染出来（米游社、TapTap 这类）。"),
    ("review/service.py", "人工审核的准入规则：什么草稿可以批准、什么必须打回。"),
    ("topics/loader.py", "读取 16 个主题档案 YAML 并缓存。"),
    ("extract/blocks.py", "把 HTML 按标题 / 段落 / 图片的顺序切成块。"),
    ("vision/roles.py", "判断一张图是位置图、解谜步骤图，还是广告封面。"),
    ("matching/matcher.py", "把攻略里说的位置和官方点位对上号。"),
    ("corpus.py", "批量导入一批 URL：抓取、解析、入库一条龙。"),
    ("dedup.py", "同一个目标有多条攻略时，留最厚的那条，其余标记被取代。"),
    ("ingest.py", "一篇文章从 HTML 到入库的完整管线。"),
]


def plain_for(rel: str) -> str:
    for key, text in MOD_PLAIN:
        if key in rel:
            return text
    return ""


def docline(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    try:
        d = ast.get_docstring(ast.parse(text)) or ""
        if d:
            return d.strip().splitlines()[0].strip()[:120]
    except SyntaxError:
        pass
    for raw in text.splitlines():
        s = raw.strip()
        if s and not s.startswith(("#", "from ", "import ")):
            return s[:120]
    return ""


def classify(rel: str) -> str:
    for name in LORDER:
        for pat in L[name][3]:
            if pat.endswith("/"):
                if rel.startswith(pat):
                    return name
            elif rel == pat:
                return name
    return "证据与账本层" if rel.startswith("hsrmap/guides/") else "数据底座"


def scan() -> dict:
    layers = OrderedDict((n, []) for n in LORDER)
    for p in sorted((ROOT / "hsrmap").rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        rel = str(p.relative_to(ROOT)).replace(chr(92), "/")
        try:
            lines = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            lines = 0
        layers[classify(rel)].append({"rel": rel, "lines": lines, "doc": docline(p),
                                      "plain": plain_for(rel),
                                      "mtime": time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
    return layers


def stages_model() -> dict:
    sys.path.insert(0, str(ROOT))
    try:
        from hsrmap.guides import stages
        return {"requirements": list(stages.REQUIREMENTS), "solve_kinds": list(stages.SOLVE_KINDS),
                "statuses": list(stages.STATUSES), "done": list(stages.DONE_STATUSES)}
    except Exception as exc:
        return {"error": type(exc).__name__ + ": " + str(exc)[:140]}


def topics_table() -> list:
    sys.path.insert(0, str(ROOT))
    out = []
    try:
        from hsrmap.guides.topics.loader import list_topics
        for spec in list_topics():
            c = spec.get("completion") or {}
            out.append({"key": spec.get("topic_key"), "display": spec.get("display_name"),
                        "kind": spec.get("guide_kind"), "scope": spec.get("scope"),
                        "req": c.get("requirement"), "solve": c.get("solve_kind"),
                        "upgrade": c.get("point_upgrade", True),
                        "enabled": bool(spec.get("enabled", True)), "priority": spec.get("priority")})
    except Exception as exc:
        out.append({"error": type(exc).__name__ + ": " + str(exc)[:140]})
    return out


def cli_tree() -> dict:
    text = (ROOT / "hsrmap" / "cli.py").read_text(encoding="utf-8", errors="replace")
    top = re.findall(r'sub\.add_parser\("([^"]+)"', text)
    m = re.search(r'for name in \(([^)]+)\):', text)
    guides = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    guides += re.findall(r'gsub\.add_parser\("([^"]+)"', text)
    return {"top": top, "guides": guides}


def db_tables() -> list:
    spec = [("guide.db / published.db", "攻略主库（工作库 + 发布库）", "hsrmap/guide_db.py"),
            ("core.db", "官方地图快照", "hsrmap/database.py"),
            ("detail.db", "官方点位详情", "hsrmap/detail_db.py"),
            ("user.db", "本地用户进度", "hsrmap/user_db.py")]
    out = []
    for label, plain, rel in spec:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        out.append({"label": label, "plain": plain, "src": rel,
                    "tables": re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", text)})
    return out


def gaps() -> dict:
    """还差什么：直接问判定层要 missing_solve / missing_locate，再补上点位所在地图。"""
    sys.path.insert(0, str(ROOT))
    res = {"missing_solve": [], "missing_locate": [], "error": ""}
    try:
        from hsrmap.guides import stages
        con = sqlite3.connect("file:" + (ROOT / "data/guides/guide.db").as_posix() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        class _Shim:
            pass

        sh = _Shim()
        sh.conn = con
        rep = stages.completeness_report(sh)
        con.close()
        #: 点位所在地图一律问官方点位表自己带的 map_path／region，不要再自己走
        #: core.db 的 maps.id——那是库内自增 id，拿它去对 map_nodes.source_id 会串到别的星球去。
        loc = {}
        try:
            from hsrmap.guides.topics.official import official_points_for_topic
            missing = (rep.get("missing_solve") or []) + (rep.get("missing_locate") or [])
            tree = map_tree()
            for topic in sorted({str(r["topic"]) for r in missing}):
                for point in (official_points_for_topic(topic) or []):
                    pid = str(point.get("source_point_id") or "")
                    if not pid:
                        continue
                    where = str(point.get("map_path") or point.get("region") or "")
                    #: 副本房间在官方树里挂在「特殊房间」组下，看不出属于哪个区域；补一句归属。
                    if where.endswith("特殊房间") and point.get("map_id"):
                        owner = room_owner(str(point.get("map_id")), tree)
                        if owner and owner != "特殊房间":
                            where += "（属 " + owner + "）"
                    loc[pid] = where
        except Exception:  # noqa: BLE001 - 官方点位表拿不到不该让整页失败
            pass

        for key in ("missing_solve", "missing_locate"):
            for r in (rep.get(key) or [])[:40]:
                res[key].append({"point": r["point"], "topic": r["topic"], "solve_kind": r["solve_kind"],
                                 "source": r["source_kind"] or "无", "guide": r["guide_id"],
                                 "title": r["title"], "req_src": r.get("requirement_source"),
                                 "upgraded": r.get("requirement_source") == "point",
                                 "where": loc.get(str(r["point"])) or "（未记录）"})
        res["points"] = rep.get("points")
        res["done"] = rep.get("done")
    except Exception as exc:  # noqa: BLE001
        res["error"] = type(exc).__name__ + ": " + str(exc)[:200]
    return res


def map_tree() -> dict:
    """官方地图树：id -> 节点、id -> 父 id。只读 data/snapshots/<current>/raw/map_tree.json。"""
    try:
        cur = json.loads((ROOT / "data/current.json").read_text(encoding="utf-8"))
        raw = json.loads((ROOT / "data" / cur["path"] / "raw/map_tree.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 树缺了只是少一列注释
        return {}
    index, parent = {}, {}

    def walk(nodes):
        for node in nodes:
            nid = str(node.get("id"))
            index[nid] = node
            if node.get("parent_id") is not None:
                parent[nid] = str(node.get("parent_id"))
            if node.get("children"):
                walk(node["children"])

    walk((raw.get("data") or {}).get("tree") or [])
    return {"index": index, "parent": parent}


def room_owner(map_id: str, tree: dict) -> str:
    """「特殊房间」在地图树里靠 related_id 指回真正所属的区域，顺父链往上找第一个带 related_id 的节点。

    注意顺序：**先看 related_id，再看父链**。反过来写会把「特殊房间 / 特殊房间」这个分组节点
    当成答案（它的名字就叫「特殊房间」），于是所有副本房间都归属到它自己身上。
    """
    index, parent = tree.get("index") or {}, tree.get("parent") or {}
    cur, seen = str(map_id), 0
    while cur and seen < 8:
        node = index.get(cur)
        if node is None:
            return ""
        rid = str(node.get("related_id") or "0")
        if rid not in ("", "0") and rid != cur:
            owner = index.get(rid)
            return str(owner.get("name") or "") if owner is not None else ""
        cur = parent.get(cur, "")
        seen += 1
    return ""


def profiles() -> list:
    d = ROOT / "hsrmap" / "guides" / "topics" / "profiles"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


def snapshot() -> dict:
    snap = {}
    try:
        con = sqlite3.connect("file:" + (ROOT / "data/guides/guide.db").as_posix() + "?mode=ro", uri=True)
        q = lambda s: con.execute(s).fetchone()[0]
        snap["entries"] = q("SELECT COUNT(*) FROM guide_entry")
        snap["published"] = q("SELECT COUNT(*) FROM guide_entry WHERE status='published'")
        snap["official"] = q("SELECT COUNT(*) FROM guide_entry WHERE status='published' AND source_kind='Official'")
        snap["community"] = q("SELECT COUNT(*) FROM guide_entry WHERE status='published' AND source_kind='Community'")
        snap["covered"] = q("SELECT COUNT(DISTINCT source_point_id) FROM guide_entry WHERE status='published'")
        snap["pages"] = q("SELECT COUNT(*) FROM guide_page")
        snap["steps"] = q("SELECT COUNT(*) FROM guide_steps")
        snap["assets"] = q("SELECT COUNT(*) FROM guide_assets")
        con.close()
    except Exception as exc:
        snap["error"] = type(exc).__name__
    return snap


def esc(s) -> str:
    return (str(s if s is not None else "-")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


INTRO = "把《崩坏：星穹铁道》官方互动地图和网上的图文攻略抓下来，自动判断「玩家照着现有材料能不能把东西拿到手」——能就标完成，不能就指出缺的是<b>位置</b>还是<b>解法</b>，再把缺的排成下一轮找源的工作队列。"

STEPS = [
    ("01", "抓", "把官方地图快照、官方点位详情、网上的图文攻略存到本地", "抓取与语料层 / 数据底座"),
    ("02", "拆", "网页切成文字块和图片；图分成镜，图和点位对上号", "抽取与视觉层"),
    ("03", "判", "逐点位判断：缺位置还是缺解法，还是已经算完成", "完整性判定层 + 主题档案层"),
    ("04", "审", "机器觉得能发的，也要人过一眼才准发", "审核层"),
    ("05", "发", "发布前比对、原子发布、发完审计有没有编造", "发布与审计层 + 证据与账本层"),
]

NAV = [("n-gaps", "还差什么"), ("n-pipe", "五步流水线"), ("n-layers", "十个层"), ("n-model", "判定层"),
       ("n-topics", "主题档案"), ("n-flow", "数据流"), ("n-db", "数据库"), ("n-cli", "命令行")]

FLOW = """真实世界                          本地语料                          判定与产出
─────────────────────────────  ────────────────────────────────  ───────────────────────────────
官方互动地图                       抓取与语料层                       完整性判定层
  ├─ 地图快照 core.db    ───────▶   ├─ 各站适配器 sources/            stages.py
  ├─ 点位详情 detail.db  ───────▶   ├─ 需要跑 JS 的页面 render        「要求 × 证据 → 状态」
  └─ 官方点位与官方图    ───────▶   ├─ 导入 / 重导 ingest、reingest             │
                                    └─ 图片资产 assets/                        ▼
社区图文攻略            ───────▶                                     主题档案层
  ├─ 17173 / 3DM / 游民   ───────▶  抽取与视觉层                      16 个主题各自的规矩
  ├─ TapTap / 米游社      ───────▶   ├─ HTML 切成块 extract/            ├─ 要不要第二阶段
  └─ 游侠 / 九游 / B站    ───────▶   ├─ 大模型抽取 llm/                 ├─ 证据形态是什么
                                    ├─ 视觉分镜 vision/                └─ 匹配器 / 版式 / 视觉角色
                                    ├─ 图文匹配 matching/                        │
                                    └─ 区域解析 regions/                         ▼
                                            │                          证据与账本层
                                            ▼                           ├─ 搜索证据账本 evidence
                                    审核层                                ├─ 台账 ledger
                                    人工闸门                              ├─ 覆盖率 coverage
                                    锚定 / 文本接地两种准入                └─ 检索计划 planner
                                            │                                    │
                                            ▼                                    ▼
                                    发布与审计层                        接口层
                                    ├─ 发布前 diff 回归闸门               cli / serve / viewer_app
                                    ├─ 原子发布 atomic                            │
                                    ├─ 已发布内容审计 audit                        ▼
                                    └─ 收官判定 closure                  前端 web/
                                                                         地图 viewer + 攻略中心 + 审核台"""

CSS = """
*{box-sizing:border-box}
:root{
 --bg:#0d1117;--bg2:#111823;--panel:#161d29;--panel2:#1b2432;--line:#26303f;--line2:#33405200;
 --fg:#e6e9ef;--fg2:#aab4c4;--dim:#78859a;
 --ok:#5fd08a;--warn:#e8b44c;--bad:#e8735f;--acc:#63b3ed;
}
body{margin:0;background:radial-gradient(1200px 600px at 15% -10%,#17202e 0%,var(--bg) 55%) fixed;
 color:var(--fg);font:14px/1.7 "Microsoft YaHei","PingFang SC",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--acc);text-decoration:none}
.wrap{max-width:1180px;margin:0 auto;padding:34px 22px 90px}
.hero{padding:10px 0 22px;border-bottom:1px solid var(--line);margin-bottom:26px}
.hero h1{margin:0 0 6px;font-size:27px;font-weight:700;letter-spacing:-.4px}
.hero h1 span{color:var(--acc)}
.hero .sub{color:var(--fg2);font-size:13px}
.lede{background:linear-gradient(135deg,#182234,#141a24);border:1px solid var(--line);border-left:3px solid var(--acc);
 border-radius:10px;padding:16px 20px;margin:18px 0 24px;font-size:15px;line-height:1.85;color:#dbe2ec}
.lede b{color:var(--acc)}
.chips{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:9px 15px;min-width:104px}
.chip b{display:block;font-size:20px;color:var(--fg);font-variant-numeric:tabular-nums;line-height:1.25}
.chip span{color:var(--dim);font-size:11.5px}
h2{font-size:17px;margin:38px 0 4px;font-weight:650;letter-spacing:.2px;scroll-margin-top:66px}
.nav{position:sticky;top:0;z-index:9;display:flex;flex-wrap:wrap;gap:5px;padding:9px 0;margin-bottom:4px;
 background:linear-gradient(180deg,rgba(13,17,23,.97),rgba(13,17,23,.86));backdrop-filter:blur(8px);
 border-bottom:1px solid var(--line)}
.nav a{font-size:12.5px;color:var(--fg2);padding:4px 11px;border-radius:16px;border:1px solid transparent;white-space:nowrap}
.nav a:hover{color:var(--fg);border-color:var(--line);background:var(--panel)}
.nav a b{color:var(--acc);font-weight:600;margin-right:5px}
.yaml{margin:0 0 9px;padding-top:9px;border-top:1px dashed var(--line);line-height:1.5}
.yaml .pill{font-size:11px;padding:2px 8px;margin:2px 3px 2px 0}
h2 em{font-style:normal;color:var(--dim);font-size:12.5px;font-weight:400;margin-left:8px}
.hint{color:var(--fg2);font-size:13px;margin:0 0 14px}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:12px}
.step{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:14px 15px;position:relative;overflow:hidden}
.step:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--acc);opacity:.55}
.step .no{font-size:11px;color:var(--dim);letter-spacing:1.5px}
.step h4{margin:2px 0 6px;font-size:15.5px}
.step p{margin:0;color:var(--fg2);font-size:12.5px;line-height:1.6}
.step .who{margin-top:9px;font-size:11px;color:var(--dim);border-top:1px dashed var(--line);padding-top:6px}
.layers{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:13px}
.layer{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px 16px;position:relative;transition:.15s}
.layer:hover{border-color:#3b4a60;transform:translateY(-1px)}
.layer .top{display:flex;align-items:center;gap:9px;margin-bottom:7px}
.tag{width:34px;height:34px;border-radius:9px;display:flex;align-items:center;justify-content:center;
 font:600 10.5px/1 Consolas,monospace;color:#0d1117;flex:0 0 auto}
.layer h3{margin:0;font-size:15.5px}
.layer .cnt{color:var(--dim);font-size:11.5px;margin-left:auto;white-space:nowrap}
.layer .plain{color:#cfd7e3;font-size:13px;margin:0 0 9px;line-height:1.7}
details{margin-top:2px}
summary{cursor:pointer;color:var(--dim);font-size:12px;list-style:none;padding:3px 0}
summary::-webkit-details-marker{display:none}
summary:before{content:"▸ ";color:var(--acc)}
details[open] summary:before{content:"▾ "}
.mods{margin:6px 0 0;padding:0;list-style:none}
.mods li{padding:7px 0;border-top:1px solid #1e2735}
.mods .rel{font:12px/1.5 Consolas,monospace;color:var(--acc)}
.mods .ln{color:var(--dim);font-size:11px;margin-left:6px}
.mods .d{display:block;color:var(--fg2);font-size:12px;margin-top:2px}
.mods .p{display:block;color:#d7c58a;font-size:12px;margin-top:2px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
pre.flow{margin:0;font:12px/1.55 Consolas,"Cascadia Mono",monospace;color:#c6d0dd;overflow-x:auto;white-space:pre}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 9px;border-bottom:1px solid #1e2735;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:500;font-size:11.5px;white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:Consolas,monospace;font-size:12px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.dim{color:var(--dim)}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;border:1px solid var(--line);
 background:#131a24;font-size:11.5px;margin:2px 4px 2px 0;color:var(--fg2)}
.pill.hi{color:#0d1117;background:var(--ok);border-color:var(--ok);font-weight:600}
.pill.mid{color:#0d1117;background:var(--warn);border-color:var(--warn);font-weight:600}
.model{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:13px}
.model .panel h4{margin:0 0 8px;font-size:14px}
.model .panel .note{color:var(--dim);font-size:12px;margin-top:8px}
.big{font-size:38px;font-weight:700;line-height:1.15;color:var(--warn);font-variant-numeric:tabular-nums}
.big span{font-size:15px;font-weight:500;color:var(--fg2);margin-left:8px}
.big.ok{color:var(--ok)}
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:13px}
@media(max-width:760px){.cmp{grid-template-columns:1fr}}
.cmp .panel h4{margin:0 0 4px;font-size:14px}
.cmp ol{margin:6px 0 0 18px;padding:0;color:var(--fg2);font-size:12.5px}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);color:var(--dim);font-size:12px}
"""


def build_html(arch: dict) -> str:
    H = ["<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>hsrmap 项目框架台</title><style>" + CSS + "</style></head><body><div class='wrap'>"]
    H.append("<div class='hero'><h1>hsrmap <span>项目框架台</span></h1>")
    H.append("<div class='sub'>从源码自动生成 · " + esc(arch["generated"]) + " · " +
             str(arch["module_count"]) + " 个模块 / " + str(arch["line_count"]) + " 行 · 全部数据只读提取</div></div>")
    H.append("<div class='lede'>" + INTRO + "</div>")

    s = arch["snapshot"]
    if "error" not in s:
        H.append("<div class='chips'>")
        for k, label in (("entries", "攻略条目"), ("published", "已发布"), ("official", "官方来源"),
                         ("community", "社区来源"), ("covered", "覆盖点位"), ("pages", "抓取页面"),
                         ("steps", "步骤条数"), ("assets", "挂载图片")):
            H.append("<div class='chip'><b>" + str(s.get(k, "-")) + "</b><span>" + label + "</span></div>")
        H.append("</div>")

    H.append("<div class='nav'>" + "".join("<a href='#" + a + "'><b>·</b>" + esc(t) + "</a>" for a, t in NAV) + "</div>")

    g = arch.get("gaps") or {}
    ms, ml = g.get("missing_solve") or [], g.get("missing_locate") or []
    left = len(ms) + len(ml)
    H.append("<h2 id=\"n-gaps\">还差什么<em>判定层亲口说的缺口</em></h2>")
    if g.get("error"):
        H.append("<div class='panel bad'>读不到判定层：" + esc(g["error"]) + "</div>")
    elif not left:
        H.append("<div class='panel'><div class='big ok'>全部收口</div>"
                 "<p class='hint' style='margin:6px 0 0'>" + str(g.get("points") or 0) +
                 " 个点位，每一个玩家照着现有材料都能拿到手。</p></div>")
    else:
        H.append("<div class='panel'><div class='big'>" + str(left) + " <span>个点位还没收口</span></div>"
                 "<p class='hint' style='margin:6px 0 10px'>" +
                 str(g.get("done")) + " / " + str(g.get("points")) + " 已完成。"
                 "缺<b>解法</b>的 " + str(len(ms)) + " 个，缺<b>位置</b>的 " + str(len(ml)) + " 个。"
                 "下表直接来自判定层，不是覆盖率——覆盖率只说「发布了几条」，这个说「玩家拿不拿得到」。</p>")
        H.append("<table><tr><th>点位</th><th>主题</th><th>所在位置</th><th>缺什么</th>"
                 "<th>现有来源</th><th>为什么算缺</th></tr>")
        for r in ms + ml:
            why = "官方说明把要求上调成要解法" if r.get("upgraded") else "主题默认就要解法"
            if r["point"] in {x["point"] for x in ml}:
                why = "连位置证据都没有"
            src = ("<span class='dim'>没有任何匹配条目</span>" if r["guide"] is None
                   else "<span class='dim mono'>" + esc(r["source"]) + " e" + esc(r["guide"]) + "</span>")
            H.append("<tr><td class='mono'>" + esc(r["point"]) + "</td><td>" + esc(r["topic"]) + "</td>"
                     "<td class='dim'>" + esc(r["where"]) + "</td>"
                     "<td class='bad'>" + esc("解法" if r["point"] in {x["point"] for x in ms} else "位置") +
                     " <span class='dim mono'>" + esc(r["solve_kind"]) + "</span></td>"
                     "<td>" + src + "</td><td class='dim'>" + esc(why) + "</td></tr>")
        H.append("</table></div>")

    H.append("<h2 id=\"n-pipe\">这个项目怎么跑<em>五步流水线</em></h2>")
    H.append("<p class='hint'>从左到右是一条数据的一生：抓回来 → 拆开 → 判断 → 人工过目 → 发布留档。</p><div class='steps'>")
    for no, name, desc, who in STEPS:
        H.append("<div class='step'><div class='no'>" + no + "</div><h4>" + esc(name) + "</h4><p>" +
                 esc(desc) + "</p><div class='who'>" + esc(who) + "</div></div>")
    H.append("</div>")

    H.append("<h2 id=\"n-layers\">十个层<em>每层一句人话</em></h2>")
    H.append("<p class='hint'>展开任意一层可以看到它下面所有模块，以及每个文件自己声明的用途。</p><div class='layers'>")
    for name in LORDER:
        icon, color, plain, _pats = L[name]
        mods = arch["layers"].get(name, [])
        if not mods:
            continue
        total = sum(m["lines"] for m in mods)
        H.append("<div class='layer'><div class='top'>")
        H.append("<div class='tag' style='background:" + color + "'>" + esc(icon) + "</div>")
        H.append("<h3>" + esc(name) + "</h3><div class='cnt'>" + str(len(mods)) + " 模块 · " +
                 str(total) + " 行</div></div>")
        H.append("<p class='plain'>" + esc(plain) + "</p>")
        if name == "主题档案层" and arch.get("profiles"):
            H.append("<div class='yaml'>" +
                     "".join("<span class='pill mono'>" + esc(x) + ".yaml</span>" for x in arch["profiles"]) +
                     "</div>")
        H.append("<details><summary>展开 " + str(len(mods)) + " 个模块</summary><ul class='mods'>")
        for m in sorted(mods, key=lambda x: -x["lines"]):
            H.append("<li><span class='rel'>" + esc(m["rel"]) + "</span><span class='ln'>" +
                     str(m["lines"]) + " 行 · " + esc(m["mtime"]) + "</span>")
            if m.get("plain"):
                H.append("<span class='p'>人话：" + esc(m["plain"]) + "</span>")
            H.append("<span class='d'>" + esc(m["doc"] or "(没有 docstring)") + "</span></li>")
        H.append("</ul></details></div>")
    H.append("</div>")

    m = arch["model"]
    H.append("<h2 id=\"n-model\">判定层：全项目唯一的口径<em>「能不能拿到」而不是「写得详不详细」</em></h2>")
    H.append("<p class='hint'>每个点位先问两个问题：<b>要不要第二阶段</b>（到了就能拿，还是到了还得动手），"
             "再看手上证据补上了 <b>范围 / 位置 / 解法</b> 里的哪几种。</p>")
    if "error" in m:
        H.append("<div class='panel bad'>" + esc(m["error"]) + "</div>")
    else:
        H.append("<div class='model'>")
        H.append("<div class='panel'><h4>维度一 · 完成要求</h4>" +
                 "".join("<span class='pill " + ("hi" if x == "LOCATE_ONLY" else "mid") + "'>" + esc(x) + "</span>"
                         for x in m["requirements"]) +
                 "<div class='note'>LOCATE_ONLY = 走到就能拿，位置证据本身就是完整攻略。<br>"
                 "LOCATE_AND_SOLVE = 到了还得动手，另需解法。</div></div>")
        H.append("<div class='panel'><h4>维度二 · 要动手的话，是哪一种</h4>" +
                 "".join("<span class='pill'>" + esc(x) + "</span>" for x in m["solve_kinds"]) + "</div>")
        H.append("<div class='panel'><h4>六种状态</h4>" +
                 "".join("<span class='pill " + ("hi" if x in m["done"] else "") + "'>" + esc(x) + "</span>"
                         for x in m["statuses"]) +
                 "<div class='note'>绿色两种算「玩家拿得到」。不搞百分比——分数没有稳定语义。</div></div>")
        H.append("</div>")

    H.append("<div class='cmp' style='margin-top:13px'>")
    H.append("<div class='panel'><h4 class='ok'>若虫 · 第 1 阶段就到手</h4>"
             "<p class='dim' style='font-size:12.5px;margin:2px 0 0'>位置即全部难点，官方点位图就是完整攻略。</p>"
             "<ol><li>官方地图上有点位</li><li>玩家走过去</li><li>直接收集</li></ol>"
             "<p class='ok' style='margin:8px 0 0'>→ <b>COMPLETE</b></p></div>")
    H.append("<div class='panel'><h4 class='warn'>浮脂溯源 · 第 2 阶段还要解</h4>"
             "<p class='dim' style='font-size:12.5px;margin:2px 0 0'>官方图只解决「在哪」，不解决「怎么转」。</p>"
             "<ol><li>官方点位图告诉你在哪</li><li>玩家走到机关前</li><li><b>还得按 Q 顺时针 / E 逆时针转对</b></li></ol>"
             "<p class='warn' style='margin:8px 0 0'>→ 只有位置 = <b>SOLVE_MISSING</b>；位置 + 旋转序列 = <b>COMPLETE</b></p></div>")
    H.append("</div>")

    tp = arch["topics"]
    lo = sum(1 for t in tp if t.get("req") == "LOCATE_ONLY")
    H.append("<h2 id=\"n-topics\">十六个主题档案<em>" + str(lo) + " 个只要第一阶段，其余要解法</em></h2>")
    H.append("<p class='hint'>主题档案是「这个主题的规矩」：要不要第二阶段、点级能不能上调、用哪个匹配器和版式。</p>")
    H.append("<div class='panel'><table><tr><th>主题</th><th>游戏内名称</th><th>内容类型</th><th>范围</th>"
             "<th>完成要求</th><th>解法类型</th><th>点级上调</th><th class='n'>优先级</th><th>启用</th></tr>")
    for t in tp:
        if "error" in t:
            H.append("<tr><td colspan='9' class='bad'>" + esc(t["error"]) + "</td></tr>")
            continue
        req = ("<span class='ok'>走到就能拿</span>" if t["req"] == "LOCATE_ONLY"
               else "<span class='warn'>到了还得动手</span>")
        H.append("<tr><td class='mono'>" + esc(t["key"]) + "</td><td>" + esc(t["display"]) + "</td>"
                 "<td class='dim'>" + esc(t["kind"]) + "</td><td class='dim'>" + esc(t["scope"]) + "</td>"
                 "<td>" + req + "</td><td class='dim mono'>" + esc(t["solve"]) + "</td>"
                 "<td class='dim'>" + ("可以" if t["upgrade"] else "已关闭") + "</td>"
                 "<td class='n dim'>" + esc(t["priority"]) + "</td>"
                 "<td>" + ("<span class='ok'>是</span>" if t["enabled"] else "<span class='dim'>否</span>") + "</td></tr>")
    H.append("</table></div>")

    H.append("<h2 id=\"n-flow\">数据流<em>真实世界 → 本地语料 → 判定与产出</em></h2>")
    H.append("<div class='panel'><pre class='flow'>" + esc(arch["flow"]) + "</pre></div>")

    H.append("<h2 id=\"n-db\">数据放在哪<em>四个 sqlite 库</em></h2><div class='layers'>")
    for d in arch["db"]:
        H.append("<div class='layer'><h3 class='mono' style='font-size:14px'>" + esc(d["label"]) + "</h3>"
                 "<p class='plain'>" + esc(d["plain"]) + " · <span class='mono dim'>" + esc(d["src"]) + "</span></p>"
                 "<p style='margin:0'>" +
                 "".join("<span class='pill mono'>" + esc(x) + "</span>" for x in d["tables"]) + "</p></div>")
    H.append("</div>")

    c = arch["cli"]
    H.append("<h2 id=\"n-cli\">命令行<em>顶层 " + str(len(c["top"])) + " 个命令 · guides 下 " + str(len(c["guides"])) + " 个子命令</em></h2>")
    H.append("<div class='panel'><h4 style='margin:0 0 6px;font-size:14px'>hsrmap &lt;命令&gt;</h4>" +
             "".join("<span class='pill mono'>" + esc(x) + "</span>" for x in c["top"]) +
             "<h4 style='margin:14px 0 6px;font-size:14px'>hsrmap guides &lt;子命令&gt;</h4>" +
             "".join("<span class='pill mono'>" + esc(x) + "</span>" for x in c["guides"]) + "</div>")

    H.append("<footer>本页由 <span class='mono'>framework/build.py</span> 生成。"
             "模块清单、docstring、行数、主题档案、数据库表、命令树全部从源码与 guide.db 只读提取；"
             "分层配色与人话解释写在该脚本里。重跑即可刷新。监控台：<span class='mono'>127.0.0.1:8768</span></footer>")
    H.append("</div></body></html>")
    return "\n".join(H)


def main() -> int:
    layers = scan()
    #: 只写目录名：这份 json 会跟着交付包走，绝对路径带着构建机的用户名，不该外发。
    arch = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "root_name": ROOT.name,
            "layers": layers, "layer_meta": {n: {"icon": L[n][0], "color": L[n][1], "plain": L[n][2]} for n in LORDER},
            "model": stages_model(), "topics": topics_table(), "db": db_tables(),
            "cli": cli_tree(), "snapshot": snapshot(), "flow": FLOW, "intro": INTRO, "steps": STEPS,
            "profiles": profiles(), "gaps": gaps()}
    arch["module_count"] = sum(len(v) for v in layers.values())
    arch["line_count"] = sum(m["lines"] for v in layers.values() for m in v)
    OUT_JSON.write_text(json.dumps(arch, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_HTML.write_text(build_html(arch), encoding="utf-8")
    print("layers %d, modules %d, lines %d" % (len(layers), arch["module_count"], arch["line_count"]))
    print("written " + str(OUT_HTML))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
