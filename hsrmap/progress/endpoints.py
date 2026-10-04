"""端点合同 + **框架层**拒绝写接口（a1-9 §10）。

registry 里每个端点都有 `mutating` 字段。progress client 想调用一个端点，必须先经过
`read_contract()`：`mutating=true` 直接抛 `WriteEndpointForbidden`。

这样 `save_point_status` / `add_mark_map_point` / `sync_game_spot` 即使被误写进调用链，
也进不去网络层——比「开发者记得别调用」可靠。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from hsrmap.progress.realm import load_registry


class EndpointUnavailable(RuntimeError):
    """注册表里没有这个端点。"""


class WriteEndpointForbidden(PermissionError):
    """想调用一个 mutating 端点：progress 层是只读的，一律拒绝。"""

    def __init__(self, name: str, path: str = "") -> None:
        self.endpoint = name
        super().__init__(f"progress 层只读：拒绝调用写端点 {name} {path}".strip())


@dataclass(frozen=True)
class EndpointContract:
    name: str
    method: str
    path: str
    auth: str
    mutating: bool
    parameters: tuple[str, ...] = ()

    @property
    def needs_cookie(self) -> bool:
        return self.auth.lower() in {"cookie", "credentials", "auth"}


def load_contracts(path: Path | str | None = None) -> dict[str, EndpointContract]:
    registry = load_registry(path)
    block = registry.get("endpoints")
    if not isinstance(block, dict) or not block:
        raise EndpointUnavailable("注册表缺 endpoints 段")
    out: dict[str, EndpointContract] = {}
    for name, item in block.items():
        out[str(name)] = EndpointContract(
            name=str(name),
            method=str(item.get("method") or "GET").upper(),
            path=str(item.get("path") or ""),
            auth=str(item.get("auth") or "none"),
            #: 缺 mutating 字段时按**最保守**处理：当成写端点，宁可拒绝。
            mutating=bool(item.get("mutating", True)),
            parameters=tuple(str(p) for p in item.get("parameters") or ()),
        )
    return out


def read_contract(contracts: dict[str, EndpointContract], name: str) -> EndpointContract:
    contract = contracts.get(str(name))
    if contract is None:
        raise EndpointUnavailable(f"注册表里没有端点 {name}")
    if contract.mutating:
        raise WriteEndpointForbidden(contract.name, contract.path)
    return contract


def mutating_endpoints(contracts: dict[str, EndpointContract]) -> list[str]:
    return sorted(name for name, item in contracts.items() if item.mutating)


def readonly_endpoints(contracts: dict[str, EndpointContract]) -> list[str]:
    return sorted(name for name, item in contracts.items() if not item.mutating)


def summarise(contracts: Iterable[EndpointContract]) -> dict[str, Any]:
    items = list(contracts)
    return {
        "total": len(items),
        "mutating": sorted(item.name for item in items if item.mutating),
        "readonly": sorted(item.name for item in items if not item.mutating),
        "requires_cookie": sorted(item.name for item in items if item.needs_cookie),
    }
