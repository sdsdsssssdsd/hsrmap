"""Topic Admin: the metrics behind the admin page (a1-6 §27).

The document keeps the dashboard itself for later and asks for the numbers
first. Every topic answers the same question — what should happen next — and
that answer is *derived* from the topic state, never written as a fixed string.

```text
source shortage        -> DISCOVER_SOURCE
QA failures dominant   -> FIX_SOURCE_PIPELINE
review backlog         -> REVIEW
approved unpublished   -> PUBLISH
coverage regression    -> INVESTIGATE_REGRESSION
```
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

#: Actions, in the order they win when several conditions hold at once.
NEXT_ACTIONS = (
    "INVESTIGATE_REGRESSION",
    "FIX_SOURCE_PIPELINE",
    "DISCOVER_SOURCE",
    "REVIEW",
    "PUBLISH",
    "HOLD",
)


def derive_next_action(state: dict[str, Any]) -> str:
    """Pick the next action from the topic state (a1-6 §27)."""
    targets = int(state.get("official_targets") or 0)
    published = int(state.get("published") or 0)
    needs_source = int(state.get("needs_source") or 0)
    needs_review = int(state.get("needs_review") or 0)
    approved = int(state.get("approved") or 0)
    matched = int(state.get("matched") or 0)
    qa_fail = int(state.get("qa_fail") or 0)
    qa_pass = int(state.get("qa_pass") or 0)
    regression = bool(state.get("coverage_regression"))
    if regression:
        return "INVESTIGATE_REGRESSION"
    checked = qa_pass + qa_fail
    if checked and qa_fail / checked >= 0.5:
        return "FIX_SOURCE_PIPELINE"
    # a source shortage means missing sources are the *largest* bucket, not just
    # present: a topic with 9 of 10 published and one unmatched target is not a
    # discovery problem
    if needs_source and needs_source >= max(published, needs_review, approved):
        return "DISCOVER_SOURCE"
    if needs_review > approved:
        return "REVIEW"
    if approved > published or matched > published:
        return "PUBLISH"
    return "HOLD"


def _topic_state(
    working: Any,
    published: Any,
    topic: str,
    *,
    ctx: Any = None,
    regression: bool = False,
) -> dict[str, Any]:
    from hsrmap.guides.ledger import topic_ledger
    from hsrmap.guides.planner import host_health
    from hsrmap.guides.topics.official import official_points_for_topic

    try:
        points = list(official_points_for_topic(topic, ctx=ctx) or [])
    except Exception:
        points = []
    ledger = topic_ledger(working, topic, official_points=points, published_db=published)
    health = host_health(working)
    qa_pass = sum(int(item.get("qa_pass") or 0) for item in health.values())
    qa_fail = sum(int(item.get("qa_fail") or 0) for item in health.values())
    review = _review_stats(working)
    state = {
        "topic": topic,
        "official_targets": ledger.get("official_targets", 0),
        "published": ledger.get("published", 0),
        "matched": ledger.get("matched", 0),
        "approved": ledger.get("approved", 0),
        "needs_review": ledger.get("needs_review", 0),
        "needs_source": ledger.get("no_source_yet", 0),
        "no_public_source": int((ledger.get("counts") or {}).get("NO_PUBLIC_SOURCE_FOUND") or 0),
        "rejected": int((ledger.get("counts") or {}).get("SOURCE_REJECTED") or 0),
        "engine": ledger.get("engine"),
        "qa_pass": qa_pass,
        "qa_fail": qa_fail,
        "qa_pass_rate": round(qa_pass / (qa_pass + qa_fail), 3) if (qa_pass + qa_fail) else None,
        "review_duplicate_ratio": review["duplicate_ratio"],
        "coverage_regression": regression,
    }
    state["next_action"] = derive_next_action(state)
    return state


def _review_stats(working: Any) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in working.conn.execute("SELECT status, COUNT(*) AS c FROM review_item GROUP BY status")
    ]
    counts = {str(row["status"]): int(row["c"]) for row in rows}
    total = sum(counts.values())
    merged = counts.get("MERGED", 0)
    return {
        "counts": counts,
        "total": total,
        "duplicate_ratio": round(merged / total, 4) if total else 0.0,
    }


def topic_admin_rows(
    working: Any,
    published: Any,
    *,
    ctx: Any = None,
    topics: Iterable[str] | None = None,
    regressions: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """One row per enabled topic, with the derived next action."""
    if topics is None:
        from hsrmap.guides.topics.loader import list_topics

        topics = [str(item["topic_key"]) for item in list_topics(enabled_only=True)]
    flagged = {str(item) for item in regressions}
    return [
        _topic_state(working, published, str(topic), ctx=ctx, regression=str(topic) in flagged)
        for topic in topics
    ]


def review_admin(working: Any) -> dict[str, Any]:
    """Review queue shape, including the duplicate ratio (§27)."""
    stats = _review_stats(working)
    pending = [
        dict(row)
        for row in working.conn.execute(
            "SELECT id, page_id, status, source_point_id, created_at FROM review_item"
            " WHERE status IN ('NEEDS_REVIEW','AUTO_SUGGEST') ORDER BY id DESC LIMIT 50"
        )
    ]
    return {**stats, "pending_sample": pending}


def sources_admin(working: Any) -> dict[str, Any]:
    """Per-host yield and the search evidence behind it (§十八)."""
    from hsrmap.guides.evidence import summary as evidence_summary
    from hsrmap.guides.planner import source_yield

    return {"hosts": source_yield(working), "evidence": evidence_summary(working)}


def snapshots_admin(reports_dir: str | Path | None = None) -> dict[str, Any]:
    """What the last publish/audit/closure runs recorded (§27)."""
    root = Path(reports_dir) if reports_dir is not None else None
    out: dict[str, Any] = {"reports": {}}
    if root is None or not root.is_dir():
        return out
    for name in (
        "closure.json",
        "publish-diff.json",
        "snapshot-manifest.json",
        "published-audit.json",
        "quarantine.json",
        "ai-cache.json",
    ):
        path = root / name
        if not path.is_file():
            continue
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out["reports"][name] = {
            "generated_at": body.get("generated_at") or body.get("created_at"),
            "result": (body.get("gate") or {}).get("result") or body.get("result"),
            "keys": sorted(body)[:8],
        }
    return out
