from __future__ import annotations

from typing import Any


_CHROME = ("17173", "崩坏：星穹铁道", "崩坏星穹铁道", "关于崩坏", "更多相关")


def _usable(row: dict[str, Any]) -> bool:
    name = str(row.get("map_name") or "").strip()
    if not name or int(row.get("candidate_count") or 0) < 1:
        return False
    if name.startswith("《") or any(name.startswith(token) or token == name for token in _CHROME):
        return False
    if name.isdigit() or len(name) < 2:
        return False
    return True


def pick_ticker_canaries(items: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    if not items:
        return []
    cap = min(8, max(5, limit))
    usable = [row for row in items if _usable(row)]
    if not usable:
        usable = [row for row in items if int(row.get("candidate_count") or 0) >= 1]
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def take(row: dict[str, Any]) -> None:
        if row in picked or len(picked) >= cap:
            return
        picked.append(row)
        seen.add(str(row.get("map_name") or ""))

    multi = sorted(
        [row for row in usable if int(row.get("candidate_count") or 0) >= 4],
        key=lambda row: (-int(row.get("candidate_count") or 0), row.get("id")),
    )
    if multi:
        take(multi[0])
    low = sorted(usable, key=lambda row: (float(row.get("confidence") or 0), row.get("id") or 0))
    if low:
        take(low[0])
    for row in sorted(usable, key=lambda row: (str(row.get("map_name") or ""), row.get("id") or 0)):
        name = str(row.get("map_name") or "")
        if name and name not in seen:
            take(row)
        if len(picked) >= cap:
            break
    for row in usable:
        if len(picked) >= max(5, min(cap, 6)):
            break
        if str(row.get("map_name") or "") in seen:
            continue
        take(row)
    return picked[:cap]
