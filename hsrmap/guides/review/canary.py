from __future__ import annotations

from typing import Any

# C12: 海原市3 + 海原电视塔3 + 其它4；两家以上来源；含转载 cluster（17173 vs 3DM）。
# 无官方图的浮脂在千星城，本批攻略未覆盖，不硬塞。
CANARY_POINT_IDS = (
    "5171",
    "5170",
    "5169",
    "5196",
    "5212",
    "5215",
    "4620",
    "4698",
    "5016",
    "5064",
)


def flatten_topic_points(topic: dict[str, Any], label: str = "浮脂溯源") -> list[dict[str, Any]]:
    origin = (topic or {}).get("origin") or topic or {}
    display = label or origin.get("name") or "浮脂溯源"
    out: list[dict[str, Any]] = []
    for amap in origin.get("maps") or []:
        path = str(amap.get("path") or "")
        parts = [part.strip() for part in path.split("/") if part.strip()]
        region = parts[1] if len(parts) >= 2 else (amap.get("name") or "")
        for point in amap.get("points") or []:
            source_id = str(point.get("source_id") or point.get("source_point_id") or "")
            if not source_id:
                continue
            out.append(
                {
                    "source_point_id": source_id,
                    "map_id": str(amap.get("map_id") or ""),
                    "map_name": amap.get("name") or "",
                    "map_path": path,
                    "region": region,
                    "label": point.get("label") or display,
                    "x": point.get("x"),
                    "y": point.get("y"),
                    "canary": source_id in CANARY_POINT_IDS,
                }
            )
    return out


def flatten_grease_points(topic: dict[str, Any]) -> list[dict[str, Any]]:
    return flatten_topic_points(topic, label="浮脂溯源")
