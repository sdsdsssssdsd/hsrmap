"""发布包构建（a1-8 八；DoD 6：发布物由 allowlist/build pipeline 生成，不是手工镜像目录）。

规则很简单，而且和仓库卫生用同一套边界：

* **收录**：源码树里的一切，减去 hygiene 的 forbidden 规则命中的东西
  （运行态 data/、交付镜像 submit/、生成报告、运行态 SQLite、缓存、密钥、备份、超大生成物）；
* **唯一例外**：web/dist —— 它是构建产物，但交付包必须自带，否则收到包的人跑不起来；
* **产物**：输出目录（默认 submit/）+ zip（默认 submit.zip）+ 目录内的 release-manifest.json
  （文件清单、字节数、生成时间、zip 的 sha256）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from hsrmap.hygiene import DEFAULT_MAX_BYTES, FORBIDDEN_RULES, SKIP_DIRS, _forbidden_rule

#: 即使命中 forbidden 也要带进交付包的路径前缀（相对仓库根）。
RELEASE_KEEP: tuple[str, ...] = ("web/dist",)

#: 永远不进交付包的文件名（构建工具自己的产物）。
RELEASE_SKIP_NAMES: tuple[str, ...] = ("release-manifest.json",)


def _kept(rel: str) -> bool:
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in RELEASE_KEEP)


def collect(root: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> list[tuple[str, Path]]:
    """按 allowlist 收集要进交付包的文件（相对路径 + 绝对路径）。"""
    root = Path(root)
    files: list[tuple[str, Path]] = []
    for base, dirs, names in os.walk(root):
        rel_base = "" if Path(base) == root else os.path.relpath(base, root).replace("\\", "/") + "/"
        keep_dirs: list[str] = []
        for name in sorted(dirs):
            rel = rel_base + name
            if name in SKIP_DIRS:
                continue
            if _forbidden_rule(rel + "/", FORBIDDEN_RULES) is not None and not _kept(rel):
                continue
            keep_dirs.append(name)
        dirs[:] = keep_dirs
        for name in sorted(names):
            if name in RELEASE_SKIP_NAMES:
                continue
            rel = rel_base + name
            if _forbidden_rule(rel, FORBIDDEN_RULES) is not None and not _kept(rel):
                continue
            path = root / rel
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if max_bytes and size > max_bytes and not _kept(rel):
                continue
            files.append((rel, path))
    files.sort(key=lambda item: item[0])
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(
    root: Path,
    out: Path,
    zip_path: Path,
    *,
    clean: bool = True,
    dry_run: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """建交付目录 + 交付包；返回报告（不打印）。"""
    root, out, zip_path = Path(root), Path(out), Path(zip_path)
    files = collect(root, max_bytes=max_bytes)
    total = sum(path.stat().st_size for _, path in files)
    if dry_run:
        return {"ok": True, "dry_run": True, "files": len(files), "bytes": total,
                "out": str(out), "zip": str(zip_path), "paths": [rel for rel, _ in files]}
    if clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    for rel, path in files:
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    #: 清单先落盘再打包：zip 里包含清单本身，所以清单里不能写 zip 自己的 sha256
    #: （那是个自我指涉的循环）。zip 的摘要写在 zip 旁边的 .sha256 文件里，也写在报告里。
    manifest = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        #: 只记目录名，不记绝对路径：清单会跟着包发给别人，构建机上的用户名/目录结构不是包的内容。
        "root_name": root.name,
        "files": len(files),
        "bytes": total,
        "entries": [{"path": rel, "bytes": (root / rel).stat().st_size} for rel, _ in files],
        "excluded_by": [rule.name for rule in FORBIDDEN_RULES],
        "kept_build_products": list(RELEASE_KEEP),
        "note": "zip 的 sha256 与字节数在 zip 同名的 .sha256 文件与构建报告里。",
    }
    manifest_path = out / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if zip_path.exists():
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in sorted([rel for rel, _ in files] + [manifest_path.name]):
            archive.write(out / rel, rel)
    digest = _sha256(zip_path)
    Path(str(zip_path) + ".sha256").write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    return {"ok": True, "dry_run": False, "files": len(files), "bytes": total,
            "zip_bytes": zip_path.stat().st_size, "zip_sha256": digest,
            "out": str(out), "zip": str(zip_path), "manifest": str(manifest_path)}


def render_report(report: dict) -> str:
    lines = [
        "Release build",
        f"  out ............. {report['out']}",
        f"  zip ............. {report['zip']}",
        f"  files ........... {report['files']}（{report['bytes'] / 1048576:.2f} MB）",
    ]
    if not report.get("dry_run"):
        lines.append(f"  zip ............. {report['zip_bytes'] / 1048576:.2f} MB  sha256 {report['zip_sha256'][:16]}…")
    lines.append("  RESULT .......... " + ("DRY-RUN" if report.get("dry_run") else "OK"))
    return "\n".join(lines)
