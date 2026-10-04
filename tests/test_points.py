"""Phase 1 point picker must mix labels and resolve 浮脂 by name, not ID."""

from hsrmap_phase1.points import select_phase1_points


def test_select_phase1_points_prefers_named_grease_labels_then_mixes_others():
    resolved = [
        {"semantic_key": "floating_grease_origin_retrace", "observed_source_id": 77},
        {"semantic_key": "floating_grease_notes", "observed_source_id": 88},
    ]
    points = (
        [{"id": i, "label_id": 77, "x_pos": i, "y_pos": i} for i in range(1, 6)]
        + [{"id": 10, "label_id": 88, "x_pos": 1, "y_pos": 1}]
        + [{"id": 20 + i, "label_id": 100 + i, "x_pos": i, "y_pos": i} for i in range(20)]
    )

    selected = select_phase1_points(points, resolved, count=8)
    ids = [p["id"] for p in selected]
    labels = [p["label_id"] for p in selected]

    assert labels.count(77) >= 3
    assert 88 in labels
    assert len(selected) == 8
    assert len(set(ids)) == 8
    assert len(set(labels)) >= 4
