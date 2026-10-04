"""Realm 合同（a1-9 §8）：国服 / 国际服的 host 分开建模。

**binding host 和 srmap host 是两个 API family**，不要混成一个 `host`：

* binding：查「这个账号在哪些服有哪些角色」（`getUserGameRolesByCookie` 一族）；
* srmap：官方互动地图自己的数据与进度（`/v1/map/...`）。

合同来自 `phase1/endpoint_registry.json` 的 `realms` 段（由 P6.0 的 contract discovery 维护）。
本模块**只读文件**：不联网、不猜 host、找不到就报错（fail closed）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hsrmap.paths import REGISTRY_PATH

#: 本项目只做国服（用户 2026-10 指定）；国际服的合同照样保留，但默认不是它。
DEFAULT_REALM = "cn"


class RealmUnavailable(RuntimeError):
    """注册表里没有这个 realm，或者注册表缺 realms 段。"""


@dataclass(frozen=True)
class SrMapRealm:
    name: str
    game_biz: str
    binding_host: str
    map_host: str
    map_host_nocdn: str
    entry: str

    @property
    def is_cn(self) -> bool:
        return self.name.startswith("cn")


def load_registry(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else Path(REGISTRY_PATH)
    if not target.is_file():
        raise RealmUnavailable(f"endpoint registry 不存在：{target}")
    return json.loads(target.read_text(encoding="utf-8"))


def load_realms(path: Path | str | None = None) -> dict[str, SrMapRealm]:
    registry = load_registry(path)
    block = registry.get("realms")
    if not isinstance(block, dict) or not block:
        raise RealmUnavailable("注册表缺 realms 段：先跑 P6.0 contract discovery")
    realms: dict[str, SrMapRealm] = {}
    for name, item in block.items():
        try:
            realms[str(name)] = SrMapRealm(
                name=str(name),
                game_biz=str(item["game_biz"]),
                binding_host=str(item["binding_host"]).rstrip("/"),
                map_host=str(item["map_host"]).rstrip("/"),
                map_host_nocdn=str(item.get("map_host_nocdn") or item["map_host"]).rstrip("/"),
                entry=str(item.get("entry") or ""),
            )
        except KeyError as exc:
            raise RealmUnavailable(f"realm {name} 缺字段 {exc}") from exc
    return realms


def realm_for(name: str | None = None, *, path: Path | str | None = None) -> SrMapRealm:
    """按名字取 realm；不传就用注册表的默认值（本项目是 cn）。"""
    realms = load_realms(path)
    registry = load_registry(path)
    wanted = str(name or registry.get("default_realm") or DEFAULT_REALM)
    if wanted not in realms:
        raise RealmUnavailable(f"未知 realm：{wanted}（可选：{sorted(realms)}）")
    return realms[wanted]
