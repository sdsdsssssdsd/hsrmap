"""本地服务：离线地图与审核台**分成两个进程/端口**。

用户 2026-10 要求：审核网页与离线地图不要耦合。这里的边界是：

* 离线地图（kind=map）：默认端口 8766，根路径就是地图；需要快照/core.db 与 web/dist，
  **不打开** guide.db / published.db；
* 审核台（kind=review）：默认端口 8767，根路径跳 /review；需要 guide.db / published.db，
  **不绑定**快照与 core.db；
* kind=all：两者挂在同一个进程（兼容老用法：hsrmap serve --app all）。

两个进程共用同一个用户库（user.db，用户标记与设置），但各自只开自己需要的句柄。
"""

from __future__ import annotations

import socket
import threading
import webbrowser

import uvicorn

from hsrmap.paths import USER_DB
from hsrmap.user_db import UserDatabase
from hsrmap.viewer_app import create_app, create_map_app, create_review_app

#: 离线地图的历史默认端口。
MAP_PORT = 8766
#: 审核台端口（与地图分开，互不影响）。
REVIEW_PORT = 8767

#: kind → 分区组合。
KINDS: dict[str, tuple[str, ...]] = {
    "map": ("map",),
    "review": ("review",),
    "all": ("map", "review"),
}

#: kind → 打开浏览器时的落地路径。
LANDING = {"map": "/", "review": "/review", "all": "/review"}


def resolve_kind(value: str | None) -> str:
    kind = str(value or "map").strip().lower() or "map"
    if kind not in KINDS:
        raise SystemExit(f"unknown app kind: {value!r} (expected one of {sorted(KINDS)})")
    return kind


def resolve_port(port: int | None, kind: str = "map") -> int:
    if port:
        return int(port)
    return MAP_PORT if kind == "map" else REVIEW_PORT


def resolve_data_mode(stored: str | None) -> str:
    return stored if stored in {"offline", "hybrid", "live"} else "offline"


def pick_port(start: int = MAP_PORT, attempts: int = 20) -> int:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port")


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) != 0


def _stored_data_mode() -> str:
    try:
        user = UserDatabase(USER_DB)
        stored = user.get_meta("data_mode")
        user.close()
    except Exception:
        stored = None
    return resolve_data_mode(stored)


# --------------------------------------------------------------------------- #
# 起飞前检查（a1-8 十六：入口要么能用，要么说清楚缺什么）
# --------------------------------------------------------------------------- #

def preflight(kind: str) -> tuple[list[str], list[str]]:
    """(致命问题, 提醒)。

    致命 = 起来也是坏的（审核台没有攻略库、地图没有前端产物）；
    提醒 = 起得来但看不到东西（没有快照/没有发布库）。
    以前缺库时 uvicorn 直接抛栈，双击 .bat 的人只看到一闪而过的窗口。
    """
    from hsrmap.paths import CURRENT_PATH, GUIDE_DB, GUIDE_PUBLISHED_DB, ROOT, get_runtime
    from hsrmap.viewer_app import WEB_DIST

    kind = resolve_kind(kind)
    fatal: list[str] = []
    warnings: list[str] = []
    if kind in {"map", "all"}:
        if not (WEB_DIST / "index.html").is_file():
            fatal.append(f"前端产物缺失：{WEB_DIST}（先跑 cd web && npm run build）")
        if not CURRENT_PATH.is_file():
            warnings.append(f"还没有离线快照：{CURRENT_PATH}（先跑 python -m hsrmap sync；地图会起得来但没数据）")
    if kind in {"review", "all"}:
        for label, path in (("攻略库", GUIDE_DB), ("发布库", GUIDE_PUBLISHED_DB)):
            if not path.is_file():
                fatal.append(f"{label}不存在：{path}（map 进程不需要它，review 需要）")
    runtime = get_runtime()
    if runtime.source == "user" and not str(runtime.root).startswith(str(ROOT)):
        warnings.append(f"运行目录在用户数据目录：{runtime.root}（用 --data-dir 指向仓库 data/ 可切回）")
    return fatal, warnings


def report_preflight(kind: str) -> int:
    """打印检查结果；致命问题返回 2（gate 阻断），否则 0。"""
    fatal, warnings = preflight(kind)
    for item in warnings:
        print(f"[hsrmap] 提醒：{item}")
    for item in fatal:
        print(f"[hsrmap] 无法启动：{item}")
    return 2 if fatal else 0


def build_app(kind: str = "map", *, data_mode: str | None = None, **app_kwargs):
    """按分区建 app（不启服务器，便于测试与复用）。

    app_kwargs 原样转给 create_map_app / create_review_app / create_app
    （测试用它注入临时库路径，CLI 以后也可以据此开放 --db 之类开关）。
    """
    kind = resolve_kind(kind)
    mode = resolve_data_mode(data_mode) if data_mode is not None else _stored_data_mode()
    if kind == "map":
        return create_map_app(data_mode=mode, **app_kwargs)
    if kind == "review":
        return create_review_app(**app_kwargs)
    return create_app(sections=KINDS[kind], data_mode=mode, **app_kwargs)


def run_server(
    host: str = "127.0.0.1",
    port: int | None = None,
    *,
    kind: str = "map",
    open_browser: bool = True,
) -> None:
    if host != "127.0.0.1":
        raise SystemExit("viewer binds 127.0.0.1 only")
    kind = resolve_kind(kind)
    port = resolve_port(port, kind)
    url = f"http://{host}:{port}{LANDING[kind]}"
    fatal, warnings = preflight(kind)
    for item in warnings:
        print(f"[hsrmap] 提醒：{item}")
    if fatal:
        for item in fatal:
            print(f"[hsrmap] 无法启动：{item}")
        return 2
    print(f"[hsrmap] {url}")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if not _port_free(host, port):
        print(f"[hsrmap] 端口 {port} 上已经有一个服务在跑：{url}（刚给你打开了它的页面；要换端口就改 --port）")
        return 0
    uvicorn.run(build_app(kind), host=host, port=port, log_level="info")
    return 0
