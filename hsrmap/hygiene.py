"""仓库卫生检查（a1-8 九）：.gitignore 挡不住已经进树的东西，所以按规则扫树。

三类规则：

* **forbidden**：源码树里根本不该出现的路径（运行态、交付镜像、交付包、生成报告、缓存、
  构建产物、密钥、运行态 SQLite、备份/临时物）；
* **oversized**：超过阈值的二进制（生成图、数据快照），除白名单外一律报；
* **baseline**：当前已知、正在清理中的违规，登记在 tools/hygiene_baseline.json 里。
  CI 只对**新增**违规失败，这样 S3 可以把基线一条条清零，而不是被历史噪音淹没。

目录一旦命中 forbidden 规则就整棵短路（不继续往下走）：既快，也让基线记录的是目录而不是几万个文件。
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: 超过这个大小的非白名单文件算「生成物」。
DEFAULT_MAX_BYTES = 2 * 1024 * 1024

#: 扫描时永远跳过的目录（工具链自己的缓存，不是项目内容）。
SKIP_DIRS = (".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
             ".ruff_cache", ".mypy_cache", ".idea", ".vscode")

#: 基线文件默认位置（相对仓库根）。
BASELINE_REL = "tools/hygiene_baseline.json"


@dataclass(frozen=True)
class Rule:
    name: str
    patterns: tuple[str, ...]
    reason: str


FORBIDDEN_RULES: tuple[Rule, ...] = (
    Rule("runtime-data", ("data/**", "var/**", "logs/**", "artifacts/**", "cache/**"),
         "运行态目录（数据库/快照/抓取页/资产/缓存/日志）不属于源码树"),
    #: `*.zip.sha256` 也要挡：它是上一次构建的产物，混进包里就成了「包里的摘要说上一次的事」。
    Rule("release-mirror", ("submit/**", "*.zip", "*.zip.sha256", "*.7z"),
         "交付镜像与交付包（含它们的摘要）由 release 流程生成，不进源码树"),
    Rule("generated-reports", ("reports/**",), "生成的报告可复现"),
    Rule("runtime-db", ("*.db", "*.sqlite", "*.sqlite-*", "*.db-wal", "*.db-shm", "*.db-journal"),
         "运行态 SQLite（发布快照是 artifact，不是源码）"),
    Rule("secrets", (".env",), "密钥不进仓库（.env.example 只是键名清单）"),
    Rule("python-cache", ("**/__pycache__/**", "**/*.pyc", "**/*.pyo"), "Python 缓存"),
    Rule("web-build", ("web/dist/**",), "前端构建产物由 release 时的 npm ci && npm run build 生成"),
    Rule("backup", ("*.bak", "*.tmp"), "备份/临时物"),
)

#: oversized 白名单：路径前缀（相对仓库根）。
OVERSIZED_ALLOWED: tuple[str, ...] = ()


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    kind: str
    reason: str
    size: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "rule": self.rule, "kind": self.kind, "reason": self.reason, "size": self.size}


def _matches(rel: str, pattern: str) -> bool:
    """把 .gitignore 风格的少量模式匹配到相对路径上（支持 ** 前缀与目录前缀）。"""
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return rel == prefix or rel.startswith(prefix + "/")
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern[3:])
    if "/" not in pattern:
        return fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pattern)
    return fnmatch.fnmatch(rel, pattern)


def _forbidden_rule(rel: str, rules: Sequence[Rule]) -> Rule | None:
    for rule in rules:
        if any(_matches(rel, pattern) for pattern in rule.patterns):
            return rule
    return None


def load_baseline(path: Path) -> dict[str, str]:
    """基线：{相对路径: 规则名}。文件不存在就是空基线。"""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = payload.get("entries") or []
    out: dict[str, str] = {}
    for item in entries:
        rel = str(item.get("path") or "")
        if rel:
            out[rel] = str(item.get("rule") or "")
    return out


def write_baseline(path: Path, findings: Iterable[Finding], *, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    entries = [{"path": item.path, "rule": item.rule, "kind": item.kind} for item in sorted(findings, key=lambda f: f.path)]
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "max_bytes": int(max_bytes),
        "note": "当前已知的仓库卫生违规（S3 起逐条清零）。check 只对新增违规失败。",
        "entries": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return payload


def check_tree(
    root: Path,
    *,
    baseline: Mapping[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    rules: Sequence[Rule] = FORBIDDEN_RULES,
    skip_dirs: Sequence[str] = SKIP_DIRS,
) -> dict[str, Any]:
    """扫一棵树的卫生状况；返回 findings / new / baselined 三份清单。"""
    root = Path(root)
    known = dict(baseline or {})
    findings: list[Finding] = []
    scanned = 0
    for base, dirs, files in os.walk(root):
        rel_base = os.path.relpath(base, root).replace("\\", "/")
        rel_base = "" if rel_base == "." else rel_base + "/"
        keep: list[str] = []
        for name in sorted(dirs):
            if name in skip_dirs:
                continue
            rel = rel_base + name
            rule = _forbidden_rule(rel + "/", rules)
            if rule is not None:
                findings.append(Finding(rel + "/", rule.name, "forbidden", rule.reason))
                continue
            keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            rel = rel_base + name
            scanned += 1
            rule = _forbidden_rule(rel, rules)
            if rule is not None:
                findings.append(Finding(rel, rule.name, "forbidden", rule.reason))
                continue
            if max_bytes and not any(rel.startswith(prefix) for prefix in OVERSIZED_ALLOWED):
                try:
                    size = (root / rel).stat().st_size
                except OSError:
                    continue
                if size > max_bytes:
                    findings.append(Finding(rel, "oversized", "oversized",
                                            f"超过 {max_bytes // (1024 * 1024)} MB 的生成物/二进制", size))
    new = [item for item in findings if item.path not in known]
    baselined = [item for item in findings if item.path in known]
    stale = sorted(path for path in known if path not in {item.path for item in findings})
    return {
        "root": str(root),
        "scanned_files": scanned,
        "max_bytes": max_bytes,
        "findings": findings,
        "new": new,
        "baselined": baselined,
        "stale_baseline": stale,
        "ok": not new,
    }


def render_report(report: Mapping[str, Any]) -> str:
    lines = [
        "Repo hygiene",
        f"  root ............ {report['root']}",
        f"  scanned files ... {report['scanned_files']}",
        f"  findings ........ {len(report['findings'])}（基线内 {len(report['baselined'])} / 新增 {len(report['new'])}）",
    ]
    if report["new"]:
        lines.append("  NEW:")
        for item in report["new"][:20]:
            lines.append(f"    - [{item.rule}] {item.path}")
    if report.get("stale_baseline"):
        lines.append(f"  baseline 里已消失的条目 {len(report['stale_baseline'])} 条（可 --update-baseline 收紧）")
    lines.append("  RESULT .......... " + ("PASS" if report["ok"] else "FAIL（有新增违规）"))
    return "\n".join(lines)
