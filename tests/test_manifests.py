"""Tests for the plugin manifests: .claude-plugin/plugin.json and marketplace.json."""
from __future__ import annotations

import json
import unittest

from tests.helpers import REPO_ROOT, NoNetworkTestCase

PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"

# The one line the plugin gallery shows. Both manifests carry it, word
# for word, and 0.2.0 makes it say creators rather than app founders.
DESCRIPTION = (
    "Competitor research to vetted Reel scripts: a four-stage Instagram "
    "Reels content pipeline for creators."
)
VERSION = "0.2.1"


class ManifestTests(NoNetworkTestCase):
    def test_manifests_are_valid_json_and_names_agree(self) -> None:
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))

        self.assertEqual(plugin["name"], "contentos")
        self.assertEqual(marketplace["name"], "contentos")
        self.assertEqual(len(marketplace["plugins"]), 1)
        self.assertEqual(marketplace["plugins"][0]["name"], plugin["name"])

    def test_manifests_describe_creators_and_version_0_2_1(self) -> None:
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))

        self.assertEqual(plugin["version"], VERSION)
        self.assertEqual(plugin["description"], DESCRIPTION)
        self.assertEqual(marketplace["plugins"][0]["description"], DESCRIPTION)
        for text in (
            PLUGIN_JSON.read_text(encoding="utf-8"),
            MARKETPLACE_JSON.read_text(encoding="utf-8"),
        ):
            self.assertNotIn("founder", text.lower())

    def test_marketplace_source_is_repo_root(self) -> None:
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))

        self.assertEqual(marketplace["plugins"][0]["source"], "./")

    def test_plugin_declares_sensitive_apify_user_config(self) -> None:
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))

        apify_config = plugin["userConfig"]["APIFY_API_TOKEN"]
        self.assertEqual(apify_config["type"], "string")
        self.assertTrue(apify_config["sensitive"])
        self.assertFalse(apify_config["required"])


    def test_session_start_hook_copies_the_plugin_option(self) -> None:
        # Claude Code passes plugin settings to hooks only, never to the
        # Bash tool, so one SessionStart hook hands the key on. No matcher,
        # so it runs on startup, resume, clear, and compact alike.
        hooks = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))

        self.assertEqual(list(hooks["hooks"]), ["SessionStart"])
        entries = hooks["hooks"]["SessionStart"]
        self.assertEqual(len(entries), 1)
        self.assertNotIn("matcher", entries[0])
        self.assertEqual(len(entries[0]["hooks"]), 1)

        hook = entries[0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(
            hook["command"],
            'python3 "${CLAUDE_PLUGIN_ROOT}/skills/contentos/scripts/contentos.py" sync-plugin-key',
        )
        # A shell-form command must never splice a setting into itself.
        self.assertNotIn("user_config", json.dumps(hooks))

if __name__ == "__main__":
    unittest.main()
