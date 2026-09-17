"""Tests for the contentos.py CLI skeleton: dispatch, stubs, --version."""
from __future__ import annotations

import json
import unittest
import urllib.request

from tests.helpers import NoNetworkTestCase, REPO_ROOT, run_cli, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect
# (contentos.py lives directly in SCRIPTS_DIR), so this import must come
# after it.
import contentos  # noqa: E402


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

    def test_every_subcommand_is_registered_and_stubs_exit_1(self) -> None:
        # This adapts as later tasks wire up real handlers: every name is
        # still expected to be a recognized subcommand (never exit 2), but
        # only the ones still marked as stubs are expected to exit 1 with
        # the not-implemented message.
        with temp_project() as project_dir:
            for name in contentos.SUBCOMMANDS:
                with self.subTest(subcommand=name):
                    code, _out, err = run_cli([name, "--project", str(project_dir)])

                    self.assertNotEqual(code, 2)
                    if contentos.is_stub(name):
                        self.assertEqual(code, 1)
                        self.assertIn(f"{name}: not implemented", err)
                    else:
                        self.assertNotIn("not implemented", err)

    def test_no_network_helper_blocks_urlopen(self) -> None:
        with self.assertRaises(AssertionError):
            urllib.request.urlopen("http://example.invalid")

        opener = urllib.request.OpenerDirector()
        with self.assertRaises(AssertionError):
            opener.open("http://example.invalid")


if __name__ == "__main__":
    unittest.main()
