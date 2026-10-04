"""攻略够不够：两个维度 + 三种证据能力（用户 2026-10 指示的逻辑层）。

同一个点位，玩家要付出的操作不一样，所以「攻略是否完美」不是一个比例，而是一个判定：

```text
维度一 completion_requirement   这个点位要到哪一步才算拿到
    LOCATE_ONLY        走到地图点就能拿到（若虫、折纸鸟、尘灵的位置）
    LOCATE_AND_SOLVE   到了还要做事（触发、方向序列、模块解谜、平台路线、挑战）
维度二 solve_kind               要做事的话，是哪一种事
    NONE / INTERACT / INPUT_SEQUENCE / MODULE_PUZZLE / TRANSFORM_ROUTE
    / PLATFORM_ROUTE / CHALLENGE / OTHER
```

证据侧同样分三种能力（一份攻略可以有其中几种）：

```text
SCOPE   范围证据：「该地图共 N 个」——知道总数，不知道是哪一个
LOCATE  定位证据：官方点位截图、一行「位于…」、或一段带方位文字的路线
SOLVE   解法证据：到了之后怎么做（方向序列、模块步骤、触发方式）
```

要求 × 证据 → **六个状态**（没有百分比，每个点位只落在其中一个）：

```text
NO_EVIDENCE      什么都没有
SCOPE_ONLY       只有「该地图共 N 个」这类范围证据
LOCATE_MISSING   知道怎么做/有多少，但不知道是哪一个、在哪儿
LOCATE_COMPLETE  「到点即得」的点位：定位证据已齐，玩家到点就能拿到（第 1 阶段完成）
SOLVE_MISSING    位置有了、缺解法（第 1 阶段完成，第 2 阶段缺）
COMPLETE         「到点还要解」的点位：定位 + 解法都齐了（第 2 阶段完成）
```

所以官方点位图对 LOCATE_ONLY 点位就是最完整的攻略（官方截图 + 一行说明 = LOCATE 齐），
对 LOCATE_AND_SOLVE 点位只是「告诉你地方在哪儿」，仍然缺 SOLVE。下游三件事都从这里派生：

1. 抓取：缺 LOCATE 的点位要找点位/定位证据，缺 SOLVE 的点位要找解法页面（`query_families`）；
2. 发布审计：一份攻略对某个点位算不算完整，看它补上的是哪种证据；
3. 下一步任务：`missing_locate` / `missing_solve` 两张清单就是工作队列，而不是笼统的「缺源」。
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable, Mapping

from hsrmap.guide_db import GuideDatabase

#: 证据等级（claims）是判定层的**注解**，不是第二套判定：这里只 import 纯函数，避免循环依赖。
from hsrmap.guides.claims import evidence_layers, point_evidence

#: 解法信号：一步「怎么做」的指令。只写「在哪儿」的官方点位说明不会命中。
SOLUTION_PATTERNS = (
    re.compile(r"[上下左右]{2,}"),               # 方向序列：上上左下 …
    #: 带步数的方向序列：「右2步，左1步，上1步」（九游 3.0 那篇整篇都是这个写法）。
    #: 要求**至少两段**，免得一句「往右边走两步」的路过描述被当成走法。
    re.compile(r"(?:[上下左右]\s*\d{1,2}\s*步[，,、。；;\s]*){2,}"),
    re.compile(r"(顺时针|逆时针)"),               # ROTATE 类
    re.compile(r"(点击|选择|输入|依次|按顺序|操作|转动|旋转|移动|推到|拖动|演奏|变身|跟随|击败|射击|击落|跳跃)"),
    re.compile(r"(影子出现|本体|模块|机关|算碑)"),
    re.compile(r"第[0-9一二三四五六七八九十]+步|[①②③④⑤⑥⑦⑧⑨⑩]"),
    re.compile(r"第[0-9一二三四五六七八九十]+个"),
)


def has_solution_steps(steps: Iterable[Any]) -> bool:
    """步骤里有没有「怎么做」，而不只是「在哪儿」。"""
    texts = [
        str((step or {}).get("text") if isinstance(step, dict) else step or "").strip()
        for step in steps or []
    ]
    texts = [text for text in texts if text]
    #: 一条指令就够；纯「位于某处」的位置说明不算指令
    return any(pattern.search(text) for text in texts for pattern in SOLUTION_PATTERNS)


# --------------------------------------------------------------------------- #
# 维度一 / 维度二：完成要求
# --------------------------------------------------------------------------- #

LOCATE_ONLY = "LOCATE_ONLY"
LOCATE_AND_SOLVE = "LOCATE_AND_SOLVE"
REQUIREMENTS = (LOCATE_ONLY, LOCATE_AND_SOLVE)

SOLVE_NONE = "NONE"
SOLVE_INTERACT = "INTERACT"
SOLVE_INPUT_SEQUENCE = "INPUT_SEQUENCE"
SOLVE_MODULE_PUZZLE = "MODULE_PUZZLE"
SOLVE_TRANSFORM_ROUTE = "TRANSFORM_ROUTE"
SOLVE_PLATFORM_ROUTE = "PLATFORM_ROUTE"
SOLVE_CHALLENGE = "CHALLENGE"
SOLVE_OTHER = "OTHER"
SOLVE_KINDS = (
    SOLVE_NONE, SOLVE_INTERACT, SOLVE_INPUT_SEQUENCE, SOLVE_MODULE_PUZZLE,
    SOLVE_TRANSFORM_ROUTE, SOLVE_PLATFORM_ROUTE, SOLVE_CHALLENGE, SOLVE_OTHER,
)

#: 点位说明把要求「上调」的信号：主题默认 LOCATE_ONLY，但这个点其实还要做事。
#: 用户的例子：官方说明写「位于门旁的凳子上。」→ 到点即得；
#: 写「完成此处「黄金替罪羊」解谜获得。」→ 这个点还要解题（即使主题默认只要到点）。
_UPGRADE_PUZZLE = ("解谜", "解密", "谜题", "机关", "算碑")
#: 「挑战」单独出现时是**设定文案**，不是这个点的动作——开拓妖精大挑战的官方说明是
#: 「被尘灵所喜爱的世界游戏，来这里挑战你的极限吧！」，命中它只因为这个主题的名字里有「挑战」。
#: 只有「完成…挑战」「挑战…获得/成功/通关」这类写法才算「到了还要做事」。
_UPGRADE_CHALLENGE = re.compile(r"(完成[^。；\n]{0,16}挑战|挑战[^。；\n]{0,10}(获得|成功|通关))")
#: 「击落/射击/击败」这类官方说明是**到了之后要做的动作**，不是位置：无名尘灵的
#: 「击落空中气球获得。」就该把这个点从「到点即得」上调成「到了还要动手」。
_UPGRADE_INTERACT = ("按", "点击", "交互", "对话", "调查", "开启", "变身", "击落", "射击", "击败")


def topic_completion(topic: str) -> dict[str, Any]:
    """主题的完成要求（档案里的 completion 块）；缺省按「要解」处理，宁可保守。

    `point_upgrade`（默认 True）决定「点级上调」对不对此主题生效：档案已经由人工判定过
    「找到位置就行，之后玩家自己操作」的主题（王下一桶、小小哈努行动），官方说明里的
    「变身／对话／按」只是玩法描述，不该再把整批点拉回第 2 阶段。
    """
    from hsrmap.guides.topics.loader import get_topic

    try:
        spec = get_topic(str(topic or "").replace("-", "_"))
    except Exception:  # noqa: BLE001 - 未知主题不该让报告失败
        return {"requirement": LOCATE_AND_SOLVE, "solve_kind": SOLVE_OTHER, "reason": "", "point_upgrade": True}
    block = spec.get("completion") or {}
    return {
        "requirement": str(block.get("requirement") or LOCATE_AND_SOLVE).upper(),
        "solve_kind": str(block.get("solve_kind") or SOLVE_OTHER).upper(),
        "reason": str(block.get("reason") or ""),
        "point_upgrade": bool(block.get("point_upgrade", True)),
    }


def point_requirement(detail_text: str | None, topic: str) -> tuple[str, str, str]:
    """(要求, 解法类型, 依据) —— 主题默认 + 点级上调。

    依据是 `topic`（主题档案）还是 `point`（这一条官方说明把它上调了）：报告里分开，
    这样「主题默认」和「这个点不一样」不会混在一起。
    """
    block = topic_completion(topic)
    requirement = block["requirement"]
    solve_kind = block["solve_kind"]
    body = str(detail_text or "").strip()
    if requirement == LOCATE_ONLY and body and block.get("point_upgrade", True):
        if any(hint in body for hint in _UPGRADE_PUZZLE) or _UPGRADE_CHALLENGE.search(body):
            return LOCATE_AND_SOLVE, SOLVE_OTHER, "point"
        if any(hint in body for hint in _UPGRADE_INTERACT):
            return LOCATE_AND_SOLVE, SOLVE_INTERACT, "point"
    return requirement, solve_kind, "topic"


# --------------------------------------------------------------------------- #
# 三种证据能力
# --------------------------------------------------------------------------- #

#: 「这个点在哪儿」的字眼。刻意不收「地图」「房间」：像「此处地图区域存在1个王下一桶」
#: 这种范围说明会被误判成定位证据（实测发生过），而「位于房间角落」照样命中「位于」「角落」。
LOCATE_TEXT_HINTS = (
    "位于", "在此处", "就在", "旁边", "上方", "下方", "里面", "角落", "凳子", "桌上",
    "屏幕上", "半空", "门口", "墙上", "窗户", "地面", "路旁", "树枝", "碎石", "门禁",
)
_LOCATE_TEXT = re.compile("|".join(LOCATE_TEXT_HINTS))
_INTERACT_HINTS = ("按", "点击", "对话", "调查", "变身", "击落", "射击", "击败")


def locating_text(text: str | None) -> bool:
    """这句话有没有说「在哪儿」——官方文字条目和范围说明共用这条判断。"""
    return bool(_LOCATE_TEXT.search(str(text or "")))


#: 官方说明里「怎么做」的动词，就是点级上调用的那一组（_UPGRADE_INTERACT）再加上「互动」。
#: 「交互」「开启」只在**上调**里出现过，于是出现过这种不对称：模型说「这条官方说明写了要交互，
#: 所以这个点缺解法」，却又不肯把同一句话算作解法证据（若虫 2960/4114、折纸小鸟 2297 卡在这）。
_OFFICIAL_ACTION_HINTS = tuple(_UPGRADE_INTERACT) + ("互动",)


def official_action_text(text: str | None) -> bool:
    """官方说明写没写「怎么做」（「电梯上升或下降时与其交互」／「击落空中气球获得。」）。

    它既是「这个点要解法」的理由，也是「这行说明就是解法」的证据；主题默认就要解法的点位
    （黄金替罪羊的按键序列、梦境迷钟的模块顺序）不能靠它过关，所以调用方只在
    **主题默认「到点就行」+ 这一点被这行说明上调** 时把它传进来。
    """
    body = str(text or "")
    return any(hint in body for hint in _OFFICIAL_ACTION_HINTS)


def is_solve_text(text: str | None) -> bool:
    """这句话算不算「怎么做」的证据（解法信号，或到了之后的交互动作）。

    `evidence_state` 与证据等级（`point_evidence`）必须用**同一个**判断：
    状态说「有解法」、证据等级却说「没有解法」，等于给同一个点位两套说法。
    """
    body = str(text or "")
    return has_solution_steps([body]) or any(hint in body for hint in _INTERACT_HINTS)


def evidence_state(
    *,
    entry_key: str,
    images: int,
    steps: list[str],
    official_point: bool = False,
    official_text: str = "",
) -> dict[str, bool]:
    """三种证据能力：SCOPE（范围/数量）、LOCATE（能不能到点）、SOLVE（到了怎么做）。

    已发布攻略里有什么，就记什么。官方点位说明是很好的素材，但它要变成条目
    （`guides official-seed`）才算**攻略产出**——把外部数据当成绩效会让报告虚高。

    例外是第 1 阶段（用户 2026-10 指示）：**定位以官方为准，官方没有再补充**。
    点位出现在官方地图上，就等于玩家能照着官方地图走到它，所以 `official_point=True`
    时 LOCATE 直接成立；官方数据是最终版，没有「再等官方补一张图」这回事。
    于是还没做完的目标只可能缺 SOLVE，而不是缺 LOCATE。
    """
    key = str(entry_key or "")
    texts = [str(text or "").strip() for text in steps or [] if str(text or "").strip()]
    scope = key.startswith(("set:", "map:", "global:"))
    locate = bool(images)
    #: 范围条目的正文说的是整张地图有多少个（「该地图共 260 个若虫」），不是某一个点的位置，
    #: 所以除非它带图，范围条目本身不构成 LOCATE 证据。
    if not locate and not scope:
        locate = any(_LOCATE_TEXT.search(text) for text in texts)
    #: 第 1 阶段以官方为准：官方地图上存在的点位本身就是 LOCATE 证据。
    if not locate and official_point:
        locate = True
    solve = any(is_solve_text(text) for text in texts)
    if not solve and official_text:
        #: 主题默认「到点就行」，是这条官方说明（「…时与其交互」「…互动后获取」）把它上调成
        #: 需要解法的，那么这句话本身就是解法证据——同一条上调规则的两端。主题默认就要解法的
        #: 点位不传这句话进来（黄金替罪羊的按键序列不能被一行位置说明放过去）。
        solve = official_action_text(official_text)
    return {"scope": scope, "locate": locate, "solve": solve}


# --------------------------------------------------------------------------- #
# 要求 × 证据 → 状态
# --------------------------------------------------------------------------- #

NO_EVIDENCE = "NO_EVIDENCE"
SCOPE_ONLY = "SCOPE_ONLY"
LOCATE_MISSING = "LOCATE_MISSING"
LOCATE_COMPLETE = "LOCATE_COMPLETE"
SOLVE_MISSING = "SOLVE_MISSING"
COMPLETE_STATUS = "COMPLETE"
STATUSES = (NO_EVIDENCE, SCOPE_ONLY, LOCATE_MISSING, LOCATE_COMPLETE, SOLVE_MISSING, COMPLETE_STATUS)

#: 两种「已完成」：到点即得的点位终点是 LOCATE_COMPLETE，还要解的点位终点是 COMPLETE。
DONE_STATUSES = (LOCATE_COMPLETE, COMPLETE_STATUS)

#: 还需要再找东西的状态（`missing_locate` / `missing_solve` 两个工作队列）。
MISSING_STATUSES = (NO_EVIDENCE, SCOPE_ONLY, LOCATE_MISSING, SOLVE_MISSING)


def point_status(requirement: str, evidence: dict[str, bool]) -> str:
    """证据 × 要求 → 状态（不是百分比）。"""
    locate = bool(evidence.get("locate"))
    solve = bool(evidence.get("solve"))
    scope = bool(evidence.get("scope"))
    if requirement == LOCATE_ONLY:
        #: 到点即得：定位证据（或一段能带他走到的路线说明）就够了
        if locate or solve:
            return LOCATE_COMPLETE
        return SCOPE_ONLY if scope else NO_EVIDENCE
    if locate and solve:
        return COMPLETE_STATUS
    if locate:
        return SOLVE_MISSING
    if solve:
        #: 知道怎么做、却不知道是哪一个 / 在哪儿
        return LOCATE_MISSING
    return SCOPE_ONLY if scope else NO_EVIDENCE


def is_done(status: str) -> bool:
    """这个状态算不算「玩家照着攻略就能拿到」。"""
    return str(status) in DONE_STATUSES


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #


#: 批次大小：SQLite 默认变量上限 999，取 500 留足余量。
_BULK_CHUNK = 500


class EntryIndex:
    """一次预加载的攻略索引（a1-8 十二）：整个点位循环不再碰数据库。

    匹配规则和逐点查询时**逐字相同**（见 `_published_entry` 的说明）：
    `source_point_id` 完全相等，或 `set:` 键里以**完整 id** 出现。变的只是取数方式——
    以前每个点位三条查询（候选条目 + steps + assets），现在固定三条查询取回全部。

    以前那条 SQL 还要靠 `LIKE '%<pid>%'` 做预筛，于是 388 会被 3388 的攻略「覆盖」、
    相位的 928 会被另一主题 set 键里的 1928 覆盖；索引版直接按规则建表，
    预筛那一层（连同它的路径特判）自然消失。
    """

    def __init__(
        self,
        db: GuideDatabase,
        *,
        status: str = "published",
        lookup: str = "auto",
    ) -> None:
        """`lookup`：`string`（键匹配，默认）/ `relation`（正规关系表）/ `auto`。

        `auto` 先问关系表齐不齐（`targets.relations_in_sync`），齐才走关系表——关系表缺绑定时
        用它会让完成度**静默漏攻略**，所以宁可退回字符串并在报告里写明走的是哪条（a1-8 十二）。
        """
        self.lookup = str(lookup or "string")
        self.sync_state: dict[str, Any] | None = None
        if self.lookup in {"auto", "relation"}:
            from hsrmap.guides.targets import relations_in_sync

            self.sync_state = relations_in_sync(db)
            if self.lookup == "auto":
                self.lookup = "relation" if self.sync_state.get("in_sync") else "string"
            elif not self.sync_state.get("in_sync"):
                raise ValueError(
                    "关系表还没补齐（%d 条绑定缺失）：先跑 python -m hsrmap guides target-shadow --apply"
                    % int(self.sync_state.get("missing") or 0)
                )
        self.rows: list[Any] = list(
            db.conn.execute(
                "SELECT id, source_kind, source_point_id, title FROM guide_entry"
                " WHERE IFNULL(status, '') = ? ORDER BY id DESC",
                (status,),
            )
        )
        self.steps, self.images = self._bulk_children(db, [int(row["id"]) for row in self.rows])
        self._rows_by_id: dict[int, Any] = {int(row["id"]): row for row in self.rows}
        self._by_point: dict[str, list[int]] = {}
        if self.lookup == "relation":
            from hsrmap.guides.targets import relation_index

            self._by_point = relation_index(db)
        self._by_key: dict[str, list[Any]] = {}
        #: set: 键就是点位列表，所以按「成员点位」倒排一次，循环里就是纯字典查找。
        self._by_member: dict[str, list[Any]] = {}
        for row in self.rows:
            key = str(row["source_point_id"] or "")
            self._by_key.setdefault(key, []).append(row)
            if key.startswith("set:"):
                for member in set(re.findall(r"\d+", key)):
                    self._by_member.setdefault(member, []).append(row)

    @staticmethod
    def _bulk_children(
        db: GuideDatabase, guide_ids: list[int]
    ) -> tuple[dict[int, list[str]], dict[int, int]]:
        steps: dict[int, list[str]] = {}
        images: dict[int, int] = {}
        ids = sorted({int(item) for item in guide_ids})
        for start in range(0, len(ids), _BULK_CHUNK):
            chunk = ids[start:start + _BULK_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for row in db.conn.execute(
                f"SELECT guide_id, text FROM guide_steps WHERE guide_id IN ({placeholders})"
                " ORDER BY guide_id, step_index",
                chunk,
            ):
                steps.setdefault(int(row["guide_id"]), []).append(str(row["text"]))
            for row in db.conn.execute(
                f"SELECT guide_id, COUNT(*) AS c FROM guide_assets WHERE guide_id IN ({placeholders})"
                " GROUP BY guide_id",
                chunk,
            ):
                images[int(row["guide_id"])] = int(row["c"])
        return steps, images

    def steps_for(self, guide_id: Any) -> list[str]:
        return self.steps.get(int(guide_id), [])

    def images_for(self, guide_id: Any) -> int:
        return self.images.get(int(guide_id), 0)

    def matching(self, point_id: str) -> list[Any]:
        """这个点位名下的条目，新的在前。"""
        key = str(point_id or "")
        if not key:
            return []
        if self.lookup == "relation":
            return [
                self._rows_by_id[guide_id]
                for guide_id in self._by_point.get(key, [])
                if guide_id in self._rows_by_id
            ]
        found = [*self._by_key.get(key, []), *self._by_member.get(key, [])]
        unique = {int(row["id"]): row for row in found}
        return [unique[guide_id] for guide_id in sorted(unique, reverse=True)]


def _matching_entries(db: GuideDatabase, point_id: str) -> list[Any]:
    """单点查询（兼容入口）；循环里请用 `EntryIndex`，否则又是一次 N+1。"""
    return EntryIndex(db).matching(point_id)


def _published_entry(db: GuideDatabase, point_id: str) -> Any:
    """这个点位的攻略：它自己的条目，或**点名了它**的范围条目（新的优先）。

    以前这里只有 `LIKE '%<pid>%'`，于是 388 会被 3388 的攻略「覆盖」、相位的 928 会被另一主题
    的 set 键里的 1928 覆盖——完成度虚高。现在只认两种：`source_point_id` 完全相等，
    或 `set:` 键里以**完整 id** 出现（set 键就是点位列表；map:/global: 键不含点位 id）。
    """
    rows = _matching_entries(db, point_id)
    return rows[0] if rows else None


def _best_entry(index: EntryIndex, point_id: str, *, need_solve: bool) -> tuple[Any, list[str], int]:
    """挑最能回答这个点位的那一条：需要解法时，优先选真有解法的条目。

    官方补发的条目 id 比社区条目大，而它只有「完成此处…解谜获得。」这样一行——如果只看
    「最新的那条」，它会**盖住**旁边那条带图、带「Q为顺时针，E为逆时针」的社区攻略，
    点位就永远算缺解法。所以需要解法时按「有解法的优先」挑，找不到才退回最新那条。
    """
    fallback: tuple[Any, list[str], int] | None = None
    for row in index.matching(point_id):
        steps = index.steps_for(row["id"])
        images = index.images_for(row["id"])
        if fallback is None:
            fallback = (row, steps, images)
        if not need_solve or has_solution_steps(steps):
            return row, steps, images
    return fallback if fallback is not None else (None, [], 0)


def completeness_report(
    db: GuideDatabase,
    *,
    detail_db: Any = None,
    topics: Iterable[str] | None = None,
    profile: bool = False,
    lookup: str = "auto",
) -> dict[str, Any]:
    """完成度报告；`profile=True` 时额外报「这次算花了多少条 SQL / 多少毫秒」。

    性能验收（a1-8 十二）不是拍脑袋定秒数，而是固定「查询数不随点位数增长」这条结构指标：
    把这条指标做进报告，回归时才看得见。
    """
    if not profile:
        return _completeness_report(db, detail_db=detail_db, topics=topics, lookup=lookup)
    from hsrmap.guide_db import count_queries

    started = time.perf_counter()
    with count_queries(db) as measured:
        report = _completeness_report(db, detail_db=detail_db, topics=topics, lookup=lookup)
        elapsed = (time.perf_counter() - started) * 1000
    report["sql"] = {"queries": measured["count"], "elapsed_ms": round(elapsed, 1)}
    return report


def _point_row(
    index: EntryIndex,
    key: str,
    point: Mapping[str, Any],
    detail: Mapping[str, Any],
) -> dict[str, Any] | None:
    """一个点位的判定行（完整性报告与 Web 点位详情**共用同一段**，不产生第二套真相）。"""
    pid = str(point.get("source_point_id") or "")
    if not pid:
        return None
    requirement, solve_kind, requirement_source = point_requirement(detail.get("text"), key)
    #: 缺解法的点位要挑「真有解法的那条」——否则官方那一行说明会盖住社区攻略。
    entry, steps, images = _best_entry(index, pid, need_solve=requirement == LOCATE_AND_SOLVE)
    entry_key = str(entry["source_point_id"] or "") if entry is not None else ""
    #: 第 1 阶段以官方为准：这里的点位全部来自官方地图（official_points_for_topic），
    #: 所以 `official_point=True`——定位不是缺口，缺口只可能是 SOLVE。
    #: 主题默认「到点就行」的点位，是被这一条官方说明上调成「要解法」的；那把这句话
    #: 一起交给证据判断——写「怎么做」的是它，判定「缺解法」的也是它。主题默认就要
    #: 解法的主题（或 point_upgrade=False 的主题）不传，真谜题只能靠真攻略。
    official_text = ""
    if requirement_source == "point" and topic_completion(key).get("requirement") == LOCATE_ONLY:
        official_text = str(detail.get("text") or "")
    evidence = evidence_state(
        entry_key=entry_key,
        images=images,
        steps=steps,
        official_point=True,
        official_text=official_text,
    )
    status = point_status(requirement, evidence)
    #: 证据等级（a1-8 五）：六状态语义不变，这里只是回答「这个状态凭什么成立」。
    #: 用的是**同一批** steps/entry，所以不与判定层产生第二套真相。
    point_levels = point_evidence(
        official=True,
        solve_steps=[text for text in steps if is_solve_text(text)],
        guide_id=int(entry["id"]) if entry is not None else 0,
        #: 官方那一行把点位上调成「要解法」时，它自己就是解法证据（见 point_evidence 说明）。
        official_text=official_text,
    )
    return {
        "topic": key,
        "point": pid,
        "requirement": requirement,
        "solve_kind": solve_kind,
        "requirement_source": requirement_source,
        "scope": evidence["scope"],
        "locate": evidence["locate"],
        "solve": evidence["solve"],
        "status": status,
        "done": is_done(status),
        "guide_id": int(entry["id"]) if entry is not None else None,
        "source_kind": str(entry["source_kind"]) if entry is not None else "",
        "title": str(entry["title"]) if entry is not None else "",
        "official_text": official_text,
        "locate_evidence": point_levels["locate"],
        "solve_evidence": point_levels["solve"],
        "missing": "" if is_done(status) else status,
    }


def point_report(
    db: GuideDatabase,
    point_id: str,
    *,
    topic: str | None = None,
    detail_db: Any = None,
    lookup: str = "auto",
) -> dict[str, Any] | None:
    """单个点位的完成状态 + 证据分层（Web 点位详情用）。

    和 `completeness_report` 走同一个 `_point_row`：详情页说的「为什么算完成」
    必须和总览里的 1006/1006 是同一句话。
    """
    from hsrmap.guides.official import latest_detail_db, official_details
    from hsrmap.guides.topics.loader import list_topics
    from hsrmap.guides.topics.official import official_points_for_topic

    pid = str(point_id or "")
    if not pid:
        return None
    keys = [str(topic)] if topic else [
        str(item["topic_key"]) for item in list_topics(enabled_only=True)
    ]
    index = EntryIndex(db, lookup=lookup)
    for key in keys:
        try:
            points = official_points_for_topic(key) or []
        except Exception:  # noqa: BLE001 - 未知主题不该让详情页 500
            continue
        match = next(
            (item for item in points if str(item.get("source_point_id") or "") == pid), None
        )
        if match is None:
            continue
        details = official_details(detail_db or latest_detail_db(), [pid])
        return _point_row(index, key, match, details.get(pid) or {})
    return None


def _completeness_report(
    db: GuideDatabase,
    *,
    detail_db: Any = None,
    topics: Iterable[str] | None = None,
    lookup: str = "auto",
) -> dict[str, Any]:
    """每个点位：完成要求 + 三种证据 + 状态；按主题汇总，并给出缺 LOCATE / 缺 SOLVE 的清单。

    这两张清单就是下一轮的工作队列：`missing_solve` 找解法页面，`missing_locate` 找定位证据。
    """
    from hsrmap.guides.official import latest_detail_db, official_details
    from hsrmap.guides.topics.loader import list_topics
    from hsrmap.guides.topics.official import official_points_for_topic

    keys = [str(key) for key in (topics or [item["topic_key"] for item in list_topics(enabled_only=True)])]
    rows: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    #: 一次预加载：这条循环是 1006 个点位的主循环，里面再查库就是 N+1（a1-8 十二）。
    #: auto：关系表齐就走关系表，否则退回字符串键（两条路已在 1006 点位上 shadow 对比一致）。
    index = EntryIndex(db, lookup=lookup) if keys else None
    for key in keys:
        try:
            points = official_points_for_topic(key) or []
        except Exception:  # noqa: BLE001 - 未知主题（例如测试用的假主题）不该让报告失败
            points = []
        point_ids = [str(point.get("source_point_id") or "") for point in points if point.get("source_point_id")]
        if not point_ids:
            continue
        wanted = [pid for pid in point_ids if pid not in details]
        if wanted:
            details.update(official_details(detail_db or latest_detail_db(), wanted))
        for point in points:
            row = _point_row(index, key, point, details.get(str(point.get("source_point_id") or "")) or {})
            if row is not None:
                rows.append(row)
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = summary.setdefault(
            str(row["topic"]),
            {
                "points": 0,
                "requirement": str(row["requirement"]),
                "solve_kind": str(row["solve_kind"]),
                "done": 0,
                "complete": 0,
                "locate_complete": 0,
                "missing_solve": 0,
                "missing_locate": 0,
                "scope_only": 0,
                "no_evidence": 0,
                "statuses": {},
            },
        )
        bucket["points"] += 1
        status = str(row["status"])
        bucket["statuses"][status] = int(bucket["statuses"].get(status) or 0) + 1
        if row["done"]:
            bucket["done"] += 1
        if status == COMPLETE_STATUS:
            bucket["complete"] += 1
        elif status == LOCATE_COMPLETE:
            bucket["locate_complete"] += 1
        elif status == SOLVE_MISSING:
            bucket["missing_solve"] += 1
        elif status == LOCATE_MISSING:
            bucket["missing_locate"] += 1
        elif status == SCOPE_ONLY:
            bucket["scope_only"] += 1
        else:
            bucket["no_evidence"] += 1
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[str(row["status"])] = int(counts.get(str(row["status"])) or 0) + 1
    return {
        "points": len(rows),
        "done": counts[COMPLETE_STATUS] + counts[LOCATE_COMPLETE],
        "complete": counts[COMPLETE_STATUS],
        "locate_complete": counts[LOCATE_COMPLETE],
        "solve_missing": counts[SOLVE_MISSING],
        "locate_missing": counts[LOCATE_MISSING],
        "scope_only": counts[SCOPE_ONLY],
        "no_evidence": counts[NO_EVIDENCE],
        "statuses": counts,
        "missing_locate": [row for row in rows if str(row["status"]) in {LOCATE_MISSING, SCOPE_ONLY, NO_EVIDENCE}],
        "missing_solve": [row for row in rows if str(row["status"]) == SOLVE_MISSING],
        #: 1006/1006 不该掩盖转录与推断：完成的点位按「靠什么证据成立」再分三层。
        "evidence_layers": evidence_layers(rows),
        #: 这份报告是查字符串键还是查正规关系表（a1-8 十二）：关系表没补齐时会退回字符串，
        #: 报告里必须写明，免得读的人以为换了实现。
        "lookup": {
            "mode": getattr(index, "lookup", "none"),
            "relations": getattr(index, "sync_state", None),
        },
        "topics": summary,
        "rows": rows,
    }


def status_summary(
    db: GuideDatabase,
    *,
    topics: Iterable[str] | None = None,
    detail_db: Any = None,
    lookup: str = "auto",
) -> dict[str, Any]:
    """六状态的计数（不带逐点明细）：给收口/覆盖报告用。

    覆盖率说「发布了多少条」，这个说「玩家照着攻略能拿到多少」——后者才是完整性的真相。
    """
    report = completeness_report(db, topics=topics, detail_db=detail_db, lookup=lookup)
    return {
        "points": report["points"],
        "lookup": report.get("lookup"),
        "done": report["done"],
        "complete": report["complete"],
        "locate_complete": report["locate_complete"],
        "solve_missing": report["solve_missing"],
        "locate_missing": report["locate_missing"],
        "scope_only": report["scope_only"],
        "no_evidence": report["no_evidence"],
        "missing_locate": report["locate_missing"] + report["scope_only"] + report["no_evidence"],
        "statuses": report["statuses"],
    }


#: 中文列名：文档表格直接由这个函数生成，避免文档里的数字和逻辑层脱节。
MARKDOWN_COLUMNS = (
    ("topic", "主题"),
    ("points", "点位"),
    ("done", "已完成"),
    ("locate_complete", "到达即完成"),
    ("complete", "含解法完成"),
    ("missing_solve", "缺解法"),
    ("missing_locate", "缺定位"),
    ("scope_only", "仅范围"),
    ("no_evidence", "无证据"),
)


def completeness_markdown(report: dict[str, Any]) -> str:
    """把 `completeness_report` 的主题汇总排成 markdown 表（用于进度文档）。"""
    topics = report.get("topics") or {}
    order = sorted(topics, key=lambda key: (-int(topics[key].get("points") or 0), str(key)))
    lines = [
        "| " + " | ".join(label for _key, label in MARKDOWN_COLUMNS) + " |",
        "| " + " | ".join("---" for _key, _label in MARKDOWN_COLUMNS) + " |",
    ]
    totals = {key: 0 for key, _label in MARKDOWN_COLUMNS if key != "topic"}
    for key in order:
        bucket = topics[key]
        cells = [str(key)]
        for field, _label in MARKDOWN_COLUMNS[1:]:
            value = int(bucket.get(field) or 0)
            totals[field] += value
            cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("| " + " | ".join(["合计"] + [str(totals[field]) for field, _label in MARKDOWN_COLUMNS[1:]]) + " |")
    layers = report.get("evidence_layers") or {}
    if layers:
        #: 1006/1006 不该掩盖转录与推断（a1-8 五）：完成的点位按「靠什么证据成立」再分一层。
        lines += [
            "",
            "完成点的证据分层（六状态不变，这里回答「凭什么算完成」）：",
            "",
            "| 层 | 点位 | 说明 |",
            "| --- | ---: | --- |",
            f"| 直接证据 | {int(layers.get('direct') or 0)} | 官方地图 / 社区正文（EXACT·FRAGMENT·ASSEMBLED）就够 |",
            f"| 图解法转录 | {int(layers.get('transcription') or 0)} | 需要「[图解法转录 <sha>]」这一步才成立 |",
            f"| 交叉推断 | {int(layers.get('inference') or 0)} | 需要写明依据的推断才成立 |",
            f"| 未完成 | {int(layers.get('missing') or 0)} | 缺定位或缺解法 |",
        ]
    return "\n".join(lines)
