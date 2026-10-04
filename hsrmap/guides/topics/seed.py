from __future__ import annotations

import json

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.topics.loader import get_topic, list_topics
from hsrmap.guides.topics.official import official_points_for_topic


def seed_topics(db: GuideDatabase) -> list[dict]:
    out = []
    for topic in list_topics():
        out.append(_upsert_topic_row(db, topic))
    return out


def _upsert_topic_row(db: GuideDatabase, topic: dict) -> dict:
    return db.upsert_topic(
        {
            "topic_key": topic["topic_key"],
            "display_name": topic.get("display_name"),
            "guide_kind": topic.get("guide_kind"),
            "scope_type": topic.get("scope"),
            "matcher_profile": (topic.get("matcher") or {}).get("profile"),
            "layout_profile": (topic.get("layout") or {}).get("profile"),
            "priority": topic.get("priority") or 0,
            "enabled": topic.get("enabled", True),
        }
    )


def seed_official_targets(db: GuideDatabase, topic_key: str, ctx=None) -> int:
    spec = get_topic(topic_key)
    topic = _upsert_topic_row(db, spec)
    points = official_points_for_topic(spec["topic_key"], ctx=ctx)
    scope = str(spec.get("scope") or "POINT")
    count = 0
    if spec.get("seed_global"):
        db.upsert_target(
            {
                "topic_id": topic["id"],
                "target_type": "GLOBAL",
                "target_key": f"global:topic:{spec['topic_key']}",
                "metadata_json": json.dumps({"label": spec.get("display_name")}, ensure_ascii=False),
            }
        )
        count += 1
    if scope == "MAP_LABEL":
        seen: set[str] = set()
        for point in points:
            map_id = str(point.get("map_id") or "")
            if not map_id or map_id in seen:
                continue
            seen.add(map_id)
            db.upsert_target(
                {
                    "topic_id": topic["id"],
                    "target_type": "MAP_LABEL",
                    "target_key": f"map:{map_id}:topic:{spec['topic_key']}",
                    "map_id": map_id,
                    "metadata_json": json.dumps(
                        {"map_name": point.get("map_name"), "label": point.get("label")},
                        ensure_ascii=False,
                    ),
                }
            )
            count += 1
        return count
    if scope == "POINT_SET":
        groups: dict[str, list] = {}
        for point in points:
            pid = str(point.get("source_point_id") or "")
            if not pid:
                continue
            group_key = str(point.get("region") or point.get("map_id") or spec["topic_key"])
            groups.setdefault(group_key, []).append(point)
        for group_key, rows in groups.items():
            map_id = str(rows[0].get("map_id") or "")
            official_ids = [str(row.get("source_point_id")) for row in rows if row.get("source_point_id")]
            db.upsert_target(
                {
                    "topic_id": topic["id"],
                    "target_type": "POINT_SET",
                    "target_key": f"set:{map_id or group_key}:topic:{spec['topic_key']}",
                    "map_id": map_id or None,
                    "metadata_json": json.dumps(
                        {
                            "map_name": rows[0].get("map_name"),
                            "region": group_key,
                            "official_point_ids": official_ids,
                            "label": rows[0].get("label"),
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            count += 1
        return count
    for point in points:
        pid = str(point.get("source_point_id") or "")
        if not pid:
            continue
        db.upsert_target(
            {
                "topic_id": topic["id"],
                "target_type": "POINT",
                "target_key": f"point:{pid}",
                "map_id": point.get("map_id"),
                "source_point_id": pid,
                "metadata_json": json.dumps(
                    {
                        "map_name": point.get("map_name"),
                        "label": point.get("label"),
                        "x": point.get("x"),
                        "y": point.get("y"),
                    },
                    ensure_ascii=False,
                ),
            }
        )
        count += 1
    return count
