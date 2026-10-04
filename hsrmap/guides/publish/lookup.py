from __future__ import annotations

from typing import Any

from hsrmap.guides.topics.loader import list_topics
from hsrmap.guides.topics.official import official_points_for_topic


def resolve_guide_keys(ctx, source_point_id: str) -> list[str]:
    pid = str(source_point_id or "").strip()
    keys = [pid] if pid else []
    if ctx is None or not pid:
        return keys
    row = ctx.core.conn.execute(
        """
        SELECT m.source_id AS map_id, l.name AS label
        FROM points p
        JOIN maps m ON m.id = p.map_id
        LEFT JOIN point_labels pl ON pl.point_id = p.id
        LEFT JOIN label_nodes l ON l.id = pl.label_id
        WHERE p.source_id = ?
        """,
        (pid,),
    ).fetchall()
    if not row:
        return keys
    map_id = str(row[0]["map_id"] or "")
    labels = {str(item["label"]) for item in row if item["label"]}
    if not map_id:
        return keys
    for spec in list_topics(enabled_only=True):
        names = {str(name) for name in ((spec.get("official_labels") or {}).get("names") or []) if name}
        if spec.get("display_name"):
            names.add(str(spec["display_name"]))
        if not (names & labels):
            continue
        topic = spec["topic_key"]
        keys.append(f"map:{map_id}:topic:{topic}")
        keys.append(f"set:{map_id}:topic:{topic}")
        keys.append(f"global:topic:{topic}")
    return keys


def expand_guide_index(ctx, raw: dict[str, int]) -> dict[str, int]:
    out = dict(raw)
    if ctx is None:
        return out
    points_by_topic: dict[str, list[dict[str, Any]]] = {}
    for spec in list_topics(enabled_only=True):
        topic = spec["topic_key"]
        if any(
            key == f"global:topic:{topic}"
            or key.startswith(f"map:") and key.endswith(f":topic:{topic}")
            or key.startswith(f"set:") and key.endswith(f":topic:{topic}")
            for key in raw
        ):
            points_by_topic[topic] = official_points_for_topic(topic, ctx=ctx)
    for key, count in raw.items():
        parts = str(key).split(":")
        topic = ""
        map_id = ""
        global_topic = False
        if len(parts) == 4 and parts[0] in {"map", "set"} and parts[2] == "topic":
            map_id, topic = parts[1], parts[3]
        elif len(parts) == 3 and parts[0] == "global" and parts[1] == "topic":
            topic = parts[2]
            global_topic = True
        if not topic:
            continue
        for point in points_by_topic.get(topic) or []:
            if global_topic or str(point.get("map_id") or "") == map_id:
                pid = str(point.get("source_point_id") or "")
                if pid:
                    out[pid] = int(out.get(pid) or 0) + int(count)
    return out
