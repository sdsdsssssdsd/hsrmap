from __future__ import annotations

from pathlib import Path
from typing import Any

from hsrmap.guides.crawler.base import adapter_for


def _parse_seeds(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data: dict[str, Any] = {"queries": [], "preferred_sources": [], "urls": []}
    section = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("topic:"):
            data["topic"] = line.split(":", 1)[1].strip()
        elif line.endswith(":") and not line.startswith(" "):
            section = line[:-1].strip()
        elif line.strip().startswith("- ") and section:
            data.setdefault(section, []).append(line.strip()[2:].strip())
    return data


def _extend_unique(dst: list[str], src: list[str] | None) -> None:
    for item in src or []:
        if item and item not in dst:
            dst.append(item)


def load_seeds(path: Path | None = None, topic: str | None = None) -> dict[str, Any]:
    if path is not None:
        return _parse_seeds(path)
    if topic:
        return load_seeds_for_topic(topic)
    return _parse_seeds(Path(__file__).with_name("seeds.yaml"))


def load_seeds_for_topic(topic_key: str) -> dict[str, Any]:
    key = topic_key.replace("-", "_")
    data: dict[str, Any] = {"topic": key, "queries": [], "preferred_sources": [], "urls": []}
    try:
        from hsrmap.guides.topics.loader import get_topic

        spec = get_topic(key)
        disc = spec.get("discovery") or {}
        _extend_unique(data["queries"], disc.get("queries"))
        _extend_unique(data["preferred_sources"], disc.get("preferred_sources"))
        _extend_unique(data["urls"], disc.get("urls"))
    except KeyError:
        pass
    roots = [
        Path(__file__).parent / "topics" / key / "seeds.yaml",
        Path(__file__).parent / "topics" / "profiles" / f"{key}.seeds.yaml",
    ]
    shared = Path(__file__).with_name("seeds.yaml")
    if shared.exists() and str(_parse_seeds(shared).get("topic") or "").replace("-", "_") == key:
        roots.append(shared)
    for seed_path in roots:
        if seed_path.exists():
            extra = _parse_seeds(seed_path)
            _extend_unique(data["queries"], extra.get("queries"))
            _extend_unique(data["preferred_sources"], extra.get("preferred_sources"))
            _extend_unique(data["urls"], extra.get("urls"))
    return data


def discover_urls(seeds: dict[str, Any] | None = None) -> list[str]:
    payload = seeds or load_seeds()
    urls = list(payload.get("urls") or [])
    preferred = payload.get("preferred_sources") or []
    ordered = []
    for domain in preferred:
        ordered.extend(url for url in urls if domain in url and url not in ordered)
    ordered.extend(url for url in urls if url not in ordered)
    return ordered


def describe(url: str) -> dict[str, Any]:
    adapter = adapter_for(url)
    return {"url": url, "source": adapter.name, "adapter": adapter.adapter_name}
