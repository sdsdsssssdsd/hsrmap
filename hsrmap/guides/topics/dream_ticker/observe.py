from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hsrmap.guides.derived import DerivedGuideStore
from hsrmap.guides.ingest import _asset_bytes, _whitelist_from_maps
from hsrmap.guides.regions.official import resolve_official_map
from hsrmap.guides.store import RawGuideStore
from hsrmap.guides.vision.generic_roles import is_bind_evidence, normalize_role
from hsrmap.guides.vision.region_reader import read_region
from hsrmap.guides.vision.roles import classify_role
from hsrmap.guides.vision.sanitize import sanitize_region
from hsrmap.paths import GUIDE_ASSETS, GUIDE_DERIVED, GUIDE_RAW


def maps_catalog(official_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    parents: set[str] = set()
    for point in official_points:
        mid = str(point.get("map_id") or "")
        name = str(point.get("map_name") or "")
        path = str(point.get("map_path") or "")
        if mid and mid not in catalog:
            catalog[mid] = {"map_id": mid, "name": name, "path": path, "renderable": True}
        for part in [part.strip() for part in path.split("/") if part.strip()]:
            if part != name:
                parents.add(part)
        region = str(point.get("region") or "")
        if region and region != name:
            parents.add(region)
    out = list(catalog.values())
    for name in sorted(parents):
        out.append({"map_id": None, "name": name, "path": name, "renderable": False})
    return out


def observe_page_images(
    page_id: int,
    blocks: list[dict[str, Any]],
    provider,
    *,
    store: RawGuideStore | None = None,
    official_points: list[dict[str, Any]] | None = None,
    derived: DerivedGuideStore | None = None,
) -> list[dict[str, Any]]:
    store = store or RawGuideStore(GUIDE_RAW, GUIDE_ASSETS)
    derived = derived or DerivedGuideStore(GUIDE_DERIVED)
    maps = maps_catalog(official_points or [])
    whitelist = _whitelist_from_maps(maps)
    nearby = " ".join(block.get("text") or "" for block in blocks if block.get("type") in {"heading", "paragraph"})
    cache_root = GUIDE_DERIVED / "_cache" / "ticker-vision"
    cache_root.mkdir(parents=True, exist_ok=True)
    observations = []
    for block in blocks:
        if block.get("type") != "image":
            continue
        sha = str(block.get("asset") or "")
        cached = _load_cache(cache_root, sha) if sha else None
        if cached:
            observations.append({**cached, "block_id": block.get("id")})
            continue
        blob = _asset_bytes(store, sha)
        prior = _prior_role(page_id, sha, block.get("id"))
        if prior and prior.get("role") not in {None, "", "unknown", "UNRELATED"}:
            role_raw = {"role": prior.get("role"), "confidence": prior.get("role_confidence") or 0.7}
        else:
            role_raw = classify_role(blob, sha256=sha, alt=block.get("alt") or "", src=block.get("src") or "", provider=provider)
        role = normalize_role(role_raw.get("role"))
        obs: dict[str, Any] = {
            "block_id": block.get("id"),
            "sha256": sha,
            "role": role,
            "role_confidence": role_raw.get("confidence"),
            "map_name_raw": None,
            "resolved_map": {"status": "NO_MATCH"},
            "article_ordinal": None,
            "instruction_text": [],
            "grounded": False,
        }
        if role in {"LOCATION_MAP", "RESULT"} and blob:
            try:
                region = sanitize_region(
                    read_region(
                        blob,
                        sha256=sha,
                        provider=provider,
                        whitelist=whitelist,
                        alt=block.get("alt") or "",
                        src=block.get("src") or "",
                        nearby=nearby,
                    )
                )
                obs.update(region)
                obs["resolved_map"] = resolve_official_map(region.get("map_name_raw"), maps)
            except Exception:
                obs["resolved_map"] = {"status": "NO_MATCH"}
        if sha:
            _save_cache(cache_root, sha, obs)
        observations.append(obs)
    derived.save(page_id, "ticker-image-roles.json", {"observations": observations, "topic_key": "dream_ticker"})
    return observations


def page_blocks(page_id: int, raw_html_path: str | None) -> list[dict[str, Any]]:
    if not raw_html_path:
        return []
    html_path = Path(raw_html_path)
    extracted = html_path.parent.parent / "extracted" / f"{html_path.stem}.json"
    if not extracted.exists():
        return []
    return json.loads(extracted.read_text(encoding="utf-8"))


def _prior_role(page_id: int, sha: str, block_id) -> dict[str, Any] | None:
    path = GUIDE_DERIVED / str(page_id) / "image-roles.json"
    if not path.exists():
        return None
    for obs in (json.loads(path.read_text(encoding="utf-8")) or {}).get("observations") or []:
        if str(obs.get("sha256") or "") == sha or (block_id and obs.get("block_id") == block_id):
            return obs
    return None


def _load_cache(root: Path, sha: str) -> dict[str, Any] | None:
    path = root / f"{sha}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cache(root: Path, sha: str, obs: dict[str, Any]) -> None:
    path = root / f"{sha}.json"
    path.write_text(json.dumps(obs, ensure_ascii=False), encoding="utf-8")
