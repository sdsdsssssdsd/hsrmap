from __future__ import annotations

from typing import Any

from hsrmap.guides.topics.loader import list_topics


def classify_topics(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(str(block.get("text") or "") for block in blocks)
    topics = []
    for spec in list_topics():
        names = [spec.get("display_name"), *list((spec.get("official_labels") or {}).get("names") or [])]
        hits = [name for name in names if name and str(name) in text]
        if not hits:
            continue
        topics.append(
            {
                "topic_key": spec["topic_key"],
                "confidence": 0.95 if spec.get("display_name") in hits else 0.88,
            }
        )
    return {"topics": topics}
