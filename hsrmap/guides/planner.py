"""Target-driven discovery, priority frontier and yield metrics (a1-6 §六–§八).

Three questions this module answers, all of them offline:

1. **What is missing, and which evidence?** the completion model's point-level gaps
   (`missing LOCATE` / `missing SOLVE`, user 2026-10 指示) unioned with the ledger's
   `NEEDS_SOURCE` / `NO_PUBLIC_SOURCE_FOUND` targets (which still cover map-level
   labels), each turned into query families chosen by the missing capability (§七).
2. **What is worth fetching next?** every candidate URL is scored with the
   rule table from §六 instead of being taken in file order.
3. **Was it worth it?** per-host and per-target yield, plus a host health score,
   so the frontier can prefer hosts that actually produce corpus.
"""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlparse

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.crawler.identity import ArticleFamilyResolver
from hsrmap.guides.evidence import searched_targets, summary as evidence_summary

#: The two names guides actually use for the game.
GAME_NAMES = ("崩坏星穹铁道", "崩铁")

#: Query shapes per target (a1-6 §七).
QUERY_SHAPES = ("{game} {topic} {place}", "{game} {place} {topic}", "{topic} {place} 全收集", "{topic} {place} 解谜")

#: 查询形状按「还缺哪种证据」分组（用户 2026-10 指示）：缺定位就去搜位置，
#: 缺解法就去搜解法——不再用同一组查询去打两种缺口。
LOCATE_QUERY_SHAPES = (
    "{game} {topic} {place} 位置",
    "{game} {place} {topic} 在哪",
    "{topic} {place} 位置 全收集",
)
SOLVE_QUERY_SHAPES = (
    "{game} {topic} {place} 解谜",
    "{game} {topic} {place} 攻略 步骤",
    "{topic} {place} 怎么解",
)
MISSING_SHAPES = {"LOCATE": LOCATE_QUERY_SHAPES, "SOLVE": SOLVE_QUERY_SHAPES, "ANY": QUERY_SHAPES}

#: Target statuses that mean "we still owe the corpus a source".
GAP_STATUSES = ("NEEDS_SOURCE", "NO_PUBLIC_SOURCE_FOUND")

#: Priority rules (a1-6 §六). Positive rules add, negative rules subtract.
FRONTIER_WEIGHTS = {
    "needs_source": 50,
    "high_yield_host": 30,
    "family_continuation": 20,
    "unseen_url": 10,
    "qa_fail_history": -30,
    "duplicate_family": -50,
    "js_only": -80,
    "known_mirror": -100,
}

#: A host counts as high yield once it produced this many published guides.
HIGH_YIELD_PUBLISHED = 1


def _host(url: str) -> str:
    """Registrable-ish host of a URL: lower-case netloc without a port."""
    return (urlparse(str(url or "")).netloc or "").lower().split(":")[0]


def _dict_rows(db: GuideDatabase, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.conn.execute(sql, params)]


# --------------------------------------------------------------------- #
# §七 — from targets to queries
# --------------------------------------------------------------------- #


def query_families(
    topic_display: str,
    target: dict[str, Any] | None = None,
    *,
    missing: str = "",
    game_names: Iterable[str] = GAME_NAMES,
) -> list[str]:
    """The query shapes worth trying for one missing target (a1-6 §七).

    `missing` 是完成模型给出的缺口（LOCATE / SOLVE / ANY）：缺定位就只问位置，
    缺解法就只问解法。留空等于 ANY（还不知道缺什么时，两种都试）。
    """
    target = target or {}
    shapes = MISSING_SHAPES.get(str(missing or "").upper() or "ANY", QUERY_SHAPES)
    topic = str(topic_display or "").strip()
    place = str(target.get("map_name") or target.get("region") or target.get("map_path") or "").strip()
    label = str(target.get("label") or "").strip()
    # An official label that the topic name already contains adds nothing.
    if label and label not in topic and label not in place:
        place = f"{place} {label}".strip()
    queries: list[str] = []
    for game in game_names:
        for shape in shapes:
            query = shape.format(game=game, topic=topic, place=place).strip()
            query = " ".join(part for part in query.split() if part)
            if query and query not in queries:
                queries.append(query)
    return queries


def _official_points(topic: str, ctx: Any = None, supplied: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if supplied is not None:
        return list(supplied)
    try:
        from hsrmap.guides.topics.official import official_points_for_topic

        return list(official_points_for_topic(topic, ctx=ctx) or [])
    except Exception:
        return []


def _official_maps(topic: str, ctx: Any = None, supplied: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if supplied is not None:
        return list(supplied)
    try:
        from hsrmap.guides.topics.official import official_maps_for_topic

        return list(official_maps_for_topic(topic, ctx=ctx) or [])
    except Exception:
        return []


def target_gaps(
    db: GuideDatabase,
    topic: str,
    *,
    ctx: Any = None,
    official_points: list[dict[str, Any]] | None = None,
    official_maps: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Targets the ledger still marks as missing a public source.

    A MAP_LABEL target is named by its map, so the map's own name is looked up —
    otherwise every query for a topic would be the bare topic name (§七).
    """
    from hsrmap.guides.ledger import topic_ledger

    points = _official_points(topic, ctx, official_points)
    maps = _official_maps(topic, ctx, official_maps)
    by_map = {str(item.get("map_id") or ""): item for item in maps}
    ledger = topic_ledger(db, topic, official_points=points)
    by_point = {str(point.get("source_point_id") or ""): point for point in points}
    out = []
    for target in ledger.get("targets") or []:
        if str(target.get("status")) not in GAP_STATUSES:
            continue
        target_key = str(target.get("target_key") or "")
        if target_key.startswith("map:"):
            map_id = target_key.split(":")[1]
            amap = by_map.get(map_id) or {}
            out.append({
                **target,
                "gap_source": "ledger",
                "missing": "ANY",
                "map_id": map_id,
                "map_name": amap.get("name"),
                "map_path": amap.get("path"),
                "region": (str(amap.get("path") or "").split("/")[0].strip() or None),
            })
            continue
        point = by_point.get(str(target.get("source_point_id") or "")) or {}
        out.append({
            **target,
            "gap_source": "ledger",
            "missing": "ANY",
            **{key: point.get(key) for key in ("map_name", "region", "map_path", "label")},
        })
    return out


def completion_gaps(
    db: GuideDatabase,
    topic: str,
    *,
    ctx: Any = None,
    points: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """缺 LOCATE / 缺 SOLVE 的点位——直接从完成模型来的工作队列。

    用户 2026-10 指示：下一个该搜什么，要由「缺哪种证据」决定，而不是笼统的「缺源」。
    所以这里返回的是点位级的缺口（`missing` = LOCATE / SOLVE / ANY），查询家族据此分化。
    """
    from hsrmap.guides.stages import completeness_report

    try:
        report = completeness_report(db, topics=[str(topic).replace("-", "_")])
    except Exception:  # noqa: BLE001 - 计划不该因为完成模型不认识这个主题而失败
        return []
    by_point = {str(item.get("source_point_id") or ""): item for item in _official_points(topic, ctx, points)}
    out: list[dict[str, Any]] = []
    for row in list(report.get("missing_solve") or []) + list(report.get("missing_locate") or []):
        point_id = str(row.get("point") or "")
        if not point_id:
            continue
        status = str(row.get("status") or "")
        missing = "SOLVE" if status == "SOLVE_MISSING" else ("ANY" if status == "NO_EVIDENCE" else "LOCATE")
        point = by_point.get(point_id) or {}
        out.append({
            "target_key": f"point:{point_id}",
            "source_point_id": point_id,
            "status": status,
            "missing": missing,
            "requirement": row.get("requirement"),
            "solve_kind": row.get("solve_kind"),
            "gap_source": "completion",
            "map_id": point.get("map_id"),
            "map_name": point.get("map_name"),
            "region": point.get("region"),
            "map_path": point.get("map_path"),
            "label": point.get("label"),
        })
    return out


def _done_points(db: GuideDatabase, topic: str) -> tuple[set[str], int]:
    """(完成模型说「已完成」的点位 id, 还没做完的点位数)；读不到时返回空集合。"""
    from hsrmap.guides.stages import completeness_report

    try:
        report = completeness_report(db, topics=[str(topic).replace("-", "_")])
    except Exception:  # noqa: BLE001 - 计划不该因为完成模型读不到数据而失败
        return set(), 0
    rows = report.get("rows") or []
    return (
        {str(row.get("point") or "") for row in rows if row.get("done")},
        sum(1 for row in rows if not row.get("done")),
    )


def discovery_plan(
    db: GuideDatabase,
    topic: str,
    *,
    ctx: Any = None,
    official_points: list[dict[str, Any]] | None = None,
    official_maps: list[dict[str, Any]] | None = None,
    limit: int = 10,
    include_searched: bool = True,
) -> dict[str, Any]:
    """A concrete search plan for one topic (a1-6 §七 + §八 evidence)."""
    key = str(topic).replace("-", "_")
    display = key
    try:
        from hsrmap.guides.topics.loader import get_topic

        display = str(get_topic(key).get("display_name") or key)
    except Exception:
        display = key
    from hsrmap.guides.topics.evidence import evidence_form, search_hint, text_sufficient

    #: 缺口来自两处并集：完成模型（缺 LOCATE / 缺 SOLVE，点位级）在前，账本缺口补充
    #: 它看不到的目标（例如整张地图的 MAP_LABEL 目标）。
    gaps = completion_gaps(db, key, ctx=ctx, points=official_points)
    known = {str(item.get("target_key") or "") for item in gaps}
    ledger_gaps = target_gaps(db, key, ctx=ctx, official_points=official_points, official_maps=official_maps)
    #: 账本缺口也要过一遍完成模型：点位已经「到得了」的目标不再排检索词。
    #: 例如二次元 JUMP 归入第 1 阶段后（进去之后是玩家自己的操作，不需要攻略），
    #: 它的 102 个 NO_PUBLIC_SOURCE_FOUND 目标已经没有待办了。
    done_points, missing_points = _done_points(db, key)
    if done_points:
        ledger_gaps = [
            item
            for item in ledger_gaps
            if not (
                (str(item.get("source_point_id") or "") in done_points)
                or (str(item.get("target_key") or "").startswith("map:") and missing_points == 0)
            )
        ]
    gaps += [item for item in ledger_gaps if str(item.get("target_key") or "") not in known]
    evidence = searched_targets(db, topic=key)
    on_text = text_sufficient(key)
    planned = [];
    for target in gaps:
        target_key = str(target.get("target_key") or target.get("source_point_id") or "")
        seen = evidence.get(target_key) or evidence.get(str(target.get("source_point_id") or ""))
        if seen and not include_searched:
            continue
        missing = str(target.get("missing") or "ANY").upper()
        planned.append({
            "target_key": target_key,
            "status": target.get("status"),
            "missing": missing,
            "gap_source": target.get("gap_source") or "ledger",
            "requirement": target.get("requirement"),
            "solve_kind": target.get("solve_kind"),
            "map_name": target.get("map_name"),
            "region": target.get("region"),
            "label": target.get("label"),
            "queries": query_families(display, target, missing=missing),
            "evidence_form": evidence_form(key)["kind"],
            "text_sufficient": on_text,
            "searched": bool(seen),
            "accepted_urls": int(seen["accepted"]) if seen else 0,
            "evidence": seen or {},
        })
    return {
        "topic": key,
        "display_name": display,
        "evidence_form": evidence_form(key),
        "search_hint": search_hint(key),
        "targets_with_gap": len(gaps),
        "targets_missing_locate": sum(1 for item in gaps if str(item.get("missing")) in {"LOCATE", "ANY"}),
        "targets_missing_solve": sum(1 for item in gaps if str(item.get("missing")) == "SOLVE"),
        "targets_from_completion": sum(1 for item in gaps if str(item.get("gap_source")) == "completion"),
        "targets_never_searched": sum(1 for item in planned if not item["searched"]),
        "planned": len(planned[:limit]) if limit else len(planned),
        "evidence": evidence_summary(db, topic=key),
        "plan": planned[:limit] if limit else planned,
    }


# --------------------------------------------------------------------- #
# §六 — priority frontier
# --------------------------------------------------------------------- #


def host_health(db: GuideDatabase) -> dict[str, dict[str, Any]]:
    """Per-host corpus yield: pages, QA outcome and asset success (§十八/§十九)."""
    hosts: dict[str, dict[str, Any]] = {}

    def bucket(host: str) -> dict[str, Any]:
        return hosts.setdefault(
            host,
            {
                "host": host,
                "pages": 0,
                "qa_pass": 0,
                "qa_fail": 0,
                "qa_unchecked": 0,
                "js_only": 0,
                "published": 0,
                "assets_ok": 0,
                "assets_blocked": 0,
                "assets_failed": 0,
            },
        )

    for row in _dict_rows(db, "SELECT canonical_url, qa_status, qa_reason FROM guide_page"):
        entry = bucket(_host(str(row.get("canonical_url") or "")))
        entry["pages"] += 1
        status = str(row.get("qa_status") or "")
        reason = str(row.get("qa_reason") or "")
        if status == "QA_PASS":
            entry["qa_pass"] += 1
        elif status:
            entry["qa_fail"] += 1
        else:
            entry["qa_unchecked"] += 1
        if reason == "JS_RENDER_REQUIRED":
            entry["js_only"] += 1
    for row in _dict_rows(db, "SELECT source_url, status FROM guide_entry WHERE IFNULL(status, '') = 'published'"):
        bucket(_host(str(row.get("source_url") or "")))["published"] += 1
    for row in _dict_rows(db, "SELECT source_url, status FROM guide_asset_cache"):
        entry = bucket(_host(str(row.get("source_url") or "")))
        status = str(row.get("status") or "")
        if status in {"FETCHED", "CACHE_HIT"}:
            entry["assets_ok"] += 1
        elif status == "HTTP_BLOCKED":
            entry["assets_blocked"] += 1
        else:
            entry["assets_failed"] += 1
    for entry in hosts.values():
        checked = entry["qa_pass"] + entry["qa_fail"]
        assets = entry["assets_ok"] + entry["assets_blocked"] + entry["assets_failed"]
        # An unchecked page is not a failing page, and a host whose assets live on
        # a CDN is not a host with broken assets: both stay neutral (1.0).
        entry["qa_pass_rate"] = round(entry["qa_pass"] / checked, 3) if checked else None
        entry["asset_success_rate"] = round(entry["assets_ok"] / assets, 3) if assets else None
        # one number for the frontier rules: published corpus plus a healthy mix
        entry["score"] = int(
            min(60, 20 * entry["published"])
            + 40 * (entry["qa_pass_rate"] if entry["qa_pass_rate"] is not None else 1.0)
            * (entry["asset_success_rate"] if entry["asset_success_rate"] is not None else 1.0)
        )
        entry["high_yield"] = entry["published"] >= HIGH_YIELD_PUBLISHED
    return hosts


def score_candidate(
    candidate: dict[str, Any],
    *,
    health: dict[str, dict[str, Any]] | None = None,
    known_families: Iterable[str] = (),
    accepted_families: Iterable[str] = (),
    resolver: ArticleFamilyResolver | None = None,
) -> dict[str, Any]:
    """Score one candidate URL with the §六 rule table, with reasons attached."""
    engine = resolver or ArticleFamilyResolver()
    url = str(candidate.get("url") or "")
    ref = engine.resolve(url)
    host = _host(url)
    stats = (health or {}).get(host) or {}
    known = set(known_families)
    accepted = set(accepted_families)
    reasons: list[dict[str, Any]] = []

    def rule(name: str, weight: int, detail: str = "") -> None:
        reasons.append({"rule": name, "weight": weight, "detail": detail})

    needs_source = bool(candidate.get("needs_source")) or str(candidate.get("status") or "") in GAP_STATUSES
    if needs_source:
        rule("needs_source", FRONTIER_WEIGHTS["needs_source"], str(candidate.get("target_key") or ""))
    if stats.get("high_yield"):
        rule("high_yield_host", FRONTIER_WEIGHTS["high_yield_host"], f"{stats.get('published', 0)} published")
    if candidate.get("continuation"):
        rule("family_continuation", FRONTIER_WEIGHTS["family_continuation"], ref.family)
    if ref.family not in known and ref.family not in accepted:
        rule("unseen_url", FRONTIER_WEIGHTS["unseen_url"], ref.canonical_url)
    if int(stats.get("qa_fail") or 0) > 0:
        rule("qa_fail_history", FRONTIER_WEIGHTS["qa_fail_history"], f"{stats['qa_fail']} QA failures")
    if ref.family in known or ref.family in accepted:
        rule("duplicate_family", FRONTIER_WEIGHTS["duplicate_family"], ref.family)
    if candidate.get("js_only") or int(stats.get("js_only") or 0) > 0:
        rule("js_only", FRONTIER_WEIGHTS["js_only"], "needs a browser to render")
    if engine.mirror_host(ref.raw_host):
        rule("known_mirror", FRONTIER_WEIGHTS["known_mirror"], ref.raw_host)
    return {
        "url": url,
        "topic": candidate.get("topic"),
        "target_key": candidate.get("target_key"),
        "family": ref.family,
        "host": host,
        "source": candidate.get("source"),
        "score": sum(item["weight"] for item in reasons),
        "reasons": reasons,
    }


def frontier(
    db: GuideDatabase,
    candidates: Iterable[dict[str, Any]],
    *,
    limit: int | None = None,
    resolver: ArticleFamilyResolver | None = None,
) -> list[dict[str, Any]]:
    """Candidates ordered by what is most likely to fill a target gap."""
    engine = resolver or ArticleFamilyResolver()
    known = {
        engine.family(str(row["canonical_url"]))
        for row in db.conn.execute("SELECT canonical_url FROM guide_page WHERE canonical_url IS NOT NULL AND canonical_url != ''")
    }
    health = host_health(db)
    scored = [score_candidate(item, health=health, known_families=known, resolver=engine) for item in candidates]
    scored.sort(key=lambda item: -item["score"])
    return scored[:limit] if limit else scored


def candidates_from_seeds(topic: str) -> list[dict[str, Any]]:
    """Seed URLs declared by a topic profile, as frontier candidates."""
    from hsrmap.guides.discover import discover_urls, load_seeds

    seeds = load_seeds(topic=topic)
    key = str(topic).replace("-", "_")
    return [
        {"url": url, "topic": key, "source": "seed", "needs_source": False}
        for url in (discover_urls(seeds) or seeds.get("urls") or [])
    ]


def candidates_from_evidence(db: GuideDatabase, *, topic: str | None = None) -> list[dict[str, Any]]:
    """Accepted search results that were never turned into a page."""
    sql = [
        "SELECT s.url, s.topic, s.target_key, s.decision, s.rank FROM source_search_result s",
        "WHERE s.decision = 'ACCEPTED'",
    ]
    params: list[Any] = []
    if topic:
        sql.append("AND s.topic = ?")
        params.append(str(topic))
    sql.append("ORDER BY s.id")
    return [
        {
            "url": str(row["url"]),
            "topic": row["topic"],
            "target_key": row["target_key"],
            "needs_source": True,
            "source": "evidence",
        }
        for row in db.conn.execute(" ".join(sql), tuple(params))
    ]


# --------------------------------------------------------------------- #
# §十八 — yield
# --------------------------------------------------------------------- #


def source_yield(db: GuideDatabase) -> list[dict[str, Any]]:
    """Per host: what was fetched, what passed QA, what got published."""
    health = host_health(db)
    return sorted(health.values(), key=lambda item: (-item["published"], -item["pages"], item["host"]))


def target_yield(
    db: GuideDatabase,
    topic: str,
    *,
    ctx: Any = None,
    official_points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Per target: how many guides exist and where they came from."""
    from hsrmap.guides.ledger import topic_ledger

    key = str(topic).replace("-", "_")
    points = _official_points(key, ctx, official_points)
    ledger = topic_ledger(db, key, official_points=points)
    sources: dict[str, int] = {}
    for row in _dict_rows(
        db,
        "SELECT source_point_id, COUNT(*) AS c FROM guide_entry WHERE IFNULL(status, '') = 'published' GROUP BY source_point_id",
    ):
        sources[str(row["source_point_id"])] = int(row["c"])
    rows = []
    for target in ledger.get("targets") or []:
        target_key = str(target.get("target_key") or "")
        point_id = str(target.get("source_point_id") or "")
        rows.append({
            "target_key": target_key,
            "status": target.get("status"),
            "guides": int(sources.get(point_id, 0)),
        })
    return {
        "topic": key,
        "official_targets": ledger.get("official_targets", 0),
        "published": ledger.get("published", 0),
        "targets": rows,
        "targets_with_guides": sum(1 for row in rows if row["guides"]),
    }
