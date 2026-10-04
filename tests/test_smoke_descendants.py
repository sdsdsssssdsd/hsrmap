"""Cross-region smoke must walk nested map_nodes, not only the top-level name."""

from pathlib import Path

from hsrmap.database import CoreDatabase
from hsrmap.sync import descendant_source_ids, first_descendant_map_with_points


def test_first_descendant_map_walks_nested_folders(tmp_path: Path):
    db = CoreDatabase(tmp_path / "core.db")
    db.conn.executemany(
        """
        INSERT INTO map_nodes(source_id, parent_source_id, node_type, name, is_renderable)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("1", None, 1, "雅利洛-VI", 0),
            ("2", "1", 1, "行政区", 0),
            ("3", "2", 2, "行政区", 1),
        ],
    )
    db.conn.execute(
        """
        INSERT INTO maps(source_id, name, canvas_width, canvas_height, origin_x, origin_y, fragment_count, coordinate_transform)
        VALUES ('3', '行政区', 100, 100, 10, 10, 1, 'hoyolab_hsr_origin_translation_v1')
        """
    )
    db.conn.execute(
        "INSERT INTO points(source_id, map_id, x_pos, y_pos, raster_x, raster_y) VALUES ('p1', 1, 1, 2, 11, 12)"
    )
    db.conn.commit()

    assert descendant_source_ids(db, "1") == {"1", "2", "3"}
    row = first_descendant_map_with_points(db, "1")
    assert row is not None
    assert row["source_id"] == "3"
    db.close()
