"""Network-free collection identity regressions; no release writes are allowed."""
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.world_release import collect, inventory_fingerprint, release_tag


def response(document):
    raw = json.dumps(document, separators=(",", ":")).encode()
    return SimpleNamespace(content=raw, json=lambda: json.loads(raw), raise_for_status=lambda: None)


class WorldReceiptCollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.document = {"schemaVersion": 1, "regions": [
            {"id": "example", "sourceId": "example"},
            {"id": "missing", "sourceId": "missing"}]}
        self.inventory = self.root / "inventory.json"
        self.inventory.write_text(json.dumps(self.document), encoding="utf-8")
        self.args = SimpleNamespace(inventory=self.inventory, edition="fixture-20261001",
                                    work=self.root / "work", publish=False)
        self.repository = "fixture/YatoPath-Maps"
        self.tag = release_tag("example", self.args.edition)
        self.base = "https://github.com/" + self.repository + "/releases/download/" + self.tag + "/"
        self.catalog = {"schemaVersion": 1, "packs": [self.pack("example", self.base)]}
        self.receipt = {"schemaVersion": 1, "sourceId": "example", "tag": self.tag,
                        "verified": True, "inventorySHA256": inventory_fingerprint(self.inventory),
                        "sourceSHA256": "c" * 64, "packCount": 1,
                        "catalogSHA256": hashlib.sha256(response(self.catalog).content).hexdigest()}
        self.release = {"tag_name": self.tag, "assets": [
            {"name": name, "browser_download_url": self.base + name}
            for name in ("receipt.json", "catalog.json")]}
        self.current = {"schemaVersion": 1, "packs": [self.pack("mc-monaco",
            "https://github.com/fixture/YatoPath-Maps/releases/download/monaco-20261001/")]}

    @staticmethod
    def pack(identifier, base):
        return {"id": identifier, "name": identifier,
                "road": {"url": base + identifier + ".sqlite", "bytes": 4096, "sha256": "a" * 64},
                "vectorBasemap": {"url": base + identifier + ".pmtiles", "bytes": 4096, "sha256": "b" * 64}}

    def run_collect(self):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.side_effect = [response(self.receipt), response(self.catalog)]
        with patch("tools.world_release.checked_inventory", return_value=self.document), \
             patch("tools.world_release.REPOSITORY", self.repository), \
             patch("tools.world_release.run_gh", return_value=[[self.release]]) as gh, \
             patch("tools.world_release.requests.Session", return_value=session), \
             patch("tools.world_release.requests.get", return_value=response(self.current)), \
             contextlib.redirect_stdout(io.StringIO()):
            collect(self.args)
        self.assertEqual(gh.call_count, 1)
        self.assertEqual(gh.call_args.args[:1], ("api",))

    def assert_rejected(self, expression):
        with self.assertRaisesRegex(ValueError, expression):
            self.run_collect()
        self.assertFalse((self.args.work / "verified-receipts.json").exists())
        self.assertFalse((self.args.work / "world-status.json").exists())

    def test_verified_collection_records_exact_metadata_hashes_and_remains_incomplete(self):
        self.run_collect()
        index_file = self.args.work / "verified-receipts.json"
        index = json.loads(index_file.read_text())
        report = json.loads((self.args.work / "world-status.json").read_text())
        catalog = json.loads((self.args.work / "catalog.json").read_text())
        self.assertEqual(index["repository"], self.repository)
        self.assertEqual(index["edition"], self.args.edition)
        self.assertEqual(index["inventorySHA256"], self.receipt["inventorySHA256"])
        self.assertEqual(index["receipts"], [{"sourceId": "example", "tag": self.tag,
            "receiptURL": self.base + "receipt.json",
            "receiptSHA256": hashlib.sha256(response(self.receipt).content).hexdigest(),
            "catalogURL": self.base + "catalog.json", "catalogSHA256": self.receipt["catalogSHA256"],
            "sourceSHA256": "c" * 64, "packCount": 1}])
        self.assertEqual(report["verifiedReceiptIndexSHA256"], hashlib.sha256(index_file.read_bytes()).hexdigest())
        self.assertEqual(report["verifiedSourceCount"], 1)
        self.assertEqual(report["missingSources"], ["missing"])
        self.assertFalse(report["complete"])
        self.assertEqual({pack["id"] for pack in catalog["packs"]}, {"example", "mc-monaco"})
        self.assertEqual(next(pack for pack in catalog["packs"] if pack["id"] == "mc-monaco"), self.current["packs"][0])

    def test_wrong_source_or_release_tag_or_inventory_never_counts_as_verified(self):
        original = copy.deepcopy(self.receipt)
        for field, value in (("sourceId", "other"), ("tag", "other-tag"),
                             ("inventorySHA256", "d" * 64)):
            with self.subTest(field=field):
                self.receipt = {**original, field: value}
                self.assert_rejected("Unverified or mismatched")

    def test_metadata_asset_cannot_point_to_another_repository_or_tag(self):
        for suffix in ("receipt.json", "catalog.json"):
            with self.subTest(asset=suffix):
                original = copy.deepcopy(self.release)
                next(asset for asset in self.release["assets"] if asset["name"] == suffix)["browser_download_url"] = \
                    "https://github.com/other/YatoPath-Maps/releases/download/other/" + suffix
                self.assert_rejected("metadata belongs to another")
                self.release = original

    def test_catalog_changed_after_verified_receipt_is_rejected(self):
        self.catalog["packs"][0]["name"] = "Changed"
        self.assert_rejected("catalog changed")

    def test_pack_count_must_match_exact_verified_catalog_and_not_boolean(self):
        for count in (0, 2, True):
            with self.subTest(count=count):
                self.receipt["packCount"] = count
                self.assert_rejected("pack count differs")

    def test_map_assets_must_be_in_the_same_immutable_release(self):
        for url in ("https://github.com/other/YatoPath-Maps/releases/download/other/a.sqlite",
                    self.base + "../other/a.sqlite", self.base + "a.sqlite?mutable=1"):
            with self.subTest(url=url):
                self.catalog["packs"][0]["road"]["url"] = url
                self.receipt["catalogSHA256"] = hashlib.sha256(response(self.catalog).content).hexdigest()
                self.assert_rejected("map asset belongs to another")

    def test_pending_semantic_receipt_cannot_be_forged_by_setting_verified(self):
        self.receipt.update(verified=True, publishable=False, status="pending-review")
        self.assert_rejected("Unverified or mismatched")


if __name__ == "__main__":
    unittest.main()
