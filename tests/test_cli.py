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
        # Registration proof: `--help` exits 0 with usage text for every
        # subcommand argparse actually knows about. Running a subcommand
        # bare and checking its code is never 2 stopped being a sound proof
        # of registration once a real handler could raise its own usage
        # error (e.g. `research` without a config legitimately exits 2) --
        # that no longer distinguishes "argparse doesn't know this
        # subcommand" from "argparse knows it, ran it, and the handler
        # failed for its own reason". `--help` never reaches the handler,
        # so it proves registration without that ambiguity. Only
        # subcommands still marked as stubs are run bare here, to confirm
        # they still report themselves not implemented; non-stub
        # subcommands are never run bare in this test -- that is each
        # handler's own test module's job.
        with temp_project() as project_dir:
            for name in contentos.SUBCOMMANDS:
                with self.subTest(subcommand=name):
                    code, out, _err = run_cli([name, "--help"])

                    self.assertEqual(code, 0)
                    self.assertIn("usage:", out)

                    if contentos.is_stub(name):
                        code, _out, err = run_cli([name, "--project", str(project_dir)])
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
