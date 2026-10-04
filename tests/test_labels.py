"""Semantic labels resolve by name, not by hardcoded source IDs."""

from hsrmap_phase1.labels import resolve_semantic_labels


def test_resolve_semantic_labels_by_display_name_not_hardcoded_id():
    tree = [
        {
            "id": 658,
            "name": "解密战利品",
            "parent_id": 0,
            "depth": 1,
            "children": [
                {
                    "id": 9999,
                    "name": "浮脂溯源·二次元ROTATE！",
                    "parent_id": 658,
                    "depth": 2,
                    "icon": "https://example.test/a.png",
                    "children": [],
                }
            ],
        },
        {
            "id": 23,
            "name": "地标",
            "parent_id": 0,
            "depth": 1,
            "children": [
                {
                    "id": 10001,
                    "name": "「浮脂记事」",
                    "parent_id": 23,
                    "depth": 2,
                    "icon": "https://example.test/b.png",
                    "children": [],
                }
            ],
        },
    ]

    resolved = resolve_semantic_labels(tree)
    by_key = {item["semantic_key"]: item for item in resolved}

    assert by_key["floating_grease_origin_retrace"]["observed_source_id"] == 9999
    assert by_key["floating_grease_origin_retrace"]["display_name"] == "浮脂溯源·二次元ROTATE！"
    assert by_key["floating_grease_notes"]["observed_source_id"] == 10001
    assert by_key["floating_grease_notes"]["display_name"] == "「浮脂记事」"
