"""正式工作流（a1-8 七）：把 %TEMP% 里的一次性脚本收进有 schema、有事务、有测试的入口。

目前两条：

* `record_search_payload` —— 检索账本写入（run + results），幂等；
* `transcribe_payload` —— 图解转录发布：验证「图确实挂在这条条目上」，再走评审发布通道。

两条都遵循同一套约定：JSON 有 `schema_version`；先校验再写；默认不写盘（dry-run），
`--apply` 才落库；返回结构化结果，由 CLI 打印。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.evidence import DECISIONS, record_search

#: 检索账本载荷版本。
SEARCH_PAYLOAD_VERSION = 1
#: 图解转录载荷版本。
TRANSCRIBE_PAYLOAD_VERSION = 1


class WorkflowError(ValueError):
    """载荷不合法（缺字段、决策名不认识、图不存在…）。"""


# --------------------------------------------------------------------------- #
# A. 检索账本写入
# --------------------------------------------------------------------------- #


def _existing_run(db: GuideDatabase, *, topic: str, target_key: str, query: str, provider: str) -> dict[str, Any] | None:
    row = db.conn.execute(
        "SELECT id FROM source_search_run WHERE topic = ? AND target_key = ? AND query = ? AND provider = ?"
        " ORDER BY id DESC LIMIT 1",
        (topic, target_key, query, provider),
    ).fetchone()
    return {"id": int(row["id"])} if row is not None else None


def _existing_results(db: GuideDatabase, run_id: int) -> list[tuple[str, str]]:
    rows = db.conn.execute(
        "SELECT url, decision FROM source_search_result WHERE run_id = ? ORDER BY rank", (run_id,)
    ).fetchall()
    return [(str(row["url"]), str(row["decision"]).upper()) for row in rows]


def record_search_payload(db: GuideDatabase, payload: Mapping[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """按 JSON 写一条检索（run + results）。

    幂等：同 topic/target_key/query/provider 且候选与决策完全一致时直接返回既有 run，
    不重复制造逻辑相同的账本。
    """
    version = int(payload.get("schema_version") or 0)
    if version != SEARCH_PAYLOAD_VERSION:
        raise WorkflowError(f"schema_version 必须是 {SEARCH_PAYLOAD_VERSION}，收到 {version!r}")
    topic = str(payload.get("topic") or "").strip()
    query = str(payload.get("query") or "").strip()
    if not topic or not query:
        raise WorkflowError("topic 与 query 必填")
    target_key = str(payload.get("target_key") or "")
    provider = str(payload.get("provider") or "")
    status = str(payload.get("status") or "OK")
    reason = str(payload.get("reason") or "")
    results = [dict(item) for item in (payload.get("results") or [])]
    if not results:
        raise WorkflowError("results 至少一条（查无结果也要写成一条带理由的 IRRELEVANT）")
    for index, item in enumerate(results, start=1):
        decision = str(item.get("decision") or "").upper()
        if decision not in DECISIONS:
            raise WorkflowError(f"第 {index} 条 decision 不认识：{item.get('decision')!r}（可选 {DECISIONS}）")
        if not str(item.get("url") or "").strip():
            raise WorkflowError(f"第 {index} 条缺 url")

    existing = _existing_run(db, topic=topic, target_key=target_key, query=query, provider=provider)
    wanted = sorted((str(item["url"]), str(item.get("decision") or "").upper()) for item in results)
    if existing is not None and sorted(_existing_results(db, existing["id"])) == wanted:
        return {"ok": True, "skipped": True, "run_id": existing["id"], "result_count": len(results),
                "reason": "同 topic/target/query/provider 且候选与决策完全一致（幂等）"}
    if dry_run:
        return {"ok": True, "dry_run": True, "topic": topic, "target_key": target_key, "query": query,
                "provider": provider, "results": len(results), "existing_run": existing["id"] if existing else None}

    out = record_search(db, topic=topic, target_key=target_key, query=query, results=results,
                        provider=provider, status=status, reason=reason)
    return {"ok": True, "skipped": False, "run_id": out.get("run_id"), "result_count": out.get("result_count"),
            "topic": topic, "target_key": target_key}


# --------------------------------------------------------------------------- #
# B. 图解转录发布
# --------------------------------------------------------------------------- #


def _transcription_sha(text: str) -> str:
    import re

    match = re.match(r"^\[图解法转录\s+([0-9a-fA-F]{8,64})\]", str(text or "").strip())
    return match.group(1).lower() if match else ""


def _asset_exists(root: Path, sha_prefix: str) -> bool:
    folder = root / sha_prefix[:2]
    if not folder.is_dir():
        return False
    return any(item.name.lower().startswith(sha_prefix) for item in folder.iterdir())


def transcribe_payload(
    db: GuideDatabase,
    payload: Mapping[str, Any],
    *,
    assets_root: Path,
    dry_run: bool = False,
    official_points: Sequence[Mapping[str, Any]] | None = None,
    create_item=None,
    approve_item=None,
) -> dict[str, Any]:
    """发布一条「图解法转录」条目。

    载荷：
        {`schema_version`: 1,
         topic_key, target_key, map_name,
         page: {url}                      # 必须已经 import 过（guide_page 里有）
         steps: [{text, assets: [sha]}],  # text 以 [图解法转录 <sha>] 开头时必须带对应 asset
         summary?: str}

    校验：转录步骤声明的 sha 必须在**这条条目自己的图**里，且在磁盘上真的存在；
    随后走 create_item → approve_item（同一条评审与审计通道），不绕过任何门禁。
    """
    version = int(payload.get("schema_version") or 0)
    if version != TRANSCRIBE_PAYLOAD_VERSION:
        raise WorkflowError(f"schema_version 必须是 {TRANSCRIBE_PAYLOAD_VERSION}，收到 {version!r}")
    topic_key = str(payload.get("topic_key") or "")
    target_key = str(payload.get("target_key") or "")
    map_name = str(payload.get("map_name") or "")
    page_url = str((payload.get("page") or {}).get("url") or "")
    if not all((topic_key, target_key, map_name, page_url)):
        raise WorkflowError("topic_key / target_key / map_name / page.url 都必填")
    page = db.conn.execute("SELECT id FROM guide_page WHERE canonical_url = ? LIMIT 1", (page_url,)).fetchone()
    if page is None:
        raise WorkflowError(f"来源页还没导入：{page_url}（先 hsrmap guides import-page）")
    steps = [dict(item) for item in (payload.get("steps") or [])]
    if not steps:
        raise WorkflowError("steps 至少一条")
    for index, step in enumerate(steps, start=1):
        text = str(step.get("text") or "").strip()
        if not text:
            raise WorkflowError(f"第 {index} 步没有文字")
        sha_prefix = _transcription_sha(text)
        if not sha_prefix:
            continue
        assets = [str(item).lower() for item in (step.get("assets") or [])]
        if not any(asset.startswith(sha_prefix) for asset in assets):
            raise WorkflowError(f"第 {index} 步写着从图 {sha_prefix} 转录，但这一步没有挂这张图")
        if not _asset_exists(assets_root, sha_prefix):
            raise WorkflowError(f"第 {index} 步引用的图在磁盘上不存在：{sha_prefix}")
    if dry_run:
        return {"ok": True, "dry_run": True, "topic_key": topic_key, "target_key": target_key,
                "page_id": int(page["id"]), "steps": len(steps)}

    if create_item is None or approve_item is None:  # pragma: no cover - 由 CLI 注入
        from hsrmap.guides.review.service import approve_item as _approve
        from hsrmap.guides.review.service import create_item as _create

        create_item, approve_item = _create, _approve

    draft = {
        "schema_version": 2,
        "topic_key": topic_key,
        "target_type": "POINT_SET",
        "target_key": target_key,
        "member_points": target_key.split(":")[1].split("-") if ":" in target_key else [],
        "map_name": map_name,
        "steps": [{"text": str(step.get("text") or ""), "images": [str(a).lower() for a in (step.get("assets") or [])]}
                  for step in steps],
        "images": [{"sha256": str(a).lower()} for step in steps for a in (step.get("assets") or [])],
        "binding_method": "IMAGE_TRANSCRIPTION",
        "source_pages": [page_url],
        "summary": str(payload.get("summary") or ""),
    }
    item = create_item(db, {"page_id": int(page["id"]), "source_point_id": "", "status": "NEEDS_REVIEW", "draft": draft})
    out = approve_item(db, int(item["id"]), official_points=list(official_points or []))
    entry = out.get("entry") or {}
    return {"ok": True, "dry_run": False, "item_id": item.get("id"), "entry_id": entry.get("id"),
            "source_point_id": entry.get("source_point_id"), "status": entry.get("status")}
