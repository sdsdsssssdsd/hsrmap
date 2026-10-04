from __future__ import annotations

import re
from typing import Any

from hsrmap.guides.matching.spatial import parse_floor_label


def _with_navigation(
    points: list[dict[str, Any]],
    navigation_conn: Any | None,
) -> list[dict[str, Any]]:
    """给候选点位补导航上下文（a1-8-1 §十九）。

    **只加字段**：`navigation_conn` 为 None（缺省）时原样返回**同一个列表对象**，
    命中/排序一个字节都不变；给了图库才多一个 `navigation_context` 字段，
    深层地图的攻略才可能被绑上（「千星城中心城区2层二次元界JUMP」）。
    """
    if navigation_conn is None:
        return points
    from hsrmap.graph_nav import attach_navigation_context

    return attach_navigation_context(points, navigation_conn)


def query_candidates(
    map_name: str | None,
    official_points: list[dict[str, Any]],
    *,
    semantic: str | None = "浮脂溯源",
    label_names: list[str] | None = None,
    navigation_conn: Any | None = None,
) -> list[dict[str, Any]]:
    if not map_name:
        return []
    names = [item for item in (list(label_names or []) + ([semantic] if semantic else [])) if item]
    hits = []
    for point in official_points:
        if not _region_hit(map_name, point):
            continue
        label = str(point.get("label") or point.get("name") or point.get("semantic_key") or "")
        if names and not any(token in label for token in names):
            continue
        hits.append(point)
    return _with_navigation(hits, navigation_conn)


def _region_hit(map_name: str, point: dict[str, Any]) -> bool:
    if str(point.get("map_name") or "") == map_name:
        return True
    if str(point.get("region") or "") == map_name:
        return True
    path = str(point.get("map_path") or "")
    if map_name and map_name in path:
        return True
    floor = parse_floor_label(map_name)
    if floor is None:
        return False
    blob = f"{point.get('map_name') or ''} {path}"
    return parse_floor_label(blob) == floor and ("层" in map_name or "区域" in map_name)

#: 中文数字折叠：官方写「-1层房间（永夜）」，页面写「负二层」「二层」。
_CN_DIGITS = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
_SPLIT = re.compile(r"[「」【】（）()·、,，/|]+")
_FLOOR = re.compile(r"(-?\d+)\s*层")
#: 昼夜/房间限定词：出现在文本里就必须出现在胜出点位的路径里，否则退回「不确定」。
_QUALIFIERS = ("永夜", "黎明", "宝库", "房间")


def _fold(text: str) -> str:
    body = str(text or "")
    for cn, digit in _CN_DIGITS.items():
        body = body.replace(cn, digit)
    return body.replace("负", "-").replace(" ", "")


def _floors(text: str) -> set[str]:
    return {match.group(1) for match in _FLOOR.finditer(_fold(text))}


def _path_tokens(point: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for blob in (str(point.get("region") or ""), str(point.get("map_name") or ""), str(point.get("map_path") or "")):
        for part in _SPLIT.split(_fold(blob)):
            part = part.strip()
            if len(part) >= 2 and not _FLOOR.fullmatch(part):
                tokens.append(part)
    return list(dict.fromkeys(tokens))


def query_candidates_by_text(
    text: str | None,
    official_points: list[dict[str, Any]],
    *,
    label_names: list[str] | None = None,
    min_tokens: int = 2,
    navigation_conn: Any | None = None,
) -> list[dict[str, Any]]:
    """单元正文自己说清了「哪个区域、哪一层」时，找出唯一符合的点位。

    只认官方地图路径里的词（区域名、区域名里的括号词、楼层、房间后缀），并且要求：

    - 至少命中 min_tokens 个词（只命中区域名不算，那可能是一整片区域）；
    - 文本里写了楼层时，点位的楼层必须完全一致（「-2层」不匹配「2层」）；
    - 最高分**严格**高于第二名，且文本里的「永夜/黎明/宝库/房间」这类限定词必须出现在
      胜出点位的路径里——否则返回空。宁可少发一条，也不猜错点位（双子区域踩过这个坑）。
    """
    body = _fold(text)
    if not body:
        return []
    names = [item for item in (label_names or []) if item]
    wanted_floors = _floors(body)
    scored: list[tuple[int, dict[str, Any]]] = []
    for point in official_points:
        label = str(point.get("label") or point.get("name") or point.get("semantic_key") or "")
        if names and not any(token in label for token in names):
            continue
        tokens = _path_tokens(point)
        if not tokens:
            continue
        point_floors = _floors(" ".join([str(point.get("map_name") or ""), str(point.get("map_path") or "")]))
        floor_hit = bool(wanted_floors and point_floors and (wanted_floors & point_floors))
        if wanted_floors and point_floors and not floor_hit:
            continue
        #: 楼层也算一个词：页面写「穹顶关塞二层」时，「穹顶关塞」+「2层」= 两个证据。
        hits = sum(1 for token in tokens if token in body) + (1 if floor_hit else 0)
        if hits < min_tokens:
            continue
        scored.append((hits, point))
    if not scored:
        return []
    scored.sort(key=lambda row: row[0], reverse=True)
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if best_score <= runner_up:
        return []
    best_blob = _fold(" ".join([str(best.get("region") or ""), str(best.get("map_name") or ""), str(best.get("map_path") or "")]))
    for qualifier in _QUALIFIERS:
        if qualifier in body and qualifier not in best_blob:
            return []
    return _with_navigation([best], navigation_conn)
