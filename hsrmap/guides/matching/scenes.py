from __future__ import annotations

from io import BytesIO
from typing import Any

from hsrmap.guides.vision.generic_roles import BIND_EVIDENCE, normalize_role

_ROLE_RANK = {"PUZZLE_STEP": 0, "LOCATION_MAP": 1, "RESULT": 2}


def pick_guide_observation(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    picked: dict[str, Any] | None = None
    rank = 99
    for obs in observations:
        role = normalize_role(obs.get("role"))
        if role not in BIND_EVIDENCE or not obs.get("sha256"):
            continue
        current = _ROLE_RANK.get(role, 9)
        if current < rank:
            picked = obs
            rank = current
    return picked


def thumbnail_bytes(image_bytes: bytes, max_edge: int = 384) -> bytes:
    if not image_bytes:
        return b""
    try:
        from PIL import Image

        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        image.thumbnail((max_edge, max_edge))
        out = BytesIO()
        image.save(out, format="JPEG", quality=70)
        return out.getvalue()
    except Exception:
        return image_bytes


def build_scene_payload(guide_bytes: bytes, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not guide_bytes:
        return {"allowed": [], "parts": []}
    parts = [{"role": "guide", "source_point_id": None, "bytes": thumbnail_bytes(guide_bytes)}]
    allowed: list[str] = []
    for row in candidates:
        pid = str(row.get("source_point_id") or "")
        blob = row.get("official_image_bytes") or row.get("bytes") or b""
        if not pid or not blob:
            continue
        allowed.append(pid)
        parts.append({"role": "official", "source_point_id": pid, "bytes": thumbnail_bytes(blob)})
    if not allowed:
        return {"allowed": [], "parts": []}
    return {"allowed": allowed, "parts": parts}


def score_official_scenes(guide_bytes: bytes, candidates: list[dict[str, Any]], provider) -> dict[str, float]:
    payload = build_scene_payload(guide_bytes, candidates)
    allowed = set(payload["allowed"])
    if not payload["allowed"] or provider is None or not hasattr(provider, "compare_scenes"):
        return {}
    usable = [row for row in candidates if str(row.get("source_point_id") or "") in allowed]
    batch = _one_scene(guide_bytes, usable, allowed, provider)
    if batch:
        return batch
    hits: dict[str, float] = {}
    for row in usable:
        pid = str(row.get("source_point_id") or "")
        single = _one_scene(guide_bytes, [row], {pid}, provider)
        if pid in single:
            hits[pid] = single[pid]
    if len(hits) == 1:
        return hits
    return {}


def _one_scene(guide_bytes: bytes, candidates: list[dict[str, Any]], allowed: set[str], provider) -> dict[str, float]:
    try:
        raw = provider.compare_scenes(guide_bytes, candidates) or {}
    except Exception:
        return {}
    cand = str(raw.get("candidate") or "")
    if cand not in allowed:
        return {}
    conf = float(raw.get("confidence") or 0)
    if conf < 0.85:
        return {}
    return {cand: conf}
