"""闭环自检（a1-8 十六 + 用户 2026-10「启动入口要能打开」）。

`hsrmap dod` 管的是「这套工程形态对不对」，这里管的是**今天这台机器上，双击入口能不能用**：

1. 四个入口文件都在，而且各自指向真实存在的脚本/服务；
2. 端口互不冲突（离线地图 8766 / 审核台 8767 / 监控台 8768），
   `.bat` 里写的端口和 `hsrmap.serve` 的常量一致；
3. 起飞前检查（缺库、缺前端产物）没有致命项；
4. **第一次打开页面要发的每个请求都真的发一遍**（进程内 TestClient，不占端口），
   任何一步不是 200 就是闭环断了；
5. 首屏载荷有预算：审核队列曾经一次返回 142 MB、要等 17 秒——
   这类「页面打不开」的根因必须变成会失败的检查。

退出码：0 = 闭环成立（可以带 SKIP），2 = 有检查失败。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

#: 入口文件 -> 它应该跑什么（人话，用于报告）。
ENTRY_POINTS: tuple[tuple[str, str], ...] = (
    ("start.bat", "离线地图 8766"),
    ("start_review.bat", "审核台 8767"),
    ("monitor/start_monitor.bat", "实时监控台 8768"),
    ("monitor/stop_monitor.bat", "停监控台 8768"),
    ("framework/start.bat", "项目框架台（静态页）"),
    ("tools/windows/open-review.html", "指向审核台的快捷页"),
)

#: 首屏载荷预算（字节）：超了就说明又把整库塞进了列表接口。
PAYLOAD_BUDGET: dict[str, int] = {
    "/api/v1/review/items": 4 * 1024 * 1024,
    "/api/v1/maps/tree": 2 * 1024 * 1024,
    "/api/v1/guides/index": 1 * 1024 * 1024,
    "/api/v1/guides/evidence": 1 * 1024 * 1024,
    "/api/v1/review/maps/{item}": 1 * 1024 * 1024,
}

_PORT = re.compile(r"--port\s+(\d{4,5})|PORT\s*=\s*(\d{4,5})|127\.0\.0\.1:(\d{4,5})")


def _ok(item_id: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    return {"id": item_id, "status": "PASS", "detail": detail, **extra}


def _fail(item_id: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"id": item_id, "status": "FAIL", "detail": detail, **extra}


def _skip(item_id: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"id": item_id, "status": "SKIPPED", "detail": detail, **extra}


def check_entry_points(root: Path) -> dict[str, Any]:
    """入口文件都在，而且它们引用的脚本/目标真的存在。"""
    missing = [name for name, _note in ENTRY_POINTS if not (root / name).is_file()]
    if missing:
        return _fail("entry-points", f"入口缺失：{missing}")
    framework = root / "framework" / "build.py"
    monitor = root / "monitor" / "monitor_server.py"
    problems = []
    if not framework.is_file():
        problems.append("framework/build.py 不存在（framework/start.bat 会失败）")
    if not monitor.is_file():
        problems.append("monitor/monitor_server.py 不存在（monitor/start_monitor.bat 会失败）")
    html = (root / "tools" / "windows" / "open-review.html").read_text(encoding="utf-8", errors="replace")
    if "8767" not in html:
        problems.append("tools/windows/open-review.html 没有指向审核台端口 8767")
    if problems:
        return _fail("entry-points", "；".join(problems))
    return _ok("entry-points", f"{len(ENTRY_POINTS)} 个入口文件都在，引用的脚本齐全")


def check_ports(root: Path) -> dict[str, Any]:
    """三个服务端口互不冲突，且 .bat 与服务常量一致。"""
    from hsrmap.serve import MAP_PORT, REVIEW_PORT

    found: dict[str, set[int]] = {}
    for name, _note in ENTRY_POINTS:
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        ports = {int(a or b or c) for a, b, c in _PORT.findall(text)}
        if ports:
            found[name] = ports
    monitor = root / "monitor" / "monitor_server.py"
    monitor_port = None
    if monitor.is_file():
        text = monitor.read_text(encoding="utf-8", errors="replace")
        hits = {int(a or b or c) for a, b, c in _PORT.findall(text)}
        monitor_port = sorted(hits)[0] if hits else None
    problems = []
    if str(MAP_PORT) not in {str(p) for ports in found.values() for p in ports}:
        problems.append(f"start.bat 没有用地图端口 {MAP_PORT}")
    if str(REVIEW_PORT) not in {str(p) for ports in found.values() for p in ports}:
        problems.append(f"start_review.bat 没有用审核端口 {REVIEW_PORT}")
    used = {port: name for name, ports in found.items() for port in ports}
    if monitor_port is not None:
        if monitor_port in {MAP_PORT, REVIEW_PORT}:
            problems.append(f"监控台端口 {monitor_port} 与地图/审核台冲突")
        used.setdefault(monitor_port, "monitor/monitor_server.py")
    stop = (root / "monitor" / "stop_monitor.bat")
    if monitor_port is not None and stop.is_file():
        stop_text = stop.read_text(encoding="utf-8", errors="replace")
        if str(monitor_port) not in stop_text:
            problems.append(f"stop_monitor.bat 停的不是监控台端口 {monitor_port}")
    mapping = {str(k): v for k, v in sorted(used.items())}
    if problems:
        return _fail("ports", "；".join(problems), ports=mapping)
    return _ok("ports", f"端口互不冲突：{json.dumps(mapping, ensure_ascii=False)}", ports=mapping)


def check_preflight(root: Path) -> dict[str, Any]:
    """两个服务各自的起飞前检查（缺库/缺前端产物）没有致命项。"""
    from hsrmap.serve import preflight

    problems = []
    warnings = []
    for kind in ("map", "review"):
        fatal, notes = preflight(kind)
        problems += [f"{kind}: {item}" for item in fatal]
        warnings += notes
    if problems:
        return _fail("preflight", "；".join(problems))
    return _ok("preflight", "地图与审核台的起飞前检查都通过" + (f"（提醒 {len(warnings)} 条）" if warnings else ""))


def check_first_load(root: Path) -> dict[str, Any]:
    """把浏览器第一次打开两个页面要发的请求全部发一遍（TestClient，不占端口）。"""
    from fastapi.testclient import TestClient

    from hsrmap.serve import build_app

    steps: list[str] = []
    failures: list[str] = []
    payloads: dict[str, int] = {}

    def call(client: TestClient, path: str, *, budget: str | None = None) -> bytes:
        response = client.get(path)
        if response.status_code != 200:
            failures.append(f"{path} -> {response.status_code}")
            steps.append(f"FAIL {response.status_code} {path}")
            return b""
        size = len(response.content)
        payloads[path] = size
        if budget and size > PAYLOAD_BUDGET[budget]:
            failures.append(
                f"{path} 返回 {size / 1048576:.1f} MB，超过预算 "
                f"{PAYLOAD_BUDGET[budget] / 1048576:.1f} MB"
            )
        steps.append(f"ok   {response.status_code} {size:>9} B  {path}")
        return response.content

    map_client = TestClient(build_app("map"))
    home = call(map_client, "/")
    for asset in re.findall(rb'(?:src|href)="(/app/[^"]+)"', home):
        call(map_client, asset.decode("utf-8"))
    call(map_client, "/api/v1/maps/tree", budget="/api/v1/maps/tree")
    call(map_client, "/api/v1/guides/index", budget="/api/v1/guides/index")
    call(map_client, "/api/v1/guides/evidence", budget="/api/v1/guides/evidence")
    call(map_client, "/api/v1/user/points")
    tree = json.loads(call(map_client, "/api/v1/maps/tree") or b"[]")

    def first_renderable(nodes: list[dict[str, Any]]) -> str | None:
        for node in nodes:
            if node.get("renderable"):
                return str(node["id"])
            found = first_renderable(node.get("children") or [])
            if found:
                return found
        return None

    station = next((node for node in tree if "空间站" in str(node.get("name") or "")), None)
    #: 和前端一致的落点：优先「空间站」世界下的第一张可渲染地图。
    map_id = first_renderable((station or {}).get("children") or []) if station else None
    map_id = map_id or first_renderable(tree)
    return_early = None
    if map_id:
        call(map_client, f"/api/v1/maps/{map_id}")
        points = json.loads(call(map_client, f"/api/v1/maps/{map_id}/points") or b"[]")
        call(map_client, f"/api/v1/maps/{map_id}/labels")
        if not points:
            return_early = f"落点地图 {map_id} 没有任何点位"
        else:
            point = points[0]
            call(map_client, f"/api/v1/points/{point['id']}")
        if return_early:
            failures.append(return_early)

    review_client = TestClient(build_app("review"))
    review_home = call(review_client, "/review")
    for asset in re.findall(rb'(?:src|href)="([^"]+\.(?:js|css))"', review_home):
        call(review_client, asset.decode("utf-8"))
    body = call(review_client, "/api/v1/review/items", budget="/api/v1/review/items")
    for path in ("/api/v1/review/items", "/api/v1/guides/topics", "/api/v1/atlas/gates"):
        if path != "/api/v1/review/items":
            call(review_client, path)
    payload = json.loads(body) if body else {}
    rows = payload.get("maps") or []
    if not rows:
        failures.append("审核队列是空的（列表页会显示「队列是空的」）")
    else:
        item_id = rows[0].get("item_id")
        if item_id:
            call(review_client, f"/api/v1/review/maps/{item_id}", budget="/api/v1/review/maps/{item}")
        #: 列表行必须是「索引」：带 draft/图片数组就说明 slim 没生效。
        heavy = [key for key in ("draft", "layout", "images") if rows[0].get(key)]
        if heavy:
            failures.append(f"队列列表行仍带着重字段 {heavy}（首屏会变慢）")

    if failures:
        return _fail("first-load", "；".join(failures), steps=steps, payload_bytes=payloads)
    return _ok(
        "first-load",
        f"地图与审核台首屏 {len(steps)} 个请求全部 200（最大 {max(payloads.values()) / 1024:.0f} KB）",
        steps=steps,
        payload_bytes=payloads,
    )


def run_checks(root: Path | None = None) -> dict[str, Any]:
    base = Path(root or Path(__file__).resolve().parents[1])
    checks: list[tuple[str, Callable[[Path], dict[str, Any]]]] = [
        ("entry-points", check_entry_points),
        ("ports", check_ports),
        ("preflight", check_preflight),
        ("first-load", check_first_load),
    ]
    items: list[dict[str, Any]] = []
    for item_id, checker in checks:
        try:
            items.append(checker(base))
        except Exception as exc:  # noqa: BLE001 - 单项失败不该让整份自检塌掉
            items.append(_fail(item_id, f"{type(exc).__name__}: {exc}"))
    failed = [item for item in items if item["status"] == "FAIL"]
    skipped = [item for item in items if item["status"] == "SKIPPED"]
    return {
        "items": items,
        "passed": len(items) - len(failed) - len(skipped),
        "failed": len(failed),
        "skipped": len(skipped),
        "result": "FAIL" if failed else ("PASS" if not skipped else "PASS (partial)"),
        "ok": not failed,
    }


def render_doctor(report: dict[str, Any]) -> str:
    titles = {
        "entry-points": "入口文件与它们引用的脚本",
        "ports": "端口不冲突且与启动脚本一致",
        "preflight": "起飞前检查（缺库/缺前端产物）",
        "first-load": "首屏闭环（页面要发的请求都发一遍 + 载荷预算）",
    }
    lines = ["闭环自检（启动入口能不能用）", ""]
    for item in report["items"]:
        mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIPPED": "SKIP"}[item["status"]]
        lines.append(f"{titles.get(item['id'], item['id']):.<34} {mark}")
        if item.get("detail"):
            lines.append(f"    {item['detail']}")
        for step in (item.get("steps") or [])[:6]:
            lines.append(f"      {step}")
        if len(item.get("steps") or []) > 6:
            lines.append(f"      … 还有 {len(item['steps']) - 6} 步")
    lines += ["", f"DOCTOR RESULT ........ {report['result']}"]
    return "\n".join(lines) + "\n"
