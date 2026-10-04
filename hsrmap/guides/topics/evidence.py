"""What kind of puzzle a topic is, and whether a text guide can carry it.

黄金替罪羊只需要按上下左右四个方向键，所以一篇只写方向序列的文字攻略就是**完整来源**
（这也是它文字攻略最多、最好用的原因）。二次元 JUMP 是平台跳跃、无名尘灵与若虫是 3D 隐藏
点位：文字攻略对它们只能给出「该地图共 N 个」这类范围证据，点位级绑定仍要等视觉证据。

搜索策略因此跟主题分类走，而不是所有主题都用同一套查询（a1-6 §六/§七）：
TEXT_OK 的主题优先找纯文字攻略；VISUAL 的主题只在能拿到分区计数时才值得投入。
"""

from __future__ import annotations

from typing import Any

from hsrmap.guides.topics.loader import get_topic, list_topics

#: Kinds of puzzle the corpus has met so far.
EVIDENCE_KINDS = (
    "INPUT_SEQUENCE",  # 只需要方向/按键序列（黄金替罪羊、ROTATE）
    "MODULE_PUZZLE",  # 模块移动与旋转（梦境迷钟）
    "TRANSFORM_PUZZLE",  # 变身 + 机关（小小哈努行动）
    "PLATFORMER",  # 平台跳跃、精确走位（二次元 JUMP）
    "COLLECTIBLE_SPOT",  # 3D 隐藏点位收集（无名尘灵、若虫、折纸小鸟）
    "LOCATION_TALK",  # 到点对话（王下一桶）
    "MIXED",
    "UNKNOWN",
)

#: Kinds whose steps are fully expressible as text, even without a picture.
TEXT_FIRST_KINDS = {"INPUT_SEQUENCE", "MODULE_PUZZLE"}


def evidence_form(topic_key: str) -> dict[str, Any]:
    """The topic's evidence_form block, with an honest default when it has none."""
    try:
        spec = get_topic(str(topic_key or "").replace("-", "_"))
    except Exception:  # noqa: BLE001 - an unknown topic is not an error here
        return {"kind": "UNKNOWN", "text_sufficient": None, "reason": ""}
    form = spec.get("evidence_form") or {}
    sufficient = form.get("text_sufficient")
    return {
        "kind": str(form.get("kind") or "UNKNOWN").upper(),
        "text_sufficient": None if sufficient is None else bool(sufficient),
        "reason": str(form.get("reason") or ""),
    }


def text_sufficient(topic_key: str) -> bool:
    """True when a text-only guide is a complete source for this topic.

    An unstated flag falls back to the kind: a pure input or module puzzle is
    text-carryable by construction, anything else is not claimed to be.
    """
    form = evidence_form(topic_key)
    if form["text_sufficient"] is not None:
        return bool(form["text_sufficient"])
    return str(form["kind"]) in TEXT_FIRST_KINDS


def search_hint(topic_key: str) -> str:
    """One line telling the next search what counts as a source for this topic."""
    if text_sufficient(topic_key):
        return "文字攻略即可作为完整来源：找写清步骤/方向序列的图文攻略"
    return "文字攻略只能当范围证据：需要「该地图共N个」这类计数，点位级绑定等视觉证据"


def profile_table(*, enabled_only: bool = True) -> list[dict[str, Any]]:
    """Every topic with its evidence form, for reports and the CLI."""
    out: list[dict[str, Any]] = []
    for item in list_topics(enabled_only=enabled_only):
        key = str(item.get("topic_key") or "")
        if not key:
            continue
        form = evidence_form(key)
        out.append(
            {
                "topic_key": key,
                "display_name": str(item.get("display_name") or key),
                "guide_kind": str(item.get("guide_kind") or ""),
                "scope": str(item.get("scope") or ""),
                **form,
                "search_hint": search_hint(key),
            }
        )
    out.sort(key=lambda row: (not row["text_sufficient"], row["topic_key"]))
    return out
