from __future__ import annotations

from typing import Any

NO_GUIDE = (
    "界域定锚",
    "传送锚点",
    "空间锚点",
    "商店",
    "普通商店",
    "商铺",
    "阅读物",
    "书籍",
    "敌人",
    "普通敌人",
    "怪物",
)

LOCATION_ONLY = (
    "战利品",
    "普通战利品",
    "显眼战利品",
    "宝箱",
    "普通宝箱",
    "丰厚战利品",
    "贵重战利品",
)

PUZZLE_TOKENS = (
    "浮脂",
    "迷钟",
    "磁流",
    "不世之材",
    "解谜",
    "解密",
    "机关",
    "哈努",
    "JUMP",
    "jump",
    "扎格列斯",
    "开拓妖精",
    "二次元",
    "黄金替罪羊",
    "王下一桶",
    "次元扑满",
)

COLLECTIBLE_TOKENS = (
    "折纸小鸟",
    "若虫",
    "尘灵",
    "星轨之兔",
    "奇迹宝珠",
    "奇迹典籍",
    "涂鸦",
    "潮汐的馈赠",
    "收集",
)

CHALLENGE_TOKENS = ("JUMP", "jump", "哈努", "开拓妖精", "挑战", "王下一桶", "次元扑满")


def classify_label(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "")
    category = str(row.get("category") or "")
    hay = f"{name} {category}"
    score = 0
    kind = None
    status = "NEEDS_REVIEW"
    if any(token == name or token in name for token in NO_GUIDE) or any(token in category for token in ("传送", "商店", "阅读", "敌人")):
        if not any(token in name for token in PUZZLE_TOKENS + COLLECTIBLE_TOKENS):
            return _out(row, "NO_GUIDE_REQUIRED", False, None, 1.0, -100)
    if name in LOCATION_ONLY and not any(token in name for token in PUZZLE_TOKENS + COLLECTIBLE_TOKENS):
        return _out(row, "LOCATION_ONLY", False, "LOCATION", 0.8, 0)
    if category == "解密战利品" or "解密战利品" in category or "解谜" in category:
        score += 100
    if any(token in name for token in PUZZLE_TOKENS):
        score += 80
        kind = "CHALLENGE" if any(token in name for token in CHALLENGE_TOKENS) else "PUZZLE"
    if any(token in name for token in COLLECTIBLE_TOKENS) or "收集品" in category:
        score += 50
        if kind is None:
            kind = "COLLECTIBLE"
    if name in LOCATION_ONLY or (any(token == name or name.endswith(token) for token in LOCATION_ONLY) and score < 80):
        if score < 80:
            return _out(row, "LOCATION_ONLY", False, "LOCATION", 0.8, 0)
    if score >= 80:
        status = "GUIDE_TOPIC"
        return _out(row, status, True, kind or "PUZZLE", min(1.0, 0.6 + score / 200), score)
    if score >= 50 and kind == "COLLECTIBLE":
        return _out(row, "GUIDE_TOPIC", True, "COLLECTIBLE", 0.85, score)
    return _out(row, "NEEDS_REVIEW", False, None, 0.2, score)


def classify_labels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [classify_label(row) for row in rows]


def _out(row: dict[str, Any], status: str, needed: bool, kind: str | None, confidence: float, score: int) -> dict[str, Any]:
    return {
        "label_id": row.get("source_id") or row.get("label_id"),
        "name": row.get("name"),
        "category": row.get("category"),
        "point_count": int(row.get("point_count") or 0),
        "status": status,
        "guide_needed": needed,
        "suggested_kind": kind,
        "confidence": confidence,
        "score": score,
    }
