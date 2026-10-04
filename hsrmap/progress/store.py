"""观察存储（a1-9 §13）：新表不动 `point_progress`，观察与「本地完成」分开存。

两张表（`user.db`，见 `hsrmap/user_db.py` 的 schema）：

```text
progress_profile       profile_id / realm / region / uid_masked(打码) / created_at
progress_observation   source_point_id / profile_id / source / semantic / completed / observed_at
```

三条纪律：

* **不存 cookie**，也不存完整 UID（只有 `uid_masked`）；
* **不碰 `point_progress`**：观察只是观察，要不要变成「本地完成」由 resolver + 用户确认决定；
* **幂等**：主键是 (点位, profile, 来源, 语义)，重复导入只更新时间与状态，不产生新行。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from hsrmap.progress.models import (
    SEMANTIC_MANUAL,
    SOURCE_LOCAL,
    ProgressObservation,
    ProgressProfile,
    now,
    profile_key,
)
from hsrmap.user_db import UserDatabase


def ensure_schema(db: UserDatabase) -> None:
    """老库补表：`UserDatabase` 打开时已经跑过 SCHEMA，这里给「先拿到连接」的调用方兜底。"""
    from hsrmap.user_db import SCHEMA

    db.conn.executescript(SCHEMA)
    db.conn.commit()


def upsert_profile(db: UserDatabase, profile: ProgressProfile) -> ProgressProfile:
    created = profile.created_at or now()
    db.conn.execute(
        "INSERT INTO progress_profile(profile_id, realm, region, uid_masked, created_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(profile_id) DO UPDATE SET realm=excluded.realm, region=excluded.region,"
        " uid_masked=excluded.uid_masked",
        (profile.profile_id, profile.realm, profile.region, profile.uid_masked, created),
    )
    db.conn.commit()
    return ProgressProfile(profile.profile_id, profile.realm, profile.region, profile.uid_masked, created)


def profile_from_role(*, realm: str, role: Mapping[str, Any], uid_field: str = "game_uid") -> ProgressProfile:
    """从 probe 拿到的角色信息造一个 profile（UID 只留打码形态）。"""
    from hsrmap.progress.cookie import mask_uid

    uid_masked = mask_uid(role.get(uid_field) or role.get("uid") or "")
    region = str(role.get("region") or "")
    return ProgressProfile(
        profile_id=profile_key(realm=realm, region=region, uid_masked=uid_masked),
        realm=realm,
        region=region,
        uid_masked=uid_masked,
    )


def upsert_observation(db: UserDatabase, observation: ProgressObservation) -> ProgressObservation:
    observed = observation.observed_at or now()
    db.conn.execute(
        "INSERT INTO progress_observation(source_point_id, profile_id, source, semantic, completed, observed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(source_point_id, profile_id, source, semantic)"
        " DO UPDATE SET completed=excluded.completed, observed_at=excluded.observed_at",
        (
            observation.source_point_id,
            observation.profile_id,
            observation.resolved_source,
            observation.semantic,
            1 if observation.completed else 0,
            observed,
        ),
    )
    db.conn.commit()
    return ProgressObservation(
        observation.source_point_id, observation.profile_id, observation.semantic,
        observation.completed, observation.resolved_source, observed,
    )


def upsert_many(db: UserDatabase, observations: Iterable[ProgressObservation]) -> int:
    count = 0
    for observation in observations:
        upsert_observation(db, observation)
        count += 1
    return count


def observations(
    db: UserDatabase,
    *,
    profile_id: str | None = None,
    semantic: str | None = None,
) -> list[ProgressObservation]:
    sql = ("SELECT source_point_id, profile_id, source, semantic, completed, observed_at"
           " FROM progress_observation")
    clauses: list[str] = []
    params: list[Any] = []
    if profile_id:
        clauses.append("profile_id = ?")
        params.append(profile_id)
    if semantic:
        clauses.append("semantic = ?")
        params.append(semantic)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY source_point_id, semantic, source"
    return [
        ProgressObservation(
            source_point_id=str(row["source_point_id"]),
            profile_id=str(row["profile_id"]),
            semantic=str(row["semantic"]),
            completed=bool(row["completed"]),
            source=str(row["source"]),
            observed_at=str(row["observed_at"]),
        )
        for row in db.conn.execute(sql, params)
    ]


def profiles(db: UserDatabase) -> list[ProgressProfile]:
    return [
        ProgressProfile(
            profile_id=str(row["profile_id"]),
            realm=str(row["realm"]),
            region=str(row["region"] or ""),
            uid_masked=str(row["uid_masked"] or ""),
            created_at=str(row["created_at"] or ""),
        )
        for row in db.conn.execute(
            "SELECT profile_id, realm, region, uid_masked, created_at FROM progress_profile ORDER BY created_at"
        )
    ]


def summary(db: UserDatabase) -> dict[str, Any]:
    """给 doctor / 报告用的小结：按语义数一数，顺便证明库里没有凭据。"""
    rows = db.conn.execute(
        "SELECT semantic, COUNT(*) AS n, SUM(completed) AS done FROM progress_observation GROUP BY semantic"
    ).fetchall()
    return {
        "profiles": len(profiles(db)),
        "observations": {str(row["semantic"]): int(row["n"]) for row in rows},
        "completed_by_semantic": {str(row["semantic"]): int(row["done"] or 0) for row in rows},
        "manual_points": len(manual_points(db)),
        "stores_cookie": False,
    }


def last_observed_at(db: UserDatabase) -> str:
    row = db.conn.execute("SELECT MAX(observed_at) AS last FROM progress_observation").fetchone()
    return str(row["last"] or "") if row is not None else ""


def ensure_available(db: UserDatabase) -> dict[str, Any]:
    """给界面的一句人话：观察表在不在、有没有内容（不建库、不改语义）。"""
    try:
        count = int(db.conn.execute("SELECT COUNT(*) AS n FROM progress_observation").fetchone()["n"])
    except Exception:  # noqa: BLE001 - 老库还没补表时如实说「没有」
        return {"tables": False, "observations": 0, "message": "观察表尚未建立：先在 CLI 里跑一次 progress probe / merge"}
    if not count:
        return {"tables": True, "observations": 0,
                "message": "还没有官方地图观察：同步是 CLI 的显式动作（Viewer 不联网）"}
    return {"tables": True, "observations": count, "message": ""}


def manual_points(db: UserDatabase) -> set[str]:
    """玩家自己勾过的点（语义 manual 且 completed）。"""
    return {
        str(row["source_point_id"])
        for row in db.conn.execute(
            "SELECT source_point_id FROM progress_observation WHERE semantic = ? AND completed = 1",
            (SEMANTIC_MANUAL,),
        )
    }


def record_manual(db: UserDatabase, point_ids: Iterable[str], *, completed: bool = True,
                  profile_id: str = "local") -> int:
    """把「本地勾选」也落成观察：这样冲突界面能同时看到 manual 与 remote 两路。"""
    rows = [
        ProgressObservation(
            source_point_id=str(point_id), profile_id=profile_id, semantic=SEMANTIC_MANUAL,
            completed=bool(completed), source=SOURCE_LOCAL,
        )
        for point_id in point_ids
        if str(point_id or "").strip()
    ]
    return upsert_many(db, rows)
