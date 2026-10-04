from __future__ import annotations

from pathlib import Path
from typing import Any


def locate_on_raster(image_sha: str, fixture_hash: str | None = None) -> dict[str, Any] | None:
    if not fixture_hash:
        return None
    return {"x": 0.0, "y": 0.0, "confidence": 0.1, "image_sha": image_sha, "fixture_hash": fixture_hash}


def match_location_map(
    guide_image: Path | bytes | None = None,
    raster: Path | bytes | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Never force a nearest point. Honest MATCH / NO_MATCH / AMBIGUOUS only."""
    if guide_image is None or raster is None:
        return {"status": "NO_MATCH", "reason": "missing_inputs", "candidate": None}
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return {"status": "NO_MATCH", "reason": "opencv_unavailable", "candidate": None}

    def _read(src: Path | bytes):
        if isinstance(src, (bytes, bytearray)):
            arr = np.frombuffer(src, dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        return cv2.imread(str(src), cv2.IMREAD_GRAYSCALE)

    query = _read(guide_image)
    train = _read(raster)
    if query is None or train is None:
        return {"status": "NO_MATCH", "reason": "unreadable", "candidate": None}
    orb = cv2.ORB_create(nfeatures=800)
    kq, dq = orb.detectAndCompute(query, None)
    kt, dt = orb.detectAndCompute(train, None)
    if dq is None or dt is None or len(kq) < 12 or len(kt) < 12:
        return {"status": "NO_MATCH", "reason": "too_few_features", "candidate": None}
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = matcher.match(dq, dt)
    good = [m for m in matches if m.distance < 48]
    if len(good) < 12:
        return {"status": "NO_MATCH", "reason": "weak_match", "inliers": len(good), "candidate": None}
    src = np.float32([kq[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kt[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if homography is None:
        return {"status": "NO_MATCH", "reason": "no_homography", "candidate": None}
    inliers = int(mask.sum()) if mask is not None else 0
    if inliers < 8:
        return {"status": "NO_MATCH", "reason": "few_inliers", "inliers": inliers, "candidate": None}
    h, w = query.shape[:2]
    center = np.float32([[[w / 2, h / 2]]])
    mapped = cv2.perspectiveTransform(center, homography)[0][0]
    pool = candidates or []
    if not pool:
        return {"status": "MATCH", "x": float(mapped[0]), "y": float(mapped[1]), "candidate": None, "inliers": inliers}
    ranked = []
    for item in pool:
        dx = float(item.get("x") or 0) - float(mapped[0])
        dy = float(item.get("y") or 0) - float(mapped[1])
        ranked.append((dx * dx + dy * dy, item))
    ranked.sort(key=lambda pair: pair[0])
    if len(ranked) >= 2 and ranked[0][0] > 0 and ranked[1][0] / max(ranked[0][0], 1e-6) < 1.4:
        return {"status": "AMBIGUOUS", "x": float(mapped[0]), "y": float(mapped[1]), "candidate": None, "inliers": inliers}
    if ranked[0][0] > 80 ** 2:
        return {"status": "NO_MATCH", "reason": "far_from_candidates", "x": float(mapped[0]), "y": float(mapped[1]), "candidate": None}
    pick = ranked[0][1]
    return {
        "status": "MATCH",
        "x": float(mapped[0]),
        "y": float(mapped[1]),
        "candidate": pick.get("source_point_id"),
        "inliers": inliers,
    }
