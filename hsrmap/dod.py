"""Definition of Done（a1-8 十六）：把「卫生化完成」变成可以反复跑的检查。

12 项里能机器判定的全部机器判定；需要真实数据的项在没有数据的 checkout 里报 `SKIPPED`，
而不是假装通过（`require_data=True` 可以把 SKIPPED 升级成失败）。

退出码沿用命令契约：0 = 全部成立（可以带 SKIPPED），2 = 有条目失败。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

#: 12 项的标题（a1-8 十六），顺序与规格一致。
DOD_ITEMS: tuple[tuple[str, str], ...] = (
    ("cli-not-silent", "python -m hsrmap.cli 不再静默成功"),
    ("tests-write-nothing", "clean checkout 跑普通测试不创建 data/"),
    ("no-implicit-sqlite", "viewer / 只读操作不会隐式创建 SQLite"),
    ("repo-has-no-runtime", "仓库没有 runtime DB / cache / submit / bak / 嵌套 zip / 生成前端产物"),
    ("phase1-reference-only", "phase1 只留需要版本管理的 reference / fixture"),
    ("release-from-pipeline", "发布包由 allowlist 生成，不是手工镜像目录"),
    ("db-schema-versioned", "所有数据库都有明确的 schema version"),
    ("no-temp-scripts", "TEMP 下的 rNN*.py 不再承担任何生产流程"),
    ("locate-solve-provenance", "每个完成点位都能给出 LOCATE / SOLVE 的 evidence provenance"),
    ("layers-visible", "1006/1006 不再掩盖 transcription / inference"),
    ("no-per-point-sql", "completeness / closure 不存在逐点 N+1"),
    ("web-answers-why", "Web 能直接回答「这条为什么算完成，依据是什么」"),
)

#: 需要真实数据（发布快照 / 官方点位）才能判定的项。
DATA_ITEMS = frozenset({
    "db-schema-versioned",
    "locate-solve-provenance",
    "layers-visible",
    "no-per-point-sql",
    "web-answers-why",
    "release-from-pipeline",
})


def _ok(item_id: str, detail: str = "") -> dict[str, Any]:
    return {"id": item_id, "status": "PASS", "detail": detail}


def _fail(item_id: str, detail: str) -> dict[str, Any]:
    return {"id": item_id, "status": "FAIL", "detail": detail}


def _skip(item_id: str, detail: str) -> dict[str, Any]:
    return {"id": item_id, "status": "SKIPPED", "detail": detail}


# --------------------------------------------------------------------------- #
# 各项检查
# --------------------------------------------------------------------------- #

def check_cli_not_silent(root: Path) -> dict[str, Any]:
    """无参数调用必须是「有声音的失败」，不能 rc=0 且什么都不打印。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "hsrmap.cli"],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode == 0 and not (proc.stdout.strip() or proc.stderr.strip()):
        return _fail("cli-not-silent", "rc=0 且没有任何输出——正是要禁止的静默成功")
    if proc.returncode == 0:
        return _fail("cli-not-silent", f"rc=0（无参数应当是用法错误）：{proc.stdout.strip()[:80]}")
    return _ok("cli-not-silent", f"rc={proc.returncode}，有输出")


def check_tests_write_nothing(root: Path) -> dict[str, Any]:
    """普通测试把运行时根目录指向临时目录：用一个子进程证明它真的解析到外面。"""
    conftest = root / "tests" / "conftest.py"
    if not conftest.is_file() or "HSRMAP_DATA_DIR" not in conftest.read_text(encoding="utf-8"):
        return _fail("tests-write-nothing", "tests/conftest.py 没有注入临时运行时目录")
    with tempfile.TemporaryDirectory(prefix="dod-runtime-") as tmp:
        env = dict(os.environ)
        env["HSRMAP_DATA_DIR"] = tmp
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, "-c", "import hsrmap.paths as p; print(p.GUIDE_DB); print(p.DATA)"],
            cwd=root, env=env, capture_output=True, text=True, timeout=120,
        )
    if proc.returncode != 0:
        return _fail("tests-write-nothing", f"子进程失败：{proc.stderr.strip()[:120]}")
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        if not Path(line.strip()).is_absolute() or str(root) in line:
            return _fail("tests-write-nothing", f"运行时路径仍落在仓库里：{line.strip()}")
    return _ok("tests-write-nothing", "临时 HSRMAP_DATA_DIR 下解析到仓库之外")


def check_no_implicit_sqlite(root: Path) -> dict[str, Any]:
    from hsrmap.guide_db import GuideDatabase

    with tempfile.TemporaryDirectory(prefix="dod-sqlite-") as tmp:
        target = Path(tmp) / "missing.db"
        for name, opener in (("open_readonly", GuideDatabase.open_readonly),
                             ("open_readwrite", GuideDatabase.open_readwrite)):
            try:
                opener(target)
            except FileNotFoundError:
                pass
            except Exception as exc:  # noqa: BLE001 - 其它异常一样说明「没有拒绝建库」
                return _fail("no-implicit-sqlite", f"{name} 抛的是 {type(exc).__name__}：{exc}")
            else:
                return _fail("no-implicit-sqlite", f"{name} 居然打开了不存在的库")
            if target.exists():
                return _fail("no-implicit-sqlite", f"{name} 把库建出来了：{target}")
    return _ok("no-implicit-sqlite", "只读 / 读写打开缺库都报 FileNotFoundError，且不建文件")


def check_repo_has_no_runtime(root: Path) -> dict[str, Any]:
    from hsrmap.hygiene import BASELINE_REL, check_tree, load_baseline

    baseline_path = root / BASELINE_REL
    baseline = load_baseline(baseline_path) if baseline_path.is_file() else {}
    report = check_tree(root, baseline=baseline)
    if report["new"]:
        return _fail(
            "repo-has-no-runtime",
            "新增违规：" + ", ".join(item.path for item in report["new"][:6]),
        )
    ignore = (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").is_file() else ""
    #: 运行态与密钥必须被忽略；构建产物（web/dist）两条正路都行：
    #: 源码树里忽略它、或者它本来就是 release 白名单里的交付内容（发布包故意跟踪它，clone 即可运行）。
    from hsrmap.release import RELEASE_KEEP

    required = ["data/", "submit/", "artifacts/", "logs/", "reports/", "*.db", "*.bak", ".env"]
    missing = [pattern for pattern in required if pattern not in ignore]
    if "web/dist" not in RELEASE_KEEP and "web/dist/" not in ignore:
        missing.append("web/dist/")
    if missing:
        return _fail("repo-has-no-runtime", f".gitignore 缺边界规则：{missing}")
    return _ok(
        "repo-has-no-runtime",
        f"{report['scanned_files']} 文件；基线内 {len(report['baselined'])} / 新增 0；.gitignore 边界齐全",
    )


def check_phase1_reference_only(root: Path) -> dict[str, Any]:
    phase1 = root / "phase1"
    if not phase1.exists():
        return _skip("phase1-reference-only", "没有 phase1/ 目录")
    forbidden = (".db", ".sqlite", ".zip", ".bak", ".tmp", ".pyc")
    offenders = [
        str(path.relative_to(root))
        for path in phase1.rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden
    ]
    caches = [str(path.relative_to(root)) for path in phase1.rglob("__pycache__") if path.is_dir()]
    if offenders or caches:
        return _fail("phase1-reference-only", f"运行态残留：{(offenders + caches)[:6]}")
    return _ok("phase1-reference-only", f"{sum(1 for _ in phase1.rglob('*') if _.is_file())} 个文件，无运行态产物")


def check_release_from_pipeline(root: Path) -> dict[str, Any]:
    submit = root / "submit"
    if not submit.is_dir():
        return _skip("release-from-pipeline", "没有 submit/（还没跑过 release）")
    manifest_path = submit / "release-manifest.json"
    if not manifest_path.is_file():
        return _fail("release-from-pipeline", "submit/ 里没有 release-manifest.json：这是手工镜像的迹象")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("entries") or []
    listed = {
        str(item["path"] if isinstance(item, dict) else item).replace("\\", "/")
        for item in entries
    }
    actual = {
        str(path.relative_to(submit)).replace("\\", "/")
        for path in submit.rglob("*")
        if path.is_file()
    }
    extra = sorted(actual - listed - {"release-manifest.json"})
    missing = sorted(listed - actual)
    if extra or missing:
        return _fail(
            "release-from-pipeline",
            f"手工改动迹象：多出 {extra[:5]} / 缺失 {missing[:5]}",
        )
    if int(manifest.get("files") or 0) != len(listed):
        return _fail("release-from-pipeline", f"manifest 的计数 {manifest.get('files')} != 条目 {len(listed)}")
    zip_path = submit.with_name("submit.zip")
    sha_file = submit.with_name("submit.zip.sha256")
    if zip_path.exists() and not sha_file.exists():
        return _fail("release-from-pipeline", "submit.zip 没有同名 .sha256")
    return _ok(
        "release-from-pipeline",
        f"{len(actual)} 个文件与 manifest 完全一致（zip sha256 落盘：{sha_file.exists()}）",
    )


def check_db_schema_versioned(root: Path) -> dict[str, Any]:
    from hsrmap.paths import runtime_report

    report = runtime_report()
    data_root = Path(str(report.get("root")))
    if not data_root.exists():
        return _skip("db-schema-versioned", f"运行目录不存在：{data_root}")
    databases = sorted(data_root.rglob("*.db"))
    if not databases:
        return _skip("db-schema-versioned", f"{data_root} 下没有数据库")
    unversioned: list[str] = []
    versions: dict[str, str] = {}
    for path in databases:
        found = _schema_version(path)
        if found is None:
            unversioned.append(path.name)
            continue
        versions[path.name] = found
    if unversioned:
        return _fail("db-schema-versioned", f"没有 schema version：{unversioned[:6]}")
    return _ok("db-schema-versioned", "；".join(f"{name}={version}" for name, version in sorted(versions.items())))


def _schema_version(path: Path) -> str | None:
    """一个库自称的 schema 版本：迁移表 / schema_versions 表 / metadata / PRAGMA user_version。

    每种标记单独 try：旧库的 `schema_versions` 可能没有 `version` 列（那是 schema 指纹表，
    不是版本表），一种标记查不动不该把后面的 `user_version` 一起跳过。
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        try:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        except sqlite3.Error:
            return None
        if "schema_migration" in tables:
            try:
                row = conn.execute("SELECT MAX(version), COUNT(*) FROM schema_migration").fetchone()
                if row and int(row[0] or 0) > 0:
                    return f"v{int(row[0])}({int(row[1])} 条迁移)"
            except sqlite3.Error:
                pass
        if "schema_versions" in tables:
            try:
                row = conn.execute("SELECT COUNT(*), MAX(version) FROM schema_versions").fetchone()
                if row and int(row[0] or 0) > 0 and int(row[1] or 0) > 0:
                    return f"schema_versions×{int(row[0])}"
            except sqlite3.Error:
                pass
        if "metadata" in tables:
            try:
                row = conn.execute(
                    "SELECT value FROM metadata WHERE key IN ('schema', 'schema_version') LIMIT 1"
                ).fetchone()
                if row and str(row[0] or "").strip():
                    return f"metadata.schema={row[0]}"
            except sqlite3.Error:
                pass
        try:
            pragma = conn.execute("PRAGMA user_version").fetchone()
            if pragma and int(pragma[0] or 0) > 0:
                return f"user_version={int(pragma[0])}"
        except sqlite3.Error:
            pass
    finally:
        conn.close()
    return None


#: 这个检查器本身会写出「rNN」「TEMP」这两个词来描述规则，所以要把自己排除掉。
_TEMP_CHECK_EXEMPT = {"hsrmap/dod.py"}

#: 一次性脚本的真实调用形态：`\%TEMP\%\rNN*.py` 或 `rNN123.py`。
_TEMP_INVOCATION = re.compile(r"%TEMP%[\\/]?r\d*|r\d{2,5}\.py", re.IGNORECASE)


def check_no_temp_scripts(root: Path) -> dict[str, Any]:
    suspects: list[str] = []
    for base in ("hsrmap", "tools"):
        for path in (root / base).rglob("*.py"):
            rel = str(path.relative_to(root)).replace("\\", "/")
            if rel in _TEMP_CHECK_EXEMPT:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "temp" in text.lower() and _TEMP_INVOCATION.search(text):
                suspects.append(rel)
    for path in root.glob("*.bat"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if _TEMP_INVOCATION.search(text):
            suspects.append(path.name)
    if suspects:
        return _fail("no-temp-scripts", f"仍在调用临时脚本：{suspects[:6]}")
    archived = root / "artifacts" / "legacy" / "temp-scripts"
    count = sum(1 for _ in archived.glob("*.py")) if archived.is_dir() else 0
    return _ok("no-temp-scripts", f"生产代码零引用；历史脚本 {count} 个已归档在 artifacts/legacy/temp-scripts")


# --------------------------------------------------------------------------- #
# 需要数据的几项
# --------------------------------------------------------------------------- #

def _published_db():
    from hsrmap.paths import GUIDE_PUBLISHED_DB

    path = Path(GUIDE_PUBLISHED_DB)
    if not path.is_file():
        return None
    from hsrmap.guide_db import GuideDatabase

    return GuideDatabase.open_readonly(path)


def _completeness(db, *, profile: bool = False):
    from hsrmap.guides.stages import completeness_report

    return completeness_report(db, profile=profile)


def _tables(path: Path) -> set[str]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return set()
    try:
        return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def _family_of(tables: set[str]) -> tuple[str, int] | None:
    """这个库属于哪个已知 schema 家族（决定它的版本号该是多少）。"""
    if "point_details" in tables:
        from hsrmap.detail_db import SCHEMA_VERSION

        return ("detail", int(SCHEMA_VERSION))
    if {"maps", "points"} <= tables:
        from hsrmap.database import SCHEMA_VERSION

        return ("core", int(SCHEMA_VERSION))
    if "point_progress" in tables:
        from hsrmap.user_db import SCHEMA_VERSION

        return ("user", int(SCHEMA_VERSION))
    return None


def stamp_versions(root: Path | None = None) -> dict[str, Any]:
    """给「schema 认得出、但没写版本」的库补上 `PRAGMA user_version`（一次性修复）。

    已经带版本（迁移表 / schema_versions / metadata / user_version）的库不动；
    认不出家族的库**不猜**，单独列成 legacy 让人处理——给旧库编一个版本号才是真的撒谎。
    """
    from hsrmap.paths import runtime_report

    base = Path(str(runtime_report().get("root")))
    stamped: list[dict[str, Any]] = []
    legacy: list[str] = []
    for path in sorted(base.rglob("*.db")):
        if _schema_version(path) is not None:
            continue
        family = _family_of(_tables(path))
        if family is None:
            legacy.append(str(path.relative_to(base)).replace("\\", "/"))
            continue
        name, version = family
        conn = sqlite3.connect(path)
        try:
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
        finally:
            conn.close()
        stamped.append({
            "path": str(path.relative_to(base)).replace("\\", "/"),
            "family": name,
            "version": version,
        })
    return {"root": str(base), "stamped": stamped, "legacy": legacy}


def check_locate_solve_provenance(db) -> dict[str, Any]:
    report = _completeness(db)
    rows = report.get("rows") or []
    done = [row for row in rows if row.get("done")]
    if not done:
        return _skip("locate-solve-provenance", "没有已完成的点位")
    missing_locate = [row["point"] for row in done if not row.get("locate_evidence")]
    missing_solve = [
        row["point"]
        for row in done
        if row.get("requirement") == "LOCATE_AND_SOLVE" and not row.get("solve_evidence")
    ]
    if missing_locate or missing_solve:
        return _fail(
            "locate-solve-provenance",
            f"缺定位证据 {missing_locate[:5]} / 缺解法证据 {missing_solve[:5]}",
        )
    return _ok("locate-solve-provenance", f"{len(done)} 个完成点位都有 LOCATE / SOLVE 证据等级")


def check_layers_visible(db) -> dict[str, Any]:
    report = _completeness(db)
    layers = report.get("evidence_layers") or {}
    if set(layers) != {"direct", "transcription", "inference", "missing"}:
        return _fail("layers-visible", f"分层缺失：{layers}")
    if sum(int(value) for value in layers.values()) != int(report.get("points") or 0):
        return _fail("layers-visible", f"分层总和 {layers} != 点位数 {report.get('points')}")
    return _ok(
        "layers-visible",
        "direct {direct} / transcription {transcription} / inference {inference} / missing {missing}".format(**layers),
    )


def check_no_per_point_sql(db) -> dict[str, Any]:
    report = _completeness(db, profile=True)
    sql = report.get("sql") or {}
    queries = int(sql.get("queries") or 0)
    points = int(report.get("points") or 0)
    if not queries:
        return _fail("no-per-point-sql", "没有拿到查询计数")
    if queries > 12:
        return _fail("no-per-point-sql", f"{points} 个点位发了 {queries} 条 SQL：像逐点查询")
    return _ok(
        "no-per-point-sql",
        f"{points} 个点位 {queries} 条 SQL（{sql.get('elapsed_ms')} ms）——与点位数无关",
    )


def check_web_answers_why(db) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from hsrmap.viewer_app import create_map_app

    published_path = Path(str(getattr(db, "path", "")))
    client = TestClient(create_map_app(published_path=published_path))
    overview = client.get("/api/v1/guides/evidence")
    if overview.status_code != 200:
        return _fail("web-answers-why", f"/api/v1/guides/evidence -> {overview.status_code}")
    body = overview.json()
    if not body.get("available") or not body.get("layers"):
        return _fail("web-answers-why", f"证据总览没有分层：{str(body)[:120]}")
    point = client.get("/api/v1/guides/index").json().get("points") or {}
    if not point:
        return _skip("web-answers-why", "发布快照里没有点位")
    target = sorted(point)[0]
    detail = client.get(f"/api/v1/guides/evidence/{target}")
    if detail.status_code != 200:
        return _fail("web-answers-why", f"/api/v1/guides/evidence/{target} -> {detail.status_code}")
    payload = detail.json()
    levels = {
        step.get("evidence_level")
        for entry in payload.get("entries") or []
        for step in entry.get("steps") or []
        if step.get("evidence_level")
    }
    if not levels:
        return _fail("web-answers-why", f"点位 {target} 的步骤没有任何证据等级")
    return _ok(
        "web-answers-why",
        f"点位 {target}：{len(payload.get('entries') or [])} 条攻略、证据等级 {sorted(levels)}，"
        f"总览分层 {body['layers']}",
    )


def run_checks(
    root: Path | None = None,
    *,
    require_data: bool = False,
    progress: Callable[[str], None] | None = None,
    stamp: bool = False,
) -> dict[str, Any]:
    """跑完 12 项，返回逐项结果（`require_data` 把 SKIPPED 升级为 FAIL）。

    `stamp=True` 先给认得出 schema 的旧库补上 `PRAGMA user_version`（一次性修复），
    再按补齐后的状态判定。
    """
    base = Path(root or Path(__file__).resolve().parents[1])
    results: list[dict[str, Any]] = []
    stamped: dict[str, Any] | None = None
    if stamp:
        stamped = stamp_versions(base)

    def note(item_id: str) -> None:
        if progress is not None:
            progress(item_id)

    note("cli-not-silent")
    results.append(check_cli_not_silent(base))
    note("tests-write-nothing")
    results.append(check_tests_write_nothing(base))
    note("no-implicit-sqlite")
    results.append(check_no_implicit_sqlite(base))
    note("repo-has-no-runtime")
    results.append(check_repo_has_no_runtime(base))
    note("phase1-reference-only")
    results.append(check_phase1_reference_only(base))
    note("release-from-pipeline")
    results.append(check_release_from_pipeline(base))
    note("no-temp-scripts")
    results.append(check_no_temp_scripts(base))

    db = _published_db()
    if db is None:
        note("db-schema-versioned")
        results.append(_skip("db-schema-versioned", "没有发布快照"))
    else:
        note("db-schema-versioned")
        results.append(check_db_schema_versioned(base))
    for item_id, checker in (
        ("locate-solve-provenance", check_locate_solve_provenance),
        ("layers-visible", check_layers_visible),
        ("no-per-point-sql", check_no_per_point_sql),
        ("web-answers-why", check_web_answers_why),
    ):
        note(item_id)
        if db is None:
            results.append(_skip(item_id, "没有发布快照（data/guides/published.db）"))
            continue
        try:
            results.append(checker(db))
        except Exception as exc:  # noqa: BLE001 - 单项失败不该让整份验收塌掉
            results.append(_fail(item_id, f"{type(exc).__name__}: {exc}"))
    if db is not None:
        db.close()

    by_id = {item["id"]: item for item in results}
    ordered = [by_id[item_id] for item_id, _title in DOD_ITEMS if item_id in by_id]
    for item in ordered:
        item["title"] = dict(DOD_ITEMS)[item["id"]]
    if require_data:
        for item in ordered:
            if item["status"] == "SKIPPED" and item["id"] in DATA_ITEMS:
                item["status"] = "FAIL"
                item["detail"] = f"要求数据但被跳过：{item['detail']}"
    failed = [item for item in ordered if item["status"] == "FAIL"]
    skipped = [item for item in ordered if item["status"] == "SKIPPED"]
    return {
        "stamped": stamped,
        "items": ordered,
        "passed": len(ordered) - len(failed) - len(skipped),
        "failed": len(failed),
        "skipped": len(skipped),
        "result": "FAIL" if failed else ("PASS" if not skipped else "PASS (partial)"),
        "ok": not failed,
    }


def render_dod(report: dict[str, Any]) -> str:
    lines = ["Definition of Done (a1-8 十六)", ""]
    for index, item in enumerate(report["items"], start=1):
        mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIPPED": "SKIP"}[item["status"]]
        lines.append(f"{index:>2}. {item['title']:.<38} {mark}")
        if item.get("detail"):
            lines.append(f"      {item['detail']}")
    lines += [
        "",
        f"DOD RESULT ........... {report['result']}（{report['passed']} 通过 / "
        f"{report['failed']} 失败 / {report['skipped']} 跳过）",
    ]
    return "\n".join(lines) + "\n"
