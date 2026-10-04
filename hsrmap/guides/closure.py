"""Machine closure verdict for Guide Atlas V3 (a1-6 §32) and the ADR table (§33 步 7)."""

from __future__ import annotations

from typing import Any

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.ledger import atlas_gates, build_gates, topic_ledger
from hsrmap.guides.publishing.atomic import offline_e2e
from hsrmap.guides.publishing.diff import snapshot_diff
from hsrmap.guides.topics.loader import list_topics
from hsrmap.paths import GUIDE_ASSETS, GUIDE_DB, GUIDE_PUBLISHED_DB

#: Architecture decisions that must be either implemented or explicitly deferred.
ARCHITECTURE_DECISIONS: dict[str, str] = {
    "published targets": "IMPLEMENTED (synthetic keys) ADR-001",
    "content_block": "DEFERRED ADR-002",
    "article resolver": "IMPLEMENTED ADR-003",
    "job resume": "IMPLEMENTED ADR-004",
    "ai cache": "IMPLEMENTED ADR-005",
}


def identity_status() -> dict[str, Any]:
    """ADR-003 evidence: the resolver and the perceptual hash are importable."""
    from hsrmap.guides.assets.phash import SIMILARITY_THRESHOLD, phash
    from hsrmap.guides.crawler.identity import ArticleFamilyResolver

    resolver = ArticleFamilyResolver()
    desktop = "https://www.gamersky.com/handbook/202404/1729233.shtml"
    mobile = "https://m.gamersky.com/handbook/202404/1729233_2.shtml?utm_source=share"
    return {
        "same_article": resolver.same_article(desktop, mobile),
        "page_index": resolver.resolve(mobile).page_index,
        "phash_threshold": SIMILARITY_THRESHOLD,
        "phash_available": bool(phash),
    }


def discovery_status(working: GuideDatabase) -> dict[str, Any]:
    """Sprint 3 evidence: search ledger, host health and the frontier rule table."""
    from hsrmap.guides.evidence import summary as evidence_summary
    from hsrmap.guides.planner import FRONTIER_WEIGHTS, host_health

    health = host_health(working)
    return {
        "searches": evidence_summary(working)["searches"],
        "results": evidence_summary(working)["results"],
        "targets_searched": evidence_summary(working)["targets_searched"],
        "hosts": len(health),
        "frontier_rules": len(FRONTIER_WEIGHTS),
    }


def ai_cache_status(working: GuideDatabase) -> dict[str, Any]:
    """ADR-005 evidence: one cache for every provider, keyed with prompt_version."""
    from hsrmap.guides.ai_cache import AICache, cache_key

    stats = AICache(db=working).stats()
    sample = cache_key("deepseek", "deepseek-chat", "article_extract_v1", {"text": "x"})
    return {
        **stats,
        "key_length": len(sample),
        "key_changes_with_prompt_version": sample
        != cache_key("deepseek", "deepseek-chat", "article_extract_v2", {"text": "x"}),
    }


def jobs_status(working: GuideDatabase) -> dict[str, Any]:
    """ADR-004 evidence: the job model exists and every state is reachable."""
    from hsrmap.guides.jobs import DEFAULT_BUDGET, JOB_STATES, list_jobs

    jobs = list_jobs(working, limit=100)
    states: dict[str, int] = {}
    for job in jobs:
        states[str(job.get("state"))] = states.get(str(job.get("state")), 0) + 1
    return {
        "states": list(JOB_STATES),
        "jobs": len(jobs),
        "by_state": states,
        "budget_keys": sorted(DEFAULT_BUDGET),
        "reports": sum(1 for job in jobs if job.get("report_path")),
    }


def golden_status() -> dict[str, Any]:
    """Every enabled topic registers a golden set (a1-6 §31)."""
    from hsrmap.guides.golden import TOPICS_DIR

    from hsrmap.guides.golden import golden_level_ids, level_status

    enabled = [str(item["topic_key"]) for item in list_topics(enabled_only=True)]
    missing = [key for key in enabled if not (TOPICS_DIR / key / "golden.json").exists()]
    levels = level_status()
    passed = [item["level"] for item in levels if item["ok"]]
    return {
        "enabled_topics": len(enabled),
        "registered": len(enabled) - len(missing),
        "missing": missing,
        "levels": levels,
        "levels_ok": passed == list(golden_level_ids()),
        "levels_label": "/".join(passed) or "-",
        "ok": not missing and passed == list(golden_level_ids()),
    }


def shared_ledgers(
    working: GuideDatabase,
    published: GuideDatabase,
    *,
    ctx: Any = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """所有启用主题的账本 + 各主题的官方点位数，**只算一次**（a1-8 十二）。

    closure 里三道闸门和语料健康度看的是同一个 (working, published) 账本，
    以前各算一遍，同一个主题的 SQL 要跑三遍。
    """
    from hsrmap.guides.topics.official import official_points_for_topic

    ledgers: dict[str, dict[str, Any]] = {}
    point_counts: dict[str, int] = {}
    for item in list_topics(enabled_only=True):
        key = str(item["topic_key"])
        try:
            points = official_points_for_topic(key, ctx=ctx) or []
        except Exception:  # noqa: BLE001 - 一个主题算不出来不该让整份报告塌掉
            points = []
        point_counts[key] = len(points)
        ledgers[key] = topic_ledger(working, key, official_points=points, published_db=published)
    return ledgers, point_counts


def corpus_health(
    working: GuideDatabase,
    published: GuideDatabase,
    *,
    ctx: Any = None,
    ledgers: dict[str, dict[str, Any]] | None = None,
    point_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Corpus numbers stay a *reported* metric, never a closure gate."""
    counts: dict[str, int] = {}
    coverage: dict[str, int] = {"published": 0, "official": 0, "official_points": 0}
    for item in list_topics(enabled_only=True):
        key = str(item["topic_key"])
        report = (ledgers or {}).get(key)
        if report is not None:
            #: 点位口径的分母来自官方点位列表，账本里只有 target 数（MAP_LABEL 主题按标签算），
            #: 所以点数由调用方一起带进来。
            coverage["official_points"] += int((point_counts or {}).get(key, 0))
        else:
            try:
                from hsrmap.guides.topics.official import official_points_for_topic

                points = official_points_for_topic(key, ctx=ctx)
            except Exception:
                points = []
            report = topic_ledger(working, key, official_points=points, published_db=published)
            coverage["official_points"] += len(points)
        for name, value in (report.get("counts") or {}).items():
            counts[name] = counts.get(name, 0) + int(value)
        coverage["published"] += int(report.get("published") or 0)
        #: 账本口径：MAP_LABEL 主题按标签算目标（一只鸟一个区域标签），所以它比点位数小。
        coverage["official"] += int(report.get("official_targets") or 0)
    #: 点位口径：完成模型的分母（上面两条分支各加过一次）。两个数都报出来，
    #: 避免「621 / 611」这种跨口径比较。
    return {
        **coverage,
        "no_source_yet": counts.get("NEEDS_SOURCE", 0),
        "needs_review": counts.get("NEEDS_REVIEW", 0),
        "no_public_source": counts.get("NO_PUBLIC_SOURCE_FOUND", 0),
        "counts": counts,
    }


def _completeness_block(published: GuideDatabase) -> dict[str, Any]:
    """完成模型的计数；读不到（缺官方点位数据）时如实说明，而不是假装 0。"""
    from hsrmap.guides.stages import status_summary

    try:
        return status_summary(published)
    except Exception as exc:  # noqa: BLE001 - 收口报告不该因为完成模型读不到数据而失败
        return {"error": type(exc).__name__, "detail": str(exc)[:160]}


def closure_check(
    working: GuideDatabase,
    published: GuideDatabase,
    *,
    ctx: Any = None,
    assets_root: Any = GUIDE_ASSETS,
    waivers: list[dict[str, Any]] | None = None,
    e2e: bool = True,
    completeness: bool = True,
) -> dict[str, Any]:
    """Run every hard acceptance check and return the verdict.

    `completeness=True` 让收口报告带上完成模型（到点即完成 / 缺解法 / 缺定位）的计数：
    发布条数只说「有多少攻略」，完成状态才说「玩家照着能不能拿到」。
    """
    from hsrmap.guides.audit import audit_entries
    from hsrmap.guides.publishing.diff import entry_snapshot

    #: 账本只算一次：三道闸门和语料健康度看的是同一份 (working, published)（a1-8 十二）。
    ledgers, point_counts = shared_ledgers(working, published, ctx=ctx)
    gates = atlas_gates(working, published, ctx=ctx, ledgers=ledgers)
    report = snapshot_diff(working, published, ctx=ctx, assets_root=assets_root, waivers=waivers)
    # The acceptance is about what the Viewer actually serves, so the scoreboard
    # audits the published snapshot; the diff report keeps auditing the candidate.
    audit = audit_entries(entry_snapshot(published), working, assets_root=assets_root)
    goldens = golden_status()
    e2e_result = offline_e2e(published.path, assets_root=assets_root) if e2e else {"ok": True, "skipped": True}

    broken = report["bindings"]["broken"]
    checks: list[dict[str, Any]] = [
        {"name": "Engine Gate A", "status": gates["A"]["result"], "ok": gates["A"]["result"] == "PASS"},
        {"name": "Engine Gate B", "status": gates["B"]["result"], "ok": gates["B"]["result"] == "PASS"},
        {"name": "Engine Gate C", "status": gates["C"]["result"], "ok": gates["C"]["result"] == "PASS"},
        {"name": "Broken bindings", "status": str(len(broken)), "value": len(broken), "ok": not broken},
        {
            "name": "Broken assets",
            "status": str(len(report["assets"]["missing_files"])),
            "value": len(report["assets"]["missing_files"]),
            "ok": not report["assets"]["missing_files"],
        },
        {
            "name": "Missing core targets",
            "status": str(len([item for item in broken if item.get("kind") == "point"])),
            "value": len([item for item in broken if item.get("kind") == "point"]),
            "ok": not [item for item in broken if item.get("kind") == "point"],
        },
        {
            "name": "Hallucinated steps",
            "status": str(len(audit.get("hallucinated") or [])),
            "value": len(audit.get("hallucinated") or []),
            "ok": not audit.get("hallucinated"),
        },
        {
            "name": "Golden regression",
            "status": f"{goldens['registered']}/{goldens['enabled_topics']} + {goldens['levels_label']}",
            "ok": goldens["ok"],
        },
        {"name": "Offline E2E", "status": "PASS" if e2e_result["ok"] else "FAIL", "ok": bool(e2e_result["ok"])},
        {
            "name": "Snapshot regression",
            "status": report["gate"]["result"],
            "ok": report["gate"]["result"] != "FAIL",
        },
    ]
    #: 证据层（a1-8 六）：完成度是「有没有」，这一栏是「凭什么」。
    evidence = report.get("evidence") or {}
    evidence_findings = list(evidence.get("findings") or [])
    if evidence:
        checks.append({
            "name": "Evidence gates",
            "status": f"{evidence.get('claims', 0)} claims / {len(evidence_findings)} gap(s)",
            "value": len(evidence_findings),
            "ok": not evidence_findings and not (evidence.get("losses") or []),
        })
    open_items = [item for item in checks if not item["ok"]]
    chrome = int((audit.get("counts") or {}).get("CHROME_STEP") or 0)
    return {
        "closure": "PASS" if not open_items else "BLOCKED",
        "checks": checks,
        "open_items": open_items,
        "architecture": ARCHITECTURE_DECISIONS,
        "identity": identity_status(),
        "discovery": discovery_status(working),
        "jobs": jobs_status(working),
        "ai_cache": ai_cache_status(working),
        "corpus": corpus_health(working, published, ctx=ctx, ledgers=ledgers, point_counts=point_counts),
        "completeness": _completeness_block(published) if completeness else {"skipped": True},
        "quality_debt": {
            "hallucinated_steps": len(audit.get("hallucinated") or []),
            "ungrounded_steps": int((audit.get("counts") or {}).get("UNGROUNDED_STEP") or 0),
            "chrome_steps": chrome,
            "guides_with_problems": int(audit.get("guides_with_problems") or 0),
            "evidence_gaps": len(evidence_findings),
        },
        "evidence": {
            "adopted": bool(evidence.get("adopted")),
            "claims": int(evidence.get("claims") or 0),
            "by_level": evidence.get("by_level") or {},
            "digest": (evidence.get("candidate") or {}).get("digest", ""),
            "current_digest": (evidence.get("current") or {}).get("digest", ""),
            "findings": len(evidence_findings),
            "downgrades": len(evidence.get("downgrades") or []),
            "losses": len(evidence.get("losses") or []),
        },
        "details": {
            "gates": gates,
            "gate": report["gate"],
            "changes": report.get("change_counts"),
            "e2e": e2e_result,
            "goldens": goldens,
        },
    }


def render_closure(report: dict[str, Any]) -> str:
    """The §32 scoreboard, as text."""
    lines = ["Guide Atlas V3 Closure", ""]
    for item in report["checks"]:
        mark = "PASS" if item["ok"] else "FAIL"
        lines.append(f"{item['name']:.<24} {item['status']:>10} {mark}")
    lines += ["", "Architecture decisions"]
    for name, status in report["architecture"].items():
        lines.append(f"  {name:.<22} {status}")
    corpus = report["corpus"]
    lines += [
        "",
        "Corpus health",
        f"  Published coverage .... {corpus['published']} / {corpus.get('official_points') or corpus['official']}"
        f"  (点位口径；账本标签口径 {corpus['official']})",
        f"  Needs source .......... {corpus['no_source_yet']}",
        f"  Needs review .......... {corpus['needs_review']}",
        f"  No public source ...... {corpus['no_public_source']}",
    ]
    found = report.get("completeness") or {}
    if found and not found.get("skipped"):
        if found.get("error"):
            lines.append(f"  Completeness .......... ERROR {found['error']}")
        else:
            lines += [
                "",
                "Completion model (玩家照着能不能拿到)",
                f"  Ready to grab ......... {found['done']} / {found['points']}"
                f"  (到点即完成 {found['locate_complete']} + 含解法完成 {found['complete']})",
                f"  Missing solve ......... {found['solve_missing']}",
                f"  Missing locate ........ {found['missing_locate']}"
                f"  (缺定位 {found['locate_missing']} + 仅范围 {found['scope_only']} + 无证据 {found['no_evidence']})",
            ]
    found_evidence = report.get("evidence") or {}
    if found_evidence:
        levels = found_evidence.get("by_level") or {}
        lines += [
            "",
            "Evidence layer (凭什么)",
            f"  Claims ................ {found_evidence.get('claims', 0)}",
            "  By level .............. "
            + " / ".join(f"{key} {value}" for key, value in sorted(levels.items())),
            f"  Gaps / downgrades ..... {found_evidence.get('findings', 0)} / {found_evidence.get('downgrades', 0)}",
            f"  Evidence digest ....... {str(found_evidence.get('digest') or '')[:16]}",
        ]
    lines += [
        "",
        f"CLOSURE RESULT ........ {report['closure']}",
    ]
    debt = report["quality_debt"]
    if debt["hallucinated_steps"] or debt["chrome_steps"] or debt.get("evidence_gaps"):
        lines += [
            "",
            "Open quality debt",
            f"  hallucinated steps .... {debt['hallucinated_steps']}",
            f"  chrome steps .......... {debt['chrome_steps']}",
            f"  evidence gaps ......... {debt.get('evidence_gaps', 0)}",
            f"  guides with problems .. {debt['guides_with_problems']}",
        ]
    return "\n".join(lines) + "\n"
