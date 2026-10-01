import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from shapely.geometry import shape

from tools.prepare_beta_us_inventory import INDEX_URL, REGIONS, digest, prepare, write_json
from tools.world_release import checked_inventory


def polygon(west, south, east, north):
    return {"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]]}


class BetaUSInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = self.root / "index.json"
        self.metadata = self.root / "metadata.json"
        self.document = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"id": item[0], "iso3166-2": [item[1]],
             "urls": {"pbf": "https://download.geofabrik.de/north-america/us/" + item[5] + "-latest.osm.pbf"}},
             "geometry": polygon(-77.1, 38.8, -76.9, 39.0) if index == 0 else
                 {"type": "MultiPolygon", "coordinates": [polygon(-79, 38, -75, 40)["coordinates"],
                  polygon(-74.9, 38.2, -74.8, 38.3)["coordinates"]]}}
            for index, item in enumerate(REGIONS)]}

    def write_input(self):
        write_json(self.index, self.document)
        self.sha = digest(self.index.read_bytes())
        write_json(self.metadata, {"schemaVersion": 1, "requestedURL": INDEX_URL, "resolvedURL": INDEX_URL,
                   "httpStatus": 200, "httpDate": "Thu, 01 Oct 2026 23:00:00 GMT",
                   "fetchedAtUTC": dt.datetime.now(dt.timezone.utc).isoformat(), "sha256": self.sha,
                   "bytes": self.index.stat().st_size, "pbfDownloaded": False})

    def run_prepare(self):
        self.write_input()
        return prepare(self.index, self.metadata, self.sha, self.root / "prepared")

    def test_full_maryland_keeps_dc_overlap_and_disconnected_island(self):
        inventory = checked_inventory(self.run_prepare())
        self.assertEqual([item["sourceId"] for item in inventory["regions"]], ["beta-us-dc", "beta-us-md"])
        md = json.loads((self.root / "prepared/boundaries/us-maryland.geojson").read_text(encoding="utf-8"))
        self.assertEqual(md["geometry"], self.document["features"][1]["geometry"])
        self.assertEqual(len(shape(md["geometry"]).geoms), 2)
        proof = json.loads((self.root / "prepared/boundary-provenance.json").read_text(encoding="utf-8"))
        self.assertGreater(proof["dcMarylandFullClipOverlapAreaDegreesSquared"], 0)
        self.assertTrue(all(item["sourceClipDifferenceArea"] == 0 and not item["worldOverlapSubtracted"] for item in proof["regions"]))
        self.assertFalse(proof["pbfDownloaded"])

    def test_missing_or_duplicate_target_feature_fails_before_writing(self):
        for features in ([self.document["features"][0]], self.document["features"] + [copy.deepcopy(self.document["features"][0])]):
            self.document["features"] = features
            with self.assertRaisesRegex(ValueError, "Exactly one"):
                self.run_prepare()
            self.assertFalse((self.root / "prepared").exists())

    def test_invalid_or_unclosed_geometry_is_never_silently_repaired(self):
        for coordinates in ([[-77, 38], [-76, 39], [-77, 39], [-76, 38], [-77, 38]],
                            [[-77, 38], [-76, 38], [-76, 39], [-77, 39]]):
            self.document["features"][0]["geometry"] = {"type": "Polygon", "coordinates": [coordinates]}
            with self.assertRaisesRegex(ValueError, "repair"):
                self.run_prepare()
            self.assertFalse((self.root / "prepared").exists())

    def test_private_or_wrong_region_pbf_is_rejected(self):
        self.document["features"][1]["properties"]["urls"]["pbf"] = "https://osm-internal.download.geofabrik.de/maryland.osm.pbf"
        with self.assertRaisesRegex(ValueError, "public PBF"):
            self.run_prepare()

    def test_wrong_source_hash_or_http_metadata_fails_closed(self):
        self.write_input()
        with self.assertRaisesRegex(ValueError, "pinned SHA"):
            prepare(self.index, self.metadata, "0" * 64, self.root / "prepared")
        document = json.loads(self.metadata.read_text(encoding="utf-8"))
        document["resolvedURL"] = "https://evil.example/index-v1.json"
        write_json(self.metadata, document)
        with self.assertRaisesRegex(ValueError, "HTTP metadata"):
            prepare(self.index, self.metadata, self.sha, self.root / "prepared")

    def test_existing_output_is_preserved(self):
        self.write_input()
        output = self.root / "prepared"
        output.mkdir()
        (output / "sentinel").write_bytes(b"preserve")
        with self.assertRaisesRegex(ValueError, "fresh prepared"):
            prepare(self.index, self.metadata, self.sha, output)
        self.assertEqual((output / "sentinel").read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
