"""「查过了，公开来源确实没有」——证据账本给出的结论（a1-6 §八）。

证据账本的意义是让 `NO_PUBLIC_SOURCE_FOUND` 从「我觉得没有」变成「有记录地查过」。这个模块
把账本里的事实翻译成账本状态：

- 该目标至少有一次搜索记录（默认 1 次），
- 这些搜索**没有任何 ACCEPTED 候选**，
- 该目标没有已发布攻略，也没有还挂着的 review_item（NEEDS_REVIEW / AUTO_SUGGEST / APPROVED），
- 于是写一行 `source_search_verdict`，账本随即把目标记为 `NO_PUBLIC_SOURCE_FOUND`。

任何一条不满足就不标记（并说明原因）。这不是「把指标做好看」的开关：它只承认已经记在账本里
的搜索，理由与查询都写进 verdict，下一轮可以复核、也可以推翻（搜索到新来源后删掉 verdict 即可）。
"""

from __future__ import annotations

import json
from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.evidence import searched_targets

NO_PUBLIC_SOURCE_FOUND = "NO_PUBLIC_SOURCE_FOUND"

#: 还挂着的评审状态：有这些就说明「还没查完」，不能下结论。
PENDING_STATUSES = ("NEEDS_REVIEW", "AUTO_SUGGEST", "APPROVED")


def _published_point_ids(db: GuideDatabase) -> set[str]:
    return {
        str(row["source_point_id"])
        for row in db.conn.execute(
            "SELECT DISTINCT source_point_id FROM guide_entry"
            " WHERE IFNULL(status, '') = 'published' AND source_point_id IS NOT NULL AND source_point_id != ''"
        )
    }


def _pending_points(db: GuideDatabase, topic: str) -> set[str]:
    marks = ",".join("?" * len(PENDING_STATUSES))
    out: set[str] = set()
    for row in db.conn.execute(
        f"SELECT source_point_id, draft_json FROM review_item WHERE status IN ({marks})",
        PENDING_STATUSES,
    ):
        pid = str(row["source_point_id"] or "")
        if pid:
            out.add(pid)
        try:
            draft = json.loads(row["draft_json"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        if str(draft.get("topic_key") or "") != topic:
            continue
        for cand in draft.get("candidate_points") or []:
            if isinstance(cand, dict) and cand.get("source_point_id"):
                out.add(str(cand["source_point_id"]))
    return out


def _queries_for(db: GuideDatabase, topic: str, target_key: str, point_id: str = "") -> list[str]:
    """这个目标名下真正跑过的查询（证据账本里就有，不另外编）。"""
    keys = [item for item in (target_key, point_id) if item]
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    return [
        str(row["query"])
        for row in db.conn.execute(
            f"SELECT query FROM source_search_run WHERE topic = ? AND target_key IN ({marks})"
            " ORDER BY id DESC LIMIT 6",
            (topic, *keys),
        )
    ]


def mark_no_public_source(
    db: GuideDatabase,
    *,
    topic: str,
    min_runs: int = 1,
    apply: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """把「账本里查过、没有可用候选」的缺源目标记成 NO_PUBLIC_SOURCE_FOUND。"""
    key = str(topic).replace("-", "_")
    gaps = [
        dict(row)
        for row in db.conn.execute(
            "SELECT target_key, source_point_id, status FROM guide_target_status"
            " WHERE topic_key = ? AND status IN ('NEEDS_SOURCE', 'NO_PUBLIC_SOURCE_FOUND')"
            " ORDER BY target_key",
            (key,),
        )
    ]
    evidence = searched_targets(db, topic=key)
    published = _published_point_ids(db)
    pending = _pending_points(db, key)
    marked: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row in gaps:
        target_key = str(row.get("target_key") or "")
        pid = str(row.get("source_point_id") or "")
        seen = evidence.get(target_key) or (evidence.get(pid) if pid else None)
        runs = int((seen or {}).get("runs") or 0)
        accepted = int((seen or {}).get("accepted") or 0)
        reason = ""
        if runs < int(min_runs):
            reason = "NO_RECORDED_SEARCH"
        elif accepted:
            reason = "SEARCH_HAD_CANDIDATES"
        elif pid and pid in published:
            reason = "ALREADY_PUBLISHED"
        elif pid and pid in pending:
            reason = "REVIEW_PENDING"
        if reason:
            blocked.append({"target_key": target_key, "source_point_id": pid, "reason": reason, "runs": runs})
            continue
        marked.append({
            "target_key": target_key,
            "source_point_id": pid,
            "runs": runs,
            "queries": _queries_for(db, key, target_key, pid),
        })
    if limit:
        marked = marked[: int(limit)]
    if apply and marked:
        from hsrmap.guide_db import _now

        for item in marked:
            db.conn.execute(
                "INSERT INTO source_search_verdict(topic_key, target_key, source_point_id, status, reason,"
                " queries, runs, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(topic_key, target_key) DO UPDATE SET status=excluded.status,"
                " reason=excluded.reason, queries=excluded.queries, runs=excluded.runs,"
                " updated_at=excluded.updated_at",
                (
                    key,
                    item["target_key"],
                    item["source_point_id"] or None,
                    NO_PUBLIC_SOURCE_FOUND,
                    "账本里搜过 %d 次、没有可用候选" % item["runs"],
                    json.dumps(item["queries"][:6], ensure_ascii=False),
                    int(item["runs"]),
                    _now(),
                ),
            )
        db.conn.commit()
    return {
        "topic": key,
        "applied": bool(apply),
        "eligible": len(marked),
        "blocked": len(blocked),
        "marked": marked[:40],
        "blocked_reasons": _counts(blocked),
    }


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        name = str(row.get("reason") or "")
        out[name] = out.get(name, 0) + 1
    return out
