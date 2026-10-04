"""Map tree classification: treating every node as a raster map would fail these."""

from hsrmap_phase1.tree import classify_tree_nodes


def test_classify_tree_separates_folders_from_renderable_leaves():
    tree = [
        {
            "id": 502,
            "name": "二相乐园",
            "node_type": 1,
            "depth": 1,
            "children": [
                {
                    "id": 841,
                    "name": "海原市",
                    "node_type": 1,
                    "depth": 2,
                    "children": [
                        {
                            "id": 842,
                            "name": "海原市",
                            "node_type": 2,
                            "depth": 3,
                            "children": [],
                            "detail": '{"total_size":[8192,4096],"slices":[[{"url":"https://example.test/a.png"}]]}',
                        }
                    ],
                }
            ],
        }
    ]

    report = classify_tree_nodes(tree)

    assert report.total_nodes == 3
    assert report.nodes_with_children == 2
    assert report.leaf_nodes == 1
    assert report.folder_or_group_nodes == 2
    assert report.renderable_map_ids == [842]
    assert 841 not in report.renderable_map_ids
    assert 502 not in report.renderable_map_ids


def test_leaf_without_raster_detail_is_not_renderable():
    tree = [
        {
            "id": 41,
            "name": "星穹列车",
            "node_type": 1,
            "depth": 1,
            "children": [],
        }
    ]

    report = classify_tree_nodes(tree)

    assert report.total_nodes == 1
    assert report.leaf_nodes == 1
    assert report.renderable_map_ids == []
    assert report.unsupported_or_empty_leaves == 1
