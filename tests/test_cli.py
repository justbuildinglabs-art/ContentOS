"""Tests for the contentos.py CLI skeleton: dispatch, stubs, --version."""
from __future__ import annotations

import json
import unittest
import urllib.request

from tests.helpers import NoNetworkTestCase, REPO_ROOT, run_cli, temp_project

# Ground truth subcommand list, matching the design spec's "Skill" section.
# Kept literal here (not imported from contentos.py) so a typo or omission
# in the CLI's own list is still caught by this test.
SUBCOMMANDS = [
    "diagnose",
    "setup",
    "research",
    "frames",
    "direct-prompt",
    "synth-prompt",
    "rank",
    "write-prompt",
    "qa-prompt",
    "verify",
    "report",
    "status",
]


class CliTests(NoNetworkTestCase):
    def test_unknown_subcommand_exits_2(self) -> None:
        code, _out, err = run_cli(["bogus-command"])

        self.assertEqual(code, 2)
        self.assertTrue(err.strip())

    def test_version_matches_plugin_json(self) -> None:
        plugin = json.loads(
            (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )

        with temp_project() as project_dir:
            code, out, _err = run_cli(["--version"], cwd=project_dir)

        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), plugin["version"])

    def test_every_subcommand_is_stubbed_with_exit_1(self) -> None:
        for name in SUBCOMMANDS:
            with self.subTest(subcommand=name):
                code, _out, err = run_cli([name])

                self.assertEqual(code, 1)
                self.assertIn(f"{name}: not implemented", err)

    def test_no_network_helper_blocks_urlopen(self) -> None:
        with self.assertRaises(AssertionError):
            urllib.request.urlopen("http://example.invalid")

        opener = urllib.request.OpenerDirector()
        with self.assertRaises(AssertionError):
            opener.open("http://example.invalid")


if __name__ == "__main__":
    unittest.main()
