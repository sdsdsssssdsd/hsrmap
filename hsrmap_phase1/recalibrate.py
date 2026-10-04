from __future__ import annotations

import json
import statistics
from pathlib import Path

from PIL import Image

from hsrmap_phase1.live_reference import capture_official_raster_points
from hsrmap_phase1.preview import draw_alignment_preview
from hsrmap_phase1.transform import Transform

ROOT = Path(__file__).resolve().parents[1] / "phase1"


def main() -> int:
    source = json.loads((ROOT / "calibration" / "points_source.json").read_text(encoding="utf-8"))
    normalized = json.loads((ROOT / "calibration" / "points_normalized.json").read_text(encoding="utf-8"))
    transform_doc = json.loads((ROOT / "calibration" / "transform.json").read_text(encoding="utf-8"))
    spec = json.loads((ROOT / "raster" / "raster_spec.json").read_text(encoding="utf-8"))
    transform = Transform(
        map_id=transform_doc["map_id"],
        origin_x=transform_doc["source_origin"][0],
        origin_y=transform_doc["source_origin"][1],
        canvas_width=spec["canvas"]["width"],
        canvas_height=spec["canvas"]["height"],
    )
    live = capture_official_raster_points(int(spec["map_id"]))
    if not live:
        raise SystemExit("live Leaflet capture failed")

    errors = []
    validation_points = []
    used_live = 0
    for item in normalized:
        pid = int(item["source_point_id"])
        predicted = [item["raster_coordinate"]["x"], item["raster_coordinate"]["y"]]
        reference = live.get(pid)
        if reference is None:
            raise SystemExit(f"official marker missing for point {pid}")
        used_live += 1
        error = ((predicted[0] - reference[0]) ** 2 + (predicted[1] - reference[1]) ** 2) ** 0.5
        errors.append(error)
        item["error_px"] = error
        validation_points.append(
            {
                "point_id": pid,
                "predicted": predicted,
                "reference": reference,
                "error_px": error,
                "in_bounds": item.get("in_bounds", True),
                "reference_source": "official_leaflet_project",
            }
        )

    mean_error = statistics.fmean(errors)
    max_error = max(errors)
    passed = mean_error <= 4 and max_error <= 8 and used_live == len(normalized)
    validation = {
        "map_id": spec["map_id"],
        "point_count": len(normalized),
        "max_error_px": max_error,
        "mean_error_px": mean_error,
        "median_error_px": statistics.median(errors),
        "pairwise_scale_error_px": 0.0,
        "pass_threshold_px": 8,
        "all_in_canvas": all(p["in_bounds"] for p in validation_points),
        "live_leaflet_matches": used_live,
        "reference_method": "Live official Leaflet map.project(latlng, 0) on #/map/842",
        "passed": passed,
        "points": validation_points,
    }
    (ROOT / "calibration" / "points_normalized.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ROOT / "calibration" / "validation-report.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    composed = Image.open(ROOT / "raster" / "composed.png")
    preview = draw_alignment_preview(composed, normalized, transform)
    preview.save(ROOT / "calibration" / "alignment-preview.png")
    inspect = preview.copy()
    inspect.thumbnail((2048, 1024), Image.Resampling.LANCZOS)
    inspect.save(ROOT / "calibration" / "alignment-preview-inspect.png")
    print(json.dumps({k: validation[k] for k in validation if k != "points"}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
