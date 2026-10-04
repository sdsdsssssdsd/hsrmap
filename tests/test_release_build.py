"""交付包构建（a1-8 八；DoD 6）：allowlist 收录，不是手工镜像。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from hsrmap.release import RELEASE_KEEP, build, collect

ROOT = Path(__file__).resolve().parents[1]


def _touch(path: Path, payload: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _fake_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _touch(root / "hsrmap" / "module.py")
    _touch(root / "tests" / "test_x.py")
    _touch(root / "pyproject.toml")
    _touch(root / "web" / "dist" / "app.js")          # 构建产物，但交付包必须带
    _touch(root / "data" / "guides" / "guide.db")     # 运行态 → 排除
    _touch(root / "submit" / "stale.py")              # 交付镜像自身 → 排除
    _touch(root / ".env", b"K=v")                     # 密钥 → 排除
    _touch(root / "old.bak")                          # 备份 → 排除
    _touch(root / "reports" / "coverage.json")        # 生成报告 → 排除
    _touch(root / "__pycache__" / "mod.cpython-311.pyc")
    return root


def test_collect_keeps_sources_and_build_products_only(tmp_path: Path) -> None:
    root = _fake_tree(tmp_path)
    paths = {rel for rel, _ in collect(root)}
    assert {"hsrmap/module.py", "tests/test_x.py", "pyproject.toml"} <= paths
    assert "web/dist/app.js" in paths, "交付包必须自带前端构建产物"
    assert not any(p.startswith(("data/", "submit/", "reports/", "__pycache__/")) for p in paths)
    assert ".env" not in paths and "old.bak" not in paths
    assert RELEASE_KEEP == ("web/dist",)


def test_build_writes_dir_zip_and_manifest(tmp_path: Path) -> None:
    root = _fake_tree(tmp_path)
    out = tmp_path / "release"
    zip_path = tmp_path / "release.zip"
    report = build(root, out, zip_path)
    assert report["ok"] and report["files"] >= 4
    assert (out / "hsrmap" / "module.py").is_file()
    assert not (out / "data").exists() and not (out / ".env").exists()
    manifest = json.loads((out / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == report["files"]
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert {"hsrmap/module.py", "web/dist/app.js", "release-manifest.json"} <= names
    assert not any(n.startswith(("data/", "submit/")) for n in names)
    #: zip 的摘要在报告与同名 .sha256 里（清单进包，所以清单里不能写自己的摘要）
    assert report["zip_sha256"]
    sidecar = Path(str(zip_path) + ".sha256").read_text(encoding="utf-8")
    assert report["zip_sha256"] in sidecar


def test_build_is_clean_by_default(tmp_path: Path) -> None:
    root = _fake_tree(tmp_path)
    out = tmp_path / "release"
    _touch(out / "stale.txt")
    build(root, out, tmp_path / "release.zip")
    assert not (out / "stale.txt").exists(), "默认 clean：交付目录里不留上一次的残留"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _fake_tree(tmp_path)
    out = tmp_path / "release"
    report = build(root, out, tmp_path / "release.zip", dry_run=True)
    assert report["dry_run"] is True
    assert not out.exists()


def test_repo_root_collection_excludes_runtime(tmp_path: Path) -> None:
    """真实仓库：data/ 与 .env 这类东西永远不进交付包。"""
    paths = {rel for rel, _ in collect(ROOT)}
    assert "hsrmap/cli.py" in paths
    assert not any(p.startswith(("data/", "logs/", "submit/", "artifacts/")) for p in paths)
    assert ".env" not in paths
