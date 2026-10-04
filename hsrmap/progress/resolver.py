"""语义裁决（a1-9 §14）：把一堆观察折算成「本地完成 / 没完成 / 说不清」。

规则只有三条，但每一条都对着一个硬门：

1. `manual` 永远算完成 —— 玩家自己勾的，是唯一不需要证明的真相；
2. `game_obtained` 只在 **Gate 0 实验通过**（`VERIFIED_SEMANTICS`）后才算完成；
3. `map_mark` 默认不算完成；即便用户显式选择「接受官方地图标记」，它也必须带上
   `basis="map_mark"` 的标签，界面上不能写成「游戏内已完成」。

`unknown` 不参与推导，只会以「说不清」出现在冲突视图里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from hsrmap.progress.models import (
    SEMANTIC_GAME_OBTAINED,
    SEMANTIC_MANUAL,
    SEMANTIC_MAP_MARK,
    SEMANTIC_UNKNOWN,
    VERIFIED_SEMANTICS,
    ProgressObservation,
)

#: 允许推导完成的语义集合（不含 manual：手动勾选无条件成立）。= Gate 0 的结论。
def allowed_semantics(*, import_map_mark: bool = False, verified: Iterable[str] | None = None) -> frozenset[str]:
    """哪些**远端**语义有资格推导完成。

    `verified` 缺省取 `VERIFIED_SEMANTICS`（当前为空集 → 只有 manual 算完成，fail closed）。
    `import_map_mark` 是用户在界面上的一次显式选择，不是默认行为。
    """
    gate = frozenset(VERIFIED_SEMANTICS if verified is None else verified)
    allowed = {s for s in gate if s in (SEMANTIC_GAME_OBTAINED, SEMANTIC_MAP_MARK)}
    if import_map_mark:
        allowed.add(SEMANTIC_MAP_MARK)
    return frozenset(allowed)


@dataclass(frozen=True)
class PointDecision:
    """一个点位的裁决结果。`basis` 说明「凭什么算完成」，界面文案必须照抄它。"""

    source_point_id: str
    completed: bool = False
    basis: tuple[str, ...] = ()
    unclear: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        if not self.completed:
            return "未完成"
        if SEMANTIC_MAP_MARK in self.basis and SEMANTIC_MANUAL not in self.basis:
            return "官方地图标记"
        if SEMANTIC_GAME_OBTAINED in self.basis:
            return "游戏内已获得"
        return "本地已完成"


@dataclass(frozen=True)
class Resolution:
    points: dict[str, PointDecision] = field(default_factory=dict)
    allowed: frozenset[str] = frozenset()

    @property
    def completed(self) -> set[str]:
        return {pid for pid, decision in self.points.items() if decision.completed}

    @property
    def unclear(self) -> set[str]:
        return {pid for pid, decision in self.points.items() if decision.unclear}

    def describe(self) -> dict[str, object]:
        return {
            "allowed_remote_semantics": sorted(self.allowed),
            "completed": len(self.completed),
            "unclear": len(self.unclear),
            "gate0_verified": sorted(VERIFIED_SEMANTICS),
        }


def resolve(
    observations: Iterable[ProgressObservation],
    *,
    import_map_mark: bool = False,
    verified: Iterable[str] | None = None,
) -> Resolution:
    allowed = allowed_semantics(import_map_mark=import_map_mark, verified=verified)
    basis: dict[str, set[str]] = {}
    unclear: dict[str, set[str]] = {}
    for observation in observations:
        pid = observation.source_point_id
        semantic = observation.semantic
        if semantic == SEMANTIC_UNKNOWN:
            if observation.completed:
                unclear.setdefault(pid, set()).add(semantic)
            continue
        if semantic == SEMANTIC_MANUAL:
            if observation.completed:
                basis.setdefault(pid, set()).add(semantic)
            continue
        if semantic in allowed and observation.completed:
            basis.setdefault(pid, set()).add(semantic)
        elif observation.completed:
            #: 看到了「完成」，但这个语义还没拿到推导权 → 记成「说不清」，绝不写成完成。
            unclear.setdefault(pid, set()).add(semantic)
    points: dict[str, PointDecision] = {}
    for pid in sorted(set(basis) | set(unclear)):
        got = basis.get(pid, set())
        points[pid] = PointDecision(
            source_point_id=pid,
            completed=bool(got),
            basis=tuple(sorted(got)),
            unclear=tuple(sorted(unclear.get(pid, set()))),
        )
    return Resolution(points=points, allowed=allowed)


def resolve_groups(
    groups: Mapping[str, Iterable[ProgressObservation]],
    **kwargs,
) -> Resolution:
    """按 profile 分组裁决（多账号/多区服时互不串味）。"""
    flat: list[ProgressObservation] = []
    for items in groups.values():
        flat.extend(items)
    return resolve(flat, **kwargs)
