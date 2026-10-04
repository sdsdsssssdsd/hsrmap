"""正规关系表 vs 字符串键：补齐、shadow 对比、切换（a1-8 十二）。

「这条攻略是哪个点位的」现在完全靠 `source_point_id` 字符串判：

```text
完全相等
set: 键里以完整 id 出现
```

库里已经有 `guide_target` / `guide_entry_target` 这张正规关系表，但只覆盖了一部分条目，
所以不能直接切过去。迁移策略照规格：

```text
字符串 lookup + 关系 lookup → shadow 对比 → 完全一致才切
```

本模块给四件事：

* `key_members()`——一个键点名了哪些点位（规则与判定层逐字相同）；
* `sync_relations()`——按同一套规则把关系补齐（幂等，可干跑）；
* `relation_ids()` / `relations_in_sync()`——关系 lookup 与「关系齐不齐」；
* `shadow_compare()`——逐点位对比两条路，报差异样本。

**绝不在关系不全时静默用关系表**：`EntryIndex(lookup="auto")` 先问 `relations_in_sync()`，
齐全才走关系表，否则走字符串（并在报告里写明走的是哪条）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from hsrmap.guide_db import GuideDatabase

#: `set:` 键就是点位列表；`map:`/`global:` 键**不算**点位的证据（判定层只认完全相等与 set: 成员）。
_MEMBER = re.compile(r"\d+")
_TOPIC_SUFFIX = re.compile(r":topic:([A-Za-z0-9_]+)$")

#: 键里没有主题时，关系落在一个中性主题下（关系表只是用来回答「哪个点位」，主题是元数据）。
FALLBACK_TOPIC = "point_index"


def key_members(key: str) -> list[str]:
    """一个 source_point_id 键点名了哪些点位。"""
    text = str(key or "")
    if not text:
        return []
    if text.startswith("set:"):
        return sorted(set(_MEMBER.findall(text)), key=lambda item: (len(item), item))
    if text.startswith(("map:", "global:")):
        return []
    return [text]


def key_topic(key: str) -> str:
    match = _TOPIC_SUFFIX.search(str(key or ""))
    return match.group(1) if match else FALLBACK_TOPIC


def published_keys(db: GuideDatabase) -> list[dict[str, Any]]:
    return [
        {"id": int(row["id"]), "key": str(row["source_point_id"] or "")}
        for row in db.conn.execute(
            "SELECT id, IFNULL(source_point_id, '') AS source_point_id FROM guide_entry"
            " WHERE IFNULL(status, '') = 'published' ORDER BY id"
        )
    ]


def _target_rows(db: GuideDatabase) -> dict[str, dict[str, Any]]:
    return {
        str(row["target_key"]): dict(row)
        for row in db.conn.execute("SELECT * FROM guide_target")
    }


def _bindings(db: GuideDatabase) -> set[tuple[int, str]]:
    """已有绑定：(guide_id, 点位 id)。"""
    return {
        (int(row["guide_id"]), str(row["source_point_id"]))
        for row in db.conn.execute(
            "SELECT et.guide_id AS guide_id, t.source_point_id AS source_point_id"
            " FROM guide_entry_target et JOIN guide_target t ON t.id = et.target_id"
            " WHERE IFNULL(t.source_point_id, '') <> ''"
        )
    }


def sync_relations(db: GuideDatabase, *, apply: bool = False) -> dict[str, Any]:
    """把已发布条目的每一个成员点位绑进关系表（幂等）。

    干跑先报「会补多少个点位目标、多少条绑定、要修几行空的 source_point_id」；
    `apply=True` 才写库。目标键沿用 `point:<pid>`，和 `migrate_point_entries_to_targets` 一致。
    """
    keys = published_keys(db)
    targets = _target_rows(db)
    bindings = _bindings(db)
    expected: dict[str, set[int]] = {}
    for entry in keys:
        for member in key_members(entry["key"]):
            expected.setdefault(member, set()).add(int(entry["id"]))
    created: list[str] = []
    patched: list[str] = []
    bound: list[tuple[int, str]] = []
    fallback_topic_id: int | None = None
    for member, guide_ids in sorted(expected.items(), key=lambda item: (len(item[0]), item[0])):
        target_key = f"point:{member}"
        row = targets.get(target_key)
        if row is None:
            created.append(target_key)
            if apply:
                if fallback_topic_id is None:
                    #: 中性主题只建一次：每个点位目标都 upsert_topic 一次会白写几百行。
                    fallback_topic_id = int(db.upsert_topic({
                        "topic_key": FALLBACK_TOPIC,
                        "display_name": FALLBACK_TOPIC,
                        "scope_type": "POINT",
                    })["id"])
                row = db.upsert_target({
                    "topic_id": fallback_topic_id,
                    "target_type": "POINT",
                    "target_key": target_key,
                    "source_point_id": member,
                })
                targets[target_key] = row
        elif not str(row.get("source_point_id") or ""):
            patched.append(target_key)
            if apply:
                db.conn.execute(
                    "UPDATE guide_target SET source_point_id = ? WHERE id = ?",
                    (member, int(row["id"])),
                )
                db.conn.commit()
                row = {**row, "source_point_id": member}
                targets[target_key] = row
        target_id = int(row["id"]) if row is not None else None
        if apply and row is not None:
            target_id = int(row["id"])
        for guide_id in sorted(guide_ids):
            if (guide_id, member) in bindings:
                continue
            #: 干跑也要把「将要新增的绑定」数清楚，否则报出来的计划数会漏掉新目标那一批。
            bound.append((guide_id, member))
            if apply and target_id is not None:
                db.bind_entry_target(guide_id, target_id, role="point")
    return {
        "apply": bool(apply),
        "entries": len(keys),
        "members": len(expected),
        "bindings_expected": sum(len(ids) for ids in expected.values()),
        "bindings_existing": len(bindings),
        "targets_created": len(created),
        "targets_patched": len(patched),
        "bindings_added": len(bound),
        "sample_targets": created[:8],
        "sample_bindings": [
            {"guide_id": guide_id, "point": member} for guide_id, member in bound[:8]
        ],
    }


def relation_ids(db: GuideDatabase, point_id: str) -> list[int]:
    """走关系表的等价查询：这个点位名下的已发布条目，新的在前。"""
    return [
        int(row["id"])
        for row in db.conn.execute(
            "SELECT e.id AS id FROM guide_entry e"
            " JOIN guide_entry_target et ON et.guide_id = e.id"
            " JOIN guide_target t ON t.id = et.target_id"
            " WHERE IFNULL(e.status, '') = 'published' AND t.source_point_id = ?"
            " ORDER BY e.id DESC",
            (str(point_id or ""),),
        )
    ]


def relation_index(db: GuideDatabase) -> dict[str, list[int]]:
    """一次取回全部关系（点位 -> 条目 id，新的在前）。"""
    grouped: dict[str, list[int]] = {}
    for row in db.conn.execute(
        "SELECT t.source_point_id AS point, e.id AS id FROM guide_entry e"
        " JOIN guide_entry_target et ON et.guide_id = e.id"
        " JOIN guide_target t ON t.id = et.target_id"
        " WHERE IFNULL(e.status, '') = 'published' AND IFNULL(t.source_point_id, '') <> ''"
        " ORDER BY e.id DESC"
    ):
        grouped.setdefault(str(row["point"]), []).append(int(row["id"]))
    return grouped


def relations_in_sync(db: GuideDatabase) -> dict[str, Any]:
    """关系表是否覆盖了每一条已发布条目的每一个成员点位。

    这是「能不能切」的判据：缺一条就说明关系表还没有资格回答点位问题，
    此时用它会**静默漏攻略**（完成度虚低），所以宁可不切。
    """
    keys = published_keys(db)
    expected = {
        (int(entry["id"]), member)
        for entry in keys
        for member in key_members(entry["key"])
    }
    bound = _bindings(db)
    missing = sorted(expected - bound, key=lambda item: (item[0], len(item[1]), item[1]))
    return {
        "entries": len(keys),
        "expected_bindings": len(expected),
        "bound_bindings": len(expected & bound),
        "extra_bindings": len(bound - expected),
        "missing": len(missing),
        "in_sync": not missing,
        "sample_missing": [{"guide_id": g, "point": p} for g, p in missing[:8]],
    }


def shadow_compare(db: GuideDatabase, point_ids: Iterable[str]) -> dict[str, Any]:
    """逐点位对比「字符串键」与「关系表」两条路的结果（a1-8 十二）。

    只有 `mismatches == 0` 才允许切换；差异会连样本一起报出来，便于判断
    是关系表缺绑定、还是多绑了不该绑的点位。
    """
    from hsrmap.guides.stages import EntryIndex

    index = EntryIndex(db, lookup="string")
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for point_id in point_ids:
        point = str(point_id or "")
        if not point:
            continue
        checked += 1
        want = [int(row["id"]) for row in index.matching(point)]
        got = relation_ids(db, point)
        if want != got:
            mismatches.append({
                "point": point,
                "string_only": [item for item in want if item not in got][:5],
                "relation_only": [item for item in got if item not in want][:5],
                "string": want[:5],
                "relation": got[:5],
            })
    return {
        "points": checked,
        "matches": checked - len(mismatches),
        "mismatches": len(mismatches),
        "equal": not mismatches,
        "sample": mismatches[:10],
    }
