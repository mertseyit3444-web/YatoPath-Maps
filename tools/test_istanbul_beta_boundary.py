"""Offline administrative-boundary integrity tests, including islands and holes."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from shapely.geometry import Point

from tools.prepare_istanbul_beta_boundary import DISTRICTS, RELATION_ID, prepare, province_geometry
from tools.world_release import checked_inventory


def fixture():
    elements, members = [], []
    shapes = [([(28, 41), (29, 41), (29, 41.3), (28.7, 41.3), (28.7, 41.5), (28, 41.5), (28, 41)], "outer"),
              ([(29.1, 40.85), (29.15, 40.85), (29.15, 40.9), (29.1, 40.9), (29.1, 40.85)], "outer"),
              ([(28.2, 41.1), (28.3, 41.1), (28.3, 41.2), (28.2, 41.2), (28.2, 41.1)], "inner")]
    next_node = 1
    for way_id, (coordinates, role) in enumerate(shapes, start=100):
        nodes = []
        for lon, lat in coordinates[:-1]:
            elements.append({"type": "node", "id": next_node, "lon": lon, "lat": lat})
            nodes.append(next_node)
            next_node += 1
        nodes.append(nodes[0])
        elements.append({"type": "way", "id": way_id, "nodes": nodes,
                         "version": 1, "timestamp": "2026-10-01T00:00:00Z"})
        members.append({"type": "way", "ref": way_id, "role": role})
    for identifier, name in enumerate(sorted(DISTRICTS), start=1000):
        elements.append({"type": "relation", "id": identifier, "tags": {
            "name": name, "boundary": "administrative", "admin_level": "6"},
            "version": 1, "timestamp": "2026-10-01T00:00:00Z"})
        members.append({"type": "relation", "ref": identifier, "role": "subarea"})
    elements.append({"type": "relation", "id": RELATION_ID, "version": 1,
        "timestamp": "2026-10-01T00:00:00Z", "members": members,
        "tags": {"type": "boundary", "boundary": "administrative", "admin_level": "4", "ISO3166-2": "TR-34"}})
    return {"elements": elements}


class IstanbulBetaBoundaryTests(unittest.TestCase):
    def test_all_components_and_holes_preserved_without_bbox_or_repair(self):
        geometry, _, districts, _ = province_geometry(fixture())
        self.assertEqual(geometry.geom_type, "MultiPolygon")
        self.assertTrue(geometry.covers(Point(29.12, 40.87)))  # Detached island.
        self.assertFalse(geometry.covers(Point(28.25, 41.15)))  # Interior hole.
        self.assertFalse(geometry.covers(Point(28.9, 41.4)))  # Bbox-only corner.
        self.assertEqual(len(districts), 39)

    def test_wrong_administrative_identity_is_rejected(self):
        for field, value in (("admin_level", "6"), ("ISO3166-2", "TR-35")):
            document = fixture()
            document["elements"][-1]["tags"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "province administrative"):
                province_geometry(document)

    def test_missing_adalar_cannot_be_called_whole_province(self):
        document = fixture()
        adalar = next(x for x in document["elements"] if x["type"] == "relation" and x.get("tags", {}).get("name") == "adalar")
        document["elements"][-1]["members"] = [member for member in document["elements"][-1]["members"] if member["ref"] != adalar["id"]]
        with self.assertRaisesRegex(ValueError, "All 39"):
            province_geometry(document)

    def test_missing_boundary_node_or_unclosed_way_fails_instead_of_silent_trim(self):
        original = fixture()
        for missing_node in (True, False):
            document = copy.deepcopy(original)
            if missing_node:
                document["elements"].pop(0)
            else:
                next(x for x in document["elements"] if x["type"] == "way")["nodes"].pop()
            with self.subTest(missing_node=missing_node), self.assertRaises(ValueError):
                province_geometry(document)

    def test_unknown_member_or_duplicate_input_fails_closed(self):
        for mutation in ("unknown-role", "duplicate-element"):
            document = fixture()
            if mutation == "unknown-role":
                document["elements"][-1]["members"][0]["role"] = "unknown"
            else:
                document["elements"].append(copy.deepcopy(document["elements"][0]))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                province_geometry(document)

    def test_pinned_output_is_pipeline_compatible_but_not_map_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "full.json"
            source.write_text(json.dumps(fixture()), encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            output = root / "prepared"
            proof = prepare(source, digest, output)
            self.assertEqual(proof["sourceSHA256"], digest)
            self.assertFalse(proof["geometryRepaired"])
            self.assertFalse(proof["roadPackVerified"])
            self.assertFalse(proof["vectorMapVerified"])
            self.assertFalse(proof["hostVerified"])
            document = checked_inventory(output / "inventory.json")
            self.assertEqual(document["regions"][0]["id"], "tr-istanbul")
            with self.assertRaisesRegex(ValueError, "fresh"):
                prepare(source, digest, output)

    def test_changed_input_cannot_use_previous_provenance_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "full.json"
            source.write_text(json.dumps(fixture()), encoding="utf-8")
            output = root / "prepared"
            with self.assertRaisesRegex(ValueError, "pinned SHA"):
                prepare(source, "0" * 64, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
