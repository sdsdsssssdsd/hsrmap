"""进度观察模型（a1-9 §4/§13）：先把「看到什么」记下来，再谈「所以算不算完成」。

三种语义必须分开存，因为它们**不是同一件事**：

```text
manual          玩家在本程序里勾的（唯一可信的本地真相）
map_mark        官方互动地图上的标记（未证实是否等于游戏状态）
game_obtained   游戏内真实开箱（未证实；Gate 0 的实验对象）
unknown         来源说不清 → 永远不参与推导
```

`completed` 只是一个观察值；是否把它当成「玩家拿到了」由 `resolver` 决定，而且必须遵守
「unknown / 未验证语义不得推导 completed」这条硬规则。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

#: 来源（谁说的）。
SOURCE_LOCAL = "local"
SOURCE_HOYOLAB_MAP = "hoyolab_map"
SOURCE_HOYOLAB_GAME = "hoyolab_game"

#: 语义（这句话在说什么）。
SEMANTIC_MANUAL = "manual"
SEMANTIC_MAP_MARK = "map_mark"
SEMANTIC_GAME_OBTAINED = "game_obtained"
SEMANTIC_UNKNOWN = "unknown"

#: 全部语义；`unknown` 是显式的「说不清」，不是缺省值。
SEMANTICS = (SEMANTIC_MANUAL, SEMANTIC_MAP_MARK, SEMANTIC_GAME_OBTAINED, SEMANTIC_UNKNOWN)

#: **Gate 0**（a1-9 §12 的 2×2 实验）的结论就落在这里。
#: 在实验证明「官方地图状态 == 游戏真实开箱」之前，这里必须是空集：
#: 空集意味着 `map_mark` 与 `game_obtained` 都无权推导 `completed`。
#: 实验之后的改法只有一句：把 "game_obtained"（或 "map_mark"）加进来，并附上证据链接。
VERIFIED_SEMANTICS: frozenset[str] = frozenset()

#: 语义 → 默认来源（写观察时的缺省，允许显式覆盖）。
DEFAULT_SOURCE = {
    SEMANTIC_MANUAL: SOURCE_LOCAL,
    SEMANTIC_MAP_MARK: SOURCE_HOYOLAB_MAP,
    SEMANTIC_GAME_OBTAINED: SOURCE_HOYOLAB_GAME,
    SEMANTIC_UNKNOWN: SOURCE_LOCAL,
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def profile_key(*, realm: str, region: str = "", uid_masked: str = "", salt: str = "hsrmap") -> str:
    """确定性的本地 profile 键：**不存完整 UID**，只存由打码 UID 派生的短键。"""
    payload = f"{salt}|{realm}|{region}|{uid_masked}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ProgressProfile:
    profile_id: str
    realm: str
    region: str = ""
    uid_masked: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class ProgressObservation:
    """一条观察：某个点位、在某个语义下、被某个来源看到是什么状态。"""

    source_point_id: str
    profile_id: str
    semantic: str
    completed: bool
    source: str = ""
    observed_at: str = ""

    def __post_init__(self) -> None:
        if not str(self.source_point_id or "").strip():
            raise ValueError("source_point_id 必填")
        if not str(self.profile_id or "").strip():
            raise ValueError("profile_id 必填")
        if self.semantic not in SEMANTICS:
            raise ValueError(f"未知语义：{self.semantic!r}（可选：{SEMANTICS}）")

    @property
    def resolved_source(self) -> str:
        return self.source or DEFAULT_SOURCE.get(self.semantic, SOURCE_LOCAL)

    def allows_completion(self, *, import_map_mark: bool = False) -> bool:
        """这条观察有没有资格推导「本地完成」。"""
        if not self.completed:
            return False
        if self.semantic == SEMANTIC_MANUAL:
            return True
        if self.semantic == SEMANTIC_MAP_MARK:
            #: 默认不推导：官方地图标记是「官方地图上的状态」，不是「游戏里的状态」。
            return bool(import_map_mark) and self.semantic in VERIFIED_SEMANTICS
        if self.semantic == SEMANTIC_GAME_OBTAINED:
            return self.semantic in VERIFIED_SEMANTICS
        return False  # unknown：永远不推导
