import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import osmium
from shapely.geometry import box

from tools.build_region_pack import build, clipped_parts
from tools.build_world_regions import official_url
from tools.world_release import checked_inventory, source_key, split_boundary, release_tag
from tools.prepare_world_production import exclusive_regions


class WorldReleaseTests(unittest.TestCase):
    def test_overlapping_sources_preserve_union_and_have_exclusive_interiors(self):
        small, broad, duplicate = box(1,1,2,2), box(0,0,3,3), box(0,0,3,3)
        items = [({"id": id, "sourceId": id, "boundarySHA256": "original"}, geometry)
                 for id, geometry in (("small", small), ("broad", broad), ("duplicate", duplicate))]
        selected, replaced = exclusive_regions(items)
        self.assertEqual(len(selected), 2)
        self.assertEqual(replaced[0]["sourceId"], "duplicate")
        self.assertEqual(selected[0][1].intersection(selected[1][1]).area, 0)
        self.assertEqual(selected[0][1].union(selected[1][1]).area, broad.area)

    def test_source_ids_cannot_escape_cache_or_collide(self):
        for name in ("france/ile-de-france", "../region", "a/b", "a-b"):
            self.assertNotIn("/", source_key(name))
            self.assertNotIn("..", source_key(name))
        self.assertNotEqual(source_key("a/b"), source_key("a-b"))
        self.assertLessEqual(len(release_tag("france/ile-de-france", "world-20261001")), 60)

    def test_public_source_origin_rejects_credentials_and_unexpected_hosts(self):
        for url in ("https://user@download.geofabrik.de/a.osm.pbf", "https://evil.geofabrik.de/a.osm.pbf",
                    "http://download.geofabrik.de/a.osm.pbf", "https://download.geofabrik.de/a-internal.osm.pbf"):
            self.assertFalse(official_url(url))
        self.assertTrue(official_url("https://download.geofabrik.de/europe/a.osm.pbf"))

    def test_split_children_preserve_area_without_overlapping_interior(self):
        original = box(-77.1, 38.8, -76.8, 39)
        first, second = split_boundary(original)
        self.assertAlmostEqual(first.union(second).area, original.area)
        self.assertEqual(first.intersection(second).area, 0)
        self.assertEqual(split_boundary(original), [first, second])

    def test_neighboring_clipped_geometries_never_reuse_one_id(self):
        left = clipped_parts(99, 0, 0, 0, 2, box(-1, -1, 1, 1), True)
        right = clipped_parts(99, 0, 0, 0, 2, box(1, -1, 3, 1), True)
        self.assertNotEqual(left[0][0], right[0][0])
        self.assertEqual(left, clipped_parts(99, 0, 0, 0, 2, box(-1, -1, 1, 1), True))
        whole = clipped_parts(99, 0, 0, 0, 2, box(-1, -1, 3, 1), True)
        self.assertEqual(whole[0][0], 99)

    def test_changed_boundary_is_rejected_before_production(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "boundaries").mkdir()
            boundary = folder / "boundaries/a.geojson"
            boundary.write_text('{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}')
            inventory = folder / "inventory.json"
            inventory.write_text(json.dumps({"schemaVersion": 1, "regions": [{"id": "a", "boundary": "a.geojson",
                "boundarySHA256": hashlib.sha256(boundary.read_bytes()).hexdigest()}]}))
            self.assertEqual(len(checked_inventory(inventory)["regions"]), 1)
            boundary.write_text(boundary.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                checked_inventory(inventory)

    def test_real_pbf_places_are_clipped_and_preserved_in_subregions(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pbf = folder / "source.osm.pbf"
            with osmium.SimpleWriter(str(pbf)) as writer:
                writer.add_node(osmium.osm.mutable.Node(id=1, location=(1.10, 1.10),
                    tags={"place": "city", "name": "Évry", "name:en": "Evry", "name:ar": "إيفري"}))
                writer.add_node(osmium.osm.mutable.Node(id=2, location=(1.11, 1.10)))
                writer.add_node(osmium.osm.mutable.Node(id=3, location=(5, 5), tags={"place": "city", "name": "Outside"}))
                writer.add_way(osmium.osm.mutable.Way(id=10, nodes=[1, 2], tags={"highway": "residential"}))
            boundary = folder / "boundary.geojson"
            boundary.write_text(json.dumps({"type": "Polygon", "coordinates": [[[1,1],[2,1],[2,2],[1,2],[1,1]]]}))
            road = folder / "test-region.sqlite"
            build(SimpleNamespace(pbf=pbf, from_db=None, boundary=boundary, source_state=None,
                                  output=road, name="Test", region_id="test-region", stable_clips=True))
            info = json.loads(road.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(set(info["searchNames"]), {"Évry", "Evry", "إيفري"})
            self.assertEqual(info["idScheme"], "osm-node-pair-geometry-v2")
            child = folder / "test-child.sqlite"
            build(SimpleNamespace(pbf=None, from_db=road, boundary=boundary, source_state="test-region",
                                  output=child, name="Child", region_id="test-child", stable_clips=True))
            self.assertEqual(json.loads(child.with_suffix(".json").read_text(encoding="utf-8"))["searchNames"], info["searchNames"])
            with closing(sqlite3.connect(road)) as parent, closing(sqlite3.connect(child)) as child_db:
                self.assertEqual(parent.execute("SELECT id FROM segments").fetchall(), child_db.execute("SELECT id FROM segments").fetchall())


if __name__ == "__main__":
    unittest.main()
