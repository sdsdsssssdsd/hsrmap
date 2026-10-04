from __future__ import annotations

from typing import Any

from hsrmap.guide_db import GuideDatabase

#: 证据声明随条目一起进快照的列（**不含 id**：声明 id 没有外部引用，而 backfill 是全表
#: 重建，跨库沿用旧 id 会撞上别的条目留下的残行；顺序不变，摘要照旧可复现）。
CLAIM_COLUMNS = (
    "guide_id",
    "step_id",
    "target_id",
    "claim_kind",
    "evidence_level",
    "grounding_tier",
    "source_page_id",
    "official_point_id",
    "asset_sha256",
    "basis_json",
    "method_version",
    "created_at",
)


def _has_claims_table(db: GuideDatabase) -> bool:
    return (
        db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'guide_claim_evidence' LIMIT 1"
        ).fetchone()
        is not None
    )


def clear_claims(dest: GuideDatabase, guide_id: int) -> None:
    """清掉目标库里这条条目的声明（未发布/被删的条目也要清）。"""
    if _has_claims_table(dest):
        dest.conn.execute("DELETE FROM guide_claim_evidence WHERE guide_id = ?", (guide_id,))


def copy_claims(src: GuideDatabase, dest: GuideDatabase, guide_id: int) -> int:
    """把一条条目的证据声明一起搬进快照：发布出去的不只是文字，还有「凭什么」。

    发布库里没有声明，manifest 的 evidence digest 就是空的，等级退化也就无从对比；
    所以声明必须和 steps/assets 一样属于被同步的内容。

    必须在 `guide_entry` 落库**之后**调用：`guide_claim_evidence.guide_id` 是有外键的。
    """
    if not _has_claims_table(dest) or not _has_claims_table(src):
        return 0
    rows = src.conn.execute(
        f"SELECT {', '.join(CLAIM_COLUMNS)} FROM guide_claim_evidence WHERE guide_id = ? ORDER BY id",
        (guide_id,),
    ).fetchall()
    if not rows:
        return 0
    placeholders = ", ".join("?" * len(CLAIM_COLUMNS))
    dest.conn.executemany(
        f"INSERT INTO guide_claim_evidence({', '.join(CLAIM_COLUMNS)}) VALUES ({placeholders})",
        [tuple(row[column] for column in CLAIM_COLUMNS) for row in rows],
    )
    return len(rows)


def copy_entry(src: GuideDatabase, dest: GuideDatabase, guide_id: int) -> dict[str, Any] | None:
    row = src.conn.execute("SELECT * FROM guide_entry WHERE id = ?", (guide_id,)).fetchone()
    dest.conn.execute("DELETE FROM guide_assets WHERE guide_id = ?", (guide_id,))
    dest.conn.execute("DELETE FROM guide_steps WHERE guide_id = ?", (guide_id,))
    dest.conn.execute("DELETE FROM guide_entry_target WHERE guide_id = ?", (guide_id,))
    clear_claims(dest, guide_id)
    if row is None or row["status"] != "published":
        dest.conn.execute("DELETE FROM guide_entry WHERE id = ?", (guide_id,))
        dest.conn.commit()
        return None
    dest.conn.execute(
        """
        INSERT OR REPLACE INTO guide_entry(
            id, point_stable_key, source_point_id, title, summary,
            source_name, source_url, source_kind, author, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row["point_stable_key"],
            row["source_point_id"],
            row["title"],
            row["summary"],
            row["source_name"],
            row["source_url"],
            row["source_kind"],
            row["author"],
            row["status"],
            row["created_at"],
            row["updated_at"],
        ),
    )
    for step in src.conn.execute("SELECT * FROM guide_steps WHERE guide_id = ?", (guide_id,)):
        dest.conn.execute(
            "INSERT INTO guide_steps(id, guide_id, step_index, text) VALUES (?, ?, ?, ?)",
            (step["id"], step["guide_id"], step["step_index"], step["text"]),
        )
    for asset in src.conn.execute("SELECT * FROM guide_assets WHERE guide_id = ?", (guide_id,)):
        dest.conn.execute(
            "INSERT INTO guide_assets(id, guide_id, step_index, asset_sha256) VALUES (?, ?, ?, ?)",
            (asset["id"], asset["guide_id"], asset["step_index"], asset["asset_sha256"]),
        )
    copy_claims(src, dest, guide_id)
    dest.conn.commit()
    return dest.get_entry(guide_id)


def sync_published(working: GuideDatabase, published: GuideDatabase) -> int:
    src_ids = [int(row["id"]) for row in working.conn.execute("SELECT id FROM guide_entry WHERE status = 'published'")]
    dest_ids = {int(row["id"]) for row in published.conn.execute("SELECT id FROM guide_entry")}
    copied = 0
    for guide_id in src_ids:
        if copy_entry(working, published, guide_id):
            copied += 1
    for guide_id in dest_ids - set(src_ids):
        copy_entry(working, published, guide_id)
    return copied
