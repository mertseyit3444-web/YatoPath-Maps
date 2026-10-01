import os
import unittest
from unittest.mock import patch

from tools.world_release import destination_repository


class DestinationTests(unittest.TestCase):
    def test_actions_destination_tracks_current_repository(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "mertseyit3444-web/YatoPath-Maps"}, clear=True):
            self.assertEqual(destination_repository(), "mertseyit3444-web/YatoPath-Maps")

    def test_explicit_destination_and_local_configuration(self):
        with patch.dict(os.environ, {"YATOPATH_MAP_REPOSITORY": "Local-Owner/YatoPath-Maps",
                                     "GITHUB_REPOSITORY": "Other-Owner/YatoPath-Maps"}, clear=True):
            self.assertEqual(destination_repository(), "Local-Owner/YatoPath-Maps")
            self.assertEqual(destination_repository("Explicit-Owner/YatoPath-Maps"), "Explicit-Owner/YatoPath-Maps")

    def test_missing_or_non_map_destination_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            for value in (None, "owner/YolTamam", "owner/YatoPath-Maps/extra", "https://github.com/owner/YatoPath-Maps",
                          "../YatoPath-Maps", "owner name/YatoPath-Maps", "owner/YatoPath-Maps;echo"):
                with self.assertRaises(ValueError):
                    destination_repository(value)


if __name__ == "__main__":
    unittest.main()
