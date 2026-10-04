"""仓库卫生检查（a1-8 九）：边界规则 + 基线只挡历史噪音，不挡新污染。"""

from __future__ import annotations

from pathlib import Path

from hsrmap.hygiene import BASELINE_REL, check_tree, load_baseline, write_baseline

ROOT = Path(__file__).resolve().parents[1]


def _touch(path: Path, payload: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_forbidden_rules_cover_every_boundary(tmp_path: Path) -> None:
    _touch(tmp_path / "data" / "guides" / "guide.db")
    _touch(tmp_path / "logs" / "run.log")
    _touch(tmp_path / "submit" / "hsrmap" / "cli.py")
    _touch(tmp_path / "reports" / "coverage.json")
    _touch(tmp_path / "web" / "dist" / "app.js")
    _touch(tmp_path / ".env", b"K=v")
    _touch(tmp_path / "x.bak")
    _touch(tmp_path / "old.zip", b"PK")
    _touch(tmp_path / "hsrmap" / "module.py")
    _touch(tmp_path / "tests" / "test_x.py")

    report = check_tree(tmp_path, baseline={})
    kinds = {item.path: item.rule for item in report["findings"]}

    assert kinds["data/"] == "runtime-data"
    assert kinds["logs/"] == "runtime-data"
    assert kinds["submit/"] == "release-mirror"
    assert kinds["reports/"] == "generated-reports"
    assert kinds["web/dist/"] == "web-build"
    assert kinds[".env"] == "secrets"
    assert kinds["x.bak"] == "backup"
    assert kinds["old.zip"] == "release-mirror"
    assert "hsrmap/module.py" not in kinds and "tests/test_x.py" not in kinds


def test_clean_tree_passes(tmp_path: Path) -> None:
    _touch(tmp_path / "hsrmap" / "module.py")
    _touch(tmp_path / "docs" / "readme.md")
    report = check_tree(tmp_path, baseline={})
    assert report["ok"] is True
    assert report["findings"] == []


def test_baseline_suppresses_known_and_still_reports_new(tmp_path: Path) -> None:
    _touch(tmp_path / "old.bak")
    baseline_path = tmp_path / BASELINE_REL
    first = check_tree(tmp_path, baseline={})
    write_baseline(baseline_path, first["findings"])

    report = check_tree(tmp_path, baseline=load_baseline(baseline_path))
    assert report["ok"] is True
    assert len(report["baselined"]) == 1

    _touch(tmp_path / "fresh.zip", b"PK")
    report = check_tree(tmp_path, baseline=load_baseline(baseline_path))
    assert report["ok"] is False
    assert [item.path for item in report["new"]] == ["fresh.zip"]


def test_oversized_binary_is_reported(tmp_path: Path) -> None:
    _touch(tmp_path / "phase1" / "big.png", b"0" * 4096)
    report = check_tree(tmp_path, baseline={}, max_bytes=1024)
    assert [item.rule for item in report["findings"]] == ["oversized"]
    assert report["findings"][0].size == 4096


def test_repository_has_no_new_violations() -> None:
    """真实仓库对基线必须干净：加了新污染，这条会红。"""
    report = check_tree(ROOT, baseline=load_baseline(ROOT / BASELINE_REL))
    assert report["ok"], [item.path for item in report["new"]][:10]


def test_gitignore_declares_the_boundary() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for needle in ("/data/", "/logs/", "/submit/", "/web/dist/", "*.bak", "*.db", ".env", "!.env.example"):
        assert needle in text, needle
