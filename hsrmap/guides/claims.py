"""证据一等化（a1-8 五）：把「这条结论凭什么成立」变成可查询的声明。

设计要点（照规格）：

* 证据等级属于**声明**，不属于整篇攻略：同一条攻略可能 LOCATE 来自官方地图、SOLVE 来自社区正文，
  甚至一条里有几步正文、一两步图解转录。
* `audit.py` 已有的 grounding 四档（EXACT / FRAGMENT / ASSEMBLED / NONE）原封不动地成为
  COMMUNITY_TEXT 下的子类型；`[图解法转录 <sha>]` 迁移成 TRANSCRIPTION + IMAGE_REF + asset_sha256；
* 交叉推断必须留下 `basis_json`（参与了哪些证据），不能只留一句「人判断过了」。

写入用 `guide_claim_evidence`（见 guide_db.MIGRATIONS 的 005/006）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.audit import grounding
from hsrmap.guides.signature import normalize_text


def _stage_helpers():
    """has_solution_steps / locating_text 住在判定层；延迟 import，避免 stages ↔ claims 循环依赖。"""
    from hsrmap.guides.stages import has_solution_steps, locating_text

    return has_solution_steps, locating_text

#: 声明方法版本：将来改分类规则时，报告里能区分「哪批声明是旧规则算的」。
METHOD_VERSION = "claims/1"

#: 一条声明在说什么。
CLAIM_KINDS = ("SCOPE", "LOCATE", "SOLVE")
#: 证据等级（从「直接」到「推断」）。
EVIDENCE_LEVELS = ("OFFICIAL", "COMMUNITY_TEXT", "TRANSCRIPTION", "CROSS_INFERENCE")
#: 落地方式。
GROUNDING_TIERS = ("EXACT", "FRAGMENT", "ASSEMBLED", "IMAGE_REF", "DERIVED")

#: 证据等级的「直接程度」：数字越小越直接。
_LEVEL_RANK = {"OFFICIAL": 0, "COMMUNITY_TEXT": 1, "TRANSCRIPTION": 2, "CROSS_INFERENCE": 3}

_TRANSCRIPTION = re.compile(r"^\[图解法转录\s+([0-9a-fA-F]{8,64})\]")


@dataclass(frozen=True)
class Claim:
    guide_id: int
    step_id: int | None
    claim_kind: str
    evidence_level: str
    grounding_tier: str
    asset_sha256: str = ""
    source_page_id: int | None = None
    official_point_id: str = ""
    basis_json: str = ""

    def as_row(self, *, created_at: str) -> tuple[Any, ...]:
        return (self.guide_id, self.step_id, None, self.claim_kind, self.evidence_level,
                self.grounding_tier, self.source_page_id, self.official_point_id or None,
                self.asset_sha256 or None, self.basis_json or None, METHOD_VERSION, created_at)


def transcription_asset(text: str) -> str:
    """这一步是不是图解法转录？是的话返回它声明的图片 sha 前缀。"""
    match = _TRANSCRIPTION.match(str(text or "").strip())
    return match.group(1).lower() if match else ""


def classify_step(
    text: str,
    *,
    corpus: str = "",
    official: bool = False,
    scope_key: bool = False,
    step_id: int | None = None,
    guide_id: int = 0,
    source_page_id: int | None = None,
    official_point_id: str = "",
) -> Claim:
    """给一步文字定级（纯函数，不碰数据库）。"""
    has_solution_steps, locating_text = _stage_helpers()
    body = str(text or "").strip()
    sha = transcription_asset(body)
    if official:
        kind = "SOLVE" if has_solution_steps([body]) else ("SCOPE" if scope_key else "LOCATE")
        #: 官方那一行说明就是语料本身：等级 OFFICIAL，落地方式按它有没有说「怎么做」记为 DERIVED。
        return Claim(int(guide_id), step_id, kind, "OFFICIAL", "DERIVED",
                     source_page_id=source_page_id, official_point_id=official_point_id)
    if sha:
        return Claim(int(guide_id), step_id, "SOLVE", "TRANSCRIPTION", "IMAGE_REF", asset_sha256=sha,
                     source_page_id=source_page_id, official_point_id=official_point_id)
    kind = "SOLVE" if has_solution_steps([body]) else ("LOCATE" if locating_text(body) else "SCOPE")
    if kind == "SCOPE" and not scope_key and body:
        #: 既不是「怎么做」也不是「在哪儿」的正文：算范围/背景说明。
        kind = "SCOPE"
    tier = grounding(body, corpus) if corpus else "DERIVED"
    if tier == "NONE":
        tier = "DERIVED"
    return Claim(int(guide_id), step_id, kind, "COMMUNITY_TEXT", tier,
                 source_page_id=source_page_id, official_point_id=official_point_id)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _scope_key(key: str) -> bool:
    return str(key or "").startswith(("set:", "map:", "global:"))


def published_entry_rows(db: GuideDatabase, *, limit: int = 0) -> list[Any]:
    """已发布条目（`limit` 只用于抽样）。声明重建与 publish gate 必须看同一批行。"""
    return db.conn.execute(
        "SELECT id, source_point_id, source_kind, source_url FROM guide_entry WHERE status = 'published'"
        + (" LIMIT ?" if limit else ""),
        ((int(limit),) if limit else ()),
    ).fetchall()


def build_claims(
    db: GuideDatabase,
    rows: Sequence[Any],
    *,
    corpus_cache: dict[str, str] | None = None,
) -> list[Claim]:
    """给这些条目按当前语料重新定级（只读）。

    `backfill` 与 publish gate 共用这一条代码路径：gate 用同一个函数重算，才能把
    「表里的声明」和「按当前语料应得的声明」逐条对比——步骤文字改了却没重建声明，
    就是 STALE_CLAIM，不能带着过期证据发布。
    """
    from hsrmap.guides.official import OFFICIAL_SOURCE_KIND

    cache = corpus_cache if corpus_cache is not None else {}
    claims: list[Claim] = []
    for entry in rows:
        guide_id = int(entry["id"])
        official = str(entry["source_kind"] or "") == OFFICIAL_SOURCE_KIND
        scope_key = _scope_key(str(entry["source_point_id"] or ""))
        page_row = db.conn.execute(
            "SELECT id, raw_text_path FROM guide_page WHERE canonical_url = ? ORDER BY id LIMIT 1",
            (str(entry["source_url"] or ""),),
        ).fetchone()
        source_page_id = int(page_row["id"]) if page_row is not None else None
        corpus = ""
        if page_row is not None and page_row["raw_text_path"]:
            path = str(page_row["raw_text_path"])
            if path not in cache:
                file = Path(path)
                cache[path] = (
                    normalize_text(file.read_text(encoding="utf-8", errors="replace"))
                    if file.is_file()
                    else ""
                )
            corpus = cache[path]
        steps = db.conn.execute(
            "SELECT id, text FROM guide_steps WHERE guide_id = ? ORDER BY step_index", (guide_id,)
        ).fetchall()
        for step in steps:
            claims.append(classify_step(
                str(step["text"] or ""), corpus=corpus, official=official, scope_key=scope_key,
                step_id=int(step["id"]), guide_id=guide_id, source_page_id=source_page_id,
                official_point_id=str(entry["source_point_id"] or ""),
            ))
    return claims


def backfill(db: GuideDatabase, *, apply: bool = False, limit: int = 0) -> dict[str, Any]:
    """按现有语料重建证据声明（幂等：先删这些 guide 的旧行，再按当前规则写一遍）。"""
    rows = published_entry_rows(db, limit=limit)
    claims = build_claims(db, rows)
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim.evidence_level] = counts.get(claim.evidence_level, 0) + 1
    report = {
        "entries": len(rows),
        "claims": len(claims),
        "by_level": counts,
        "by_kind": {kind: sum(1 for c in claims if c.claim_kind == kind) for kind in CLAIM_KINDS},
        "dry_run": not apply,
    }
    if not apply:
        return report
    created = _now()
    db.conn.execute("DELETE FROM guide_claim_evidence")
    db.conn.executemany(
        "INSERT INTO guide_claim_evidence(guide_id, step_id, target_id, claim_kind, evidence_level,"
        " grounding_tier, source_page_id, official_point_id, asset_sha256, basis_json, method_version, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [claim.as_row(created_at=created) for claim in claims],
    )
    db.conn.commit()
    report["written"] = len(claims)
    return report


def summary(db: GuideDatabase) -> dict[str, Any]:
    """表里现在有什么（按等级/类型/版本）。"""
    def group(column: str) -> dict[str, int]:
        return {
            str(row[column]): int(row["c"])
            for row in db.conn.execute(
                f"SELECT {column}, COUNT(*) AS c FROM guide_claim_evidence GROUP BY {column} ORDER BY c DESC"
            )
        }

    total = db.conn.execute("SELECT COUNT(*) AS c FROM guide_claim_evidence").fetchone()
    return {
        "total": int(total["c"]) if total is not None else 0,
        "by_level": group("evidence_level"),
        "by_kind": group("claim_kind"),
        "by_tier": group("grounding_tier"),
        "by_method": group("method_version"),
    }


#: 摘要算进去的字段：等级、落地方式、所依据的图、推断依据、方法版本。
#: 方法版本也进摘要，是为了让「换规则重建过的声明」和旧声明区分得开。
_CLAIM_COLUMNS = (
    "id, guide_id, step_id, claim_kind, evidence_level, grounding_tier, source_page_id,"
    " official_point_id, asset_sha256, basis_json, method_version"
)


def claim_rows(db: GuideDatabase, guide_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    """声明行（可按条目过滤），顺序固定——摘要必须可复现。"""
    sql = f"SELECT {_CLAIM_COLUMNS} FROM guide_claim_evidence"
    params: tuple[Any, ...] = ()
    if guide_ids is not None:
        ids = sorted({int(item) for item in guide_ids})
        if not ids:
            return []
        sql += f" WHERE guide_id IN ({','.join('?' * len(ids))})"
        params = tuple(ids)
    sql += " ORDER BY guide_id, step_id, claim_kind, id"
    return [dict(row) for row in db.conn.execute(sql, params)]


def digest_of_claims(rows: Sequence[Mapping[str, Any]]) -> str:
    """一批声明的 sha256（行顺序即语义顺序）。"""
    import hashlib

    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            f"{row.get('guide_id')}|{row.get('step_id')}|{row.get('claim_kind')}|"
            f"{row.get('evidence_level')}|{row.get('grounding_tier')}|"
            f"{row.get('asset_sha256') or ''}|{row.get('basis_json') or ''}|"
            f"{row.get('method_version') or ''}\n".encode("utf-8")
        )
    return digest.hexdigest()


def claim_digest(db: GuideDatabase) -> str:
    """声明的摘要（进 snapshot-manifest；步骤文字没变但等级退化时，diff 能看出来）。"""
    return digest_of_claims(claim_rows(db))


def digests_by_guide(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """逐条目的声明摘要（纯函数，便于和已经取出来的行共用一次查询）。"""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(int(row["guide_id"])), []).append(row)
    return {guide_id: digest_of_claims(items) for guide_id, items in sorted(grouped.items())}


def guide_digests(db: GuideDatabase, guide_ids: Iterable[int] | None = None) -> dict[str, str]:
    """逐条目的声明摘要：内容指纹没变、证据变了，manifest 也能看出来。"""
    return digests_by_guide(claim_rows(db, guide_ids))


def level_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        level = str(row.get("evidence_level") or "")
        counts[level] = counts.get(level, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Evidence 进入 publish gate（a1-8 六）
# --------------------------------------------------------------------------- #

#: gate 的问题词汇。
CLAIM_PROBLEMS = (
    #: 声明是图解法转录，却没说清是哪张图。
    "TRANSCRIPTION_ASSET_MISSING",
    #: 说了是哪张图，但那张图根本没挂在这条攻略（或它同一来源页）上。
    "TRANSCRIPTION_ASSET_NOT_ATTACHED",
    #: 交叉推断没有留下依据列表（basis_json 为空）。
    "INFERENCE_WITHOUT_BASIS",
    #: 声明没有任何可回查的来源（页 / 官方点 / 图 / 依据）。
    "CLAIM_WITHOUT_PROVENANCE",
    #: 条目已经声明过证据，却有步骤没被声明覆盖。
    "STEP_WITHOUT_CLAIM",
    #: 表里的声明和「按当前语料重算」的结果不一致：内容改过，证据没重建。
    "STALE_CLAIM",
    #: 上次发布时有的证据声明，这次整条没了。
    "EVIDENCE_REMOVED",
)

#: 直接证据：不需要任何间接手段就成立。
DIRECT_LEVELS = frozenset({"OFFICIAL", "COMMUNITY_TEXT"})
#: 间接证据：要靠图（转录）或靠推断才对得上。
INDIRECT_LEVELS = frozenset({"TRANSCRIPTION", "CROSS_INFERENCE"})


def basis_refs(value: Any) -> list[str]:
    """basis_json 里的引用列表（数组，或 {"refs"/"ids"/"evidence": [...]}）。"""
    if value is None or value == "":
        return []
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    if isinstance(payload, dict):
        for key in ("refs", "ids", "evidence", "basis"):
            item = payload.get(key)
            if isinstance(item, list):
                payload = item
                break
        else:
            return []
    if isinstance(payload, str):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def _has_provenance(row: Mapping[str, Any]) -> bool:
    """这条声明能不能回查到东西？回查不到就等于「人判断过了」。"""
    level = str(row.get("evidence_level") or "")
    if level == "OFFICIAL":
        return bool(row.get("official_point_id") or row.get("source_page_id"))
    if level == "COMMUNITY_TEXT":
        return bool(row.get("source_page_id") or row.get("official_point_id"))
    if level == "TRANSCRIPTION":
        return bool(row.get("asset_sha256"))
    if level == "CROSS_INFERENCE":
        return bool(basis_refs(row.get("basis_json")))
    return False


def attached_assets(db: GuideDatabase, guide_ids: Iterable[int] | None = None) -> dict[int, set[str]]:
    """每条条目「可援引」的图片 sha：自己挂的 + 同一来源页上其他条目挂的。

    转录写的是「哪张图」，而同一页的图是共享的（渡画泉隐那页三条条目互相引用），
    所以口径必须和 `audit.py` 一致：认整页，不是只认这条条目自己挂的那几张。
    """
    want = {int(item) for item in guide_ids} if guide_ids is not None else None
    url_of = {
        int(row["id"]): str(row["source_url"] or "")
        for row in db.conn.execute("SELECT id, IFNULL(source_url, '') AS source_url FROM guide_entry")
        if want is None or int(row["id"]) in want
    }
    own: dict[int, set[str]] = {}
    by_url: dict[str, set[str]] = {}
    for row in db.conn.execute(
        "SELECT guide_id, IFNULL(asset_sha256, '') AS sha FROM guide_assets"
    ):
        guide_id = int(row["guide_id"])
        sha = str(row["sha"] or "").lower()
        if not sha or guide_id not in url_of:
            continue
        own.setdefault(guide_id, set()).add(sha)
        by_url.setdefault(url_of[guide_id], set()).add(sha)
    return {
        guide_id: own.get(guide_id, set()) | by_url.get(url, set())
        for guide_id, url in url_of.items()
    }


def _asset_on_disk(root: Path, sha: str) -> bool:
    folder = root / sha[:2]
    return folder.is_dir() and any(path.is_file() for path in folder.glob(f"{sha}*"))


def _step_rows(db: GuideDatabase, guide_ids: Iterable[int] | None) -> dict[int, list[dict[str, Any]]]:
    sql = (
        "SELECT s.id, s.guide_id, s.step_index FROM guide_steps s"
        " JOIN guide_entry e ON e.id = s.guide_id WHERE IFNULL(e.status, '') = 'published'"
    )
    params: tuple[Any, ...] = ()
    if guide_ids is not None:
        ids = sorted({int(item) for item in guide_ids})
        if not ids:
            return {}
        sql += f" AND s.guide_id IN ({','.join('?' * len(ids))})"
        params = tuple(ids)
    sql += " ORDER BY s.guide_id, s.step_index, s.id"
    out: dict[int, list[dict[str, Any]]] = {}
    for row in db.conn.execute(sql, params):
        out.setdefault(int(row["guide_id"]), []).append(dict(row))
    return out


def evidence_gate(
    db: GuideDatabase,
    *,
    guide_ids: Iterable[int] | None = None,
    assets_root: Path | None = None,
    staleness: bool = True,
) -> dict[str, Any]:
    """证据层能不能发布（只读）。

    覆盖 a1-8 六列的四条 HARD FAIL：转录没有可查的图、交叉推断没有依据、
    必需声明没有来源、声明与内容不一致（内容改了却忘了重建）。

    只在**这条条目已经声明过证据**时才要求「每一步都有声明」：证据层是新加的一层，
    没跑过 `guides claims --backfill --apply` 的库不该被这条规则整库挡住（回到
    `adopted=False`，报告里写明）。第一版声明落库之后，漏步就再也藏不住了。
    """
    ids = sorted({int(item) for item in guide_ids}) if guide_ids is not None else None
    rows = claim_rows(db, ids)
    counts: dict[str, int] = {name: 0 for name in CLAIM_PROBLEMS}
    findings: list[dict[str, Any]] = []
    pool = attached_assets(db, ids)
    declared = {int(row["guide_id"]) for row in rows}
    root = Path(assets_root) if assets_root is not None else None

    def report(problem: str, guide_id: int, step_id: Any, detail: str) -> None:
        counts[problem] = counts.get(problem, 0) + 1
        findings.append(
            {"problem": problem, "guide_id": int(guide_id or 0), "step_id": step_id, "detail": detail}
        )

    for row in rows:
        guide_id = int(row["guide_id"])
        level = str(row.get("evidence_level") or "")
        problem = ""
        detail = ""
        if level == "TRANSCRIPTION":
            sha = str(row.get("asset_sha256") or "").lower()
            if not sha:
                problem, detail = "TRANSCRIPTION_ASSET_MISSING", "声明是转录，却没写是哪张图"
            elif not any(item.startswith(sha) for item in pool.get(guide_id, set())):
                problem, detail = "TRANSCRIPTION_ASSET_NOT_ATTACHED", sha
            elif root is not None and not _asset_on_disk(root, sha):
                problem, detail = "TRANSCRIPTION_ASSET_MISSING", f"{sha}（磁盘上找不到）"
        elif level == "CROSS_INFERENCE" and not basis_refs(row.get("basis_json")):
            problem, detail = "INFERENCE_WITHOUT_BASIS", "交叉推断没有留下依据列表"
        if not problem and not _has_provenance(row):
            problem, detail = "CLAIM_WITHOUT_PROVENANCE", level or "（没有证据等级）"
        if problem:
            report(problem, guide_id, row.get("step_id"), detail)

    claimed = {
        (int(row["guide_id"]), int(row["step_id"]))
        for row in rows
        if row.get("step_id") is not None
    }
    steps = _step_rows(db, ids)
    step_total = sum(len(items) for items in steps.values())
    for guide_id, items in sorted(steps.items()):
        if guide_id not in declared:
            continue
        missing = [int(item["id"]) for item in items if (guide_id, int(item["id"])) not in claimed]
        if missing:
            report(
                "STEP_WITHOUT_CLAIM", guide_id, missing[0],
                f"{len(missing)} 个步骤没有证据声明（先跑 guides claims --backfill --apply）",
            )

    if staleness and rows:
        wanted = [row for row in published_entry_rows(db) if ids is None or int(row["id"]) in set(ids)]
        expected = {(claim.guide_id, claim.step_id): claim for claim in build_claims(db, wanted)}
        stored = {
            (int(row["guide_id"]), row.get("step_id")): row for row in rows if row.get("step_id") is not None
        }
        for key, claim in sorted(expected.items(), key=lambda item: (item[0][0], item[0][1] or -1)):
            row = stored.get(key)
            if row is None:
                continue
            if _signature(claim) != _signature(row):
                report(
                    "STALE_CLAIM", key[0], key[1],
                    f"表里 {_signature(row)} / 重算 {_signature(claim)}",
                )
        for key in sorted(set(stored) - set(expected), key=lambda item: (item[0], item[1] or -1)):
            report("STALE_CLAIM", key[0], key[1], "步骤已经不在条目里，声明成了孤儿")

    return {
        "adopted": bool(rows),
        "claims": len(rows),
        "guides": len(declared),
        "steps": step_total,
        "by_level": level_counts(rows),
        "digest": digest_of_claims(rows),
        "assets_checked": root is not None,
        "counts": counts,
        "findings": findings,
    }


def _signature(row: Mapping[str, Any] | Claim) -> tuple[str, str, str, str]:
    if isinstance(row, Claim):
        return (row.claim_kind, row.evidence_level, row.grounding_tier, (row.asset_sha256 or "").lower())
    return (
        str(row.get("claim_kind") or ""),
        str(row.get("evidence_level") or ""),
        str(row.get("grounding_tier") or ""),
        str(row.get("asset_sha256") or "").lower(),
    )


def evidence_moves(
    current_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """逐 (条目, 步骤) 比较证据等级：越间接就是退化（a1-8 六）。

    步骤文字可能一个字没改，等级却从 COMMUNITY_TEXT 掉到 CROSS_INFERENCE——
    这正是 snapshot diff 必须当成实质退化的原因。
    """
    before = {(int(row["guide_id"]), row.get("step_id")): row for row in current_rows}
    after = {(int(row["guide_id"]), row.get("step_id")): row for row in candidate_rows}
    downgrades: list[dict[str, Any]] = []
    upgrades: list[dict[str, Any]] = []
    for key in sorted(set(before) & set(after), key=lambda item: (item[0], item[1] or -1)):
        old = str(before[key].get("evidence_level") or "")
        new = str(after[key].get("evidence_level") or "")
        if old == new:
            continue
        item = {
            "guide_id": key[0],
            "step_id": key[1],
            "claim_kind": str(after[key].get("claim_kind") or ""),
            "from": old,
            "to": new,
            "direct_to_indirect": old in DIRECT_LEVELS and new in INDIRECT_LEVELS,
        }
        if _LEVEL_RANK.get(new, 9) > _LEVEL_RANK.get(old, 9):
            downgrades.append(item)
        else:
            upgrades.append(item)
    return {"downgrades": downgrades, "upgrades": upgrades}


def evidence_losses(
    current_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """上次发布有声明、这次整条条目一个声明都不剩。"""
    have = {int(row["guide_id"]) for row in current_rows}
    left = {int(row["guide_id"]) for row in candidate_rows}
    return [
        {"problem": "EVIDENCE_REMOVED", "guide_id": guide_id, "detail": "这条条目的证据声明整批消失"}
        for guide_id in sorted(have - left)
    ]


# --------------------------------------------------------------------------- #
# Web 展示（a1-8 十三：让「为什么算完成」变成产品特征）
# --------------------------------------------------------------------------- #

def overview(db: GuideDatabase, *, topics: Iterable[str] | None = None, detail_db: Any = None) -> dict[str, Any]:
    """证据总览：完成度分层 + 声明等级 + digest。

    Atlas 以前只报一个 1006/1006，看不出这些完成是「正文引证」还是「图解转录」撑起来的。
    """
    from hsrmap.guides.stages import completeness_report

    report = completeness_report(db, topics=topics, detail_db=detail_db)
    rows = claim_rows(db)
    return {
        "completion": {
            key: report.get(key)
            for key in (
                "points",
                "done",
                "complete",
                "locate_complete",
                "solve_missing",
                "locate_missing",
                "scope_only",
                "no_evidence",
            )
        },
        "layers": report.get("evidence_layers") or evidence_layers([]),
        "claims": {
            "total": len(rows),
            "by_level": level_counts(rows),
            "digest": digest_of_claims(rows),
        },
        "lookup": report.get("lookup") or {},
    }


def entry_evidence(db: GuideDatabase, guide_id: int) -> list[dict[str, Any]]:
    """一条条目的逐步证据：等级 / 落地方式 / 来源 / 图 / 推断依据。

    左连接：**没有声明的步骤也要出现**（展示层不该把「没声明」画成「没有这一步」）。
    """
    out: list[dict[str, Any]] = []
    for row in db.conn.execute(
        "SELECT s.id AS step_id, s.step_index, IFNULL(s.text, '') AS text,"
        " c.claim_kind, c.evidence_level, c.grounding_tier, c.source_page_id,"
        " c.official_point_id, c.asset_sha256, c.basis_json, c.method_version"
        " FROM guide_steps s LEFT JOIN guide_claim_evidence c ON c.step_id = s.id"
        " WHERE s.guide_id = ? ORDER BY s.step_index, s.id",
        (int(guide_id),),
    ):
        item = dict(row)
        item["basis"] = basis_refs(item.pop("basis_json", None))
        out.append(item)
    return out


def point_evidence_view(
    db: GuideDatabase,
    *,
    entries: Sequence[Mapping[str, Any]],
    problems: Mapping[int, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """一个点位的每条攻略 + 它的证据声明（给 Web 点位详情用）。

    `problems`：审计给出的逐步问题（有 working 库时才算得出来），按 guide_id 传进来。
    """
    view: list[dict[str, Any]] = []
    for entry in entries:
        guide_id = int(entry.get("id") or 0)
        steps = []
        for claim in entry_evidence(db, guide_id):
            steps.append({
                "index": int(claim.get("step_index") or 0),
                "step_id": claim.get("step_id"),
                "text": str(claim.get("text") or ""),
                "claim_kind": claim.get("claim_kind"),
                "evidence_level": claim.get("evidence_level"),
                "grounding_tier": claim.get("grounding_tier"),
                "source_page_id": claim.get("source_page_id"),
                "official_point_id": claim.get("official_point_id"),
                "asset_sha256": claim.get("asset_sha256"),
                "basis": claim.get("basis"),
                "method_version": claim.get("method_version"),
                #: CROSS_INFERENCE 必须显式标成推断，不能在界面上和正文引证长一个样子。
                "inferred": str(claim.get("evidence_level") or "") == "CROSS_INFERENCE",
                "transcribed": str(claim.get("evidence_level") or "") == "TRANSCRIPTION",
            })
        view.append({
            "guide_id": guide_id,
            "title": str(entry.get("title") or ""),
            "source_kind": str(entry.get("source_kind") or ""),
            "source_name": str(entry.get("source_name") or ""),
            "source_url": str(entry.get("source_url") or ""),
            "author": str(entry.get("author") or ""),
            "steps": steps,
            "problems": list((problems or {}).get(guide_id) or []),
        })
    return view


def point_evidence(
    *,
    official: bool,
    solve_steps: Sequence[str],
    corpus: str = "",
    basis: Iterable[str] = (),
    guide_id: int = 0,
    official_text: str = "",
) -> dict[str, str | None]:
    """一个点位的 LOCATE / SOLVE 各是什么等级（用于完成度报告与 Web 展示）。

    * LOCATE：点位在官方地图上就是 OFFICIAL（第 1 阶段以官方为准）；
    * SOLVE：看真正提供解法的那些步骤——正文引证 < 图解转录 < 交叉推断（数字越大越「间接」）。
      没有解法步骤时：如果这一点的官方说明自己写了「怎么做」（判定层据此把它从「到点即得」
      上调成「要解法」），那么这句话**就是**解法证据，等级 OFFICIAL——否则是 None。
      两处必须一致：状态说完成、证据却说没有解法，等于自己打自己。
    """
    locate = "OFFICIAL" if official else None
    if not solve_steps:
        _has_solution, _locating = _stage_helpers()
        from hsrmap.guides.stages import official_action_text

        if official_text and official_action_text(official_text):
            return {"locate": locate, "solve": "OFFICIAL"}
        return {"locate": locate, "solve": None}
    levels = []
    for text in solve_steps:
        claim = classify_step(text, corpus=corpus, guide_id=guide_id)
        levels.append(claim.evidence_level)
    ranked = sorted(levels, key=lambda level: _LEVEL_RANK.get(level, 9))
    order = sorted(set(ranked), key=lambda level: _LEVEL_RANK.get(level, 9))
    if "CROSS_INFERENCE" in order and len(order) == 1:
        solve = "CROSS_INFERENCE"
    else:
        #: 取「最间接的那一档」：一条声明需要转录才成立，整点的 SOLVE 就算转录辅助。
        solve = ranked[-1] if ranked else None
    return {"locate": locate, "solve": solve}


def evidence_layers(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """把逐点证据汇总成四层（a1-8 五：1006/1006 不该掩盖转录与推断）。"""
    layers = {"direct": 0, "transcription": 0, "inference": 0, "missing": 0}
    for row in rows:
        if not row.get("done"):
            layers["missing"] += 1
            continue
        solve = str(row.get("solve_evidence") or "")
        if solve == "CROSS_INFERENCE":
            layers["inference"] += 1
        elif solve == "TRANSCRIPTION":
            layers["transcription"] += 1
        else:
            layers["direct"] += 1
    return layers
