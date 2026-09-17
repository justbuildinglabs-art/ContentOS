"""Tests for lib/store.py: run directory layout, config, and rules."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.helpers import NoNetworkTestCase, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so this import must come after it.
from lib.store import (  # noqa: E402
    DEFAULT_CONFIG,
    ConfigError,
    RunExists,
    RunNotFound,
    contentos_dir,
    ensure_gitignore,
    init_run,
    load_config,
    new_run_id,
    read_json,
    read_rules,
    resolve_run,
    run_dir,
    update_run,
    write_json_atomic,
)


def _write_config(project: Path, overrides: dict) -> None:
    """Write `<project>/.contentos/config.json` with exactly `overrides`."""
    contentos_dir(project).mkdir(parents=True, exist_ok=True)
    (contentos_dir(project) / "config.json").write_text(
        json.dumps(overrides), encoding="utf-8"
    )


class RunIdTests(NoNetworkTestCase):
    def test_run_id_format(self) -> None:
        fixed_now = datetime(2026, 9, 16, 14, 5, 9)

        self.assertEqual(new_run_id(fixed_now), "20260916-140509")


class WriteJsonAtomicTests(NoNetworkTestCase):
    def test_write_json_atomic_leaves_no_tmp_and_roundtrips(self) -> None:
        with temp_project() as project_dir:
            target = project_dir / "nested" / "data.json"
            payload = {"b": 2, "a": [1, 2, 3]}

            write_json_atomic(target, payload)

            self.assertTrue(target.exists())
            tmp_path = target.with_name(target.name + ".tmp")
            self.assertFalse(tmp_path.exists())
            self.assertEqual(list(target.parent.iterdir()), [target])
            self.assertEqual(read_json(target), payload)
            self.assertTrue(target.read_text(encoding="utf-8").endswith("\n"))


class LoadConfigTests(NoNetworkTestCase):
    def test_load_config_merges_defaults(self) -> None:
        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {"competitors": ["acme", "beta"], "lookback_days": 30},
            )

            config = load_config(project_dir)

        self.assertEqual(config["competitors"], ["acme", "beta"])
        self.assertEqual(config["lookback_days"], 30)
        for key, value in DEFAULT_CONFIG.items():
            if key in ("competitors", "lookback_days"):
                continue
            self.assertEqual(config[key], value, key)

    def test_load_config_preserves_unknown_keys(self) -> None:
        # Resolution: "Unknown keys are preserved, not rejected."
        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {"competitors": ["acme"], "future_field": "kept"},
            )

            config = load_config(project_dir)

        self.assertEqual(config["future_field"], "kept")

    def test_load_config_missing_file_raises_config_error(self) -> None:
        with temp_project() as project_dir:
            with self.assertRaises(ConfigError) as ctx:
                load_config(project_dir)

        self.assertEqual(
            str(ctx.exception), "no .contentos/config.json; run: /contentos setup"
        )

    def test_load_config_rejects_invalid_json(self) -> None:
        # Resolution: "Invalid JSON raises ConfigError too."
        with temp_project() as project_dir:
            contentos_dir(project_dir).mkdir(parents=True, exist_ok=True)
            (contentos_dir(project_dir) / "config.json").write_text(
                "{not valid json", encoding="utf-8"
            )

            with self.assertRaises(ConfigError):
                load_config(project_dir)

    def test_load_config_rejects_empty_competitors_and_bad_types(self) -> None:
        cases = [
            {"competitors": []},
            {"competitors": [""]},
            {"competitors": ["   "]},
            {"competitors": [123]},
            {"competitors": "acme"},
            {"competitors": ["acme"], "lookback_days": 0},
            {"competitors": ["acme"], "lookback_days": -5},
            {"competitors": ["acme"], "lookback_days": "90"},
            {"competitors": ["acme"], "lookback_days": True},
            {"competitors": ["acme"], "outlier_threshold": 0},
            {"competitors": ["acme"], "qa_pass_threshold": 0},
            {"competitors": ["acme"], "qa_pass_threshold": 11},
            {"competitors": ["acme"], "length_tolerance": 0},
            {"competitors": ["acme"], "length_tolerance": 1.5},
            {"competitors": ["acme"], "video_source": "s3"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with temp_project() as project_dir:
                    _write_config(project_dir, overrides)

                    with self.assertRaises(ConfigError):
                        load_config(project_dir)


class ResolveRunTests(NoNetworkTestCase):
    def test_resolve_latest_picks_newest_and_unknown_raises(self) -> None:
        with temp_project() as project_dir:
            # Created out of chronological order, to prove resolve_run
            # sorts by directory name rather than creation/mtime order.
            for run_id in (
                "20260101-000000",
                "20260916-120000",
                "20260501-000000",
            ):
                run_dir(project_dir, run_id).mkdir(parents=True)

            resolved = resolve_run(project_dir, "latest")

            self.assertEqual(resolved, run_dir(project_dir, "20260916-120000"))

            with self.assertRaises(RunNotFound):
                resolve_run(project_dir, "does-not-exist")

    def test_resolve_run_missing_runs_dir_raises(self) -> None:
        # Resolution: "A missing runs/ directory also raises RunNotFound."
        with temp_project() as project_dir:
            with self.assertRaises(RunNotFound):
                resolve_run(project_dir, "latest")

            with self.assertRaises(RunNotFound):
                resolve_run(project_dir, "20260101-000000")


class InitRunTests(NoNetworkTestCase):
    def test_init_run_writes_run_json_shape(self) -> None:
        with temp_project() as project_dir:
            config = dict(DEFAULT_CONFIG, competitors=["acme"])

            created = init_run(project_dir, config, "mock")

            self.assertTrue(created.is_dir())
            data = read_json(created / "run.json")

        self.assertEqual(created, run_dir(project_dir, data["run_id"]))
        self.assertEqual(data["mode"], "mock")
        self.assertEqual(data["config"], config)
        self.assertEqual(data["stages"], {})
        self.assertEqual(data["costs"], {})
        self.assertEqual(data["apify_runs"], {})
        self.assertEqual(data["warnings"], [])

        # created_at is ISO 8601 with a UTC offset.
        parsed = datetime.fromisoformat(data["created_at"])
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), timedelta(0))

    def test_init_run_rejects_bad_mode(self) -> None:
        with temp_project() as project_dir:
            config = dict(DEFAULT_CONFIG, competitors=["acme"])

            with self.assertRaises(ValueError):
                init_run(project_dir, config, "fast")

    def test_init_run_config_is_deep_copy(self) -> None:
        with temp_project() as project_dir:
            config = dict(DEFAULT_CONFIG, competitors=["acme"])

            created = init_run(project_dir, config, "live")
            config["competitors"].append("mutated-after-init")

            data = read_json(created / "run.json")

        self.assertEqual(data["config"]["competitors"], ["acme"])


class InitRunCollisionTests(NoNetworkTestCase):
    def test_init_run_collision_suffixes_instead_of_overwriting(self) -> None:
        with temp_project() as project_dir:
            config = dict(DEFAULT_CONFIG, competitors=["acme"])

            with mock.patch(
                "lib.store.new_run_id", return_value="20260916-213000"
            ):
                first_dir = init_run(project_dir, config, "mock")
                update_run(
                    first_dir,
                    stages={"research": "done"},
                    warnings="first run warning",
                )
                first_run_before = read_json(first_dir / "run.json")

                second_dir = init_run(project_dir, config, "mock")

            first_run_after = read_json(first_dir / "run.json")
            second_run = read_json(second_dir / "run.json")

        # The first run's accumulated state must be untouched.
        self.assertEqual(first_run_before, first_run_after)
        self.assertEqual(first_dir.name, "20260916-213000")

        # The second run gets a lexically-later, zero-padded suffixed id,
        # recorded consistently in both its directory name and its own
        # run.json.
        self.assertEqual(second_dir.name, "20260916-213000-02")
        self.assertEqual(second_run["run_id"], second_dir.name)
        self.assertGreater(second_dir.name, first_dir.name)

    def test_init_run_collision_suffixes_keep_latest_ordering(self) -> None:
        # Regression test: unpadded suffixes ("-2", "-9", "-10", ...)
        # sort lexically out of numeric order once there are 10+
        # collisions ("-9" > "-10" as strings), which made
        # resolve_run(..., "latest") silently return a stale run.
        with temp_project() as project_dir:
            config = dict(DEFAULT_CONFIG, competitors=["acme"])
            base_id = "20260916-213000"

            with mock.patch("lib.store.new_run_id", return_value=base_id):
                # 1 base run, then 11 forced collisions (suffixes
                # -02..-12), then one more call under test (suffix -13):
                # this crosses the single-digit -> double-digit boundary
                # ("-09" -> "-10") that broke unpadded suffixes.
                created = [init_run(project_dir, config, "mock") for _ in range(13)]

            newest = created[-1]
            self.assertEqual(newest.name, f"{base_id}-13")

            latest = resolve_run(project_dir, "latest")

        self.assertEqual(latest, newest)

        # Zero-padding means lexical order now matches creation
        # (numeric) order for every suffix in this range.
        names = [created_dir.name for created_dir in created]
        self.assertEqual(sorted(names), names)

    def test_init_run_raises_run_exists_when_all_suffixes_taken(self) -> None:
        with temp_project() as project_dir:
            config = dict(DEFAULT_CONFIG, competitors=["acme"])
            base_id = "20260916-213000"

            with mock.patch("lib.store.new_run_id", return_value=base_id):
                run_dir(project_dir, base_id).mkdir(parents=True)
                for suffix in range(2, 100):
                    run_dir(project_dir, f"{base_id}-{suffix:02d}").mkdir(
                        parents=True
                    )

                with self.assertRaises(RunExists):
                    init_run(project_dir, config, "mock")


class UpdateRunTests(NoNetworkTestCase):
    def test_update_run_merges_nested_and_appends_warnings(self) -> None:
        with temp_project() as project_dir:
            config = dict(DEFAULT_CONFIG, competitors=["acme"])
            created = init_run(project_dir, config, "mock")

            update_run(
                created,
                stages={"research": "done"},
                costs={"apify": 0.5},
                apify_runs={"reels": "run-1"},
            )
            updated = update_run(
                created,
                stages={"direct": "running"},
                apify_runs={"details": "run-2"},
                warnings="be careful",
            )

            self.assertEqual(
                updated["stages"], {"research": "done", "direct": "running"}
            )
            self.assertEqual(updated["costs"], {"apify": 0.5})
            self.assertEqual(
                updated["apify_runs"], {"reels": "run-1", "details": "run-2"}
            )
            self.assertEqual(updated["warnings"], ["be careful"])

            updated = update_run(created, warnings=["another", "one more"])
            self.assertEqual(
                updated["warnings"], ["be careful", "another", "one more"]
            )

            updated = update_run(created, mode="live")
            self.assertEqual(updated["mode"], "live")

            on_disk = read_json(created / "run.json")

        self.assertEqual(on_disk, updated)


class EnsureGitignoreTests(NoNetworkTestCase):
    def test_ensure_gitignore_idempotent(self) -> None:
        with temp_project() as project_dir:
            ensure_gitignore(project_dir)
            gitignore_path = contentos_dir(project_dir) / ".gitignore"
            first_text = gitignore_path.read_text(encoding="utf-8")

            for line in (
                ".env",
                "runs/*/videos/",
                "runs/*/frames/",
                "setup-answers.json",
            ):
                self.assertIn(line, first_text.splitlines())

            ensure_gitignore(project_dir)
            second_text = gitignore_path.read_text(encoding="utf-8")

        self.assertEqual(first_text, second_text)

    def test_ensure_gitignore_preserves_founder_edits(self) -> None:
        with temp_project() as project_dir:
            contentos_dir(project_dir).mkdir(parents=True, exist_ok=True)
            gitignore_path = contentos_dir(project_dir) / ".gitignore"
            gitignore_path.write_text(
                "# founder note\ncustom-thing/\n", encoding="utf-8"
            )

            ensure_gitignore(project_dir)
            lines = gitignore_path.read_text(encoding="utf-8").splitlines()

        self.assertIn("# founder note", lines)
        self.assertIn("custom-thing/", lines)
        for line in (
            ".env",
            "runs/*/videos/",
            "runs/*/frames/",
            "setup-answers.json",
        ):
            self.assertIn(line, lines)


class ReadRulesTests(NoNetworkTestCase):
    def test_read_rules_empty_vs_present(self) -> None:
        with temp_project() as project_dir:
            self.assertEqual(read_rules(project_dir), "")

            contentos_dir(project_dir).mkdir(parents=True, exist_ok=True)
            rules_path = contentos_dir(project_dir) / "rules.md"
            rules_path.write_text(
                "\n".join(
                    [
                        "# founder corrections",
                        "",
                        "Never use the word cheap.",
                        "# a comment in the middle",
                        "Always show the price on screen.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            rules_text = read_rules(project_dir)

        self.assertEqual(
            rules_text,
            "Never use the word cheap.\nAlways show the price on screen.",
        )

    def test_read_rules_blank_when_only_comments_or_blank_lines(self) -> None:
        with temp_project() as project_dir:
            contentos_dir(project_dir).mkdir(parents=True, exist_ok=True)
            (contentos_dir(project_dir) / "rules.md").write_text(
                "# just a comment\n\n   \n", encoding="utf-8"
            )

            self.assertEqual(read_rules(project_dir), "")


if __name__ == "__main__":
    unittest.main()
