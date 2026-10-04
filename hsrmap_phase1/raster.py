from __future__ import annotations

from typing import Any


def _parse_slices(slices: Any) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    if not isinstance(slices, list):
        return rows
    for row in slices:
        if isinstance(row, list):
            rows.append([cell for cell in row if isinstance(cell, dict)])
        elif isinstance(row, dict):
            rows.append([row])
    return rows


def raster_spec_from_detail(
    map_id: Any,
    detail: dict[str, Any],
    fragment_sizes: dict[tuple[int, int], tuple[int, int]] | None = None,
    local_files: dict[tuple[int, int], str] | None = None,
) -> dict[str, Any]:
    fragment_sizes = fragment_sizes or {}
    local_files = local_files or {}
    total = detail.get("total_size") or [0, 0]
    canvas_w, canvas_h = int(total[0]), int(total[1])
    rows = _parse_slices(detail.get("slices"))
    n_rows = max(len(rows), 1)
    n_cols = max((len(row) for row in rows), default=1)
    cell_w = canvas_w / n_cols
    cell_h = canvas_h / n_rows

    fragments: list[dict[str, Any]] = []
    index = 0
    for row_i, row in enumerate(rows):
        for col_i, cell in enumerate(row):
            source_w, source_h = fragment_sizes.get((row_i, col_i), (int(cell_w), int(cell_h)))
            fragments.append(
                {
                    "index": index,
                    "row": row_i,
                    "col": col_i,
                    "remote_url": cell.get("url"),
                    "local_file": local_files.get((row_i, col_i)),
                    "source_width": source_w,
                    "source_height": source_h,
                    "x": col_i * cell_w,
                    "y": row_i * cell_h,
                    "width": cell_w,
                    "height": cell_h,
                }
            )
            index += 1

    return {
        "map_id": map_id,
        "canvas": {"width": canvas_w, "height": canvas_h},
        "origin": list(detail.get("origin") or [0, 0]),
        "padding": detail.get("padding"),
        "fragments": fragments,
    }
