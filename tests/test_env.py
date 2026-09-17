"""Tests for lib/env.py: APIFY_API_TOKEN resolution and the diagnose check."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tests.helpers import NoNetworkTestCase, run_cli, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so this import must come after it (this module may run standalone, not
# only via discovery after another test module has already done so).
from lib.env import (  # noqa: E402
    KEY_NAME,
    PLUGIN_OPTION_NAME,
    Keys,
    check_file_permissions,
    diagnose,
    load_env_file,
    resolve_keys,
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


if __name__ == "__main__":
    unittest.main()
