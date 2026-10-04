from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """连库（不建库、不建目录）。readonly 走 sqlite 的 URI 只读模式。"""
    if readonly:
        target = path if path.is_absolute() else path.resolve()
        conn = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS guide_entry (
    id INTEGER PRIMARY KEY,
    point_stable_key TEXT,
    source_point_id TEXT NOT NULL,
    title TEXT,
    summary TEXT,
    source_name TEXT,
    source_url TEXT,
    source_kind TEXT,
    author TEXT,
    status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guide_steps (
    id INTEGER PRIMARY KEY,
    guide_id INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    text TEXT,
    FOREIGN KEY(guide_id) REFERENCES guide_entry(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS guide_assets (
    id INTEGER PRIMARY KEY,
    guide_id INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    asset_sha256 TEXT,
    FOREIGN KEY(guide_id) REFERENCES guide_entry(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS guide_source (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    domain TEXT NOT NULL,
    source_kind TEXT,
    priority INTEGER NOT NULL DEFAULT 50,
    adapter_name TEXT,
    crawl_allowed INTEGER NOT NULL DEFAULT 1,
    images_allowed INTEGER NOT NULL DEFAULT 1,
    rate_limit REAL NOT NULL DEFAULT 1.0,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS guide_page (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    cluster_id INTEGER,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT,
    author TEXT,
    published_at TEXT,
    updated_at TEXT,
    source_claim TEXT,
    original_source_url TEXT,
    raw_html_path TEXT,
    raw_text_path TEXT,
    content_sha256 TEXT,
    crawl_status TEXT NOT NULL DEFAULT 'PENDING',
    last_crawled_at TEXT,
    parser_version TEXT,
    FOREIGN KEY(source_id) REFERENCES guide_source(id)
);

CREATE TABLE IF NOT EXISTS guide_cluster (
    id INTEGER PRIMARY KEY,
    title TEXT,
    canonical_page_id INTEGER
);

CREATE TABLE IF NOT EXISTS guide_point_binding (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL,
    source_point_id TEXT NOT NULL,
    confidence REAL,
    status TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(page_id) REFERENCES guide_page(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS crawl_run (
    id INTEGER PRIMARY KEY,
    topic TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS extraction_run (
    id INTEGER PRIMARY KEY,
    page_id INTEGER,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    input_sha256 TEXT,
    output_sha256 TEXT,
    tokens INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(page_id) REFERENCES guide_page(id)
);

CREATE TABLE IF NOT EXISTS review_item (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    source_point_id TEXT,
    draft_json TEXT,
    evidence_json TEXT,
    FOREIGN KEY(page_id) REFERENCES guide_page(id)
);

--: 「查过了、公开来源确实没有」的判定（a1-6 §八）。review_item 需要 page_id 外键，
--: 而这个结论本来就没有页面可挂，所以单独一张表；账本读它来把目标从 NEEDS_SOURCE
--: 移到 NO_PUBLIC_SOURCE_FOUND，理由和查询都留在 reason / queries 里可复核。
CREATE TABLE IF NOT EXISTS source_search_verdict (
    id INTEGER PRIMARY KEY,
    topic_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    source_point_id TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    queries TEXT,
    runs INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(topic_key, target_key)
);

CREATE TABLE IF NOT EXISTS guide_topic (
    id INTEGER PRIMARY KEY,
    topic_key TEXT NOT NULL UNIQUE,
    display_name TEXT,
    guide_kind TEXT,
    scope_type TEXT,
    matcher_profile TEXT,
    layout_profile TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS guide_target (
    id INTEGER PRIMARY KEY,
    topic_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_key TEXT NOT NULL UNIQUE,
    map_id TEXT,
    label_id TEXT,
    source_point_id TEXT,
    stable_key TEXT,
    metadata_json TEXT,
    FOREIGN KEY(topic_id) REFERENCES guide_topic(id)
);

CREATE TABLE IF NOT EXISTS guide_entry_target (
    guide_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    role TEXT,
    order_index INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guide_id, target_id),
    FOREIGN KEY(guide_id) REFERENCES guide_entry(id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES guide_target(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS page_topic (
    page_id INTEGER NOT NULL,
    topic_key TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (page_id, topic_key),
    FOREIGN KEY(page_id) REFERENCES guide_page(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS guide_target_status (
    topic_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    source_point_id TEXT,
    map_id TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (topic_key, target_key)
);

CREATE TABLE IF NOT EXISTS ai_cache (
    key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT,
    output_json TEXT,
    hits INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_hit_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_cache_prompt ON ai_cache(prompt_version);

CREATE TABLE IF NOT EXISTS guide_job (
    id INTEGER PRIMARY KEY,
    job_type TEXT NOT NULL,
    topic TEXT,
    state TEXT NOT NULL,
    cursor_json TEXT,
    budget_json TEXT,
    counters_json TEXT,
    completed_json TEXT,
    failed_json TEXT,
    error TEXT,
    report_path TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_search_run (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    target_key TEXT,
    query TEXT NOT NULL,
    provider TEXT,
    status TEXT NOT NULL DEFAULT 'OK',
    reason TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS source_search_result (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    topic TEXT,
    target_key TEXT,
    query TEXT,
    rank INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL,
    canonical_url TEXT,
    family TEXT,
    host TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES source_search_run(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_search_run_target ON source_search_run(topic, target_key);
CREATE INDEX IF NOT EXISTS idx_search_result_decision ON source_search_result(decision);
CREATE INDEX IF NOT EXISTS idx_search_result_target ON source_search_result(topic, target_key);

CREATE TABLE IF NOT EXISTS guide_asset_cache (
    source_url TEXT PRIMARY KEY,
    final_url TEXT,
    sha256 TEXT,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    byte_size INTEGER,
    format TEXT,
    status TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    phash TEXT,
    downloaded_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# 版本化迁移（a1-8 十一）
# --------------------------------------------------------------------------- #
#
# 以前是「缺列就 ALTER」，项目小的时候够用，现在不够：没有版本、没有校验和、
# 没法知道某个库到底停在哪一步。现在每次写入型打开都会：
#
#   current version → 按序 apply → 同一事务 → 写入 schema_migration(version, name, checksum, applied_at) → verify
#
# 每条迁移都必须**幂等**（用 IF NOT EXISTS / 先查 PRAGMA 再 ALTER），
# 这样「当年靠隐式 ALTER 建起来的老库」和「新建的库」会收敛到同一个版本号。
# 只读打开（viewer 的 published 库、审计、报告）永远不迁移：
# 发布库应当是生成时就迁好的不可变产物。


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Any  # Callable[[sqlite3.Connection], None]


def _add_columns(conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """给表补列（幂等）。"""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _migration_checksum(migration: Migration) -> str:
    """迁移的校验和：内容变了（版本号没变）就能被发现。"""
    import inspect

    try:
        source = inspect.getsource(migration.apply)
    except (OSError, TypeError):  # pragma: no cover - 交互式定义时拿不到源码
        source = f"{migration.version}:{migration.name}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def _m001_initial(conn: sqlite3.Connection) -> None:
    """基线：SCHEMA 里的 CREATE TABLE IF NOT EXISTS 已经建好（老库同构）。"""


def _m002_review_signature(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "review_item", (
        ("source_point_id", "TEXT"), ("draft_json", "TEXT"), ("evidence_json", "TEXT"), ("signature", "TEXT"),
    ))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_review_item_signature ON review_item(signature)")


def _m003_page_qa(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "guide_page", (
        ("qa_status", "TEXT"), ("qa_reason", "TEXT"), ("qa_checked_at", "TEXT"),
        ("qa_override_by", "TEXT"), ("qa_override_reason", "TEXT"), ("qa_override_at", "TEXT"),
    ))


def _m004_extraction_metrics(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "guide_asset_cache", (("phash", "TEXT"),))
    _add_columns(conn, "extraction_run", (
        ("input_tokens", "INTEGER"), ("output_tokens", "INTEGER"), ("image_count", "INTEGER"),
        ("latency_ms", "INTEGER"), ("cost", "REAL"),
    ))


#: 证据声明表（a1-8 五）：证据等级属于**声明**，不属于整篇攻略。
CLAIM_EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS guide_claim_evidence (
    id INTEGER PRIMARY KEY,
    guide_id INTEGER NOT NULL,
    step_id INTEGER,
    target_id INTEGER,
    claim_kind TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    grounding_tier TEXT NOT NULL,
    source_page_id INTEGER,
    official_point_id TEXT,
    asset_sha256 TEXT,
    basis_json TEXT,
    method_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(guide_id) REFERENCES guide_entry(id) ON DELETE CASCADE
);
"""


def _m005_claim_evidence(conn: sqlite3.Connection) -> None:
    conn.executescript(CLAIM_EVIDENCE_SCHEMA)


def _m006_evidence_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claim_guide ON guide_claim_evidence(guide_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claim_kind_level ON guide_claim_evidence(claim_kind, evidence_level)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_status_point ON guide_entry(status, source_point_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_steps_guide ON guide_steps(guide_id, step_index)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_guide ON guide_assets(guide_id, step_index)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_target_target ON guide_entry_target(target_id, guide_id)")


#: 逐页图片清单的派生缓存（a1-8 十二的性能问题在审核队列上又出现了一次）：
#: `page_images` 要读并解析 extracted/<page>.json 与 derived/<page>/image-roles.json，
#: 2551 条队列 = 每次请求 ~7 秒。图片清单是从这两个文件派生出来的，所以缓存必须带指纹：
#: 文件变了（重新抽取）指纹就变，缓存自然失效，不会拿旧图骗人。
PAGE_IMAGE_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS page_image_cache (
    page_id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    images_json TEXT NOT NULL,
    cached_at TEXT NOT NULL
);
"""


def _m007_page_image_cache(conn: sqlite3.Connection) -> None:
    conn.executescript(PAGE_IMAGE_CACHE_SCHEMA)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial", _m001_initial),
    Migration(2, "review_signature", _m002_review_signature),
    Migration(3, "page_qa", _m003_page_qa),
    Migration(4, "extraction_metrics", _m004_extraction_metrics),
    Migration(5, "claim_evidence", _m005_claim_evidence),
    Migration(6, "evidence_indexes", _m006_evidence_indexes),
    Migration(7, "page_image_cache", _m007_page_image_cache),
)

SCHEMA_MIGRATION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


#: 打开方式（a1-8 四.2）。只有 CREATE 允许 mkdir / 建库 / 建表 / 迁移。
DB_READ_ONLY = "readonly"
DB_READ_WRITE = "readwrite"
DB_CREATE = "create"
DB_MODES = (DB_READ_ONLY, DB_READ_WRITE, DB_CREATE)


class GuideDatabase:
    """攻略语料库。

    打开方式是显式的（a1-8 四.2）：

    * open_readonly(path)：只读（viewer 的 published 库、审计、报告）；
    * open_readwrite(path)：可写，但**不**建库、不建目录、不迁移；
    * create(path)：建目录 + 建库 + 建表 + 迁移——唯一允许产生文件系统副作用的入口。

    GuideDatabase(path) 保留为 create 的兼容写法（老调用点与测试仍能用），
    新代码请用三个显式入口；db.mode 记录这次是哪种方式打开的。
    """

    def __init__(self, path: Path, *, mode: str = DB_CREATE):
        if mode not in DB_MODES:
            raise ValueError(f"unknown db mode: {mode!r} (expected one of {DB_MODES})")
        self.path = Path(path)
        self.mode = mode
        #: 这次打开补了哪些迁移、以及「版本没变但内容变了」的漂移记录（a1-8 十一）。
        self.applied_migrations: list[int] = []
        self.migration_drift: list[dict[str, Any]] = []
        if mode == DB_CREATE:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = _connect(self.path)
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()
            return
        #: 只读 / 读写都不许建库：库不存在就是错误，而不是「顺手建一个空的」。
        if not self.path.is_file():
            raise FileNotFoundError(f"{mode} 打开失败：数据库不存在 {self.path}")
        self.conn = _connect(self.path, readonly=mode == DB_READ_ONLY)
        if mode == DB_READ_WRITE and self._is_guide_db():
            #: 写入型打开会把迁移补到最新；只读打开永不迁移——发布库应当是生成时就迁好的产物。
            self._migrate()
            self.conn.commit()

    def _is_guide_db(self) -> bool:
        """这个文件是不是攻略语料库（而不是别的 sqlite）？"""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'metadata'"
        ).fetchone()
        return row is not None

    def schema_version(self) -> int:
        """当前 schema 版本（没有版本表就是 0）。"""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
        ).fetchone()
        if row is None:
            return 0
        current = self.conn.execute("SELECT MAX(version) AS v FROM schema_migration").fetchone()
        return int(current["v"] or 0) if current is not None else 0

    @classmethod
    def open_readonly(cls, path: Path) -> "GuideDatabase":
        return cls(path, mode=DB_READ_ONLY)

    @classmethod
    def open_readwrite(cls, path: Path) -> "GuideDatabase":
        return cls(path, mode=DB_READ_WRITE)

    @classmethod
    def create(cls, path: Path) -> "GuideDatabase":
        return cls(path, mode=DB_CREATE)

    def _migrate(self) -> None:
        """按版本补齐迁移（幂等），并把 (version, name, checksum, applied_at) 记进版本表。"""
        conn = self.conn
        conn.executescript(SCHEMA_MIGRATION_TABLE)
        applied = {
            int(row["version"]): str(row["checksum"])
            for row in conn.execute("SELECT version, checksum FROM schema_migration")
        }
        for migration in MIGRATIONS:
            checksum = _migration_checksum(migration)
            if migration.version in applied:
                if applied[migration.version] != checksum:
                    #: 版本号没变但内容变了：记录下来（不阻断），让报告能发现历史漂移。
                    self.migration_drift.append({
                        "version": migration.version,
                        "name": migration.name,
                        "recorded": applied[migration.version],
                        "current": checksum,
                    })
                continue
            migration.apply(conn)
            conn.execute(
                "INSERT INTO schema_migration(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, checksum, _now()),
            )
            self.applied_migrations.append(migration.version)

    def close(self) -> None:
        self.conn.close()

    def create_entry(self, body: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        cur = self.conn.execute(
            """
            INSERT INTO guide_entry(
                point_stable_key, source_point_id, title, summary,
                source_name, source_url, source_kind, author, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.get("point_stable_key"),
                str(body["source_point_id"]),
                body.get("title") or "",
                body.get("summary"),
                body.get("source_name"),
                body.get("source_url"),
                body.get("source_kind") or "Local",
                body.get("author"),
                body.get("status") or "draft",
                now,
                now,
            ),
        )
        guide_id = int(cur.lastrowid)
        for index, step in enumerate(body.get("steps") or []):
            self.conn.execute(
                "INSERT INTO guide_steps(guide_id, step_index, text) VALUES (?, ?, ?)",
                (guide_id, index, step.get("text") or ""),
            )
            for image in step.get("images") or []:
                self.conn.execute(
                    "INSERT INTO guide_assets(guide_id, step_index, asset_sha256) VALUES (?, ?, ?)",
                    (guide_id, index, image),
                )
        self.conn.commit()
        return self.get_entry(guide_id)

    def get_entry(self, guide_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM guide_entry WHERE id = ?", (guide_id,)).fetchone()
        if row is None:
            raise KeyError(guide_id)
        return self._entry(row)

    def upsert_source(self, body: dict[str, Any]) -> dict[str, Any]:
        existing = self.conn.execute("SELECT * FROM guide_source WHERE name = ?", (body["name"],)).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE guide_source SET domain=?, source_kind=?, priority=?, adapter_name=?,
                    crawl_allowed=?, images_allowed=?, rate_limit=?, enabled=?
                WHERE id=?
                """,
                (
                    body.get("domain") or existing["domain"],
                    body.get("source_kind") or existing["source_kind"],
                    int(body.get("priority", existing["priority"])),
                    body.get("adapter_name") or existing["adapter_name"],
                    1 if body.get("crawl_allowed", True) else 0,
                    1 if body.get("images_allowed", True) else 0,
                    float(body.get("rate_limit", existing["rate_limit"])),
                    1 if body.get("enabled", True) else 0,
                    existing["id"],
                ),
            )
            self.conn.commit()
            return dict(self.conn.execute("SELECT * FROM guide_source WHERE id = ?", (existing["id"],)).fetchone())
        cur = self.conn.execute(
            """
            INSERT INTO guide_source(name, domain, source_kind, priority, adapter_name, crawl_allowed, images_allowed, rate_limit, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body["name"],
                body["domain"],
                body.get("source_kind"),
                int(body.get("priority") or 50),
                body.get("adapter_name"),
                1 if body.get("crawl_allowed", True) else 0,
                1 if body.get("images_allowed", True) else 0,
                float(body.get("rate_limit") or 1.0),
                1 if body.get("enabled", True) else 0,
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM guide_source WHERE id = ?", (cur.lastrowid,)).fetchone())

    def list_sources(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM guide_source ORDER BY priority, id")]

    def add_page(self, source_id: int, body: dict[str, Any]) -> dict[str, Any]:
        existing = self.conn.execute("SELECT * FROM guide_page WHERE canonical_url = ?", (body["canonical_url"],)).fetchone()
        if existing:
            incoming_author = body.get("author")
            if not incoming_author or str(incoming_author).strip().lower() == "unknown":
                incoming_author = existing["author"]
            self.conn.execute(
                """
                UPDATE guide_page SET title=?, author=?, source_claim=?, published_at=?, content_sha256=?
                WHERE id=?
                """,
                (
                    body.get("title") or existing["title"],
                    incoming_author,
                    body.get("source_claim") or existing["source_claim"],
                    body.get("published_at") or existing["published_at"],
                    body.get("content_sha256") or existing["content_sha256"],
                    existing["id"],
                ),
            )
            self.conn.commit()
            return dict(self.conn.execute("SELECT * FROM guide_page WHERE id = ?", (existing["id"],)).fetchone())
        cur = self.conn.execute(
            """
            INSERT INTO guide_page(
                source_id, canonical_url, title, author, published_at, updated_at,
                source_claim, original_source_url, crawl_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                body["canonical_url"],
                body.get("title"),
                body.get("author"),
                body.get("published_at"),
                body.get("updated_at"),
                body.get("source_claim"),
                body.get("original_source_url"),
                body.get("crawl_status") or "PENDING",
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM guide_page WHERE id = ?", (cur.lastrowid,)).fetchone())

    def bind_point(self, page_id: int, body: dict[str, Any]) -> dict[str, Any]:
        cur = self.conn.execute(
            """
            INSERT INTO guide_point_binding(page_id, source_point_id, confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (page_id, str(body["source_point_id"]), body.get("confidence"), body.get("status") or "pending", _now()),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM guide_point_binding WHERE id = ?", (cur.lastrowid,)).fetchone())

    def upsert_topic(self, body: dict[str, Any]) -> dict[str, Any]:
        existing = self.conn.execute("SELECT * FROM guide_topic WHERE topic_key = ?", (body["topic_key"],)).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE guide_topic SET display_name=?, guide_kind=?, scope_type=?, matcher_profile=?,
                    layout_profile=?, priority=?, enabled=? WHERE id=?
                """,
                (
                    body.get("display_name"),
                    body.get("guide_kind"),
                    body.get("scope_type"),
                    body.get("matcher_profile"),
                    body.get("layout_profile"),
                    int(body.get("priority") or 0),
                    1 if body.get("enabled", True) else 0,
                    existing["id"],
                ),
            )
            self.conn.commit()
            return dict(self.conn.execute("SELECT * FROM guide_topic WHERE id = ?", (existing["id"],)).fetchone())
        cur = self.conn.execute(
            """
            INSERT INTO guide_topic(topic_key, display_name, guide_kind, scope_type, matcher_profile, layout_profile, priority, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body["topic_key"],
                body.get("display_name"),
                body.get("guide_kind"),
                body.get("scope_type"),
                body.get("matcher_profile"),
                body.get("layout_profile"),
                int(body.get("priority") or 0),
                1 if body.get("enabled", True) else 0,
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM guide_topic WHERE id = ?", (cur.lastrowid,)).fetchone())

    def upsert_target(self, body: dict[str, Any]) -> dict[str, Any]:
        existing = self.conn.execute("SELECT * FROM guide_target WHERE target_key = ?", (body["target_key"],)).fetchone()
        if existing:
            return dict(existing)
        cur = self.conn.execute(
            """
            INSERT INTO guide_target(topic_id, target_type, target_key, map_id, label_id, source_point_id, stable_key, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body["topic_id"],
                body["target_type"],
                body["target_key"],
                body.get("map_id"),
                body.get("label_id"),
                body.get("source_point_id"),
                body.get("stable_key"),
                body.get("metadata_json"),
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM guide_target WHERE id = ?", (cur.lastrowid,)).fetchone())

    def bind_page_topic(self, page_id: int, topic_key: str, confidence: float = 0.0) -> None:
        key = str(topic_key).replace("-", "_")
        self.conn.execute(
            """
            INSERT INTO page_topic(page_id, topic_key, confidence)
            VALUES (?, ?, ?)
            ON CONFLICT(page_id, topic_key) DO UPDATE SET confidence=excluded.confidence
            """,
            (int(page_id), key, float(confidence or 0)),
        )
        self.conn.commit()

    def topics_for_page(self, page_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT page_id, topic_key, confidence FROM page_topic WHERE page_id = ? ORDER BY confidence DESC, topic_key",
            (int(page_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def bind_entry_target(self, guide_id: int, target_id: int, *, role: str = "primary", order_index: int = 0) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO guide_entry_target(guide_id, target_id, role, order_index)
            VALUES (?, ?, ?, ?)
            """,
            (guide_id, target_id, role, order_index),
        )
        self.conn.commit()

    def targets_for_entry(self, guide_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT t.* FROM guide_target t
            JOIN guide_entry_target et ON et.target_id = t.id
            WHERE et.guide_id = ?
            ORDER BY et.order_index, t.id
            """,
            (guide_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def entries_for_target(self, target_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT e.* FROM guide_entry e
            JOIN guide_entry_target et ON et.guide_id = e.id
            WHERE et.target_id = ?
            ORDER BY e.id
            """,
            (target_id,),
        ).fetchall()
        return self._entries(rows)

    def migrate_point_entries_to_targets(self, topic_key: str = "floating_grease") -> int:
        topic = self.upsert_topic({"topic_key": topic_key, "scope_type": "POINT", "guide_kind": "PUZZLE"})
        count = 0
        for row in self.conn.execute("SELECT id, source_point_id FROM guide_entry"):
            pid = str(row["source_point_id"] or "")
            if not pid:
                continue
            target = self.upsert_target(
                {
                    "topic_id": topic["id"],
                    "target_type": "POINT",
                    "target_key": f"point:{pid}",
                    "source_point_id": pid,
                }
            )
            self.bind_entry_target(int(row["id"]), int(target["id"]))
            count += 1
        return count

    def upsert_cluster(self, body: dict[str, Any]) -> dict[str, Any]:
        cur = self.conn.execute(
            "INSERT INTO guide_cluster(title, canonical_page_id) VALUES (?, ?)",
            (body.get("title"), body.get("canonical_page_id")),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM guide_cluster WHERE id = ?", (cur.lastrowid,)).fetchone())

    def attach_page_to_cluster(self, page_id: int, cluster_id: int) -> None:
        self.conn.execute("UPDATE guide_page SET cluster_id = ? WHERE id = ?", (cluster_id, page_id))
        self.conn.commit()

    def page_cluster(self, page_id: int) -> int | None:
        row = self.conn.execute("SELECT cluster_id FROM guide_page WHERE id = ?", (page_id,)).fetchone()
        return None if row is None else row["cluster_id"]

    def list_for_point(self, source_point_id: str) -> list[dict[str, Any]]:
        return self.list_for_keys([str(source_point_id)])

    def entries_by_ids(self, guide_ids: list[int]) -> list[dict[str, Any]]:
        """按 id 取条目（children 批量加载）；顺序按 id 倒序，和 EntryIndex.matching 一致。"""
        ids = sorted({int(item) for item in guide_ids}, reverse=True)
        if not ids:
            return []
        rows: list[Any] = []
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows.extend(
                self.conn.execute(
                    f"SELECT * FROM guide_entry WHERE id IN ({placeholders})", chunk
                ).fetchall()
            )
        rows.sort(key=lambda row: int(row["id"]), reverse=True)
        return self._entries(rows)

    def list_for_keys(self, keys: list[str]) -> list[dict[str, Any]]:
        wanted = [str(key) for key in keys if str(key or "").strip()]
        if not wanted:
            return []
        placeholders = ",".join("?" * len(wanted))
        rows = self.conn.execute(
            f"SELECT * FROM guide_entry WHERE source_point_id IN ({placeholders}) ORDER BY id",
            wanted,
        ).fetchall()
        return self._entries(rows)

    def _child_rows(
        self, guide_ids: list[int]
    ) -> tuple[dict[int, list[Any]], dict[int, list[Any]]]:
        """一次取回多个条目的 steps / assets（a1-8 十二：消灭逐条目 N+1）。

        分块是为了不撞 SQLite 的变量上限（默认 999）；块大小固定，所以查询条数
        只跟「取多少个条目」有关，跟点位循环无关。
        """
        steps: dict[int, list[Any]] = {}
        assets: dict[int, list[Any]] = {}
        ids = sorted({int(item) for item in guide_ids})
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            for row in self.conn.execute(
                f"SELECT guide_id, step_index, text FROM guide_steps WHERE guide_id IN ({placeholders})"
                " ORDER BY guide_id, step_index",
                chunk,
            ):
                steps.setdefault(int(row["guide_id"]), []).append(dict(row))
            for row in self.conn.execute(
                f"SELECT guide_id, step_index, asset_sha256 FROM guide_assets WHERE guide_id IN ({placeholders})"
                " ORDER BY guide_id, step_index, id",
                chunk,
            ):
                assets.setdefault(int(row["guide_id"]), []).append(dict(row))
        return steps, assets

    def _entries(self, rows: list[Any]) -> list[dict[str, Any]]:
        """把一批 guide_entry 行组装成条目（children 一次取回）。"""
        ids = [int(row["id"]) for row in rows]
        steps, assets = self._child_rows(ids)
        return [
            self._entry(
                row,
                steps=steps.get(int(row["id"]), []),
                assets=assets.get(int(row["id"]), []),
            )
            for row in rows
        ]

    def list_published_index(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT source_point_id, COUNT(*) AS n
            FROM guide_entry
            WHERE IFNULL(status, 'draft') != 'rejected'
            GROUP BY source_point_id
            """
        )
        return {row["source_point_id"]: int(row["n"]) for row in rows}

    def _entry(
        self,
        row: sqlite3.Row,
        *,
        steps: list[Any] | None = None,
        assets: list[Any] | None = None,
    ) -> dict[str, Any]:
        """条目 + 它的步骤与图片；children 可以预先批量取好（见 `_entries`）。"""
        if steps is None:
            steps = list(
                self.conn.execute(
                    "SELECT step_index, text FROM guide_steps WHERE guide_id = ? ORDER BY step_index",
                    (row["id"],),
                )
            )
        if assets is None:
            assets = list(
                self.conn.execute(
                    "SELECT step_index, asset_sha256 FROM guide_assets WHERE guide_id = ? ORDER BY step_index, id",
                    (row["id"],),
                )
            )
        by_step: dict[int, list[str]] = {}
        for asset in assets:
            by_step.setdefault(int(asset["step_index"]), []).append(asset["asset_sha256"])
        return {
            "id": int(row["id"]),
            "source_point_id": row["source_point_id"],
            "point_stable_key": row["point_stable_key"],
            "title": row["title"],
            "summary": row["summary"],
            "source_name": row["source_name"],
            "source_url": row["source_url"],
            "source_kind": row["source_kind"],
            "author": row["author"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "steps": [
                {"index": int(step["step_index"]), "text": step["text"], "images": by_step.get(int(step["step_index"]), [])}
                for step in steps
            ],
        }

@contextmanager
def count_queries(db: "GuideDatabase") -> Iterator[dict[str, Any]]:
    """数这段代码发了多少条 SQL（a1-8 十二的性能验收）。

    验收标准是「查询数不随点位数增长」，所以必须先能量出来：挂 sqlite 自己的 trace
    钩子即可，调用方一行都不用改。`statements` 留前若干条，便于看清是谁在查。
    """
    counter: dict[str, Any] = {"count": 0, "statements": []}

    def _trace(statement: str) -> None:
        counter["count"] += 1
        if len(counter["statements"]) < 20:
            counter["statements"].append(str(statement))

    db.conn.set_trace_callback(_trace)
    try:
        yield counter
    finally:
        db.conn.set_trace_callback(None)

