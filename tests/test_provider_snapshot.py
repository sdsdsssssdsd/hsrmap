"""SnapshotProvider is the current Viewer data path and never hits the network."""

from hsrmap.providers.snapshot import SnapshotProvider
from hsrmap.viewer_bind import bind_viewer
import pytest

pytestmark = pytest.mark.data


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


def test_snapshot_tree_matches_frozen_core():
    ctx = bind_viewer()
    try:
        provider = SnapshotProvider(ctx)
        tree = provider.get_tree()
        flat = list(_walk(tree))
        assert len(flat) == 923
        assert sum(1 for node in flat if node["renderable"]) == 624
        prov = provider.provenance()
        assert prov["source"] == "snapshot"
        assert prov["snapshot_fallback"] == "20261001T105105Z"
        assert prov["stale"] is False
        info = provider.get_map("842")
        assert info is not None
        assert info["name"] == "海原市"
        points = provider.get_points("842")
        assert len(points) == 48
    finally:
        ctx.close()
