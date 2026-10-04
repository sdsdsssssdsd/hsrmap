from __future__ import annotations

import json
from collections import Counter
from typing import Any

from hsrmap.guide_db import GuideDatabase

STATUSES = (
    "PUBLISHED",
    "APPROVED",
    "MATCHED",
    "NEEDS_REVIEW",
    "EXTRACTED",
    "SOURCE_FOUND",
    "SOURCE_REJECTED",
    "AMBIGUOUS",
    "NO_PUBLIC_SOURCE_FOUND",
    "NEEDS_SOURCE",
    "NOT_REQUIRED",
    "NO_OFFICIAL_TARGET",
)

_RANK = {name: index for index, name in enumerate(STATUSES)}


class WaveLocked(RuntimeError):
    pass


def assert_wave_unlocked(gates: dict[str, Any], force: bool = False) -> None:
    if force:
        return
    if str((gates or {}).get("result") or "") != "PASS":
        raise WaveLocked("Atlas engine gates are not complete.")


def topic_ledger(
    db: GuideDatabase,
    topic_key: str,
    *,
    official_points: list[dict[str, Any]] | None = None,
    published_db: GuideDatabase | None = None,
) -> dict[str, Any]:
    key = topic_key.replace("-", "_")
    points = list(official_points or [])
    if not points:
        return {
            "topic": key,
            "official_targets": 0,
            "topic_status": "NO_OFFICIAL_TARGET",
            "engine": "REGISTERED",
            "corpus_label": "0 / 0",
            "counts": {"NO_OFFICIAL_TARGET": 1},
            "targets": [],
        }
    published_ids = _published_ids(published_db or db)
    mentions = _topic_mentions(db, key)
    spec: dict[str, Any] = {}
    try:
        from hsrmap.guides.topics.loader import get_topic

        spec = get_topic(key)
    except KeyError:
        spec = {}
    targets = []
    if spec.get("scope") == "MAP_LABEL":
        by_map: dict[str, list[str]] = {}
        for point in points:
            mid = str(point.get("map_id") or "")
            if not mid:
                continue
            by_map.setdefault(mid, []).append(str(point.get("source_point_id") or ""))
        for mid, pids in sorted(by_map.items()):
            tkey = f"map:{mid}:topic:{key}"
            extra = {tkey: mentions.get(tkey) or []}
            for pid in pids:
                extra[tkey] = (extra[tkey] + (mentions.get(pid) or []))
            status = _status_for(tkey, published_ids, extra)
            if status != "PUBLISHED" and pids and all(pid in published_ids for pid in pids):
                #: every point of this map has its own published guide, so the map is
                #: covered even though no single entry names the map key (a page that
                #: covers the area yields one set per region, not one per map)
                status = "PUBLISHED"
            targets.append(
                {
                    "target_key": tkey,
                    "source_point_id": tkey,
                    "map_id": mid,
                    "status": status,
                }
            )
    else:
        for point in points:
            pid = str(point.get("source_point_id") or "")
            if not pid:
                continue
            status = _status_for(pid, published_ids, mentions)
            targets.append(
                {
                    "target_key": f"point:{pid}",
                    "source_point_id": pid,
                    "map_id": point.get("map_id"),
                    "status": status,
                }
            )
    counts = Counter(row["status"] for row in targets)
    official_n = len(targets)
    published_n = max(int(counts.get("PUBLISHED") or 0), _scope_published(published_db or db, key))
    source_n = official_n - int(counts.get("NEEDS_SOURCE") or 0) - int(counts.get("NO_PUBLIC_SOURCE_FOUND") or 0)
    return {
        "topic": key,
        "official_targets": official_n,
        "source_found": source_n,
        "extracted": source_n,
        "matched": int(counts.get("MATCHED") or 0) + published_n + int(counts.get("APPROVED") or 0),
        "needs_review": int(counts.get("NEEDS_REVIEW") or 0),
        "approved": int(counts.get("APPROVED") or 0) + published_n,
        "published": published_n,
        "no_source_yet": int(counts.get("NEEDS_SOURCE") or 0),
        "topic_status": "HAS_OFFICIAL_TARGETS",
        "engine": "NOT PASSED" if published_n < official_n else "PASS",
        "corpus_label": f"{published_n} / {official_n} published",
        "counts": dict(counts),
        "targets": targets,
    }


def build_gates(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    grease = summaries.get("floating_grease") or {}
    ticker = summaries.get("dream_ticker") or {}
    goat = summaries.get("golden_scapegoat") or {}
    bird = summaries.get("origami_bird") or {}
    nymph = summaries.get("nymph") or {}
    jump = summaries.get("jump") or {}
    zag = summaries.get("zagreus_hand") or {}

    grease_state = "PASS" if int(grease.get("published") or 0) >= 20 else "BLOCKED"
    ticker_state = "PASS" if int(ticker.get("published") or 0) >= 6 else "CANARY"
    goat_state = "PASS" if int(goat.get("published") or 0) >= 3 else "REVIEW"
    a_result = "PASS" if {grease_state, ticker_state, goat_state} == {"PASS"} else "BLOCKED"

    bird_state = "PASS" if int(bird.get("published") or 0) >= 2 else "REVIEW"
    nymph_state = "PASS" if int(nymph.get("published") or 0) >= 1 else "REVIEW"
    b_result = "PASS" if bird_state == "PASS" and nymph_state == "PASS" else "BLOCKED"

    point_state = "PASS" if grease_state == "PASS" else "BLOCKED"
    label_state = "PASS" if bird_state == "PASS" else "BLOCKED BY B"
    set_state = "PASS" if int(zag.get("published") or 0) >= 1 else "MISSING"
    challenge_state = "PASS" if int(jump.get("published") or 0) >= 1 else "REVIEW"
    c_result = "PASS" if {point_state, label_state, set_state, challenge_state} == {"PASS"} else "BLOCKED"

    result = "PASS" if {a_result, b_result, c_result} == {"PASS"} else "BLOCKED"
    return {
        "result": result,
        "wave": "LOCKED" if result != "PASS" else "OPEN",
        "A": {
            "name": "Puzzle",
            "result": a_result,
            "topics": [
                _gate_topic("floating_grease", "Floating Grease", grease_state, grease, "Keep 37 published; mark 11 NEEDS_SOURCE"),
                _gate_topic("dream_ticker", "Dream Ticker", ticker_state, ticker, "Resolve canary 72/81/117; publish 6"),
                _gate_topic("golden_scapegoat", "Golden Scapegoat", goat_state, goat, "Verify 6 units; publish 3"),
            ],
        },
        "B": {
            "name": "Collectible",
            "result": b_result,
            "topics": [
                _gate_topic("origami_bird", "Origami Bird", bird_state, bird, "Select 2 maps; publish MAP_LABEL routes"),
                _gate_topic("nymph", "Nymph", nymph_state, nymph, "Publish 1 map route"),
            ],
        },
        "C": {
            "name": "Scope",
            "result": c_result,
            "topics": [
                {"topic": "POINT", "display": "POINT", "engine": point_state, "next": "Proven by floating grease"},
                {"topic": "MAP_LABEL", "display": "MAP_LABEL", "engine": label_state, "next": "Blocked until Gate B"},
                _gate_topic("zagreus_hand", "POINT_SET", set_state, zag, "Publish 1 real multi-point guide"),
                #: 2026-10：JUMP / 奇迹宝珠 / 扎格列斯之手 / 开拓妖精大挑战都归入第 1 阶段
                #: （到了位置就行，之后由玩家自己操作），所以这条闸门只检查「这些主题至少发过一条」，
                #: 不再要求「发一条解法攻略」。
                _gate_topic("jump", "CHALLENGE", challenge_state, jump, "Keep 1 published; solve guides are no longer required"),
            ],
        },
    }


def atlas_gates(
    working: GuideDatabase,
    published: GuideDatabase | None = None,
    *,
    ctx=None,
    ledgers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """三道闸门；`ledgers` 传进来时不再重算账本（a1-8 十二：closure 不重复算同一个账本）。"""
    keys = (
        "floating_grease",
        "dream_ticker",
        "golden_scapegoat",
        "origami_bird",
        "nymph",
        "jump",
        "zagreus_hand",
    )
    summaries = {}
    for key in keys:
        if ledgers is not None and key in ledgers:
            summaries[key] = ledgers[key]
            continue
        points = []
        try:
            from hsrmap.guides.topics.official import official_points_for_topic

            points = official_points_for_topic(key, ctx=ctx)
        except Exception:
            points = []
        summaries[key] = topic_ledger(working, key, official_points=points, published_db=published)
    return build_gates(summaries)


def materialize_ledger(db: GuideDatabase, report: dict[str, Any]) -> int:
    topic = report["topic"]
    db.conn.execute("DELETE FROM guide_target_status WHERE topic_key = ?", (topic,))
    count = 0
    for row in report.get("targets") or []:
        db.conn.execute(
            """
            INSERT INTO guide_target_status(topic_key, target_key, source_point_id, map_id, status, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                topic,
                row["target_key"],
                row.get("source_point_id"),
                row.get("map_id"),
                row["status"],
                None,
            ),
        )
        count += 1
    db.conn.commit()
    return count


def _gate_topic(topic: str, display: str, engine: str, summary: dict[str, Any], nxt: str) -> dict[str, Any]:
    return {
        "topic": topic,
        "display": display,
        "engine": engine,
        "official": int(summary.get("official_targets") or 0),
        "published": int(summary.get("published") or 0),
        "needs_source": int(summary.get("no_source_yet") or 0),
        "next": nxt,
    }


def _scope_published(db: GuideDatabase, topic_key: str) -> int:
    """How many targets the topic's published entries cover.

    A `set:` entry covers all of its members, so it is counted by member count,
    not as one row — otherwise publishing a region guide would look like a single
    covered target and the ledger would keep reporting the rest as missing.
    """
    suffix = f":topic:{topic_key}"
    global_key = f"global:topic:{topic_key}"
    covered: set[str] = set()
    for row in db.conn.execute(
        "SELECT source_point_id FROM guide_entry WHERE IFNULL(status, '') IN ('published', 'APPROVED')"
    ):
        pid = str(row["source_point_id"] or "")
        if pid == global_key:
            covered.add(global_key)
            continue
        if pid.endswith(suffix) and pid.startswith("set:"):
            covered.update(expand_point_key(pid))
            continue
        if pid.endswith(suffix) and pid.startswith("map:"):
            covered.add(pid)
    return len(covered)


def expand_point_key(key: str) -> list[str]:
    """The official points a synthetic key covers.

    `set:3625-3628-3632:topic:golden_scapegoat` names its members in the key
    itself, so coverage never depends on a hidden mapping (ADR-001).
    """
    value = str(key or "")
    if not value.startswith("set:"):
        return [value] if value else []
    parts = value.split(":")
    if len(parts) < 2:
        return []
    return [member.strip() for member in parts[1].split("-") if member.strip()]


def published_point_ids(db: GuideDatabase) -> set[str]:
    """Published target keys, with every `set:` expanded to its member points."""
    out: set[str] = set()
    for row in db.conn.execute(
        "SELECT source_point_id FROM guide_entry WHERE IFNULL(status, '') IN ('published', 'APPROVED')"
    ):
        for point_id in expand_point_key(str(row["source_point_id"] or "")):
            out.add(point_id)
    return out


def _published_ids(db: GuideDatabase) -> set[str]:
    return published_point_ids(db)


def _topic_mentions(db: GuideDatabase, topic_key: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in db.conn.execute("SELECT source_point_id, status, draft_json FROM review_item"):
        try:
            draft = json.loads(row["draft_json"] or "{}")
        except json.JSONDecodeError:
            continue
        item_topic = str(draft.get("topic_key") or draft.get("topic") or "").replace("-", "_")
        if item_topic != topic_key:
            continue
        status = str(row["status"] or "")
        ids = {str(row["source_point_id"] or "")}
        for cand in draft.get("candidate_points") or []:
            if isinstance(cand, dict):
                ids.add(str(cand.get("source_point_id") or ""))
        if " / " in str(draft.get("resolved_map_name") or ""):
            status = "AMBIGUOUS"
        for pid in ids:
            if not pid:
                continue
            out.setdefault(pid, []).append(status)
    #: 「查过了、公开来源确实没有」的判定（证据账本给的结论，见 nops.py）。
    #: 它既可能挂在点位上，也可能挂在整张地图的目标键上，两种都记。
    for row in db.conn.execute(
        "SELECT target_key, source_point_id, status FROM source_search_verdict WHERE topic_key = ?",
        (topic_key,),
    ):
        status = str(row["status"] or "")
        if not status:
            continue
        for key in {str(row["target_key"] or ""), str(row["source_point_id"] or "")} - {""}:
            out.setdefault(key, []).append(status)
    return out


def _status_for(pid: str, published_ids: set[str], mentions: dict[str, list[str]]) -> str:
    if pid in published_ids:
        return "PUBLISHED"
    states = mentions.get(pid) or []
    if not states:
        return "NEEDS_SOURCE"
    if any(state == "AMBIGUOUS" for state in states):
        return "AMBIGUOUS"
    if any(state == "APPROVED" for state in states):
        return "APPROVED"
    if any(state == "AUTO_SUGGEST" for state in states):
        return "MATCHED"
    if any(state == "NEEDS_REVIEW" for state in states):
        return "NEEDS_REVIEW"
    if any(state == "REJECTED" for state in states) and not any(state not in {"REJECTED"} for state in states):
        return "SOURCE_REJECTED"
    if any(state == "NO_PUBLIC_SOURCE_FOUND" for state in states):
        return "NO_PUBLIC_SOURCE_FOUND"
    return "EXTRACTED"
