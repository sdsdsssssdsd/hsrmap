"""语义探针（a1-9 §11/§12）：只查，不写。

三件事：

1. 把「这个账号能看到什么」原样搬回来：角色 → 官方计数 → 逐点状态；
2. 把**没证实**的东西标出来：请求体形状、响应字段名、语义（map_mark vs game_obtained）都标 unverified；
3. 打印 2×2 实验的操作清单，等真人（游戏内开箱 / 网页标记）把读数补上。

**它不会碰 `user.db`**：模块不 import 本地进度库、也不写任何数据库；输出只是一份 JSON 报告，
UID 一律打码，cookie 一律不出现。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from hsrmap.progress.adapter import ProgressApiError, ProgressResponse, ReadOnlyClient
from hsrmap.progress.cookie import Credential, mask_uid
from hsrmap.progress.hoyolab import (
    AppVersionUnavailable,
    default_point_status_body,
    describe_realm,
    discover_app_version,
    pick_role,
    point_status,
    roles,
    treasure_count,
)
from hsrmap.progress.realm import SrMapRealm, realm_for
from hsrmap.progress.shapes import shape_entry

PROBE_VERSION = 2  #: v2 起报告里带 `shapes`（结构指纹），用于 schema drift 比对


def record_shape(report: dict[str, Any], endpoint: str, payload: Any) -> None:
    report.setdefault("shapes", {})[endpoint] = shape_entry(payload)

#: 2×2 实验（a1-9 §12）：四种操作 × 两个读数接口。语义只有跑完这张表才敢定。
EXPERIMENT_MATRIX: tuple[dict[str, str], ...] = (
    {
        "action": "网页地图标记一个点（mark）",
        "then": "重跑 python -m hsrmap progress probe，比较 user_obtained_count 与 point_status 里该点",
        "question": "I2: point_status 是否 +1？I3: user_obtained_count 是否 +1？",
    },
    {
        "action": "网页地图取消标记同一个点（unmark）",
        "then": "重跑 probe，比较两个读数是否回退",
        "question": "回退说明计数受网页标记驱动（Case A 的一半）",
    },
    {
        "action": "游戏内开一个新箱子，**完全不碰网页地图**",
        "then": "重跑 probe，比较两个读数",
        "question": "都变 = Case C（可以谈 game_obtained）；只有 count 变 = Case B（两个接口语义不同）",
    },
    {
        "action": "重登 / 刷新官方地图后再跑一次",
        "then": "确认读数是否与上一次一致（排除缓存假象）",
        "question": "不一致说明有会话级缓存，报告要记下来",
    },
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def mask_nickname(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return text[0] + "*" * max(0, len(text) - 1)


def default_map_id() -> tuple[str | None, str]:
    """从离线快照里挑一张有点位的地图；挑不到就如实说，不编一个 id。"""
    try:
        import sqlite3

        from hsrmap.paths import CURRENT_PATH, SNAPSHOTS

        current = json.loads(Path(CURRENT_PATH).read_text(encoding="utf-8"))
        core = Path(SNAPSHOTS) / str(current["snapshot_id"]) / "core.db"
        if not core.is_file():
            return None, f"快照库不存在：{core}"
        conn = sqlite3.connect(f"file:{core.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT m.source_id, COUNT(*) AS n FROM points p JOIN maps m ON m.id = p.map_id"
                " GROUP BY m.source_id ORDER BY n DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None, "快照里没有任何点位"
        return str(row[0]), f"取自离线快照（点位最多的一张图，{row[1]} 个点）"
    except Exception as exc:  # noqa: BLE001 - 挑不到就如实报，探针不该因此失败
        return None, f"无法从快照挑地图：{type(exc).__name__}"


def sample_point_ids(map_id: str, limit: int = 5) -> list[str]:
    """从快照里取几个点位 id 用于试探 point_status 的请求体（只读）。"""
    try:
        import sqlite3

        from hsrmap.paths import CURRENT_PATH, SNAPSHOTS

        current = json.loads(Path(CURRENT_PATH).read_text(encoding="utf-8"))
        core = Path(SNAPSHOTS) / str(current["snapshot_id"]) / "core.db"
        conn = sqlite3.connect(f"file:{core.as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT p.source_id FROM points p JOIN maps m ON m.id = p.map_id"
                " WHERE m.source_id = ? ORDER BY p.id LIMIT ?",
                (str(map_id), int(limit)),
            ).fetchall()
        finally:
            conn.close()
        return [str(row[0]) for row in rows]
    except Exception:  # noqa: BLE001 - 拿不到就算了，探针照样能报计数
        return []


def _status_probe(client: ReadOnlyClient, *, uid: Any, map_id: Any, server: str, point_ids: list[str]) -> dict[str, Any]:
    if not point_ids:
        return {"attempted": False, "reason": "没有可用的 point_id 样本（快照里没读到）"}
    body = default_point_status_body(uid=uid, map_id=map_id, server=server, point_ids=point_ids)
    try:
        response = point_status(client, body=body)
    except ProgressApiError as exc:
        return {"attempted": True, "request_body_keys": sorted(body), "ok": False,
                "retcode": exc.retcode, "status": exc.status, "message": exc.message[:200],
                "note": "请求体形状 unverified：把 retcode/message 记下来用于修正合同"}
    return {
        "attempted": True,
        "request_body_keys": sorted(body),
        "ok": response.ok,
        "retcode": response.retcode,
        "message": response.message[:200],
        "data_keys": sorted(str(key) for key in (response.data or {})) if isinstance(response.data, Mapping) else [],
        "verified": False,
        #: 只记形状不记值：这份指纹进得了仓库，却不含任何 UID / 状态值（a1-9 §18 schema drift）。
        "shape": shape_entry(response.data),
    }


def run_probe(
    *,
    realm_name: str | None = None,
    uid: str | None = None,
    map_id: str | None = None,
    point_limit: int = 5,
    credential: Credential | None = None,
    client: ReadOnlyClient | None = None,
    include_experiments: bool = True,
) -> dict[str, Any]:
    """跑一次只读探针；返回报告（也是写进 progress-probe.json 的内容）。"""
    realm: SrMapRealm = realm_for(realm_name)
    report: dict[str, Any] = {
        "probe_version": PROBE_VERSION,
        "generated_at": _now(),
        "realm": describe_realm(realm),
        "credential_present": False,
        "notes": [],
        "steps": {},
        "invariants": {"I1": None, "I2": None, "I3": None},
    }
    if include_experiments:
        report["experiments"] = [dict(item) for item in EXPERIMENT_MATRIX]
    if credential is None and client is None:
        from hsrmap.progress.cookie import credential_from_env

        credential = credential_from_env()
    if client is None and credential is None:
        report["ok"] = False
        report["reason"] = "no_cookie"
        report["notes"].append(
            "没有 HSRMAP_HOYOLAB_COOKIE：这是合法状态。设置后重跑；不想给凭据也可以只用本地进度。"
        )
        return report
    report["credential_present"] = True
    if client is None:
        try:
            app_version = discover_app_version()
        except AppVersionUnavailable as exc:
            report["ok"] = False
            report["reason"] = "app_version_unavailable"
            report["notes"].append(f"fail closed：{exc}")
            return report
        report["app_version"] = app_version
        client = ReadOnlyClient(realm, credential=credential, app_version=app_version)

    role_rows: list[dict[str, Any]] = []
    try:
        role_rows = roles(client)
        report["steps"]["roles"] = {"ok": True, "count": len(role_rows)}
        record_shape(report, "binding_role_list", role_rows)
    except Exception as exc:  # noqa: BLE001 - 探针失败也要有报告
        report["steps"]["roles"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    role = pick_role(role_rows, uid)
    if role is None:
        report["ok"] = False
        report["reason"] = "no_role"
        report["notes"].append(
            "这个账号在 " + realm.game_biz + " 下没有可用角色：B 服账号需要在米游社绑定角色后才有进度。"
            "远端拿不到不影响本地功能。"
        )
        report["steps"]["role"] = {"ok": False, "requested_uid": mask_uid(uid) if uid else None}
        return report
    game_uid = role.get("game_uid") or role.get("uid")
    server = str(role.get("region") or (realm.name == "cn" and "prod_gf_cn") or "")
    report["role"] = {
        "uid_masked": mask_uid(game_uid),
        "region": server,
        "nickname_masked": mask_nickname(role.get("nickname")),
        "level": role.get("level"),
        "game_biz": role.get("game_biz") or realm.game_biz,
    }
    report["steps"]["role"] = {"ok": True}

    chosen_map = str(map_id).strip() if map_id else ""
    if not chosen_map:
        chosen_map, why = default_map_id()
        report["notes"].append(f"map_id 默认值：{why}")
    if not chosen_map:
        report["ok"] = False
        report["reason"] = "no_map_id"
        return report
    try:
        treasure = treasure_count(client, uid=game_uid, map_id=chosen_map, server=server)
        report["steps"]["treasure"] = {"ok": True, **treasure}
        record_shape(report, "get_user_treasure_count", treasure)
    except Exception as exc:  # noqa: BLE001
        report["steps"]["treasure"] = {"ok": False, "map_id": chosen_map,
                                       "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    point_ids = sample_point_ids(chosen_map, limit=int(point_limit))
    report["steps"]["point_status"] = _status_probe(
        client, uid=game_uid, map_id=chosen_map, server=server, point_ids=point_ids
    )
    shape = report["steps"]["point_status"].get("shape")
    if shape:
        report.setdefault("shapes", {})["get_point_status"] = shape
    report["ok"] = bool(report["steps"].get("treasure", {}).get("ok"))
    report["exchanges"] = list(client.exchanges)
    report["semantics"] = {
        "map_mark": "官方地图标记状态（未证实）",
        "game_obtained": "游戏内真实开箱（未证实）",
        "rule": "Gate 0 未过之前，任何语义都不允许推导 completed（a1-9 §18）",
    }
    return report


def write_report(report: Mapping[str, Any], path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def render_probe(report: Mapping[str, Any]) -> str:
    lines = ["progress probe（只读诊断；不写 user.db）", ""]
    realm = report.get("realm") or {}
    lines.append(f"  realm ............... {realm.get('name')} / {realm.get('game_biz')}")
    lines.append(f"  cookie .............. {'有' if report.get('credential_present') else '没有'}")
    if report.get("app_version"):
        lines.append(f"  app_version ......... {report['app_version']}")
    role = report.get("role") or {}
    if role:
        lines.append(
            f"  role ................ {role.get('uid_masked')} @ {role.get('region')}"
            f"（{role.get('nickname_masked')} lv{role.get('level')}）"
        )
    treasure = (report.get("steps") or {}).get("treasure") or {}
    if treasure:
        lines.append(
            f"  treasure count ...... {treasure.get('user_obtained_count')} / "
            f"{treasure.get('map_all_treasure_count')}（map {treasure.get('map_id')}）"
        )
    status = (report.get("steps") or {}).get("point_status") or {}
    if status:
        lines.append(
            f"  point_status ........ attempted={status.get('attempted')} "
            f"retcode={status.get('retcode')} {status.get('message') or ''}".rstrip()
        )
    for note in report.get("notes") or []:
        lines.append(f"  note ................ {note}")
    if report.get("experiments"):
        lines += ["", "2×2 实验（Gate 0：语义只有跑完这张表才敢定）"]
        for index, item in enumerate(report["experiments"], start=1):
            lines.append(f"  {index}. {item['action']}")
            lines.append(f"     → {item['then']}")
            lines.append(f"     ? {item['question']}")
    lines += ["", f"PROBE RESULT ........ {'OK' if report.get('ok') else 'INCOMPLETE'}"
              f"{'' if report.get('ok') else '（' + str(report.get('reason')) + '）'}"]
    return "\n".join(lines) + "\n"
