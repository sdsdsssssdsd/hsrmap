"""Search Evidence Ledger (a1-6 §八).

Every discovery attempt is recorded: which target was searched, with which
query, what came back, and why each candidate was accepted or dropped. Without
it `NO_PUBLIC_SOURCE_FOUND` is an opinion; with it, the claim "we looked and
there is nothing public" is auditable, and the next round can answer "why did
we stop searching for this target?"

```text
source_search_run     one query for one target
source_search_result  one candidate URL from that query, with its decision
```
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.crawler.identity import ArticleFamilyResolver

#: What happened to one search result (a1-6 §八).
DECISIONS = (
    "ACCEPTED",
    "DUPLICATE",
    "MIRROR",
    "IRRELEVANT",
    "JS_ONLY",
    "BLOCKED",
    "ALREADY_IMPORTED",
)

#: Decisions that mean "this URL is worth fetching".
USEFUL_DECISIONS = ("ACCEPTED",)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _host(url: str) -> str:
    return (urlparse(str(url or "")).netloc or "").lower()


def start_search(
    db: GuideDatabase,
    *,
    topic: str,
    target_key: str = "",
    query: str,
    provider: str = "",
) -> int:
    """Open a search run and return its id."""
    cursor = db.conn.execute(
        """
        INSERT INTO source_search_run(topic, target_key, query, provider, status, started_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (str(topic), str(target_key or ""), str(query), str(provider or ""), "RUNNING", _now()),
    )
    db.conn.commit()
    return int(cursor.lastrowid)


def finish_search(
    db: GuideDatabase,
    run_id: int,
    *,
    status: str = "OK",
    reason: str = "",
    result_count: int | None = None,
) -> dict[str, Any]:
    count = result_count
    if count is None:
        row = db.conn.execute(
            "SELECT COUNT(*) AS c FROM source_search_result WHERE run_id = ?", (run_id,)
        ).fetchone()
        count = int(row["c"]) if row is not None else 0
    db.conn.execute(
        "UPDATE source_search_run SET status = ?, reason = ?, result_count = ?, finished_at = ? WHERE id = ?",
        (str(status), str(reason or ""), int(count), _now(), run_id),
    )
    db.conn.commit()
    return {"run_id": run_id, "status": status, "result_count": int(count)}


def record_result(
    db: GuideDatabase,
    run_id: int,
    *,
    rank: int,
    url: str,
    decision: str,
    reason: str = "",
    topic: str = "",
    target_key: str = "",
    canonical_url: str = "",
    family: str = "",
    host: str = "",
    score: int = 0,
) -> int:
    """Store one candidate and the decision that was made about it."""
    decision = str(decision or "").upper()
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision: {decision!r} (expected one of {DECISIONS})")
    cursor = db.conn.execute(
        """
        INSERT INTO source_search_result(
            run_id, topic, target_key, query, rank, url, canonical_url, family, host,
            score, decision, reason, created_at
        ) VALUES (?, ?, ?, (SELECT query FROM source_search_run WHERE id = ?), ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(run_id),
            str(topic),
            str(target_key),
            int(run_id),
            int(rank),
            str(url),
            str(canonical_url or ""),
            str(family or ""),
            str(host or _host(url)),
            int(score),
            decision,
            str(reason or ""),
            _now(),
        ),
    )
    db.conn.commit()
    return int(cursor.lastrowid)


def record_search(
    db: GuideDatabase,
    *,
    topic: str,
    target_key: str = "",
    query: str,
    results: Iterable[dict[str, Any]],
    provider: str = "",
    status: str = "OK",
    reason: str = "",
) -> dict[str, Any]:
    """Record one search and all of its results in a single call."""
    run_id = start_search(db, topic=topic, target_key=target_key, query=query, provider=provider)
    stored = 0
    for index, item in enumerate(results):
        record_result(
            db,
            run_id,
            rank=int(item.get("rank", index + 1)),
            url=str(item.get("url") or ""),
            decision=str(item.get("decision") or "IRRELEVANT"),
            reason=str(item.get("reason") or ""),
            topic=topic,
            target_key=str(item.get("target_key") or target_key),
            canonical_url=str(item.get("canonical_url") or ""),
            family=str(item.get("family") or ""),
            host=str(item.get("host") or ""),
            score=int(item.get("score") or 0),
        )
        stored += 1
    return finish_search(db, run_id, status=status, reason=reason, result_count=stored)


def classify_candidate(
    url: str,
    *,
    db: GuideDatabase | None = None,
    resolver: ArticleFamilyResolver | None = None,
    known_families: Iterable[str] | None = None,
    accepted_families: Iterable[str] | None = None,
    blocked: bool = False,
    js_only: bool = False,
    relevant: bool = True,
) -> tuple[str, str]:
    """Decide what a candidate URL is, before it is ever fetched (a1-6 §八)."""
    engine = resolver or ArticleFamilyResolver()
    ref = engine.resolve(url)
    families = set(known_families or ())
    accepted = set(accepted_families or ())
    if db is not None and not families:
        families = {engine.family(str(row["canonical_url"])) for row in _known_pages(db)}
    if blocked:
        return "BLOCKED", "host or path answered with a block"
    if js_only:
        return "JS_ONLY", "content only exists after JS renders"
    if not relevant:
        return "IRRELEVANT", "no relation to the target"
    if ref.family in accepted:
        return "DUPLICATE", "same article already accepted from another result"
    if ref.family in families:
        if engine.mirror_host(ref.raw_host):
            return "MIRROR", f"mirror view of a known article ({ref.host})"
        return "ALREADY_IMPORTED", "same article family is already in the corpus"
    return "ACCEPTED", "new article for this target"


def _known_pages(db: GuideDatabase) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.conn.execute(
            "SELECT canonical_url FROM guide_page WHERE canonical_url IS NOT NULL AND canonical_url != ''"
        )
    ]


def searches_for(
    db: GuideDatabase,
    *,
    topic: str | None = None,
    target_key: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Recorded search runs, newest first, with their decision counts."""
    sql = [
        "SELECT r.*, (SELECT COUNT(*) FROM source_search_result s WHERE s.run_id = r.id) AS results",
        "FROM source_search_run r WHERE 1 = 1",
    ]
    params: list[Any] = []
    if topic:
        sql.append("AND r.topic = ?")
        params.append(str(topic))
    if target_key:
        sql.append("AND r.target_key = ?")
        params.append(str(target_key))
    sql.append("ORDER BY r.id DESC LIMIT ?")
    params.append(int(limit))
    rows = [dict(row) for row in db.conn.execute(" ".join(sql), tuple(params))]
    for row in rows:
        row["decisions"] = decision_counts(db, run_id=int(row["id"]))
    return rows


def decision_counts(
    db: GuideDatabase,
    *,
    topic: str | None = None,
    target_key: str | None = None,
    run_id: int | None = None,
) -> dict[str, int]:
    sql = ["SELECT decision, COUNT(*) AS c FROM source_search_result WHERE 1 = 1"]
    params: list[Any] = []
    if run_id is not None:
        sql.append("AND run_id = ?")
        params.append(int(run_id))
    if topic:
        sql.append("AND topic = ?")
        params.append(str(topic))
    if target_key:
        sql.append("AND target_key = ?")
        params.append(str(target_key))
    sql.append("GROUP BY decision")
    return {str(row["decision"]): int(row["c"]) for row in db.conn.execute(" ".join(sql), tuple(params))}


def searched_targets(
    db: GuideDatabase,
    *,
    topic: str | None = None,
    decisions: Iterable[str] = USEFUL_DECISIONS,
) -> dict[str, dict[str, Any]]:
    """Which targets have usable evidence, and which only produced rejects."""
    wanted = {str(item).upper() for item in decisions}
    sql = [
        "SELECT r.topic, r.target_key, r.id AS run_id, r.status, s.decision, s.url, s.created_at",
        "FROM source_search_run r LEFT JOIN source_search_result s ON s.run_id = r.id WHERE r.target_key != ''",
    ]
    params: list[Any] = []
    if topic:
        sql.append("AND r.topic = ?")
        params.append(str(topic))
    sql.append("ORDER BY r.id")
    out: dict[str, dict[str, Any]] = {}
    for row in db.conn.execute(" ".join(sql), tuple(params)):
        item = dict(row)
        entry = out.setdefault(
            str(item["target_key"]),
            {"topic": item["topic"], "target_key": item["target_key"], "runs": 0, "accepted": 0, "decisions": {}},
        )
        entry["runs"] += 1
        decision = str(item["decision"] or "")
        if decision:
            entry["decisions"][decision] = entry["decisions"].get(decision, 0) + 1
            if decision in wanted:
                entry["accepted"] += 1
        entry["last_url"] = item["url"] or entry.get("last_url", "")
        entry["last_seen"] = item["created_at"] or entry.get("last_seen", "")
    return out


def unsearched_targets(
    db: GuideDatabase,
    targets: Iterable[dict[str, Any]],
    *,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    """Targets that carry a gap but were never searched (a1-6 §八 last question)."""
    seen = searched_targets(db, topic=topic)
    out = []
    for target in targets:
        key = str(target.get("target_key") or target.get("source_point_id") or "")
        if not key or key in seen:
            continue
        out.append({**target, "target_key": key, "searched": False})
    return out


def summary(db: GuideDatabase, *, topic: str | None = None) -> dict[str, Any]:
    """Everything the ledger knows about search effort, for reports."""
    sql = ["SELECT COUNT(*) AS c, SUM(result_count) AS results FROM source_search_run WHERE 1 = 1"]
    params: list[Any] = []
    if topic:
        sql.append("AND topic = ?")
        params.append(str(topic))
    row = db.conn.execute(" ".join(sql), tuple(params)).fetchone()
    targets = searched_targets(db, topic=topic)
    return {
        "topic": topic or "all",
        "searches": int(row["c"] or 0) if row is not None else 0,
        "results": int(row["results"] or 0) if row is not None else 0,
        "targets_searched": len(targets),
        "targets_with_evidence": sum(1 for item in targets.values() if item["accepted"]),
        "decisions": decision_counts(db, topic=topic),
        "top_targets": sorted(targets.values(), key=lambda item: -item["accepted"])[:10],
    }
