"""M7.2 发现层（a1-8-1 §七 / §八）：五个 extractor + ID Reference Scanner。

这一层是**纯函数**：输入官方 payload（已经抓下来的 JSON），输出候选边 / 点位跳转 /
名字事实，并且每一条结论都带出处 `(键名, json 路径, discovery_source)`。
这里**不发任何网络请求、不碰数据库、不写 data/**——抓取与落库是 M7.3 的事。

五个 extractor（§七 的五层）：

| extractor | 输入 | 产出 |
| --- | --- | --- |
| `extract_tree` | `/v1/map/tree` | TREE_CHILD / RELATED_MAP / MAP_GROUP + 树事实 |
| `extract_map_info` | `/v1/map/info` | 真名 / preview / detail 事实 + `parent_id` → TREE_CHILD + related / group |
| `extract_point_list` | `/v1/map/point/list` | POINT_JUMP 边 + point_transitions（`related_jump_id`） |
| `extract_point_info` | `/v1/map/point/info` | 交叉校验的 POINT_JUMP；`map_id` 是**点自己所属的图**，不是边 |
| `extract_bundle_contract` | 官方前端 bundle 文本 | 键语义契约（哪个键代表哪种跳转），不产边 |

`scan_id_references` 是 §八 的兜底：官方将来加的新字段 extractor 不认识，但也**不许把
target 丢掉**。它按**键形态**找候选，再用**探测**（不是数值区间）判定这个值到底是不是一张
可渲染地图——因为 id 空间是重叠的（M7.0：187 个跳转目标里 180 个同时也是 label id、150 个
也是 point id）。判定结果分四类：

    UNKNOWN_RENDERABLE_TARGET  探测说是可渲染地图，但字段语义未知 → 候选跳转（§八）
    MAP_LIKE_NON_RASTER        探测说像地图（在树里）但没有 raster 证据 → 不是可导航目标
    NOT_A_MAP                  键语义 / 探测都说它不是地图引用（id 空间重叠就靠这个分开）
    UNRESOLVED                 没有探测通道：不猜

`0` / `"0"` / `""`（以及空白串）在**所有**取值形态下一律当「无」过滤掉——这是 M7.0 的 Q4
规则：`related_jump_id` / `related_id` / `jump_target_id` 都是拿 `"0"` 当空值的。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from hsrmap.graph import (
    MAP_GROUP,
    POINT_JUMP,
    RELATED_MAP,
    RETURN,
    TREE_CHILD,
    Edge,
    PointTransition,
)

# --------------------------------------------------------------------------- #
# discovery_source：这条结论是从哪一层 payload 来的（§七 的五层）
# --------------------------------------------------------------------------- #

SOURCE_MAP_TREE = "map_tree"
SOURCE_MAP_INFO = "map_info"
SOURCE_POINT_LIST = "point_list"
SOURCE_POINT_INFO = "point_info"
SOURCE_BUNDLE = "bundle_contract"
SOURCE_LABEL_TREE = "label_tree"

#: 五个 extractor 的 discovery_source。
EXTRACTOR_SOURCES: tuple[str, ...] = (
    SOURCE_MAP_TREE,
    SOURCE_MAP_INFO,
    SOURCE_POINT_LIST,
    SOURCE_POINT_INFO,
    SOURCE_BUNDLE,
)

#: 「前往对应地图」按钮文案。**不是字面证据**：M7.0 的 bundle 只证明了
#: `related_jump_id → openRedirectConfirmation{mapId}` 这条链路，中文文案来自 UI 截图。
ACTION_LABEL_REDIRECT = "前往对应地图"

# --------------------------------------------------------------------------- #
# 空值 / id 形态
# --------------------------------------------------------------------------- #

#: 官方拿这些值当「没有」：数字 0、字符串 "0"、空串（含空白）、None、bool。
ABSENT_SCALARS: tuple[Any, ...] = (None, "", 0, "0", False)


def is_absent(value: Any) -> bool:
    """`0` / `"0"` / `""` / None / False / 纯空白串 = 官方语义里的「无」。

    这是 M7.0 Q4 的硬规则：`related_id` / `related_group_map` / `related_jump_id` /
    `jump_target_id` 全都用 `"0"`（字符串零）表示空，漏掉它会把「没有跳转」当成跳到地图 0。
    """
    if value is None or isinstance(value, bool):
        return True  # bool 不是 id（True/False 都不当 1/0 用）
    if isinstance(value, str):
        return value.strip() in ("", "0")
    if isinstance(value, (int, float)):
        return float(value) == 0.0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0  # 空数组 / 空对象 = 没有引用
    return False


def as_map_id(value: Any) -> str | None:
    """把官方的一个取值归一成 map id 字符串；不是 id 形态就返回 None（**不猜**）。

    接受：正整数 / 正整数字符串 / 整数值浮点；拒绝：bool、负数、小数、非数字串。
    归一化会去掉前导零（`"007"` → `"7"`），否则同一张地图会有两种写法、幂等键会分裂。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None if value == 0 else str(value)
    if isinstance(value, float):
        if value == 0 or not float(value).is_integer():
            return None
        return str(int(value))
    if isinstance(value, str):
        text = value.strip()
        if text in ("", "0"):
            return None
        if text.isascii() and text.isdigit():
            return str(int(text))
        return None
    return None


def count_absent_values(value: Any) -> int:
    """数一个字段取值里有多少个「无」（`0>> / `"0">> / `"">> / None / 空数组）。

    `expand_id_values` 会把它们过滤掉，但过滤器不该让它们消失得无声无息：报告里要能说出
    「过滤掉了 10672 个 0」——这正是 M7.0 Q4 那条规则的现场计数。
    """
    if is_absent(value):
        return 1
    if isinstance(value, (list, tuple, set)):
        return sum(count_absent_values(item) for item in value)
    if isinstance(value, str) and "," in value:
        return sum(1 for part in value.split(",") if is_absent(part))
    return 0


def expand_id_values(value: Any) -> list[tuple[str, Any, str | None]]:
    """展开一个字段取值的所有 id 形态（§八 的值形态清单）。

    返回 `(路径后缀, 原值, 归一后的 map id)`：

    * 数字 / 数字串            → `[("", value, "1287")]`
    * 数字数组 `[1, 2]`        → `[("[0]", 1, "1"), ("[1]", 2, "2")]`
    * 逗号数字串 `"1,2"`       → `[("[0]", "1", "1"), ("[1]", "2", "2")]`
    * `0` / `"0"` / `""`       → `[]`（当「无」过滤掉，不是 `["0"]`）
    * 其它（bool / 文本 / 对象）→ `[]`
    """
    if is_absent(value):
        return []
    if isinstance(value, (list, tuple)):
        out: list[tuple[str, Any, str | None]] = []
        for index, item in enumerate(value):
            out.extend((f"[{index}]" + suffix, raw, map_id) for suffix, raw, map_id in expand_id_values(item))
        return out
    if isinstance(value, str):
        text = value.strip()
        if "," in text:
            out = []
            for index, part in enumerate(text.split(",")):
                if is_absent(part):
                    continue
                out.append((f"[{index}]", part, as_map_id(part)))
            return out
    map_id = as_map_id(value)
    if map_id is None:
        return []
    return [("", value, map_id)]


# --------------------------------------------------------------------------- #
# 键语义契约（transition registry，§七 第五层）
# --------------------------------------------------------------------------- #

#: id 空间：这个键里的数字指的是什么。
SPACE_MAP = "map"
SPACE_LABEL = "label"
SPACE_POINT = "point"
SPACE_GAME = "game"
SPACE_SELF = "self"
SPACE_UNKNOWN = "unknown"

#: 不是地图引用的 id 空间：键语义已经定死，探测到「这个数字恰好也是地图 id」也不能改成边。
NON_MAP_SPACES: frozenset[str] = frozenset({SPACE_LABEL, SPACE_POINT, SPACE_GAME, SPACE_SELF})


@dataclass(frozen=True)
class KeyContract:
    """一个官方字段的语义契约：它是什么 id 空间、算不算边、证据在哪。"""

    key: str
    space: str
    edge_type: str | None = None
    extractor: str | None = None
    evidence: str = ""
    confidence: float = 1.0

    @property
    def is_edge(self) -> bool:
        return self.edge_type is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "space": self.space,
            "edge_type": self.edge_type,
            "extractor": self.extractor,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


#: §七 第五层的 transition registry：键 → 语义。证据一律给到 bundle 符号或 payload 字段名，
#: 推断出来的（< 1.0）如实标 confidence。
KEY_CONTRACTS: tuple[KeyContract, ...] = (
    KeyContract("id", SPACE_SELF, None, None, "节点 / 点位自己的 id；不是边", 1.0),
    KeyContract(
        "parent_id", SPACE_MAP, TREE_CHILD, SOURCE_MAP_TREE,
        "map/tree 的 parent_id + children，与 map/info 的 parent_id 同源", 1.0,
    ),
    KeyContract(
        "children", SPACE_MAP, TREE_CHILD, SOURCE_MAP_TREE,
        "map/tree 的 children 数组元素 id（官方树事实）", 1.0,
    ),
    KeyContract(
        "related_id", SPACE_MAP, RELATED_MAP, SOURCE_MAP_TREE,
        "bundle: resolveVisibleRelatedItem 沿 is_hide + related_id（'0' 终止）回溯到可见区域", 1.0,
    ),
    KeyContract(
        "related_group_map", SPACE_MAP, MAP_GROUP, SOURCE_MAP_TREE,
        "bundle: changeMapGroup 用 currentGroup.related_group_map 找配对子图", 1.0,
    ),
    KeyContract(
        "related_jump_id", SPACE_MAP, POINT_JUMP, SOURCE_POINT_LIST,
        "bundle: \"0\"!==e.related_jump_id → openRedirectConfirmation{mapId: e.related_jump_id}", 1.0,
    ),
    KeyContract(
        "map_id", SPACE_MAP, None, SOURCE_POINT_INFO,
        "point/info.map_id = 点自己所属的标点层（bundle: pointMapId = info.map_id，changeLayer('plane') 走它）——不是边",
        1.0,
    ),
    KeyContract(
        "origin_map_id", SPACE_MAP, RETURN, SOURCE_BUNDLE,
        "bundle: useOriginMapIds 解析 query.origin_map_id 逗号栈 + pushOriginMapId/topOriginMapId", 1.0,
    ),
    KeyContract(
        "label_id", SPACE_LABEL, None, None,
        "point / point_label 都指向 label_nodes 的 id 空间；数字与 map id 重叠，按区间猜必错", 1.0,
    ),
    KeyContract(
        "jump_type", SPACE_LABEL, None, None,
        "label/jump_type 当前 1016/1016 全 0；bundle 里 jump_type 用于标签页（'coordinate'===e.jump_type）", 1.0,
    ),
    KeyContract(
        "jump_target_id", SPACE_LABEL, None, None,
        "label/jump_target_id 当前 1016/1016 全 0，且 bundle 里零出现 —— 不是 JUMP 来源，但要留在 registry",
        1.0,
    ),
    KeyContract(
        "map_group_type", SPACE_SELF, None, SOURCE_MAP_TREE,
        "bundle: MAP_GROUP_TYPE = Object.freeze({normal:0, light:1, night:2, foreign:3, beyond_the_tale:4})", 1.0,
    ),
    KeyContract(
        "game_map_id", SPACE_GAME, None, SOURCE_MAP_INFO,
        "map/info 的 game_map_id（游戏内地图 id 空间，不是本图谱的 map id）", 1.0,
    ),
    KeyContract(
        "point_id", SPACE_POINT, None, None,
        "api / bundle 的 point_id 参数指向 point id 空间", 1.0,
    ),
    KeyContract(
        "changeLayer", SPACE_SELF, None, SOURCE_BUNDLE,
        "bundle: changeLayer('plane') → pointMapId（跳转标点层，不是边）；其它分支才走 mapId", 1.0,
    ),
)

KEY_CONTRACT_INDEX: Mapping[str, KeyContract] = {item.key: item for item in KEY_CONTRACTS}

#: 同名键在不同 payload 里可能是不同 id 空间：label/tree 的 parent_id 指向 label_nodes，
#: 不是 map/tree 的父地图。扫描这些 payload 时要显式覆盖，否则会把 label id 当成地图。
KEY_CONTRACT_OVERRIDES: Mapping[str, Mapping[str, KeyContract]] = {
    SOURCE_LABEL_TREE: {
        "parent_id": KeyContract("parent_id", SPACE_LABEL, None, None,
                                 "label/tree 的 parent_id 指向 label_nodes（同名不同空间）", 1.0),
    },
}


def key_contracts_for(endpoint: str) -> Mapping[str, KeyContract]:
    """某个 payload 的键语义覆盖表（没有覆盖就是空表）。"""
    return KEY_CONTRACT_OVERRIDES.get(str(endpoint), {})


def contract_for_key(key: str) -> KeyContract | None:
    """查键语义：精确命中优先，其次按后缀/形态猜**空间**（不猜边类型）。"""
    hit = KEY_CONTRACT_INDEX.get(key)
    if hit is not None:
        return hit
    lowered = str(key).lower()
    if lowered.endswith("_label_id"):
        return KeyContract(key, SPACE_LABEL, None, None, "键名以 _label_id 结尾 → label id 空间", 0.6)
    if lowered.endswith("_point_id"):
        return KeyContract(key, SPACE_POINT, None, None, "键名以 _point_id 结尾 → point id 空间", 0.6)
    if lowered.endswith("_map_id") or lowered.endswith("_map") or lowered.endswith("_id"):
        return KeyContract(key, SPACE_MAP, None, None, "键名像 map 引用（形态匹配），语义未证实", 0.5)
    return None


# --------------------------------------------------------------------------- #
# 候选边 / 跳转 / 事实
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CandidateEdge:
    """一条候选边 + 它的出处。**还没探测**：调用方拿它去落库或再探测。"""

    source_map_id: str
    target_map_id: str
    edge_type: str
    key: str
    json_path: str
    discovery_source: str
    source_point_id: str | None = None
    source_label_id: str | None = None
    confidence: float = 1.0
    bidirectional: bool = False
    raw_value: Any = None
    note: str = ""

    def __post_init__(self) -> None:
        #: 端点必须是 id 形态："" / "0" / None / bool 都不是地图 id（比 Edge 更严，因为
        #: CandidateEdge 是发现层的产物，宁可在入口就拒掉，也不许把「无」写成一条边）。
        if as_map_id(self.source_map_id) is None or as_map_id(self.target_map_id) is None:
            raise ValueError(f"candidate edge needs id-shaped endpoints: {self.source_map_id!r} → {self.target_map_id!r}")
        if not str(self.key) or not str(self.json_path):
            raise ValueError("candidate edge needs (key, json_path) provenance")

    @property
    def key_tuple(self) -> tuple[str, str, str, str | None]:
        """map_edges 的 UNIQUE 键（幂等去重用）。"""
        return (str(self.source_map_id), str(self.target_map_id), self.edge_type, self.source_point_id)

    def provenance(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "json_path": self.json_path,
            "value": self.raw_value,
            "note": self.note,
        }

    def as_edge(self, discovered_at: str | None = None) -> Edge:
        return Edge(
            source_map_id=str(self.source_map_id),
            target_map_id=str(self.target_map_id),
            edge_type=self.edge_type,
            source_point_id=None if self.source_point_id is None else str(self.source_point_id),
            source_label_id=None if self.source_label_id is None else str(self.source_label_id),
            discovery_source=self.discovery_source,
            confidence=float(self.confidence),
            bidirectional=bool(self.bidirectional),
            raw_json=self.provenance(),
            discovered_at=discovered_at,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_map_id": self.source_map_id,
            "target_map_id": self.target_map_id,
            "edge_type": self.edge_type,
            "key": self.key,
            "json_path": self.json_path,
            "discovery_source": self.discovery_source,
            "source_point_id": self.source_point_id,
            "source_label_id": self.source_label_id,
            "confidence": self.confidence,
            "bidirectional": self.bidirectional,
            "raw_value": self.raw_value,
            "note": self.note,
        }


@dataclass(frozen=True)
class CandidateTransition:
    """点位上的跳转（§十三）：Viewer 只读这个形状，不需要认识 HoYo 的原始字段。"""

    point_id: Any
    target_map_source_id: str
    transition_type: str
    key: str
    json_path: str
    discovery_source: str
    action_label: str | None = None
    source_point_id: str | None = None
    raw_value: Any = None
    note: str = ""

    def as_transition(self) -> PointTransition:
        raw = {
            "key": self.key,
            "json_path": self.json_path,
            "value": self.raw_value,
            "point_source_id": self.source_point_id,
            "note": self.note,
        }
        return PointTransition(
            point_id=int(self.point_id),
            target_map_source_id=str(self.target_map_source_id),
            transition_type=self.transition_type,
            action_label=self.action_label,
            raw_json=raw,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "point_source_id": self.source_point_id,
            "target_map_source_id": self.target_map_source_id,
            "transition_type": self.transition_type,
            "action_label": self.action_label,
            "key": self.key,
            "json_path": self.json_path,
            "discovery_source": self.discovery_source,
            "raw_value": self.raw_value,
            "note": self.note,
        }


@dataclass(frozen=True)
class MapFact:
    """`/v1/map/info` 的事实（不是边）：真名 / preview / detail / 可见性。

    **preview 不能当可渲染判据**（M7.0：979 自己的 preview 为空，URL 只在父容器 info 里，
    这类 435 例）；`has_detail` 才是 raster 的前置条件，判定交给 `render_probe`。
    """

    map_id: str
    key: str
    json_path: str
    discovery_source: str = SOURCE_MAP_INFO
    name: str | None = None
    node_type: int | None = None
    depth: int | None = None
    parent_id: str | None = None
    parent_name: str | None = None
    preview: str | None = None
    is_hide: bool | None = None
    has_detail: bool = False

    @property
    def name_missing(self) -> bool:
        return not str(self.name or "").strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "name": self.name,
            "node_type": self.node_type,
            "depth": self.depth,
            "parent_id": self.parent_id,
            "parent_name": self.parent_name,
            "preview": self.preview,
            "is_hide": self.is_hide,
            "has_detail": self.has_detail,
            "json_path": self.json_path,
            "discovery_source": self.discovery_source,
        }


@dataclass(frozen=True)
class BundleContractHit:
    """bundle 里一个符号的命中情况（§七 第五层）。"""

    symbol: str
    kind: str
    implies_edge_type: str | None
    occurrences: int
    evidence: str = ""
    note: str = ""

    @property
    def found(self) -> bool:
        return self.occurrences > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "implies_edge_type": self.implies_edge_type,
            "occurrences": self.occurrences,
            "found": self.found,
            "evidence": self.evidence,
            "note": self.note,
        }


@dataclass(frozen=True)
class BundleConstant:
    """从 bundle 文本里解析出来的枚举（例如 MAP_GROUP_TYPE）。"""

    name: str
    values: tuple[tuple[str, int], ...]
    json_path: str
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "values": dict(self.values),
            "json_path": self.json_path,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class Extraction:
    """一个 extractor 的输出：候选边 + 跳转 + 事实 + 契约 + 诊断。"""

    source: str
    edges: tuple[CandidateEdge, ...] = ()
    transitions: tuple[CandidateTransition, ...] = ()
    facts: tuple[MapFact, ...] = ()
    contracts: tuple[BundleContractHit, ...] = ()
    constants: tuple[BundleConstant, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def counts(self) -> dict[str, int]:
        counts = Counter(edge.edge_type for edge in self.edges)
        return dict(sorted(counts.items()))

    def edges_from(self, map_id: str) -> tuple[CandidateEdge, ...]:
        """闭包遍历用（§六）：只返回以该地图为 source 的边。

        `closure()` 会断言 `expand(map_id)` 返回的边 source 必须等于 map_id；
        map/info 的 `parent_id → 自己` 是反方向的边，这里**如实丢掉**，由调用方用
        `edges`（全量）落库。
        """
        wanted = str(map_id)
        return tuple(edge for edge in self.edges if edge.source_map_id == wanted)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "counts": self.counts(),
            "edges": [edge.as_dict() for edge in self.edges],
            "transitions": [item.as_dict() for item in self.transitions],
            "facts": [item.as_dict() for item in self.facts],
            "contracts": [item.as_dict() for item in self.contracts],
            "constants": [item.as_dict() for item in self.constants],
            "diagnostics": list(self.diagnostics),
        }


# --------------------------------------------------------------------------- #
# payload 外形
# --------------------------------------------------------------------------- #


#: 官方外壳可能带的顶层键。只带 `data>>（测试夹具 / 摘录片段）时也当外壳脱。
_ENVELOPE_KEYS: frozenset[str] = frozenset({"retcode", "message", "data"})


def payload_data(payload: Any) -> Any:
    """脱下 `{retcode, message, data}>> 外壳；已经是内层就原样返回。

    判定：带 `data>> 且顶层键只在 `{retcode, message, data}>> 里（官方外壳，或者只摘了
    data 的片段）。顶层还有别的业务键就**不脱**——那说明 `data>> 是业务字段，不是外壳。
    """
    if isinstance(payload, Mapping) and "data" in payload:
        if "retcode" in payload or "message" in payload:
            return payload.get("data")
        if set(payload) <= _ENVELOPE_KEYS:
            return payload.get("data")
    return payload


def _seq(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


# --------------------------------------------------------------------------- #
# 1) Tree extractor
# --------------------------------------------------------------------------- #


def extract_tree(payload: Any, *, source: str = SOURCE_MAP_TREE) -> Extraction:
    """`/v1/map/tree` → TREE_CHILD（parent_id）/ RELATED_MAP（related_id）/ MAP_GROUP（related_group_map）。

    节点事实（tree_leaf / is_hide / map_group_type / map_shape）作为 `facts` 带出去，
    它们是 M7.3 回填 `map_nodes` 的输入，不是边。
    """
    data = payload_data(payload)
    tree = data.get("tree") if isinstance(data, Mapping) else data
    nodes = _seq(tree)

    edges: list[CandidateEdge] = []
    facts: list[MapFact] = []
    diagnostics: list[str] = []

    def walk(items: Sequence[Any], path: str, inherited_parent: Any) -> None:
        for index, node in enumerate(items):
            if not isinstance(node, Mapping):
                diagnostics.append(f"{path}[{index}] 不是对象，跳过")
                continue
            node_path = f"{path}[{index}]"
            map_id = as_map_id(node.get("id"))
            if map_id is None:
                diagnostics.append(f"{node_path} 没有可用 id，跳过")
                continue
            parent_raw = node.get("parent_id", inherited_parent)
            parent_id = None if is_absent(parent_raw) else as_map_id(parent_raw)
            if not is_absent(parent_raw) and parent_id is None:
                diagnostics.append(f"{node_path}.parent_id={parent_raw!r} 不是 id 形态")
            if parent_id is not None and parent_id != map_id:
                edges.append(
                    CandidateEdge(
                        source_map_id=parent_id,
                        target_map_id=map_id,
                        edge_type=TREE_CHILD,
                        key="parent_id",
                        json_path=f"{node_path}.parent_id",
                        discovery_source=source,
                        raw_value=parent_raw,
                        note="官方树事实：父节点 → 子节点",
                    )
                )
            related_raw = node.get("related_id")
            related_id = None if is_absent(related_raw) else as_map_id(related_raw)
            if related_id is not None and related_id != map_id:
                edges.append(
                    CandidateEdge(
                        source_map_id=map_id,
                        target_map_id=related_id,
                        edge_type=RELATED_MAP,
                        key="related_id",
                        json_path=f"{node_path}.related_id",
                        discovery_source=source,
                        raw_value=related_raw,
                        note="隐藏容器指回它可见的那张区域地图（bundle: resolveVisibleRelatedItem）",
                    )
                )
            group_raw = node.get("related_group_map")
            group_id = None if is_absent(group_raw) else as_map_id(group_raw)
            if group_id is not None and group_id != map_id:
                edges.append(
                    CandidateEdge(
                        source_map_id=map_id,
                        target_map_id=group_id,
                        edge_type=MAP_GROUP,
                        key="related_group_map",
                        json_path=f"{node_path}.related_group_map",
                        discovery_source=source,
                        raw_value=group_raw,
                        note="昼 / 夜 / 异域 / 异世的配对子图（bundle: changeMapGroup）",
                    )
                )
            children = _seq(node.get("children"))
            facts.append(
                MapFact(
                    map_id=map_id,
                    key="children",
                    json_path=f"{node_path}.children",
                    discovery_source=source,
                    name=node.get("name"),
                    node_type=node.get("node_type"),
                    depth=node.get("depth"),
                    parent_id=parent_id,
                    preview=node.get("preview"),
                    is_hide=bool(node.get("is_hide")),
                    has_detail=bool(node.get("detail")),
                )
            )
            walk(children, f"{node_path}.children", node.get("id"))

    walk(nodes, "$.data.tree", None)
    return Extraction(source=source, edges=tuple(edges), facts=tuple(facts), diagnostics=tuple(diagnostics))


# --------------------------------------------------------------------------- #
# 2) Map-info extractor
# --------------------------------------------------------------------------- #


def extract_map_info(payload: Any, *, source: str = SOURCE_MAP_INFO) -> Extraction:
    """`/v1/map/info` → 真名 / preview / detail 事实 + `parent_id` 边 + related / group。

    容器（`node_type=1`）的 `children[].name` 才是深层小地图的**真名**来源（M7.0）——
    本快照里 624 张已同步地图的 info 都没有 children，容器 info 只在 live 上拿得到。
    """
    data = payload_data(payload)
    info = data.get("info") if isinstance(data, Mapping) else None
    if not isinstance(info, Mapping):
        return Extraction(source=source, diagnostics=("payload 里没有 data.info，跳过",))

    map_id = as_map_id(info.get("id"))
    if map_id is None:
        return Extraction(source=source, diagnostics=("data.info.id 不是 id 形态，跳过",))

    edges: list[CandidateEdge] = []
    facts: list[MapFact] = []
    diagnostics: list[str] = []
    base = "$.data.info"

    parent_raw = info.get("parent_id")
    parent_id = None if is_absent(parent_raw) else as_map_id(parent_raw)
    if parent_id is not None and parent_id != map_id:
        edges.append(
            CandidateEdge(
                source_map_id=parent_id,
                target_map_id=map_id,
                edge_type=TREE_CHILD,
                key="parent_id",
                json_path=f"{base}.parent_id",
                discovery_source=source,
                raw_value=parent_raw,
                note="map/info 自带 parent_id：与 tree 的父子边同键，落库时幂等去重",
            )
        )
    related_raw = info.get("related_id")
    related_id = None if is_absent(related_raw) else as_map_id(related_raw)
    if related_id is not None and related_id != map_id:
        edges.append(
            CandidateEdge(
                source_map_id=map_id,
                target_map_id=related_id,
                edge_type=RELATED_MAP,
                key="related_id",
                json_path=f"{base}.related_id",
                discovery_source=source,
                raw_value=related_raw,
                note="与 tree 的 related_id 同源",
            )
        )
    group_raw = info.get("related_group_map")
    group_id = None if is_absent(group_raw) else as_map_id(group_raw)
    if group_id is not None and group_id != map_id:
        edges.append(
            CandidateEdge(
                source_map_id=map_id,
                target_map_id=group_id,
                edge_type=MAP_GROUP,
                key="related_group_map",
                json_path=f"{base}.related_group_map",
                discovery_source=source,
                raw_value=group_raw,
                note="与 tree 的 related_group_map 同源",
            )
        )

    facts.append(
        MapFact(
            map_id=map_id,
            key="info",
            json_path=base,
            discovery_source=source,
            name=info.get("name"),
            node_type=info.get("node_type"),
            depth=info.get("depth"),
            parent_id=parent_id,
            parent_name=info.get("parent_name"),
            preview=info.get("preview"),
            is_hide=bool(info.get("is_hide")),
            has_detail=bool(info.get("detail")),
        )
    )
    for index, child in enumerate(_seq(info.get("children"))):
        if not isinstance(child, Mapping):
            continue
        child_id = as_map_id(child.get("id"))
        if child_id is None:
            continue
        facts.append(
            MapFact(
                map_id=child_id,
                key="children[].name",
                json_path=f"{base}.children[{index}].name",
                discovery_source=source,
                name=child.get("name"),
                node_type=child.get("node_type"),
                depth=child.get("depth"),
                parent_id=map_id,
                parent_name=info.get("name"),
                preview=child.get("preview"),
                is_hide=bool(child.get("is_hide")),
                has_detail=bool(child.get("detail")),
            )
        )
    return Extraction(source=source, edges=tuple(edges), facts=tuple(facts), diagnostics=tuple(diagnostics))


# --------------------------------------------------------------------------- #
# 3) Point-list extractor
# --------------------------------------------------------------------------- #


def extract_point_list(payload: Any, map_id: str, *, source: str = SOURCE_POINT_LIST) -> Extraction:
    """`/v1/map/point/list` → POINT_JUMP 边 + point_transitions。

    `related_jump_id` 就是「前往对应地图」的目标（bundle 字面证据）。`label_id` 不是边
    （label id 空间与 map id 空间重叠，按区间猜必错）。payload 里只有官方 source id，
    points 表主键由调用方替换（`CandidateTransition.point_id`）。
    """
    source_map = as_map_id(map_id)
    if source_map is None:
        return Extraction(source=source, diagnostics=(f"map_id={map_id!r} 不是 id 形态，跳过整份 point/list",))
    data = payload_data(payload)
    points = data.get("point_list") if isinstance(data, Mapping) else data
    edges: list[CandidateEdge] = []
    transitions: list[CandidateTransition] = []
    diagnostics: list[str] = []
    for index, point in enumerate(_seq(points)):
        if not isinstance(point, Mapping):
            diagnostics.append(f"$.data.point_list[{index}] 不是对象，跳过")
            continue
        point_path = f"$.data.point_list[{index}]"
        point_source_id = as_map_id(point.get("id"))
        label_id = as_map_id(point.get("label_id"))
        jump_raw = point.get("related_jump_id")
        if is_absent(jump_raw):
            continue
        target = as_map_id(jump_raw)
        if target is None:
            diagnostics.append(f"{point_path}.related_jump_id={jump_raw!r} 不是 id 形态")
            continue
        if point_source_id is None:
            diagnostics.append(f"{point_path} 没有可用 id：有跳转目标 {target} 但没法建立 transition")
            continue
        edges.append(
            CandidateEdge(
                source_map_id=source_map,
                target_map_id=target,
                edge_type=POINT_JUMP,
                key="related_jump_id",
                json_path=f"{point_path}.related_jump_id",
                discovery_source=source,
                source_point_id=point_source_id,
                source_label_id=label_id,
                raw_value=jump_raw,
                note="点位弹窗「前往对应地图」→ 目标地图",
            )
        )
        transitions.append(
            CandidateTransition(
                point_id=point_source_id,  # 由调用方换成 points 主键
                target_map_source_id=target,
                transition_type=POINT_JUMP,
                key="related_jump_id",
                json_path=f"{point_path}.related_jump_id",
                discovery_source=source,
                action_label=ACTION_LABEL_REDIRECT,
                source_point_id=point_source_id,
                raw_value=jump_raw,
                note="action_label 来自 UI 文案（非字面证据）；链路本身是 bundle 字面证据",
            )
        )
    return Extraction(source=source, edges=tuple(edges), transitions=tuple(transitions), diagnostics=tuple(diagnostics))


# --------------------------------------------------------------------------- #
# 4) Point-info extractor
# --------------------------------------------------------------------------- #


def extract_point_info(payload: Any, *, source: str = SOURCE_POINT_INFO) -> Extraction:
    """`/v1/map/point/info` → 与 point/list 同值的 POINT_JUMP（交叉校验）。

    `info.map_id` 是**点自己所属的标点层**（bundle: `pointMapId = info.map_id`），
    「跳转标点层」走它，**不是地图边**——只作为边 source 记录下来。
    """
    data = payload_data(payload)
    info = data.get("info") if isinstance(data, Mapping) else None
    if not isinstance(info, Mapping):
        return Extraction(source=source, diagnostics=("payload 里没有 data.info，跳过",))
    point_source_id = as_map_id(info.get("id"))
    if point_source_id is None:
        return Extraction(source=source, diagnostics=("data.info.id 不是 id 形态，跳过",))
    map_id = as_map_id(info.get("map_id"))
    label_id = as_map_id(info.get("label_id"))
    jump_raw = info.get("related_jump_id")
    diagnostics: list[str] = []
    if is_absent(jump_raw):
        diagnostics.append(f"point {point_source_id} 的 related_jump_id 是「无」")
        return Extraction(source=source, diagnostics=tuple(diagnostics))
    target = as_map_id(jump_raw)
    if target is None:
        diagnostics.append(f"$.data.info.related_jump_id={jump_raw!r} 不是 id 形态")
        return Extraction(source=source, diagnostics=tuple(diagnostics))
    if map_id is None:
        #: 没有 map_id 就不知道这条跳转的 source 是哪张图：不猜，但把 target 记进诊断（不丢）。
        diagnostics.append(f"point {point_source_id} 有跳转目标 {target} 但 info.map_id 缺失：无法定位 source")
        return Extraction(source=source, diagnostics=tuple(diagnostics))

    edge = CandidateEdge(
        source_map_id=map_id,
        target_map_id=target,
        edge_type=POINT_JUMP,
        key="related_jump_id",
        json_path="$.data.info.related_jump_id",
        discovery_source=source,
        source_point_id=point_source_id,
        source_label_id=label_id,
        raw_value=jump_raw,
        note="point/info 与 point/list 的交叉校验",
    )
    transition = CandidateTransition(
        point_id=point_source_id,
        target_map_source_id=target,
        transition_type=POINT_JUMP,
        key="related_jump_id",
        json_path="$.data.info.related_jump_id",
        discovery_source=source,
        action_label=ACTION_LABEL_REDIRECT,
        source_point_id=point_source_id,
        raw_value=jump_raw,
        note="交叉校验；map_id 只是点自己所属的标点层，不是边",
    )
    return Extraction(source=source, edges=(edge,), transitions=(transition,), diagnostics=tuple(diagnostics))


# --------------------------------------------------------------------------- #
# 5) Bundle contract extractor
# --------------------------------------------------------------------------- #

#: 官方前端 bundle 里与地图跳转相关的符号。kind 说明它在 UI 里管什么。
BUNDLE_SYMBOLS: tuple[tuple[str, str, str | None, str], ...] = (
    ("openRedirectConfirmation", "navigation_action", POINT_JUMP,
     "点位弹窗 → 「前往对应地图」；payload 里 mapId 就是 related_jump_id"),
    ("related_jump_id", "payload_key", POINT_JUMP, "bundle 直接读它，'0' 表示没有跳转"),
    ("changeLayer", "interaction", None,
     "「跳转标点层 / 3D」：plane 分支走 pointMapId（不是边），其它分支才走 mapId"),
    ("resolveVisibleRelatedItem", "related_resolution", RELATED_MAP,
     "沿 is_hide + related_id（'0' 终止）回溯到可见区域"),
    ("changeMapGroup", "group_switch", MAP_GROUP, "用 related_group_map 找昼 / 夜配对子图"),
    ("origin_map_id", "return_stack", RETURN, "URL query 里的返回栈（逗号分隔）"),
    ("pushOriginMapId", "return_stack", RETURN, "进入目标图前把当前图压栈"),
    ("topOriginMapId", "return_stack", RETURN, "返回栈顶 = 上一张图"),
    ("backOriginMapId", "return_stack", RETURN, "弹出栈顶后的剩余栈"),
    ("map_group_type", "payload_key", None, "MAP_GROUP_TYPE 枚举（昼 / 夜 / 异域 / 异世）"),
    ("jump_target_id", "payload_key", None, "bundle 里零出现：当前不是跳转来源，但留在 registry"),
)

_MAP_GROUP_TYPE_RE = re.compile(r"MAP_GROUP_TYPE\s*=\s*Object\.freeze\(\s*\{([^}]*)\}\s*\)")
_ENUM_PAIR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\d+)")


def _snippet(text: str, index: int, *, width: int = 140) -> str:
    """把命中处周围压成一行短证据（报告要能读，不能塞整个 minified bundle）。"""
    start = max(0, index - width)
    chunk = text[start : index + width].replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", chunk).strip()


def extract_bundle_contract(bundle_text: str, *, source: str = SOURCE_BUNDLE) -> Extraction:
    """官方前端 bundle → 键语义契约（§七 第五层）。

    纯函数：只扫文本。找到的符号给出次数 + 一段短证据；**没找到的符号也如实列出**
    （`jump_target_id` 在 bundle 里零出现，这本身就是「它不是 JUMP 来源」的证据）。
    顺带把 `MAP_GROUP_TYPE` 枚举解析出来，`map_group_type` 的取值不用手抄。
    """
    text = bundle_text or ""
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest() if text else ""
    contracts: list[BundleContractHit] = []
    diagnostics: list[str] = []
    for symbol, kind, edge_type, note in BUNDLE_SYMBOLS:
        occurrences = text.count(symbol)
        evidence = _snippet(text, text.index(symbol)) if occurrences else ""
        contracts.append(
            BundleContractHit(
                symbol=symbol,
                kind=kind,
                implies_edge_type=edge_type,
                occurrences=occurrences,
                evidence=evidence,
                note=note,
            )
        )
    missing = tuple(item.symbol for item in contracts if not item.found)
    if missing:
        diagnostics.append("bundle 里没找到这些符号：" + ", ".join(missing))

    constants: list[BundleConstant] = []
    match = _MAP_GROUP_TYPE_RE.search(text)
    if match:
        pairs = tuple((name, int(value)) for name, value in _ENUM_PAIR_RE.findall(match.group(1)))
        constants.append(
            BundleConstant(
                name="MAP_GROUP_TYPE",
                values=pairs,
                json_path="bundle:MAP_GROUP_TYPE",
                evidence=_snippet(text, match.start()),
            )
        )
    else:
        diagnostics.append("bundle 里没有 MAP_GROUP_TYPE 枚举")

    diagnostics.append(f"bundle sha256={digest[:16]}…（{len(text)} 字符）" if text else "bundle 文本为空")
    return Extraction(source=source, contracts=tuple(contracts), constants=tuple(constants),
                      diagnostics=tuple(diagnostics))


def bundle_contract_index(extraction: Extraction) -> dict[str, dict[str, Any]]:
    """把 bundle 扫描结果变成可查的契约表（symbol → 命中信息）。"""
    return {hit.symbol: hit.as_dict() for hit in extraction.contracts}


def bundle_key_contracts(extraction: Extraction | None = None) -> list[dict[str, Any]]:
    """键 → 语义的注册表（§七 的 transition_registry）。给了 bundle 结果就附上命中情况。"""
    hits = {} if extraction is None else {hit.symbol: hit for hit in extraction.contracts}
    out: list[dict[str, Any]] = []
    for item in KEY_CONTRACTS:
        payload = item.as_dict()
        hit = hits.get(item.key)
        payload["bundle_found"] = None if hit is None else hit.found
        payload["bundle_occurrences"] = None if hit is None else hit.occurrences
        out.append(payload)
    return out


# --------------------------------------------------------------------------- #
# ID Reference Scanner（§八）
# --------------------------------------------------------------------------- #

#: §八 的键形态清单。
REFERENCE_KEY_PATTERNS: tuple[str, ...] = (
    "*_id",
    "*_map",
    "target*",
    "related*",
    "jump*",
    "group*",
    "*_ids",
    "origin_map_id",
)

UNKNOWN_RENDERABLE_TARGET = "UNKNOWN_RENDERABLE_TARGET"
MAP_LIKE_NON_RASTER = "MAP_LIKE_NON_RASTER"
NOT_A_MAP = "NOT_A_MAP"
UNRESOLVED = "UNRESOLVED"

SCAN_CONCLUSIONS: tuple[str, ...] = (
    UNKNOWN_RENDERABLE_TARGET,
    MAP_LIKE_NON_RASTER,
    NOT_A_MAP,
    UNRESOLVED,
)

#: 探测通道的四个结论（id 空间重叠时必须靠它，不能靠数值区间）。
PROBE_RENDERABLE = "RENDERABLE"
PROBE_MAP_LIKE = "MAP_LIKE"
PROBE_NOT_MAP = "NOT_MAP"
PROBE_UNKNOWN = "UNKNOWN"

PROBE_OUTCOMES: tuple[str, ...] = (PROBE_RENDERABLE, PROBE_MAP_LIKE, PROBE_NOT_MAP, PROBE_UNKNOWN)

DEFAULT_ENDPOINT = "payload"


def is_reference_key(key: str) -> bool:
    """键名是不是 §八 列的那几种形态。"""
    lowered = str(key).lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in REFERENCE_KEY_PATTERNS)


@dataclass(frozen=True)
class MapProbeResult:
    """一次「这个数字是不是地图」的探测结论 + 依据。

    `overlaps` 记的是 id 空间重叠事实（这个数字同时出现在哪些别的 id 空间），
    它是「为什么不能按区间猜」的现场证据。
    """

    map_id: str
    outcome: str
    reasons: tuple[str, ...] = ()
    source: str = "unprobed"
    overlaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in PROBE_OUTCOMES:
            raise ValueError(f"unknown probe outcome: {self.outcome!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "outcome": self.outcome,
            "source": self.source,
            "reasons": list(self.reasons),
            "overlaps": list(self.overlaps),
        }


#: 探测通道：online 用 map/info + raster，offline 用 maps / map_nodes 证据。
MapProbe = Callable[[str], MapProbeResult]


@dataclass(frozen=True)
class IdReference:
    """一条 id 引用 + 结论 + 出处（§八 要求逐条记下来的六元组）。"""

    endpoint: str
    json_path: str
    key: str
    raw_value: Any
    map_id: str
    conclusion: str
    discovery_source: str
    key_space: str = SPACE_UNKNOWN
    claimed_by: str | None = None
    probe: MapProbeResult | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.conclusion not in SCAN_CONCLUSIONS:
            raise ValueError(f"unknown scan conclusion: {self.conclusion!r}")

    @property
    def is_candidate(self) -> bool:
        """探测说是可渲染地图、但字段语义未知：候选跳转，不许丢（§八 / §五）。"""
        return self.conclusion == UNKNOWN_RENDERABLE_TARGET

    def as_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "json_path": self.json_path,
            "key": self.key,
            "raw_value": self.raw_value,
            "map_id": self.map_id,
            "conclusion": self.conclusion,
            "discovery_source": self.discovery_source,
            "key_space": self.key_space,
            "claimed_by": self.claimed_by,
            "reasons": list(self.reasons),
            "probe": None if self.probe is None else self.probe.as_dict(),
        }


def _classify(
    key: str,
    map_id: str,
    contract: KeyContract | None,
    probe: MapProbeResult | None,
) -> tuple[str, tuple[str, ...]]:
    """键语义 + 探测结论 → 四类结论之一。

    顺序很重要：**先看键语义**（它是 label 引用就不可能是地图边，哪怕数字恰好也是地图 id），
    再看探测（键语义未知时才由探测说了算）。两边都不确定就是 UNRESOLVED——不猜。
    """
    reasons: list[str] = []
    space = SPACE_UNKNOWN if contract is None else contract.space
    if contract is not None:
        reasons.append(f"键 {key!r} 的 id 空间 = {space}（{contract.evidence or '语义未证实'}）")
    else:
        reasons.append(f"键 {key!r} 不在契约表里：字段语义未知")

    probe_outcome = PROBE_UNKNOWN if probe is None else probe.outcome
    if probe is not None:
        reasons.extend(probe.reasons)
        if probe.overlaps:
            reasons.append("id 空间重叠：" + "、".join(probe.overlaps))
    else:
        reasons.append("没有探测通道：不猜这个数字是不是地图")

    if space in NON_MAP_SPACES:
        if probe_outcome == PROBE_RENDERABLE:
            reasons.append(f"探测到 {map_id} 确实是一张可渲染地图：**id 空间重叠**，按区间猜会错；本键语义不是地图引用")
        return NOT_A_MAP, tuple(reasons)

    if probe_outcome == PROBE_RENDERABLE:
        reasons.append(f"探测到 {map_id} 是可渲染地图：字段语义虽然未知，target 不许丢（候选跳转）")
        return UNKNOWN_RENDERABLE_TARGET, tuple(reasons)
    if probe_outcome == PROBE_MAP_LIKE:
        reasons.append(f"探测到 {map_id} 像地图（在树里）但没有 raster 证据：不是可导航目标")
        return MAP_LIKE_NON_RASTER, tuple(reasons)
    if probe_outcome == PROBE_NOT_MAP:
        reasons.append(f"探测到 {map_id} 不是地图")
        return NOT_A_MAP, tuple(reasons)
    return UNRESOLVED, tuple(reasons)


@dataclass(frozen=True)
class ScanReport:
    """一次扫描的结果（可聚合）。"""

    endpoint: str
    discovery_source: str
    references: tuple[IdReference, ...] = ()
    skipped_absent: int = 0
    scanned_nodes: int = 0
    diagnostics: tuple[str, ...] = ()

    def by_conclusion(self) -> dict[str, int]:
        counts = Counter(item.conclusion for item in self.references)
        return {name: counts.get(name, 0) for name in SCAN_CONCLUSIONS}

    def key_histogram(self) -> dict[str, int]:
        return dict(sorted(Counter(item.key for item in self.references).items()))

    def candidates(self) -> tuple[IdReference, ...]:
        return tuple(item for item in self.references if item.is_candidate)

    def unclaimed_candidates(self) -> tuple[IdReference, ...]:
        return tuple(item for item in self.references if item.is_candidate and item.claimed_by is None)

    def examples(self, conclusion: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        picked = [item for item in self.references if conclusion is None or item.conclusion == conclusion]
        return [item.as_dict() for item in picked[:limit]]

    def summary(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "discovery_source": self.discovery_source,
            "scanned_nodes": self.scanned_nodes,
            "skipped_absent": self.skipped_absent,
            "references": len(self.references),
            "by_conclusion": self.by_conclusion(),
            "keys": self.key_histogram(),
            "candidates": [item.as_dict() for item in self.candidates()[:20]],
            "diagnostics": list(self.diagnostics[:20]),
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self.summary(), "all": [item.as_dict() for item in self.references]}


def scan_id_references(
    payload: Any,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    discovery_source: str | None = None,
    probe: MapProbe | None = None,
    rules: Iterable[Callable[[str], bool]] | None = None,
    key_contracts: Mapping[str, KeyContract] | None = None,
    max_depth: int = 32,
) -> ScanReport:
    """§八 的 ID Reference Scanner：递归遍历任意 dict / list，找所有像 id 引用的字段。

    - 键形态：`*_id` / `*_map` / `target*` / `related*` / `jump*` / `group*` / `*_ids` / `origin_map_id`；
    - 值形态：数字 / 数字串 / 数字数组 / 逗号数字串（`expand_id_values`）；
    - `0` / `"0"` / `""` 一律当「无」过滤掉，并计入 `skipped_absent`；
    - 结论由**键语义 + 探测**共同决定，`probe=None` 时是 UNRESOLVED（不猜）。

    每条结论都带 `(endpoint, json 路径, 键名, 原值, 结论, discovery_source)`。
    """
    matcher = list(rules) if rules is not None else [is_reference_key]
    overrides = dict(key_contracts) if key_contracts is not None else dict(key_contracts_for(endpoint))
    source = discovery_source or endpoint
    references: list[IdReference] = []
    diagnostics: list[str] = []
    counter = {"nodes": 0, "absent": 0}

    def visit(node: Any, path: str, seen: set[int], depth: int) -> None:
        if depth > max_depth:
            diagnostics.append(f"{path} 超过 max_depth={max_depth}，停止下探")
            return
        if isinstance(node, Mapping):
            if id(node) in seen:
                diagnostics.append(f"{path} 有环，停止下探")
                return
            seen.add(id(node))
            counter["nodes"] += 1
            for key, value in node.items():
                key_path = f"{path}.{key}"
                if any(match(str(key)) for match in matcher):
                    values = expand_id_values(value)
                    absent_here = count_absent_values(value)
                    counter["absent"] += absent_here
                    if not values and not absent_here:
                        diagnostics.append(f"{key_path}={value!r} 像引用但不是 id 形态")
                    contract = overrides.get(str(key)) or contract_for_key(str(key))
                    for suffix, raw_item, map_id in values:
                        if map_id is None:
                            diagnostics.append(f"{key_path}{suffix}={raw_item!r} 像引用但不是 id 形态")
                            continue
                        item_path = key_path + suffix
                        result = None if probe is None else probe(map_id)
                        conclusion, reasons = _classify(str(key), map_id, contract, result)
                        references.append(
                            IdReference(
                                endpoint=endpoint,
                                json_path=item_path,
                                key=str(key),
                                raw_value=raw_item,
                                map_id=map_id,
                                conclusion=conclusion,
                                discovery_source=source,
                                key_space=SPACE_UNKNOWN if contract is None else contract.space,
                                claimed_by=None if contract is None else contract.extractor,
                                probe=result,
                                reasons=reasons,
                            )
                        )
                visit(value, key_path, seen, depth + 1)
            seen.discard(id(node))
            return
        if isinstance(node, (list, tuple)):
            if id(node) in seen:
                diagnostics.append(f"{path} 有环，停止下探")
                return
            seen.add(id(node))
            counter["nodes"] += 1
            for index, item in enumerate(node):
                visit(item, f"{path}[{index}]", seen, depth + 1)
            seen.discard(id(node))

    visit(payload, "$", set(), 0)
    return ScanReport(
        endpoint=endpoint,
        discovery_source=source,
        references=tuple(references),
        skipped_absent=counter["absent"],
        scanned_nodes=counter["nodes"],
        diagnostics=tuple(diagnostics),
    )


def aggregate_scans(reports: Iterable[ScanReport], *, examples: int = 5) -> dict[str, Any]:
    """把多份扫描聚合成报告片段（逐份留着会爆；结论计数 + 代表例子足够定位）。"""
    items = list(reports)
    by_conclusion: Counter[str] = Counter()
    by_key: Counter[str] = Counter()
    by_space: Counter[str] = Counter()
    per_endpoint: dict[str, Counter[str]] = {}
    candidates: list[dict[str, Any]] = []
    unclaimed: list[dict[str, Any]] = []
    references = 0
    skipped = 0
    for report in items:
        references += len(report.references)
        skipped += report.skipped_absent
        bucket = per_endpoint.setdefault(report.endpoint, Counter())
        for item in report.references:
            by_conclusion[item.conclusion] += 1
            by_key[item.key] += 1
            by_space[item.key_space] += 1
            bucket[item.conclusion] += 1
            if item.is_candidate and len(candidates) < examples * 4:
                candidates.append(item.as_dict())
            if item.is_candidate and item.claimed_by is None and len(unclaimed) < examples * 4:
                unclaimed.append(item.as_dict())
    return {
        "reports": len(items),
        "references": references,
        "skipped_absent": skipped,
        "by_conclusion": {name: by_conclusion.get(name, 0) for name in SCAN_CONCLUSIONS},
        "by_key": dict(by_key.most_common()),
        "by_key_space": dict(by_space.most_common()),
        "per_endpoint": {name: dict(sorted(counter.items())) for name, counter in sorted(per_endpoint.items())},
        "candidates": candidates[: examples * 4],
        "unclaimed_candidates": unclaimed[: examples * 4],
        "diagnostics": [note for report in items for note in report.diagnostics[:examples]][: examples * 4],
    }


def dump_json(payload: Any) -> str:
    """报告统一用这个序列化（ensure_ascii=False，人能读）。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


__all__ = [
    "ABSENT_SCALARS",
    "ACTION_LABEL_REDIRECT",
    "BUNDLE_SYMBOLS",
    "BundleConstant",
    "BundleContractHit",
    "CandidateEdge",
    "CandidateTransition",
    "DEFAULT_ENDPOINT",
    "EXTRACTOR_SOURCES",
    "Extraction",
    "IdReference",
    "KEY_CONTRACTS",
    "KEY_CONTRACT_INDEX",
    "KEY_CONTRACT_OVERRIDES",
    "KeyContract",
    "MAP_LIKE_NON_RASTER",
    "MapFact",
    "MapProbe",
    "MapProbeResult",
    "NOT_A_MAP",
    "NON_MAP_SPACES",
    "PROBE_MAP_LIKE",
    "PROBE_NOT_MAP",
    "PROBE_RENDERABLE",
    "PROBE_UNKNOWN",
    "REFERENCE_KEY_PATTERNS",
    "SCAN_CONCLUSIONS",
    "SOURCE_BUNDLE",
    "SOURCE_LABEL_TREE",
    "SOURCE_MAP_INFO",
    "SOURCE_MAP_TREE",
    "SOURCE_POINT_INFO",
    "SOURCE_POINT_LIST",
    "SPACE_GAME",
    "SPACE_LABEL",
    "SPACE_MAP",
    "SPACE_POINT",
    "SPACE_SELF",
    "SPACE_UNKNOWN",
    "ScanReport",
    "UNKNOWN_RENDERABLE_TARGET",
    "UNRESOLVED",
    "aggregate_scans",
    "as_map_id",
    "bundle_contract_index",
    "bundle_key_contracts",
    "contract_for_key",
    "count_absent_values",
    "dump_json",
    "expand_id_values",
    "extract_bundle_contract",
    "extract_map_info",
    "extract_point_info",
    "extract_point_list",
    "extract_tree",
    "is_absent",
    "is_reference_key",
    "key_contracts_for",
    "payload_data",
    "scan_id_references",
]
