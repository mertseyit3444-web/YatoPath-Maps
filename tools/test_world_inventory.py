import unittest

from tools.plan_world_regions import (
    normalize_boundary,
    plan,
    public_pbf,
    regional_boundaries,
)


def polygon(west, south, east, north):
    return {"type": "Polygon", "coordinates": [[[west, south], [east, south],
            [east, north], [west, north], [west, south]]]}


class WorldInventoryTests(unittest.TestCase):
    def test_dateline_islands_are_split_into_valid_bounded_regions(self):
        west = polygon(-180, -20, -175, -15)["coordinates"]
        east = polygon(175, -20, 180, -15)["coordinates"]
        parts = regional_boundaries({"type": "MultiPolygon", "coordinates": [west, east]})
        self.assertEqual([suffix for suffix, _ in parts], ["west", "east"])
        for _, g in parts:
            self.assertLessEqual(g.bounds[2] - g.bounds[0], 180)
            self.assertTrue(g.is_valid)

    def test_selects_leaf_extracts_and_keeps_unpublished_status(self):
        def feature(id, parent=None):
            return {"type": "Feature", "properties": {"id": id, "parent": parent,
                    "urls": {"pbf": "https://download.geofabrik.de/" + id + "-latest.osm.pbf"}},
                    "geometry": polygon(1, 1, 2, 2)}
        regions, rejected = plan({"type": "FeatureCollection", "features": [feature("country"),
                                 feature("country/state", "country")]})
        self.assertEqual(rejected, [])
        self.assertEqual(len(regions), 1)
        self.assertTrue(regions[0]["id"].startswith("gf-country-state-"))
        self.assertEqual(regions[0]["status"], "planned")

    def test_invalid_geometry_is_reported_without_a_fake_download(self):
        regions, rejected = plan({"type": "FeatureCollection", "features": [{"type": "Feature",
            "properties": {"id": "bad", "urls": {"pbf": "https://download.geofabrik.de/bad.osm.pbf"}},
            "geometry": polygon(2, 1, 2, 2)}]})
        self.assertEqual(regions, [])
        self.assertEqual(len(rejected), 1)

    def test_only_public_official_sources_are_accepted(self):
        for url in ("http://download.geofabrik.de/a.osm.pbf", "https://evil.example/a.osm.pbf",
                    "https://download.geofabrik.de/a-internal.osm.pbf",
                    "https://user@download.geofabrik.de/a.osm.pbf"):
            self.assertFalse(public_pbf(url))
        self.assertTrue(public_pbf("https://download.geofabrik.de/europe/a-latest.osm.pbf"))

    def test_source_spellings_cannot_overwrite_each_others_boundaries(self):
        features = [{"type": "Feature", "properties": {"id": name, "urls": {
                    "pbf": "https://download.geofabrik.de/" + name + ".osm.pbf"}},
                    "geometry": polygon(1, 1, 2, 2)} for name in ("a-b", "a/b")]
        regions, rejected = plan({"type": "FeatureCollection", "features": features})
        self.assertEqual(rejected, [])
        self.assertEqual(len({r["id"] for r in regions}), 2)

    def test_nested_shell_repair_keeps_inner_area_and_records_the_change(self):
        outer = polygon(0, 0, 10, 10)["coordinates"]
        inner = polygon(1, 1, 1.1, 1.1)["coordinates"]
        fixed, audit = normalize_boundary({"type": "MultiPolygon", "coordinates": [outer, inner]})
        self.assertTrue(fixed.is_valid)
        self.assertEqual(fixed.area, 100)
        self.assertEqual(len(fixed.interiors), 0)
        self.assertLess(audit["relativeAreaChange"], 0.005)

    def test_large_overlapping_area_changes_require_manual_review(self):
        a = polygon(0, 0, 10, 10)["coordinates"]
        b = polygon(1, 1, 9, 9)["coordinates"]
        with self.assertRaisesRegex(ValueError, "repair exceeded"):
            normalize_boundary({"type": "MultiPolygon", "coordinates": [a, b]})


if __name__ == "__main__":
    unittest.main()
