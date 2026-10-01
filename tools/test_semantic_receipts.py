"""Network-free evidence regressions; zero roads never accepts a map release."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import osmium

from tools.build_region_pack import NoRunnableRoads, build
from tools.world_release import build_source, collect, inventory_fingerprint, release_tag, source_key
from tools.test_source_download import Response, Session


class SemanticReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.url = "https://download.geofabrik.de/europe/example-latest.osm.pbf"
        self.boundary = self.root / "boundaries" / "example.geojson"
        self.boundary.parent.mkdir()
        self.boundary.write_text(json.dumps({"type": "Polygon", "coordinates":
            [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}), encoding="utf-8")
        self.inventory = self.root / "inventory.json"
        self.inventory.write_text(json.dumps({"schemaVersion": 1, "regions": [{
            "id": "example", "sourceId": "example", "name": "Example", "pbfURL": self.url,
            "boundary": self.boundary.name,
            "boundarySHA256": hashlib.sha256(self.boundary.read_bytes()).hexdigest()}]}), encoding="utf-8")
        self.pbf = self.root / "fixture.osm.pbf"
        # Genuine complete PBF: a runnable road lies OUTSIDE the target boundary.
        with osmium.SimpleWriter(str(self.pbf)) as writer:
            writer.add_node(osmium.osm.mutable.Node(id=1, location=(2, 2)))
            writer.add_node(osmium.osm.mutable.Node(id=2, location=(2.001, 2)))
            writer.add_way(osmium.osm.mutable.Way(id=10, nodes=[1, 2], tags={"highway": "residential"}))
        self.args = SimpleNamespace(inventory=self.inventory, source="example", edition="fixture-20261001",
            work=self.root / "work", publish=False, source_pbf=self.pbf,
            source_sha256=hashlib.sha256(self.pbf.read_bytes()).hexdigest(), max_source_bytes=1024 * 1024,
            pmtiles_bin=self.root / "unused-pmtiles", asset_limit=250_000_000, maxzoom=15)
        self.folder = self.args.work / source_key(self.args.source)
        disk = patch("tools.world_release.shutil.disk_usage", return_value=SimpleNamespace(free=10_000_000_000))
        disk.start()
        self.addCleanup(disk.stop)

    def test_complete_pbf_zero_count_has_typed_evidence_and_no_broken_package(self):
        output = self.root / "zero.sqlite"
        with self.assertRaises(NoRunnableRoads) as raised:
            build(SimpleNamespace(pbf=self.pbf, from_db=None, source_state=None, boundary=self.boundary,
                  region_id="example", name="Example", output=output, stable_clips=True))
        self.assertEqual(raised.exception.evidence["roadCount"], 0)
        self.assertTrue(raised.exception.evidence["inputFullyRead"])
        self.assertEqual(raised.exception.evidence["inputKind"], "pbf")
        self.assertEqual(raised.exception.evidence["clippingBoundarySHA256"],
                         hashlib.sha256(self.boundary.read_bytes()).hexdigest())
        self.assertFalse(output.exists())

    def test_zero_source_preserves_pending_evidence_but_still_fails_without_publishing(self):
        with patch("tools.world_release.run_gh") as gh, patch("tools.world_release.subprocess.run") as process:
            with self.assertRaisesRegex(ValueError, "disposition requires review"):
                build_source(self.args)
        gh.assert_not_called()
        process.assert_not_called()
        proof = json.loads((self.folder / "source-provenance.json").read_text())
        receipt = json.loads((self.folder / "semantic-receipt.json").read_text())
        self.assertEqual(proof["sha256"], hashlib.sha256(self.pbf.read_bytes()).hexdigest())
        self.assertEqual(proof["bytes"], self.pbf.stat().st_size)
        self.assertEqual(proof["origin"], "local-pinned")
        self.assertIsNone(proof["resolvedURL"])
        self.assertIsNone(proof["revision"])
        self.assertEqual(receipt["status"], "pending-review")
        self.assertFalse(receipt["verified"])
        self.assertFalse(receipt["publishable"])
        self.assertEqual(receipt["packCount"], 0)
        self.assertEqual(receipt["context"]["inventorySHA256"], inventory_fingerprint(self.inventory))
        self.assertEqual(receipt["context"]["inventoryByteSHA256"], hashlib.sha256(self.inventory.read_bytes()).hexdigest())
        self.assertEqual(receipt["observations"][0]["boundarySHA256"], hashlib.sha256(self.boundary.read_bytes()).hexdigest())
        self.assertEqual(receipt["observations"][0]["roadCount"], 0)
        self.assertFalse((self.folder / "receipt.json").exists())
        self.assertFalse((self.folder / "catalog.json").exists())
        self.assertFalse((self.folder / "packages").exists())
        self.assertNotIn(str(self.root), json.dumps(receipt))

    def test_acquired_redirect_revision_is_preserved_before_real_zero_parse(self):
        self.args.source_pbf = None
        dated = self.url.replace("-latest", "-261001")
        session = Session(Response(self.url, status=302, headers={"Location": dated}),
                          Response(dated, body=self.pbf.read_bytes()))
        with patch("tools.world_release.requests.Session") as factory:
            factory.return_value.__enter__.return_value = session
            with self.assertRaisesRegex(ValueError, "disposition requires review"):
                build_source(self.args)
        proof = json.loads((self.folder / "source-provenance.json").read_text())
        self.assertEqual(proof["resolvedURL"], dated)
        self.assertEqual(proof["revision"], "example-261001.osm.pbf")
        self.assertEqual(proof["sha256"], hashlib.sha256(self.pbf.read_bytes()).hexdigest())

    def assert_incomplete_parse_has_no_semantic_receipt(self, body):
        self.pbf.write_bytes(body)
        self.args.source_sha256 = hashlib.sha256(self.pbf.read_bytes()).hexdigest()
        # Exercise a real libosmium parse failure in the production process
        # boundary; Windows native error handles close when that process exits.
        payload = {key: str(value) if isinstance(value, Path) else value
                   for key, value in vars(self.args).items()}
        code = """import json,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tools.world_release import build_source
values=json.loads(sys.argv[1])
for key in ('inventory','work','source_pbf','pmtiles_bin'): values[key]=Path(values[key])
with patch('tools.world_release.shutil.disk_usage',return_value=SimpleNamespace(free=10_000_000_000)):
    build_source(SimpleNamespace(**values))
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, json.dumps(payload)],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RuntimeError", result.stderr)
        self.assertNotIn("disposition requires review", result.stderr)
        self.assertTrue((self.folder / "source-provenance.json").exists())
        self.assertFalse((self.folder / "semantic-receipt.json").exists())

    def test_malformed_pbf_is_not_a_zero_road_receipt(self):
        self.assert_incomplete_parse_has_no_semantic_receipt(b"not a PBF")

    def test_truncated_genuine_pbf_is_not_a_completed_zero_receipt(self):
        self.assert_incomplete_parse_has_no_semantic_receipt(self.pbf.read_bytes()[:-5])

    def test_message_matching_exception_cannot_forge_completed_zero_evidence(self):
        with patch("tools.world_release.build", side_effect=SystemExit("No runnable roads found; incomplete input")):
            with self.assertRaises(SystemExit):
                build_source(self.args)
        self.assertTrue((self.folder / "source-provenance.json").exists())
        self.assertFalse((self.folder / "semantic-receipt.json").exists())

    def test_pinned_source_limit_is_checked_before_hashing_or_provenance(self):
        self.args.max_source_bytes = self.pbf.stat().st_size - 1
        with patch("tools.world_release.sha256", side_effect=AssertionError("Hashing must not begin")):
            # checked_inventory hashes boundaries through sha256, so isolate the
            # already-verified plan to exercise source intake ordering itself.
            with patch("tools.world_release.checked_inventory", return_value=json.loads(self.inventory.read_text())):
                with self.assertRaisesRegex(ValueError, "Pinned source PBF exceeds"):
                    build_source(self.args)
        self.assertFalse((self.folder / "source-provenance.json").exists())
        self.assertFalse((self.folder / "semantic-receipt.json").exists())

    def test_collector_rejects_pending_receipt_even_if_named_as_production_receipt(self):
        with self.assertRaises(ValueError):
            build_source(self.args)
        receipt = json.loads((self.folder / "semantic-receipt.json").read_text())
        releases = [[{"tag_name": release_tag(self.args.source, self.args.edition), "assets": [
            {"name": "receipt.json", "browser_download_url": "https://example.invalid/receipt.json"},
            {"name": "catalog.json", "browser_download_url": "https://example.invalid/catalog.json"}]}]]
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value.json.return_value = receipt
        with patch("tools.world_release.run_gh", return_value=releases), \
             patch("tools.world_release.REPOSITORY", "fixture/YatoPath-Maps"), \
             patch("tools.world_release.requests.Session", return_value=session), \
             patch("tools.world_release.requests.get") as current_catalog:
            with self.assertRaisesRegex(ValueError, "Unverified"):
                collect(self.args)
        self.assertEqual(session.get.call_count, 1)
        current_catalog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
