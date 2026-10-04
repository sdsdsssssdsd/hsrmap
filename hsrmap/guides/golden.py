from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hsrmap.guides.extract.blocks import html_to_blocks
from hsrmap.guides.llm.provider import hallucinated_steps
from hsrmap.guides.topics.loader import list_topics
from hsrmap.paths import PHASE1, ROOT

TOPICS_DIR = Path(__file__).resolve().parent / "topics"


def topic_golden_path(topic_key: str) -> Path:
    return TOPICS_DIR / topic_key / "golden.json"


def missing_topic_goldens() -> list[str]:
    missing: list[str] = []
    for spec in list_topics(enabled_only=True):
        key = spec["topic_key"]
        path = topic_golden_path(key)
        if not path.is_file():
            missing.append(key)
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            missing.append(key)
            continue
        if payload.get("ref"):
            ref = ROOT / str(payload["ref"])
            if not ref.is_file():
                missing.append(key)
            continue
        if payload.get("status") in {"NO_OFFICIAL_TARGET", "LABEL_EXISTS_NO_POINTS", "NO_PUBLIC_SOURCE_FOUND"}:
            continue
        if not isinstance(payload.get("sections"), list) or not payload["sections"]:
            missing.append(key)
    return missing


#: The four golden levels of a1-6 §31. A level is not a document word: it names
#: the pipeline stage, the test modules that prove it and the fixtures they use.
@dataclass(frozen=True)
class GoldenLevel:
    id: str
    name: str
    pipeline: str
    meaning: str
    tests: tuple[str, ...]
    fixtures: tuple[str, ...] = ()


GOLDEN_LEVELS: tuple[GoldenLevel, ...] = (
    GoldenLevel(
        "A",
        "extraction",
        "HTML -> blocks",
        "解析器把真实页面变成有序块（标题/段落/图片，广告与家具容器丢弃）",
        ("tests/test_guide_real_parse.py", "tests/test_guide_blocks.py"),
        (
            "tests/fixtures/guides/sample_17173.html",
            "tests/fixtures/guides/sample_gamersky.html",
            "tests/fixtures/guides/sample_3dm.html",
            "tests/fixtures/guides/sample_taptap.html",
        ),
    ),
    GoldenLevel(
        "B",
        "matching",
        "blocks -> target",
        "块/单元 → 官方 target（含 MAP_LABEL 合成键与页面锚点）",
        ("tests/test_guide_units.py", "tests/test_guide_match.py", "tests/test_guide_rematch.py"),
    ),
    GoldenLevel(
        "C",
        "publish",
        "approved -> published",
        "批准 → 发布库条目与排布渲染（puzzle / collection / challenge）",
        ("tests/test_guide_publish.py", "tests/test_guide_layout_render.py"),
    ),
    GoldenLevel(
        "D",
        "lookup",
        "published -> viewer",
        "发布库 → Viewer 离线查询（by-point / index / atlas）",
        ("tests/test_guide_atlas_api.py", "tests/test_guide_index.py"),
    ),
)


def golden_level_ids() -> tuple[str, ...]:
    return tuple(level.id for level in GOLDEN_LEVELS)


def _test_count(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return text.count("\ndef test_") + (1 if text.startswith("def test_") else 0)


def level_status(*, root: Path | None = None) -> list[dict[str, Any]]:
    """A/B/C/D with the evidence that each level is really tested (§31)."""
    base = Path(root) if root is not None else ROOT
    out: list[dict[str, Any]] = []
    for level in GOLDEN_LEVELS:
        tests = []
        for name in level.tests:
            path = base / name
            tests.append({"path": name, "exists": path.is_file(), "tests": _test_count(path)})
        fixtures = [
            {"path": name, "exists": (base / name).is_file()} for name in level.fixtures
        ]
        ok = bool(tests) and all(item["exists"] and item["tests"] > 0 for item in tests)
        ok = ok and all(item["exists"] for item in fixtures)
        out.append({
            "level": level.id,
            "name": level.name,
            "pipeline": level.pipeline,
            "meaning": level.meaning,
            "tests": tests,
            "fixtures": fixtures,
            "test_count": sum(item["tests"] for item in tests),
            "ok": ok,
        })
    return out


def levels_ok(*, root: Path | None = None) -> bool:
    return all(level["ok"] for level in level_status(root=root))


def load_golden(path: Path | None = None) -> dict[str, Any]:
    target = path or (PHASE1 / "calibration" / "golden_guides.json")
    return json.loads(target.read_text(encoding="utf-8"))


def load_topic_golden(topic_key: str) -> dict[str, Any]:
    path = topic_golden_path(topic_key)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("ref"):
        payload = {**load_golden(ROOT / str(payload["ref"])), "topic_key": topic_key}
    payload.setdefault("topic_key", topic_key)
    return payload


def run_topic_golden(topic_key: str, canary_dir: Path, provider, golden_path: Path | None = None) -> dict[str, Any]:
    if golden_path is not None:
        return run_golden(canary_dir, provider, golden_path, topic=topic_key)
    return run_golden(canary_dir, provider, topic_golden_path(topic_key), topic=topic_key)


def run_golden(
    canary_dir: Path,
    provider,
    golden_path: Path | None = None,
    *,
    topic: str | None = None,
) -> dict[str, Any]:
    golden = load_golden(golden_path)
    topic_key = str(topic or golden.get("topic_key") or "floating_grease")
    relevance_ok = map_ok = section_ok = step_ok = image_ok = 0
    halluc = 0
    invalid = 0
    cache: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    for item in golden["sections"]:
        name = item["source_page"]
        if name not in cache:
            html = (canary_dir / name).read_text(encoding="utf-8")
            blocks = html_to_blocks(html)
            try:
                extracted = provider.extract_sections(blocks, page_id=0, topic=topic_key)
                if not isinstance(extracted, dict) or "sections" not in extracted:
                    raise ValueError("invalid")
            except Exception:
                invalid += 1
                extracted = {"sections": []}
            cache[name] = (blocks, extracted)
        blocks, extracted = cache[name]
        try:
            classified = provider.classify_article(blocks)
            if classified.get("relevant"):
                relevance_ok += 1
        except Exception:
            invalid += 1
        halluc += len(hallucinated_steps(extracted, blocks))
        sections = extracted.get("sections") or []
        cands = [s for s in sections if item["map_name"] and item["map_name"] in (s.get("map_name") or "")]
        match = next((s for s in cands if s.get("map_name") == item["map_name"]), None) or (cands[-1] if cands else None)
        if match:
            map_ok += 1
            if item["section_start"] in (match.get("map_name") or ""):
                section_ok += 1
            if len(match.get("steps") or []) == int(item.get("step_count") or 0):
                step_ok += 1
            roles = item.get("image_roles") or {}
            if not roles:
                image_ok += 1
            else:
                by_id = {b.get("id"): b for b in blocks}
                srcs = " ".join((by_id.get(i) or {}).get("src") or "" for i in match.get("image_block_ids") or [])
                if all(filename in srcs for filename in roles):
                    image_ok += 1
        elif item.get("step_count") == 0 and not sections:
            step_ok += 1
            image_ok += 1
    total = len(golden["sections"])
    return {
        "article_relevance": {"ok": relevance_ok, "total": total},
        "map_name_extraction": {"ok": map_ok, "total": total},
        "section_segmentation": {"ok": section_ok, "total": total, "rate": section_ok / total},
        "step_segmentation": {"ok": step_ok, "total": total, "rate": step_ok / total},
        "image_assignment": {"ok": image_ok, "total": total, "rate": image_ok / total},
        "hallucinated_steps": halluc,
        "fatal_json_invalid": invalid,
    }
