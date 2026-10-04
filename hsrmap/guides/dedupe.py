from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from hsrmap.guide_db import GuideDatabase


def _thickness(db: GuideDatabase, guide_id: int) -> tuple[int, int, int]:
    """(has_solution, steps, assets) — how much a guide actually tells the reader.

    「厚」不等于「有用」：一条 44 步的条目如果每步都是视频时间戳（`t11-t10:3.0`），
    它就不该压过一条 4 步、每步都写着「影子出现前：右、左、左…」的条目。带解法的排前面，
    两边都没有解法（或都有）时再比条数与图片。
    """
    from hsrmap.guides.stages import has_solution_steps

    texts = [
        str(row[0])
        for row in db.conn.execute("SELECT text FROM guide_steps WHERE guide_id = ?", (guide_id,))
    ]
    assets = db.conn.execute(
        "SELECT COUNT(*) FROM guide_assets WHERE guide_id = ?", (guide_id,)
    ).fetchone()[0]
    return (1 if has_solution_steps(texts) else 0), len(texts), int(assets or 0)


def duplicate_targets(db: GuideDatabase) -> list[dict[str, Any]]:
    """Targets that more than one published guide claims, thickest first."""
    rows = db.conn.execute(
        """
        SELECT source_point_id AS target, COUNT(*) AS n
        FROM guide_entry
        WHERE IFNULL(status, '') IN ('published', 'APPROVED')
          AND IFNULL(source_point_id, '') <> ''
        GROUP BY source_point_id
        HAVING n > 1
        ORDER BY n DESC, target
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        target = str(row["target"])
        entries = [
            dict(item)
            for item in db.conn.execute(
                "SELECT id, source_point_id, title, source_url, status, created_at"
                " FROM guide_entry WHERE source_point_id = ?"
                " AND IFNULL(status, '') IN ('published', 'APPROVED') ORDER BY id",
                (target,),
            )
        ]
        for entry in entries:
            solve, steps, assets = _thickness(db, int(entry["id"]))
            entry["solve"] = solve
            entry["steps"] = steps
            entry["assets"] = assets
        # 带解法的先留下；都有（或都没有）解法时比厚度，最后按发布顺序（id 稳定）
        entries.sort(
            key=lambda item: (-item["solve"], -item["steps"], -item["assets"], int(item["id"]))
        )
        out.append({"target": target, "keep": entries[0], "drop": entries[1:]})
    return out


def dedupe_entries(
    db: GuideDatabase, *, apply: bool = False, limit: int | None = None
) -> dict[str, Any]:
    """One guide per target: the thickest stays, the rest become "superseded".

    Coverage is per target, so a second guide for the same target adds nothing to
    it — but it does add a duplicate card in the viewer and inflate every entry
    count. Superseded entries are dropped from the published snapshot by the next
    atomic publish ("copy_entry" only copies published entries).
    """
    #: 先撤「步骤全是时间戳」的假条目，再比重复：假条目自己也有竞争者时才算撤得掉，
    #: 而它一旦被撤，后面的重复判定就只剩真正的攻略（否则 44 步时间戳会靠「厚」留下来）。
    timestamp_report = retire_timestamp_entries(db, apply=apply)
    groups = duplicate_targets(db)
    if limit:
        groups = groups[: int(limit)]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    superseded: list[dict[str, Any]] = []
    for group in groups:
        for entry in group["drop"]:
            superseded.append({
                "entry_id": int(entry["id"]),
                "target": group["target"],
                "kept": int(group["keep"]["id"]),
                "steps": entry["steps"],
                "assets": entry["assets"],
                "title": entry["title"],
            })
            if apply:
                db.conn.execute(
                    "UPDATE guide_entry SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (now, int(entry["id"])),
                )
    if apply:
        db.conn.commit()
    return {
        "applied": bool(apply),
        "targets_with_duplicates": len(groups),
        "superseded": len(superseded),
        "groups": [
            {
                "target": group["target"],
                "kept": {"entry_id": int(group["keep"]["id"]), "steps": group["keep"]["steps"]},
                "dropped": [int(item["id"]) for item in group["drop"]],
            }
            for group in groups
        ][:40],
        "entries": superseded[:40],
        "timestamp_entries": timestamp_report,
    }

#: 有的页面把**视频章节时间戳**当正文抽出来（「t11-t10:3.0」「t52-t0:814.0」）：
#: 一条 44 步里 43 步是这种 token 的条目不是攻略。它留在 published 里，既是查看器里的一张
#: 假卡片，又会让「这个目标已经发布过了」挡住真正带解法的草稿（哀丽秘榭 2 个点位就这么被挡过）。
_TIMESTAMP_STEP = re.compile(r"^t?\d+(?:[-_]t?\d+)*\s*[:：]\s*\d")
_TIMESTAMP_SHARE = 0.8


def timestamp_steps(db: GuideDatabase, guide_id: int) -> tuple[int, int]:
    """(时间戳步数, 总步数)。"""
    texts = [
        str(row[0] or "")
        for row in db.conn.execute("SELECT text FROM guide_steps WHERE guide_id = ?", (guide_id,))
    ]
    hits = sum(1 for text in texts if _TIMESTAMP_STEP.match(text.strip()))
    return hits, len(texts)


def retire_timestamp_entries(db: GuideDatabase, *, apply: bool = False) -> dict[str, Any]:
    """把「步骤几乎全是时间戳」的已发布条目记成 superseded（可复核、可恢复）。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    retired: list[dict[str, Any]] = []
    for row in db.conn.execute(
        "SELECT id, source_point_id, title FROM guide_entry"
        " WHERE IFNULL(status, '') = 'published' ORDER BY id"
    ):
        guide_id = int(row["id"])
        hits, total = timestamp_steps(db, guide_id)
        if not total or hits < max(3, int(total * _TIMESTAMP_SHARE)):
            continue
        #: 这个目标只剩它一条时先留着：发布快照的覆盖闸门按「已发布键」算覆盖，
        #: 撤掉唯一的条目会报覆盖率回退（折纸小鸟 147 → 107）。等有了替代条目再撤。
        target = str(row["source_point_id"] or "")
        others = db.conn.execute(
            "SELECT COUNT(*) FROM guide_entry WHERE source_point_id = ?"
            " AND IFNULL(status, '') = 'published' AND id != ?",
            (target, guide_id),
        ).fetchone()[0]
        if not int(others or 0):
            continue
        retired.append({
            "entry_id": guide_id,
            "target": str(row["source_point_id"] or ""),
            "title": str(row["title"] or ""),
            "timestamp_steps": hits,
            "steps": total,
        })
        if apply:
            db.conn.execute(
                "UPDATE guide_entry SET status = 'superseded', updated_at = ? WHERE id = ?",
                (now, guide_id),
            )
    if apply:
        db.conn.commit()
    return {"applied": bool(apply), "retired": len(retired), "entries": retired[:40]}
