"""M7 整合验收（a1-8-1 §24）：一条命令把「新快照落地后该验什么」跑完。

用法：
    python tools/m7_acceptance.py                # 人类可读
    python tools/m7_acceptance.py --json         # 机器可读
    python tools/m7_acceptance.py --expect-snapshot 20261004T060003Z
    python tools/m7_acceptance.py --data-dir <另一个数据根>   # 例如拿 staging 快照做「切换前演练」

退出码：0 = 全部通过；2 = 有检查不过（发布前必须处理）；1 = 环境问题（例如库打不开）。

它**只读**：不建库、不写任何东西（除了 stdout），也不联网。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hsrmap.graph import load_edges  # noqa: E402
from hsrmap.graph_audit import audit_graph  # noqa: E402
from hsrmap.paths import DATA  # noqa: E402

#: 冻结快照在 M7 之前、之后的 sha256 都必须是这个（它记录了「字节变过但内容一致」这件事）。
FROZEN_SNAPSHOT = "20261001T105105Z"
FROZEN_SHA = "bb3b5569937a219cf48261ea792092f339eb3d52b9b86e1fac58032f5d7629aa"
CLOSURE_DIGEST = "cd08466d868cc25d"  #: 记录基线；closure-check 的实测值应等于它
EXPECTED = {"map_nodes": 923, "maps": 624, "points": 5330, "label_nodes": 1016}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return -1


def _graph_conn(core: sqlite3.Connection, data_root: Path | None = None) -> tuple[sqlite3.Connection, bool]:
    """图库连接（只读）：core 自带 `map_edges` 就用它，否则用 `<data_root>/graph/core.db`。"""
    from hsrmap.graph_nav import open_graph_connection

    return open_graph_connection(core, data_root=data_root)


def collect(expect_snapshot: str | None = None, data_dir: str | None = None) -> dict:
    checks: list[dict] = []
    #: `--data-dir` 指向另一个数据根时，连发布快照与旁挂图库也一起挪过去 ——
    #: 这样能在**指针真正切换之前**拿 staging 快照演练一遍（本轮就是这么验证的）。
    data = Path(data_dir) if data_dir else Path(DATA)
    current_path = data / "current.json"

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    if not current_path.is_file():
        return {"checks": [{"check": "current.json", "ok": False, "detail": "指针文件不存在"}],
                "ok": False, "snapshot_id": ""}
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    snapshot_id = str(payload.get("snapshot_id") or "")
    core_path = data / str(payload.get("core_db") or "")
    check("current.json 指向存在", core_path.is_file(), f"{snapshot_id} → {core_path}")
    if expect_snapshot:
        check("指针已切到预期快照", snapshot_id == expect_snapshot, f"{snapshot_id}（期望 {expect_snapshot}）")

    frozen = data / "snapshots" / FROZEN_SNAPSHOT / "core.db"
    if frozen.is_file():
        sha = _sha256(frozen)
        check("旧快照字节未被再次改写", sha == FROZEN_SHA, f"sha256 {sha[:16]}…")

    if not core_path.is_file():
        return {"checks": checks, "ok": False, "snapshot_id": snapshot_id}

    core = sqlite3.connect(f"file:{core_path}?mode=ro", uri=True)
    core.row_factory = sqlite3.Row
    try:
        counts = {name: _count(core, name) for name in ("map_nodes", "maps", "points", "label_nodes")}
        for name, want in EXPECTED.items():
            got = counts[name]
            check(f"计数 {name}", got == want, f"{got}（期望 {want}）")

        #: §24 第 7 条的字面判据：每张地图都真的抓过 map/info（有 sha256 为证）——
        #: 「深层地图有 map/info」不是「我们以为抓过」，而是「库里有指纹」。
        missing_info = int(core.execute(
            "SELECT COUNT(*) FROM maps WHERE COALESCE(map_info_sha256, '') = ''"
        ).fetchone()[0])
        check("每张地图都有 map/info（§24 #7）", missing_info == 0, f"缺 {missing_info} 张")

        graph_conn, owned = _graph_conn(core, None if data_dir is None else data)
        try:
            if graph_conn is None:
                check("图库可用", False, "core.db 没有 map_edges，旁挂库也没有")
                edges = []
            else:
                edges = load_edges(graph_conn)
                report = audit_graph(graph_conn)
                gate = report["gate"]
                check("graph gate", bool(gate["ok"]), json.dumps(gate.get("reasons") or [], ensure_ascii=False))
                check("unresolved 可导航目标", int(report["unresolved_targets_total"]) == 0,
                      str(report["unresolved_targets_total"]))
                check("孤儿可渲染地图", int(report["orphan_renderables_total"]) == 0,
                      str(report["orphan_renderables_total"]))
                canary = report.get("canary") or {}
                check("canary 943→5637→979", bool(canary.get("ok")),
                      f"target={canary.get('target_map_id')} named={canary.get('named_sample')}")
                naming = report.get("naming") or {}
                #: 注意口径：`*_without_name` 只看**树里的原始名**（map_nodes.name），
                #: 而 M7.3 补的真名写在 maps.display_name —— 树名故意不被覆盖。
                #: 判断「还有没有名字」必须用 `*_without_any_name`（display_name ∪ name ∪ 树名），
                #: 否则补完名字之后这里还会一直显示 341/142，看上去像没修好。
                checks.append({
                    "check": "名字缺口（参考值，不作为失败条件）",
                    "ok": True,
                    "detail": (
                        f"仍无名 可渲染 {naming.get('renderables_without_any_name')}"
                        f" / 跳转目标 {naming.get('jump_targets_without_any_name')}"
                        f"（display_name 条数 {naming.get('display_names')}；"
                        f"树名仍空 {naming.get('renderables_without_name')} 属预期）"
                    ),
                })
        finally:
            if owned and graph_conn is not None:
                graph_conn.close()
    finally:
        core.close()

    #: 判定层零退化（§24 的「不得回归」）：证据 digest 必须等于记录的基线。
    #: 这一条比任何计数都硬 —— 名字/边/坐标怎么变都行，判定结论不能变。
    from hsrmap.paths import GUIDE_PUBLISHED_DB

    published = (data / "guides" / "published.db") if data_dir else Path(GUIDE_PUBLISHED_DB)
    if published.is_file():
        from hsrmap.guide_db import GuideDatabase
        from hsrmap.guides.claims import claim_digest

        guide_db = GuideDatabase.open_readonly(published)
        try:
            digest = claim_digest(guide_db)
            #: 六状态闭包（硬门「不得回归」）：完成点位必须仍是 1006/1006。
            from hsrmap.guides.stages import completeness_report

            completeness = completeness_report(guide_db)
        finally:
            guide_db.close()
        check("六状态完成点位 1006/1006", int(completeness["done"]) == 1006 and int(completeness["points"]) == 1006,
              f"{completeness['done']}/{completeness['points']}"
              f"（到点即得 {completeness['locate_complete']} + 含解法 {completeness['complete']}）")
        #: closure-check 打印的是**前 16 位**（digest_of_claims 本身是 64 位十六进制），
        #: 所以基线也按前缀比 —— 把 16 位基线拿去和 64 位全量比会永远失败（这里踩过一次）。
        check("证据 digest（判定层零退化）", digest[:16] == CLOSURE_DIGEST,
              f"{digest[:16]}…（基线 {CLOSURE_DIGEST}，全量 {digest[:32]}…）")
    else:
        checks.append({"check": "证据 digest", "ok": True, "detail": f"没有发布快照（{published}），跳过"})

    by_type: dict[str, int] = {}
    for edge in edges:
        by_type[edge.edge_type] = by_type.get(edge.edge_type, 0) + 1
    checks.append({"check": "边分布（参考值）", "ok": True,
                   "detail": json.dumps(by_type, ensure_ascii=False, sort_keys=True)})

    failed = [item for item in checks if not item["ok"]]
    return {"checks": checks, "ok": not failed, "snapshot_id": snapshot_id, "failed": len(failed)}


def main() -> int:
    parser = argparse.ArgumentParser(description="M7 整合验收（只读）")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--expect-snapshot", default=None)
    parser.add_argument("--data-dir", default=None, help="另一个数据根（默认取运行态 DATA）")
    args = parser.parse_args()
    try:
        result = collect(args.expect_snapshot, args.data_dir)
    except Exception as exc:  # noqa: BLE001 - 环境问题如实报，不吐 traceback
        print(f"验收跑不起来：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"M7 整合验收（当前快照 {result['snapshot_id'] or '未知'}）")
        for item in result["checks"]:
            mark = "ok  " if item["ok"] else "FAIL"
            print(f"  [{mark}] {item['check']}：{item['detail']}")
        print("结果 .............. " + ("PASS" if result["ok"] else f"FAIL（{result['failed']} 项不过）"))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
