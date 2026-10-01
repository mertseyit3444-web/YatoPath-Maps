"""Run with: python -m unittest tools.test_region_pipeline"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.build_region_pack import stable_edge_id


ROOT = Path(__file__).resolve().parent


class RegionPipelineTests(unittest.TestCase):
    def test_osm_segment_id_survives_way_node_reordering(self):
        unchanged = stable_edge_id(901, 1001, 1002)
        self.assertEqual(unchanged, stable_edge_id(901, 1002, 1001))
        self.assertNotEqual(unchanged, stable_edge_id(901, 1001, 1003))
        self.assertNotEqual(unchanged, stable_edge_id(902, 1001, 1002))

    def test_failed_empty_region_does_not_leave_a_broken_package(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "empty.sqlite"
            with closing(sqlite3.connect(source)) as db:
                db.executescript("""
                    CREATE TABLE segments(id INTEGER PRIMARY KEY, way_id INTEGER, state TEXT,
                        lat1 REAL, lon1 REAL, lat2 REAL, lon2 REAL, length REAL);
                    CREATE TABLE segment_bounds(id INTEGER, min_lon REAL, max_lon REAL,
                        min_lat REAL, max_lat REAL);
                """)
            output = folder / "empty-region.sqlite"
            result = subprocess.run([sys.executable, str(ROOT / "build_region_pack.py"),
                                     "--from-db", str(source), "--source-state", "MD",
                                     "--region-id", "empty-region", "--name", "Empty",
                                     "--output", str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_city_boundary_clips_edges_and_preserves_ids_on_rebuild(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "source.sqlite"
            with closing(sqlite3.connect(source)) as db:
                db.executescript("""
                    CREATE TABLE segments(id INTEGER PRIMARY KEY, way_id INTEGER, state TEXT,
                        lat1 REAL, lon1 REAL, lat2 REAL, lon2 REAL, length REAL);
                    CREATE TABLE segment_bounds(id INTEGER, min_lon REAL, max_lon REAL,
                        min_lat REAL, max_lat REAL);
                    INSERT INTO segments VALUES(100, 1, 'MD', 38.5, -77.2, 38.5, -76.8, 35000);
                    INSERT INTO segments VALUES(101, 2, 'MD', 38.6, -77.2, 38.6, -77.1, 8500);
                    INSERT INTO segment_bounds VALUES(100, -77.2, -76.8, 38.5, 38.5);
                    INSERT INTO segment_bounds VALUES(101, -77.2, -77.1, 38.6, 38.6);
                """)
            boundary = folder / "city.geojson"
            boundary.write_text(json.dumps({"type": "Polygon", "coordinates": [
                [[-77.0, 38.4], [-76.9, 38.4], [-76.9, 38.7], [-77.0, 38.7], [-77.0, 38.4]]
            ]}), encoding="utf-8")
            outputs = []
            for suffix in ("first", "second"):
                output = folder / suffix / "test-city.sqlite"
                output.parent.mkdir()
                subprocess.run([sys.executable, str(ROOT / "build_region_pack.py"),
                                "--from-db", str(source), "--source-state", "MD",
                                "--region-id", "test-city", "--name", "Test City",
                                "--boundary", str(boundary), "--output", str(output)],
                               check=True, capture_output=True, text=True)
                with closing(sqlite3.connect(output)) as db:
                    rows = db.execute("SELECT id,lat1,lon1,lat2,lon2 FROM segments ORDER BY id").fetchall()
                    self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
                    self.assertEqual(db.execute("SELECT road_count FROM pack_info").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT id_scheme FROM pack_info").fetchone()[0], "geometry-v1")
                info = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
                self.assertEqual(info["id"], "test-city")
                self.assertEqual(info["roadCount"], 1)
                outputs.append((rows, info["sha256"], info["boundarySha256"]))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0][0][0][0], 100)
            self.assertAlmostEqual(outputs[0][0][0][2], -77.0)
            self.assertAlmostEqual(outputs[0][0][0][4], -76.9)


if __name__ == "__main__":
    unittest.main()
