"""Tests for the plugin manifests: .claude-plugin/plugin.json and marketplace.json."""
from __future__ import annotations

import json
import unittest

from tests.helpers import REPO_ROOT, NoNetworkTestCase

PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"


class ManifestTests(NoNetworkTestCase):
    def test_manifests_are_valid_json_and_names_agree(self) -> None:
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))

        self.assertEqual(plugin["name"], "contentos")
        self.assertEqual(marketplace["name"], "contentos")
        self.assertEqual(len(marketplace["plugins"]), 1)
        self.assertEqual(marketplace["plugins"][0]["name"], plugin["name"])

    def test_marketplace_source_is_repo_root(self) -> None:
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))

        self.assertEqual(marketplace["plugins"][0]["source"], "./")

    def test_plugin_declares_sensitive_apify_user_config(self) -> None:
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))

        apify_config = plugin["userConfig"]["APIFY_API_TOKEN"]
        self.assertEqual(apify_config["type"], "string")
        self.assertTrue(apify_config["sensitive"])
        self.assertFalse(apify_config["required"])


if __name__ == "__main__":
    unittest.main()
