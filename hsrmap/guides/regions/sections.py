"""把一篇攻略切成「一个区域一段」——标题、小标题、正文里的区域名都算边界。

页面的写法不只一种，这一层要认三种边界：

* `heading` 块直接就是区域名（游民星空那种 `<h2>` 小标题）；
* `heading` 块是**序号**（「第3个」）——它是这一区的第几个谜题，不能当新区域；
* 区域名只出现在**正文段落**里（九游/游侠把「永恒圣城奥赫玛」写成 `<p>`）：只要那一行短、
  没有句子标点、并且命中**唯一**一个官方区域专名，它就是边界。

官方区域名由调用方给（`region_aliases`）：区域名、地图名、剥掉世界名后的名字都算专名；
几个区域共享的尾巴（「无名泰坦大墓」同时属于「全世矩阵」和「灾梦余温」）会因为命中不唯一而**不切**，
宁可让那一页整体留着，也不把甲区的解法挂到乙区头上。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from hsrmap.guides.signature import normalize_text
from hsrmap.guides.vision.roles import WASTE

#: 一行「报区域名」的正文最长多少（归一化后）：超过这个长度就是句子，不是地名行。
_REGION_LINE_MAX = 18
#: 有句子标点的一律当正文：地名行不会写成「永恒圣城奥赫玛，共四个」。
_SENTENCE_PUNCT = re.compile(r"[。！？；;!?]")


def region_aliases(points: Iterable[dict[str, Any]] | None) -> dict[str, str]:
    """{页面可能写的专名: 官方区域名}——专名从官方点位自己推。

    一个点位能提供三种叫法：官方 `region`、它所在的 `map_name`（页面常只写地图名），
    以及**剥掉世界名**后的名字（官方「匹诺康尼折纸大学学院」，攻略只写「折纸大学学院」）。
    同一串字如果对应两个区域（共享的尾巴），就不收——切错了比不切更糟。
    """
    seen: dict[str, set[str]] = {}
    for point in points or ():
        region = str(point.get("region") or "").strip()
        if not region:
            continue
        names = [region]
        map_name = str(point.get("map_name") or "").strip()
        if map_name and map_name != region and len(normalize_text(map_name)) >= 3:
            names.append(map_name)
        root = str(point.get("map_path") or "").split(" / ")[0].strip()
        if root and region.startswith(root):
            shortened = region[len(root):].lstrip("-·_ ")
            if len(normalize_text(shortened)) >= 5:
                names.append(shortened)
        for name in names:
            for variant in _name_variants(name):
                key = normalize_text(variant)
                if len(key) >= 3:
                    seen.setdefault(key, set()).add(region)
    return {key: next(iter(regions)) for key, regions in seen.items() if len(regions) == 1}


def _name_variants(name: str) -> list[str]:
    """一个区域名的几种写法：整名、括号里的头、括号后的尾巴。"""
    text = str(name or "")
    out = [text]
    if "」" in text:
        out.append(text.split("」")[-1])
    if "「" in text and "」" in text:
        out.append(text.split("「", 1)[1].split("」", 1)[0])
    return [item.strip() for item in out if item and item.strip()]


def build_region_sections(
    blocks: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    *,
    title: str = "",
    regions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """按区域切片；`regions` 是 `region_aliases` 的结果（没有就不切正文里的地名行）。"""
    aliases = dict(regions or {})
    obs_by_id = {str(item.get("block_id")): item for item in observations}
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_anchor: dict[str, Any] | None = None

    def close() -> None:
        nonlocal current
        if current and current.get("map_name"):
            sections.append(current)
        current = None

    def start(name: str, source: str, block_id: Any = None, seed: str = "") -> None:
        nonlocal current, last_anchor
        current = {"map_name": name, "source": source, "block_ids": [], "observations": []}
        if block_id is not None:
            current["block_ids"].append(block_id)
        if seed:
            #: 正文里的那一行本身就是这一节的开头（九游「永恒圣城奥赫玛」、
            #: 「永恒圣城奥赫玛：4个」这种计数行），丢掉它等于丢掉页面自己的数量声明。
            current["texts"] = [seed]
        last_anchor = {"map_name": name}

    for block in blocks:
        kind = block.get("type")
        text = block.get("text") or ""
        if kind == "heading":
            named = _names_region(text, aliases)
            if named:
                close()
                start(named, "text", block.get("id"), seed=text.strip())
                continue
            if "版本" in text and "海原" not in text and "电视塔" not in text:
                continue
            if current and _ordinal_heading(text):
                current.setdefault("texts", []).append(text)
                current.setdefault("block_ids", []).append(block.get("id"))
                continue
            name = _heading_map(text)
            if name:
                close()
                start(name, "heading", block.get("id"))
            continue
        if kind == "separator":
            last_anchor = None
            close()
            continue
        if kind == "image":
            obs = obs_by_id.get(str(block.get("id"))) or {}
            if obs.get("role") in WASTE:
                continue
            resolved = (obs.get("resolved_map") or {})
            name = resolved.get("map_name") or obs.get("map_name_raw")
            if name:
                if not current or current.get("map_name") != name:
                    close()
                    start(name, "hud")
                current["block_ids"].append(block.get("id"))
                current["observations"].append(obs)
                last_anchor = {"map_name": name, "block_id": block.get("id")}
            elif current and last_anchor:
                inherited = dict(obs)
                inherited["map_name_raw"] = last_anchor["map_name"]
                inherited["provenance"] = "propagated_from_previous_region_anchor"
                current["block_ids"].append(block.get("id"))
                current["observations"].append(inherited)
            continue
        #: 正文里的地名行也是边界（九游/游侠的写法）；不是边界就当正文。
        named = _names_region(text, aliases)
        if named:
            close()
            start(named, "text", block.get("id"), seed=text.strip())
            continue
        if current is None and title:
            #: 正文出现在任何小标题之前时不能丢（九游的标题带「3.0版本」，会被版本守卫跳过）
            start(title, "title")
        if current:
            current.setdefault("texts", []).append(text)
    close()
    return sections


def _names_region(text: str, aliases: dict[str, str]) -> str:
    """这一行是不是在报区域名：短、没有句子标点、并且只命中一个区域的专名。"""
    if not aliases:
        return ""
    body = str(text or "").strip()
    if not body or _SENTENCE_PUNCT.search(body):
        return ""
    normalized = normalize_text(body)
    if not normalized or len(normalized) > _REGION_LINE_MAX:
        return ""
    hits = {region for alias, region in aliases.items() if alias and alias in normalized}
    return hits.pop() if len(hits) == 1 else ""


_ORDINAL_HEADING = re.compile(r"^(第\s*\d+\s*个|点位\s*\d+)")


def _ordinal_heading(text: str) -> bool:
    return bool(_ORDINAL_HEADING.search(str(text or "").strip()))


def _heading_map(text: str) -> str | None:
    for name in ("海原电视塔", "海原市"):
        if name in text:
            return name
    if "版本" in text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"[（(]\s*\d+\s*个\s*[)）]\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\s*\d+个\s*$", "", cleaned).strip()
    return cleaned or None
