"""官方地图进度适配器（a1-9 §9/§11）：role → treasure count → point status，全部只读。

调用顺序（probe 也按这个顺序）：

```text
1. binding_role_list   这个账号在国服有哪些角色（uid / region / nickname / level）
2. get_user_treasure_count  某个地图的「官方计数」：user_obtained_count / map_all_treasure_count
3. get_point_status    逐点状态（请求体字段名 unverified，等实测）
```

**这里不解释语义**：适配器只负责把官方接口的回答原样搬回来。至于「这个 true 是地图标记还是
游戏开箱」，那是 resolver 的事，而且必须等 Gate 0（a1-9 §12 的 2×2 实验）出结论。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from hsrmap.progress.adapter import ProgressResponse, ReadOnlyClient
from hsrmap.progress.realm import SrMapRealm

#: 国服常见 region（roleInfo.region 会带回来，这里只是给诊断用的默认值）。
CN_REGIONS = ("prod_gf_cn", "prod_qd_cn")


class AppVersionUnavailable(RuntimeError):
    """app_version 自动发现失败：fail closed（a1-9 §18），不去猜一个版本号。"""


def discover_app_version() -> str:
    """从官方 entry + bundle 读出当前的 app_version；读不到就 fail closed。"""
    from hsrmap_phase1.client import discover_frontend

    try:
        frontend = discover_frontend()
    except Exception as exc:  # noqa: BLE001 - 网络/解析失败都归到这里
        raise AppVersionUnavailable(f"app_version 发现失败：{type(exc).__name__}") from exc
    version = str(frontend.get("app_version") or "").strip()
    if not version:
        raise AppVersionUnavailable("bundle 里没有 app_version：fail closed，不猜")
    return version


def roles(client: ReadOnlyClient) -> list[dict[str, Any]]:
    """这个账号在国服/国际服绑定过的角色。空列表 = 没绑定（B 服账号很常见）。"""
    response = client.require_ok(client.request(
        "binding_role_list", params={"game_biz": client.realm.game_biz},
    ))
    data = response.data
    if isinstance(data, dict):
        items = data.get("list") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [dict(item) for item in items if isinstance(item, Mapping)]


def pick_role(items: Iterable[Mapping[str, Any]], uid: str | None = None) -> dict[str, Any] | None:
    """挑一个角色：给了 uid 就按 uid 挑，否则取第一个。"""
    rows = [dict(item) for item in items]
    if not rows:
        return None
    if uid:
        for row in rows:
            if str(row.get("game_uid") or row.get("uid") or "") == str(uid):
                return row
        return None
    return rows[0]


def treasure_count(
    client: ReadOnlyClient,
    *,
    uid: Any,
    map_id: Any,
    server: str,
) -> dict[str, Any]:
    """官方计数接口：`user_obtained_count` / `map_all_treasure_count` / `treasure_label_id`。

    **语义未定**：数值受网页地图标记驱动（Case A）还是受游戏开箱驱动（Case B/C）由 Gate 0 判定。
    """
    response = client.require_ok(client.request(
        "get_user_treasure_count",
        params={"uid": uid, "map_id": map_id, "server": server},
    ))
    data = response.data if isinstance(response.data, Mapping) else {}
    return {
        "map_id": map_id,
        "server": server,
        "user_obtained_count": data.get("user_obtained_count"),
        "map_all_treasure_count": data.get("map_all_treasure_count"),
        "treasure_label_id": data.get("treasure_label_id"),
        "raw_keys": sorted(str(key) for key in data),
    }


def point_status(
    client: ReadOnlyClient,
    *,
    body: Mapping[str, Any],
) -> ProgressResponse:
    """逐点状态。请求体形状标为 unverified：probe 会把原始 retcode/message 原样报出来。"""
    return client.request("get_point_status", json_body=dict(body))


def mark_list(client: ReadOnlyClient, *, body: Mapping[str, Any] | None = None) -> ProgressResponse:
    """官方地图标记列表（只读）。"""
    return client.request("mark_map_point_list", json_body=dict(body or {}))


def default_point_status_body(*, uid: Any, map_id: Any, server: str, point_ids: Iterable[Any]) -> dict[str, Any]:
    """**猜测**的请求体（候选形状之一）。

    名字里就写着 default/guess：它进的是 probe 的诊断报告，不是生产路径。
    实测出真实形状后，把 `verified` 从 unverified 改成 bundle/probe，并让 probe 使用确认过的形状。
    """
    return {
        "uid": uid,
        "map_id": map_id,
        "server": server,
        "point_ids": [str(item) for item in point_ids],
    }


def describe_realm(realm: SrMapRealm) -> dict[str, Any]:
    return {
        "realm": realm.name,
        "game_biz": realm.game_biz,
        "binding_host": realm.binding_host,
        "map_host": realm.map_host,
        "entry": realm.entry,
    }
