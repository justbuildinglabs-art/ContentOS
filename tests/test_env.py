"""Tests for lib/env.py: APIFY_API_TOKEN resolution and the diagnose check."""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests.helpers import REPO_ROOT, NoNetworkTestCase, run_cli, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so this import must come after it (this module may run standalone, not
# only via discovery after another test module has already done so).
from lib.env import (  # noqa: E402
    KEY_NAME,
    PLUGIN_OPTION_FILE,
    PLUGIN_OPTION_NAME,
    Keys,
    check_file_permissions,
    diagnose,
    load_env_file,
    resolve_keys,
    sync_plugin_option,
)


def _write_env_file(path: Path, content: str, mode: int = 0o600) -> None:
    """Write an env file fixture and pin its permissions for determinism."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


class ResolveKeysPrecedenceTests(NoNetworkTestCase):
    def test_precedence_env_over_plugin_option_over_project_over_global(self) -> None:
        with temp_project() as project_dir, temp_project() as fake_home:
            _write_env_file(
                project_dir / ".contentos" / ".env",
                "APIFY_API_TOKEN=project_token\n",
            )
            _write_env_file(
                fake_home / ".config" / "contentos" / ".env",
                "APIFY_API_TOKEN=global_token\n",
            )

            environ = {
                "HOME": str(fake_home),
                KEY_NAME: "env_token",
                PLUGIN_OPTION_NAME: "plugin_token",
            }

            # env wins over everything else.
            keys = resolve_keys(project_dir, environ)
            self.assertEqual(keys.apify, "env_token")
            self.assertEqual(keys.source, "env")

            # remove env -> plugin_option wins.
            del environ[KEY_NAME]
            keys = resolve_keys(project_dir, environ)
            self.assertEqual(keys.apify, "plugin_token")
            self.assertEqual(keys.source, "plugin_option")

            # remove plugin_option -> project .env wins.
            del environ[PLUGIN_OPTION_NAME]
            keys = resolve_keys(project_dir, environ)
            self.assertEqual(keys.apify, "project_token")
            self.assertEqual(keys.source, "project_env")

            # remove the project file -> global .env wins.
            (project_dir / ".contentos" / ".env").unlink()
            keys = resolve_keys(project_dir, environ)
            self.assertEqual(keys.apify, "global_token")
            self.assertEqual(keys.source, "global_env")

    def test_plugin_option_name_is_read(self) -> None:
        with temp_project() as project_dir:
            environ = {
                "HOME": str(project_dir / "no-such-home"),
                PLUGIN_OPTION_NAME: "plugin_only_token",
            }

            keys = resolve_keys(project_dir, environ)

        self.assertEqual(keys.apify, "plugin_only_token")
        self.assertEqual(keys.source, "plugin_option")

    def test_empty_env_value_falls_through(self) -> None:
        with temp_project() as project_dir:
            _write_env_file(
                project_dir / ".contentos" / ".env",
                "APIFY_API_TOKEN=project_token\n",
            )
            home = str(project_dir / "no-such-home")

            # An empty APIFY_API_TOKEN must fall through to plugin_option.
            environ = {
                "HOME": home,
                KEY_NAME: "",
                PLUGIN_OPTION_NAME: "plugin_token",
            }
            keys = resolve_keys(project_dir, environ)
            self.assertEqual(keys.apify, "plugin_token")
            self.assertEqual(keys.source, "plugin_option")

            # An empty plugin_option too must fall through to project_env.
            environ[PLUGIN_OPTION_NAME] = ""
            keys = resolve_keys(project_dir, environ)
            self.assertEqual(keys.apify, "project_token")
            self.assertEqual(keys.source, "project_env")


class LoadEnvFileTests(NoNetworkTestCase):
    def test_env_file_parsing_strips_quotes_and_skips_comments_and_empties(
        self,
    ) -> None:
        with temp_project() as tmp_dir:
            env_path = tmp_dir / ".env"
            _write_env_file(
                env_path,
                "\n".join(
                    [
                        "# a leading comment",
                        "",
                        "FOO=bar",
                        'DOUBLE="double quoted"',
                        "SINGLE='single quoted'",
                        "EMPTY=",
                        'EMPTY_QUOTED=""',
                        "   ",
                        "# a trailing comment",
                        "KEEP=keepme",
                        "",
                    ]
                ),
            )

            values = load_env_file(env_path)

        self.assertEqual(
            values,
            {
                "FOO": "bar",
                "DOUBLE": "double quoted",
                "SINGLE": "single quoted",
                "KEEP": "keepme",
            },
        )


    def test_env_file_parsing_strips_a_leading_export(self) -> None:
        # `export KEY=value` is how a founder who pasted the line from
        # their shell profile would have written it.
        with temp_project() as project_dir:
            env_path = project_dir / ".contentos" / ".env"
            _write_env_file(
                env_path,
                "export APIFY_API_TOKEN=exported_token\n"
                'export QUOTED="quoted value"\n'
                "export    SPACED=spaced\n"
                "exportable=not-an-export\n",
            )

            values = load_env_file(env_path)

        self.assertEqual(
            values,
            {
                "APIFY_API_TOKEN": "exported_token",
                "QUOTED": "quoted value",
                "SPACED": "spaced",
                "exportable": "not-an-export",
            },
        )

    def test_exported_token_resolves_as_project_env(self) -> None:
        with temp_project() as project_dir:
            _write_env_file(
                project_dir / ".contentos" / ".env",
                "export APIFY_API_TOKEN=exported_token\n",
            )

            keys = resolve_keys(project_dir, {"CONTENTOS_CONFIG_DIR": ""})

        self.assertEqual(keys.apify, "exported_token")
        self.assertEqual(keys.source, "project_env")

    def test_unreadable_env_file_is_ignored_with_a_warning(self) -> None:
        # A .env that is not UTF-8 text, or that cannot be opened at all,
        # must never take the whole CLI down with a traceback.
        with temp_project() as project_dir:
            env_path = project_dir / ".contentos" / ".env"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_bytes(b"APIFY_API_TOKEN=\xff\xfe\x00binary\n")
            os.chmod(env_path, 0o600)

            self.assertEqual(load_env_file(env_path), {})

            keys = resolve_keys(project_dir, {"CONTENTOS_CONFIG_DIR": ""})

        self.assertIsNone(keys.apify)
        self.assertIsNone(keys.source)
        self.assertTrue(any("could not be read" in warning for warning in keys.warnings))

    def test_env_file_that_is_a_directory_is_ignored_with_a_warning(self) -> None:
        with temp_project() as project_dir:
            env_path = project_dir / ".contentos" / ".env"
            env_path.mkdir(parents=True)

            self.assertEqual(load_env_file(env_path), {})

            keys = resolve_keys(project_dir, {"CONTENTOS_CONFIG_DIR": ""})

        self.assertIsNone(keys.apify)
        self.assertTrue(any("could not be read" in warning for warning in keys.warnings))


class CheckFilePermissionsTests(NoNetworkTestCase):
    def test_warns_when_env_file_not_600(self) -> None:
        with temp_project() as project_dir:
            env_path = project_dir / ".contentos" / ".env"
            _write_env_file(env_path, "APIFY_API_TOKEN=project_token\n", mode=0o644)

            warning = check_file_permissions(env_path)
            self.assertIsNotNone(warning)
            self.assertIn(str(env_path), warning)
            self.assertIn("chmod 600", warning)

            # resolve_keys must surface the same warning for an insecure
            # file it finds along the way, regardless of who "wins".
            environ = {"HOME": str(project_dir / "no-such-home")}
            keys = resolve_keys(project_dir, environ)

        self.assertEqual(keys.warnings, [warning])

    def test_no_warning_when_600(self) -> None:
        with temp_project() as project_dir:
            env_path = project_dir / ".contentos" / ".env"
            _write_env_file(env_path, "APIFY_API_TOKEN=project_token\n", mode=0o600)

            self.assertIsNone(check_file_permissions(env_path))

            environ = {"HOME": str(project_dir / "no-such-home")}
            keys = resolve_keys(project_dir, environ)

        self.assertEqual(keys.warnings, [])


class ConfigDirTests(NoNetworkTestCase):
    def test_config_dir_override_and_clean_mode(self) -> None:
        with temp_project() as project_dir, temp_project() as custom_config_dir:
            _write_env_file(
                custom_config_dir / ".env",
                "APIFY_API_TOKEN=custom_global_token\n",
            )

            # CONTENTOS_CONFIG_DIR overrides the default ~/.config/contentos
            # location entirely; note there is no "HOME" key at all here,
            # proving HOME is never consulted when the override is present.
            override_environ = {"CONTENTOS_CONFIG_DIR": str(custom_config_dir)}
            keys = resolve_keys(project_dir, override_environ)
            self.assertEqual(keys.apify, "custom_global_token")
            self.assertEqual(keys.source, "global_env")

            # An explicit empty string means "clean mode": skip the global
            # file entirely, without ever touching HOME either.
            clean_environ = {"CONTENTOS_CONFIG_DIR": ""}
            keys = resolve_keys(project_dir, clean_environ)
            self.assertIsNone(keys.apify)
            self.assertIsNone(keys.source)
            self.assertEqual(keys.warnings, [])

    def test_missing_home_skips_global_file(self) -> None:
        # A missing HOME with no CONTENTOS_CONFIG_DIR override means "no
        # global config file" -- exactly like CONTENTOS_CONFIG_DIR="" --
        # rather than raising or falling back to Path.home().
        with temp_project() as project_dir:
            environ: dict = {}

            keys = resolve_keys(project_dir, environ)
            self.assertEqual(keys, Keys(apify=None, source=None, warnings=[]))

            result = diagnose(project_dir, environ=environ)

        self.assertFalse(result["apify"])


class DiagnoseCliTests(NoNetworkTestCase):
    def test_diagnose_json_shape_and_exit_zero(self) -> None:
        with temp_project() as project_dir:
            env = dict(os.environ)
            env.pop(KEY_NAME, None)
            env["CONTENTOS_CONFIG_DIR"] = ""

            code, out, err = run_cli(
                ["diagnose", "--project", str(project_dir)], env=env
            )

        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        expected_types = {
            "apify": bool,
            "apify_source": (str, type(None)),
            "project_dir": str,
            "product_md": bool,
            "rules_md": bool,
            "config_json": bool,
            "python": str,
            "ffmpeg": bool,
            "skill_root": str,
            "env_perms_ok": bool,
            "warnings": list,
            "mock": bool,
        }
        for key, expected_type in expected_types.items():
            self.assertIn(key, payload)
            self.assertIsInstance(payload[key], expected_type, key)


class SyncPluginOptionTests(NoNetworkTestCase):
    """The SessionStart hook's half: copy the /plugin setting to a file.

    Claude Code hands plugin settings to hook processes only, never to
    commands run through the Bash tool, so the hook mirrors the value
    into the global config dir where resolve_keys can find it.
    """

    def test_writes_the_option_to_a_600_file(self) -> None:
        with temp_project() as config_dir:
            environ = {
                "CONTENTOS_CONFIG_DIR": str(config_dir),
                PLUGIN_OPTION_NAME: "plugin_token",
            }

            warnings = sync_plugin_option(environ)

            mirror = config_dir / PLUGIN_OPTION_FILE
            self.assertEqual(warnings, [])
            self.assertEqual(load_env_file(mirror), {KEY_NAME: "plugin_token"})
            self.assertEqual(mirror.stat().st_mode & 0o777, 0o600)

    def test_creates_a_missing_config_dir(self) -> None:
        with temp_project() as tmp_dir:
            config_dir = tmp_dir / "fresh" / "contentos"
            environ = {
                "CONTENTOS_CONFIG_DIR": str(config_dir),
                PLUGIN_OPTION_NAME: "plugin_token",
            }

            self.assertEqual(sync_plugin_option(environ), [])

            self.assertTrue((config_dir / PLUGIN_OPTION_FILE).exists())

    def test_overwrites_an_older_copy(self) -> None:
        with temp_project() as config_dir:
            _write_env_file(config_dir / PLUGIN_OPTION_FILE, "APIFY_API_TOKEN=old_token\n")
            environ = {
                "CONTENTOS_CONFIG_DIR": str(config_dir),
                PLUGIN_OPTION_NAME: "new_token",
            }

            sync_plugin_option(environ)

            self.assertEqual(
                load_env_file(config_dir / PLUGIN_OPTION_FILE), {KEY_NAME: "new_token"}
            )

    def test_cleared_option_removes_the_copy(self) -> None:
        with temp_project() as config_dir:
            mirror = config_dir / PLUGIN_OPTION_FILE
            for cleared in ({}, {PLUGIN_OPTION_NAME: ""}, {PLUGIN_OPTION_NAME: "   "}):
                with self.subTest(cleared=cleared):
                    _write_env_file(mirror, "APIFY_API_TOKEN=old_token\n")
                    environ = dict(cleared, CONTENTOS_CONFIG_DIR=str(config_dir))

                    self.assertEqual(sync_plugin_option(environ), [])

                    self.assertFalse(mirror.exists())

    def test_never_touches_the_founders_own_global_env(self) -> None:
        with temp_project() as config_dir:
            _write_env_file(config_dir / ".env", "APIFY_API_TOKEN=founder_token\n")

            sync_plugin_option(
                {"CONTENTOS_CONFIG_DIR": str(config_dir), PLUGIN_OPTION_NAME: "plugin_token"}
            )
            sync_plugin_option({"CONTENTOS_CONFIG_DIR": str(config_dir)})

            self.assertEqual(
                (config_dir / ".env").read_text(encoding="utf-8"),
                "APIFY_API_TOKEN=founder_token\n",
            )

    def test_clean_mode_writes_nothing(self) -> None:
        with temp_project() as fake_home:
            environ = {
                "HOME": str(fake_home),
                "CONTENTOS_CONFIG_DIR": "",
                PLUGIN_OPTION_NAME: "plugin_token",
            }

            self.assertEqual(sync_plugin_option(environ), [])

            self.assertEqual(list(fake_home.iterdir()), [])

    def test_multiline_value_is_refused_without_echoing_it(self) -> None:
        with temp_project() as config_dir:
            mirror = config_dir / PLUGIN_OPTION_FILE
            _write_env_file(mirror, "APIFY_API_TOKEN=old_token\n")
            environ = {
                "CONTENTOS_CONFIG_DIR": str(config_dir),
                PLUGIN_OPTION_NAME: "secret_part\nINJECTED=1",
            }

            warnings = sync_plugin_option(environ)

            self.assertEqual(len(warnings), 1)
            self.assertNotIn("secret_part", warnings[0])
            # The old copy goes too, so a stale key never outlives the
            # setting it came from.
            self.assertFalse(mirror.exists())

    def test_unwritable_config_dir_warns_instead_of_raising(self) -> None:
        with temp_project() as tmp_dir:
            blocker = tmp_dir / "not-a-dir"
            blocker.write_text("", encoding="utf-8")
            environ = {
                "CONTENTOS_CONFIG_DIR": str(blocker),
                PLUGIN_OPTION_NAME: "plugin_token",
            }

            warnings = sync_plugin_option(environ)

            self.assertEqual(len(warnings), 1)
            self.assertNotIn("plugin_token", warnings[0])


class PluginOptionFileResolutionTests(NoNetworkTestCase):
    """The Bash-tool half: resolve_keys reads the hook's copy."""

    def test_copy_resolves_as_plugin_option(self) -> None:
        with temp_project() as project_dir, temp_project() as config_dir:
            _write_env_file(config_dir / PLUGIN_OPTION_FILE, "APIFY_API_TOKEN=plugin_token\n")

            keys = resolve_keys(project_dir, {"CONTENTOS_CONFIG_DIR": str(config_dir)})

        self.assertEqual(keys.apify, "plugin_token")
        self.assertEqual(keys.source, "plugin_option")
        self.assertEqual(keys.warnings, [])

    def test_copy_keeps_the_plugin_option_place_in_precedence(self) -> None:
        with temp_project() as project_dir, temp_project() as config_dir:
            _write_env_file(project_dir / ".contentos" / ".env", "APIFY_API_TOKEN=project_token\n")
            _write_env_file(config_dir / ".env", "APIFY_API_TOKEN=global_token\n")
            _write_env_file(config_dir / PLUGIN_OPTION_FILE, "APIFY_API_TOKEN=copied_token\n")
            environ = {
                "CONTENTOS_CONFIG_DIR": str(config_dir),
                KEY_NAME: "env_token",
                PLUGIN_OPTION_NAME: "live_option_token",
            }

            # The process env still wins.
            self.assertEqual(resolve_keys(project_dir, environ).apify, "env_token")

            # A live option variable beats the hook's copy of it.
            del environ[KEY_NAME]
            keys = resolve_keys(project_dir, environ)
            self.assertEqual((keys.apify, keys.source), ("live_option_token", "plugin_option"))

            # The copy beats both env files.
            del environ[PLUGIN_OPTION_NAME]
            keys = resolve_keys(project_dir, environ)
            self.assertEqual((keys.apify, keys.source), ("copied_token", "plugin_option"))

    def test_loose_permissions_on_the_copy_warn(self) -> None:
        with temp_project() as project_dir, temp_project() as config_dir:
            mirror = config_dir / PLUGIN_OPTION_FILE
            _write_env_file(mirror, "APIFY_API_TOKEN=plugin_token\n", mode=0o644)

            keys = resolve_keys(project_dir, {"CONTENTOS_CONFIG_DIR": str(config_dir)})
            expected = check_file_permissions(mirror)

        self.assertIsNotNone(expected)
        self.assertEqual(keys.warnings, [expected])


class SyncPluginKeyCliTests(NoNetworkTestCase):
    def test_hook_command_is_silent_and_diagnose_then_finds_the_key(self) -> None:
        # Run the exact command hooks/hooks.json declares, the way Claude
        # Code would: through sh, with CLAUDE_PLUGIN_ROOT and the option
        # in the environment. Its stdout would land in Claude's context,
        # so it must print nothing, and the key must never be echoed.
        hooks = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]

        with temp_project() as project_dir, temp_project() as config_dir:
            base_env = dict(os.environ)
            base_env.pop(KEY_NAME, None)
            base_env.pop(PLUGIN_OPTION_NAME, None)
            base_env["CONTENTOS_CONFIG_DIR"] = str(config_dir)

            hook_env = dict(base_env)
            hook_env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
            hook_env[PLUGIN_OPTION_NAME] = "hook_secret_token"
            result = subprocess.run(
                ["sh", "-c", command],
                cwd=str(project_dir),
                env=hook_env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("hook_secret_token", result.stderr)

            # A later Bash-tool command has no option variable at all.
            code, out, err = run_cli(["diagnose", "--project", str(project_dir)], env=base_env)

        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["apify"])
        self.assertEqual(payload["apify_source"], "plugin_option")
        self.assertNotIn("hook_secret_token", out)

    def test_warnings_go_to_stderr_and_exit_stays_zero(self) -> None:
        # A hook that fails must never block the founder's session.
        with temp_project() as tmp_dir:
            blocker = tmp_dir / "not-a-dir"
            blocker.write_text("", encoding="utf-8")
            env = dict(os.environ)
            env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
            env["CONTENTOS_CONFIG_DIR"] = str(blocker)
            env[PLUGIN_OPTION_NAME] = "hook_secret_token"

            code, out, err = run_cli(["sync-plugin-key"], cwd=tmp_dir, env=env)

        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())
        self.assertNotIn("hook_secret_token", err)


    def test_outside_a_hook_it_changes_nothing(self) -> None:
        # The Bash tool never sees the option, so a run from there would
        # look exactly like "setting cleared" and delete a good copy.
        # Claude Code sets CLAUDE_PLUGIN_ROOT for plugin hooks only.
        with temp_project() as config_dir:
            mirror = config_dir / PLUGIN_OPTION_FILE
            _write_env_file(mirror, "APIFY_API_TOKEN=plugin_token\n")
            env = dict(os.environ)
            env.pop("CLAUDE_PLUGIN_ROOT", None)
            env.pop(PLUGIN_OPTION_NAME, None)
            env["CONTENTOS_CONFIG_DIR"] = str(config_dir)

            code, out, err = run_cli(["sync-plugin-key"], cwd=config_dir, env=env)

            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertIn("SessionStart hook", err)
            self.assertEqual(load_env_file(mirror), {KEY_NAME: "plugin_token"})

if __name__ == "__main__":
    unittest.main()
