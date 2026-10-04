"""进度差异（a1-9 P6.4）：只读比对，默认 dry-run，写库必须显式确认。

```text
both          本地勾了 + 远端也标了
local_only    本地勾了，远端没提
remote_only   远端标了，本地没有      ← 唯一可能被合并的一桶（且要用户显式确认）
unknown       远端给了个说不清的语义  ← 永远不合并
```

`merge` 的三条自我约束：

* 默认 `confirm=False`：只出计划，一个字节都不写；
* 只会把 `False → True`，**从不把任何 `completed` 改回 False**；
* 合并范围必须由调用方点名语义（`semantics={"map_mark"}`），不点名就什么都不动。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from hsrmap.progress import store
from hsrmap.progress.models import (
    SEMANTIC_MANUAL,
    SEMANTIC_UNKNOWN,
    SOURCE_LOCAL,
    ProgressObservation,
    now,
)
from hsrmap.progress.resolver import allowed_semantics, resolve
from hsrmap.user_db import UserDatabase

#: 合并时允许写进 `point_progress` 的语义：只认手动 + 显式点名的远端语义。
MERGE_META_KEY = "progress_last_merge"


@dataclass(frozen=True)
class ProgressDiff:
    both: frozenset[str] = frozenset()
    local_only: frozenset[str] = frozenset()
    remote_only: frozenset[str] = frozenset()
    unknown: frozenset[str] = frozenset()
    remote_by_semantic: dict[str, frozenset[str]] = field(default_factory=dict)
    local_total: int = 0
    remote_total: int = 0
    allowed_remote_semantics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "local_total": self.local_total,
            "remote_total": self.remote_total,
            "both": len(self.both),
            "local_only": len(self.local_only),
            "remote_only": len(self.remote_only),
            "unknown": len(self.unknown),
            "remote_by_semantic": {k: len(v) for k, v in sorted(self.remote_by_semantic.items())},
            "allowed_remote_semantics": list(self.allowed_remote_semantics),
            "dry_run": True,
        }

    def render(self) -> str:
        lines = [
            f"本地完成 {self.local_total} · 远端观察 {self.remote_total}",
            f"  两边都有   {len(self.both)}",
            f"  仅本地     {len(self.local_only)}",
            f"  仅远端     {len(self.remote_only)}",
            f"  说不清     {len(self.unknown)}",
        ]
        if self.remote_by_semantic:
            lines.append("  远端语义：" + "、".join(
                f"{name} {len(points)}" for name, points in sorted(self.remote_by_semantic.items())
            ))
        if self.allowed_remote_semantics:
            lines.append("  可推导语义：" + "、".join(self.allowed_remote_semantics))
        else:
            lines.append("  可推导语义：（无）→ 远端状态目前只能展示，不能改本地完成")
        return "\n".join(lines)


def local_completed(db: UserDatabase, *, profile_id: str | None = None) -> set[str]:
    """本地完成 = `point_progress` 的勾选 ∪ manual 观察（两条路都算，只多不少）。"""
    rows = db.conn.execute("SELECT source_point_id FROM point_progress WHERE completed = 1").fetchall()
    done = {str(row["source_point_id"]) for row in rows}
    done |= store.manual_points(db) if profile_id is None else {
        o.source_point_id
        for o in store.observations(db, profile_id=profile_id, semantic=SEMANTIC_MANUAL)
        if o.completed
    }
    return done


def build_diff(
    db: UserDatabase,
    *,
    profile_id: str | None = None,
    import_map_mark: bool = False,
    verified: Iterable[str] | None = None,
) -> ProgressDiff:
    """只读比对：不写库、不发网络请求。"""
    observations = store.observations(db, profile_id=profile_id)
    decision = resolve(observations, import_map_mark=import_map_mark, verified=verified)
    allowed = allowed_semantics(import_map_mark=import_map_mark, verified=verified)

    local = local_completed(db, profile_id=profile_id)
    remote_by_semantic: dict[str, set[str]] = {}
    for observation in observations:
        if observation.completed and observation.semantic != SEMANTIC_MANUAL:
            remote_by_semantic.setdefault(observation.semantic, set()).add(observation.source_point_id)

    remote_all = {pid for points in remote_by_semantic.values() for pid in points}
    accepted = decision.completed - local  # 远端观察里够格推导、且本地还没有的
    unknown = decision.unclear - local - accepted
    return ProgressDiff(
        both=frozenset(local & (accepted | remote_all)),
        local_only=frozenset(local - remote_all),
        remote_only=frozenset(accepted - local),
        unknown=frozenset(unknown),
        remote_by_semantic={k: frozenset(v) for k, v in remote_by_semantic.items()},
        local_total=len(local),
        remote_total=len(remote_all),
        allowed_remote_semantics=tuple(sorted(allowed)),
    )


def merge_plan(
    diff: ProgressDiff,
    *,
    semantics: Iterable[str] | None = None,
) -> dict[str, str]:
    """能写进本地完成的点位 → 依据的语义（dry-run 也走这条函数，计划与执行同一份逻辑）。

    只认「已经在可推导集合里的语义」，而且从 `both`/`local_only` 里剔掉已经完成的点。
    """
    wanted = set(semantics or ()) & set(diff.allowed_remote_semantics)
    if not wanted:
        return {}
    settled = set(diff.both) | set(diff.local_only)
    plan: dict[str, str] = {}
    for semantic in sorted(wanted):
        for point_id in sorted(diff.remote_by_semantic.get(semantic, ())):
            if point_id in settled:
                continue
            plan.setdefault(point_id, semantic)
    return plan


def merge(
    db: UserDatabase,
    diff: ProgressDiff,
    *,
    semantics: Iterable[str],
    confirm: bool = False,
    profile_id: str = "local",
) -> dict[str, Any]:
    """把点名语义的「仅远端」点位合并进本地完成。

    `confirm=False`（缺省）只返回计划，一个字节都不写；`True` 才落库，而且只写 `completed=1`。
    ``completed=False`` 的写入数永远是 0，这条会写进报告里接受检查。
    """
    plan = merge_plan(diff, semantics=semantics)
    report: dict[str, Any] = {
        "planned": len(plan),
        "applied": 0,
        "semantics": sorted(set(semantics) & set(diff.allowed_remote_semantics)),
        "confirmed": bool(confirm),
        "dry_run": not confirm,
        "wrote_completed_false": 0,
    }
    if not confirm or not plan:
        return report

    for point_id, semantic in sorted(plan.items()):
        #: 只写 True，永不写 False：合并不会让任何已完成变成未完成。
        db.upsert_point(point_id, {"completed": True})
        store.upsert_observation(
            db,
            ProgressObservation(
                source_point_id=point_id,
                profile_id=profile_id,
                semantic=SEMANTIC_MANUAL,
                completed=True,
                source=SOURCE_LOCAL,
            ),
        )
        #: 留痕：远端当初说的是什么语义，冲突界面要能回答「这条凭什么是完成」。
        store.upsert_observation(
            db,
            ProgressObservation(
                source_point_id=point_id,
                profile_id=profile_id,
                semantic=semantic,
                completed=True,
                source="merge",
            ),
        )
    report["applied"] = len(plan)
    db.set_meta(MERGE_META_KEY, json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report
