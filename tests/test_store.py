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
    check_discovery_config,
    contentos_dir,
    ensure_gitignore,
    init_run,
    load_config,
    load_discovery_config,
    new_run_id,
    read_json,
    read_rules,
    resolve_run,
    run_dir,
    update_config_keys,
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


    def test_load_config_raises_config_error_on_an_unreadable_file(self) -> None:
        # A config.json that is not UTF-8 text, or that is a directory in
        # the file's place, is an invalid config, not a traceback.
        with temp_project() as project_dir:
            config_path = contentos_dir(project_dir) / "config.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_bytes(b'{"competitors": ["\xff\xfe"]}')

            with self.assertRaises(ConfigError) as ctx:
                load_config(project_dir)

            self.assertIn("config.json", str(ctx.exception))

        with temp_project() as project_dir:
            config_path = contentos_dir(project_dir) / "config.json"
            config_path.mkdir(parents=True)

            with self.assertRaises(ConfigError):
                load_config(project_dir)

    def test_load_config_rejects_a_fractional_count(self) -> None:
        # These keys end up as slice bounds, range() arguments, and
        # counts of things (outliers.py's top_k_videos/backfill_pool,
        # frames.py's frames_per_reel, direct.py's briefs), where a float
        # is either a TypeError or a silently wrong answer.
        for key in (
            "reels_per_account",
            "min_reels_for_median",
            "top_k_videos",
            "backfill_pool",
            "max_per_account",
            "frames_per_reel",
            "briefs",
            "parallel_agents",
            "apify_timeout_s",
            "poll_interval_s",
        ):
            for bad in (3.0, 2.5):
                with self.subTest(key=key, value=bad):
                    with temp_project() as project_dir:
                        _write_config(project_dir, {"competitors": ["acme"], key: bad})

                        with self.assertRaises(ConfigError) as ctx:
                            load_config(project_dir)

                        self.assertIn(key, str(ctx.exception))

    def test_load_config_still_accepts_a_float_for_a_measurement_key(self) -> None:
        # These are thresholds and limits, not counts, so a float is a
        # legitimate value a creator might tune to.
        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {
                    "competitors": ["acme"],
                    "outlier_threshold": 3.5,
                    "apify_max_charge_usd": 2.5,
                    "max_video_mb": 40.5,
                    "length_tolerance": 0.15,
                },
            )

            cfg = load_config(project_dir)

        self.assertEqual(cfg["outlier_threshold"], 3.5)
        self.assertEqual(cfg["apify_max_charge_usd"], 2.5)
        self.assertEqual(cfg["max_video_mb"], 40.5)
        self.assertEqual(cfg["length_tolerance"], 0.15)


class FormatAccountsConfigTests(NoNetworkTestCase):
    def test_default_config_has_format_accounts_and_max_format_briefs(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["format_accounts"], [])
        self.assertEqual(DEFAULT_CONFIG["max_format_briefs"], 2)

    def test_load_config_accepts_empty_format_accounts_and_rejects_bad_entries(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acme"]})

            config = load_config(project_dir)

        self.assertEqual(config["format_accounts"], [])

        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {"competitors": ["acme"], "format_accounts": ["dailywins", "habitlab"]},
            )

            config = load_config(project_dir)

        self.assertEqual(config["format_accounts"], ["dailywins", "habitlab"])

        bad_cases = [
            {"competitors": ["acme"], "format_accounts": "dailywins"},
            {"competitors": ["acme"], "format_accounts": [""]},
            {"competitors": ["acme"], "format_accounts": ["   "]},
            {"competitors": ["acme"], "format_accounts": [123]},
            {"competitors": ["acme"], "format_accounts": ["dailywins", None]},
        ]
        for overrides in bad_cases:
            with self.subTest(overrides=overrides):
                with temp_project() as project_dir:
                    _write_config(project_dir, overrides)

                    with self.assertRaises(ConfigError):
                        load_config(project_dir)

    def test_load_config_drops_format_accounts_already_a_competitor(self) -> None:
        # Resolution (task-3 controller ruling): a handle in both lists
        # stays a competitor. setup.py already enforces this when it
        # writes config.json; load_config enforces it too, so a
        # hand-edited config.json is just as safe -- case-insensitively,
        # since Instagram handles are case-insensitive.
        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {
                    "competitors": ["acme", "DailyWins"],
                    "format_accounts": ["dailywins", "ghostaccount", "Acme"],
                },
            )

            config = load_config(project_dir)

        self.assertEqual(config["competitors"], ["acme", "DailyWins"])
        self.assertEqual(config["format_accounts"], ["ghostaccount"])

    def test_load_config_rejects_negative_or_non_integer_max_format_briefs(self) -> None:
        # 0 is the interesting boundary here: max_format_briefs is a cap,
        # not a count that must be positive like the _NUMERIC_CONFIG_KEYS
        # loop enforces elsewhere, so 0 (no format briefs at all) is a
        # legitimate creator choice and must be accepted.
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acme"], "max_format_briefs": 0})

            config = load_config(project_dir)

        self.assertEqual(config["max_format_briefs"], 0)

        for bad in (-1, -5, 1.5, 2.0, "2", True, False, None):
            with self.subTest(bad=bad):
                with temp_project() as project_dir:
                    _write_config(
                        project_dir, {"competitors": ["acme"], "max_format_briefs": bad}
                    )

                    with self.assertRaises(ConfigError):
                        load_config(project_dir)


class SpecificityConfigTests(NoNetworkTestCase):
    """0.3.0 config keys: transcripts and the QA specificity floor."""

    def test_defaults(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["transcripts"], "auto")
        self.assertIs(DEFAULT_CONFIG["apify_transcripts"], False)
        self.assertEqual(DEFAULT_CONFIG["apify_transcript_usd_per_min"], 0.0)
        self.assertEqual(DEFAULT_CONFIG["whisper_model"], "")
        self.assertEqual(DEFAULT_CONFIG["min_specifics"], 3)

    def test_defaults_load(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acme"]})
            config = load_config(project_dir)
        self.assertEqual(config["transcripts"], "auto")
        self.assertEqual(config["min_specifics"], 3)

    def test_accepts_valid_values(self) -> None:
        good = {
            "competitors": ["acme"],
            "transcripts": "local",
            "apify_transcripts": True,
            "apify_transcript_usd_per_min": 0.02,
            "whisper_model": "/tmp/ggml-small.bin",
            "min_specifics": 5,
        }
        with temp_project() as project_dir:
            _write_config(project_dir, good)
            config = load_config(project_dir)
        self.assertEqual(config["apify_transcript_usd_per_min"], 0.02)

    def test_rejects_bad_values(self) -> None:
        bad_cases = [
            {"transcripts": "cloud"},
            {"transcripts": None},
            {"apify_transcripts": "yes"},
            {"apify_transcript_usd_per_min": -0.01},
            {"apify_transcript_usd_per_min": "0.02"},
            {"apify_transcript_usd_per_min": True},
            {"whisper_model": 3},
            {"min_specifics": 0},
            {"min_specifics": 2.5},
            # A paid backend with no price would estimate $0 and slip past
            # the cost cap, so it has to be priced before it can be on.
            {"apify_transcripts": True, "apify_transcript_usd_per_min": 0},
        ]
        for overrides in bad_cases:
            with self.subTest(overrides=overrides):
                with temp_project() as project_dir:
                    _write_config(project_dir, {"competitors": ["acme"], **overrides})
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

    def test_resolve_run_rejects_path_traversal_and_absolute_refs(self) -> None:
        # A run ref reaches resolve_run straight off the command line, so
        # it must never be able to point outside runs/.
        with temp_project() as project_dir:
            run_dir(project_dir, "20260101-000000").mkdir(parents=True)
            outside = project_dir / "outside"
            outside.mkdir()

            bad_refs = [
                "..",
                "../..",
                "../../..",
                "20260101-000000/..",
                "20260101-000000/../20260101-000000",
                "./20260101-000000",
                "../outside",
                str(outside),
                str(run_dir(project_dir, "20260101-000000")),
                "/",
                "/tmp",
                ".",
            ]
            for ref in bad_refs:
                with self.subTest(ref=ref):
                    with self.assertRaises(RunNotFound):
                        resolve_run(project_dir, ref)

            # The plain run id still resolves.
            self.assertEqual(
                resolve_run(project_dir, "20260101-000000"),
                run_dir(project_dir, "20260101-000000"),
            )

    def test_resolve_latest_skips_directories_that_are_not_runs(self) -> None:
        # runs/ can pick up a stray directory: an editor's backup folder,
        # a half-copied run, anything the creator dropped in. "latest"
        # must not hand one of those back as if it were a run.
        with temp_project() as project_dir:
            for name in (
                "20260101-000000",
                "20260501-000000-02",
                "zzz-scratch",
                "tmp",
                "20260501",
                "20260501-000000-2",
            ):
                run_dir(project_dir, name).mkdir(parents=True)

            self.assertEqual(
                resolve_run(project_dir, "latest"),
                run_dir(project_dir, "20260501-000000-02"),
            )

        with temp_project() as project_dir:
            run_dir(project_dir, "zzz-scratch").mkdir(parents=True)

            with self.assertRaises(RunNotFound):
                resolve_run(project_dir, "latest")

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
                "runs/*/prompts/",
                "setup-answers.json",
            ):
                self.assertIn(line, first_text.splitlines())

            ensure_gitignore(project_dir)
            second_text = gitignore_path.read_text(encoding="utf-8")

        self.assertEqual(first_text, second_text)

    def test_ensure_gitignore_preserves_creator_edits(self) -> None:
        with temp_project() as project_dir:
            contentos_dir(project_dir).mkdir(parents=True, exist_ok=True)
            gitignore_path = contentos_dir(project_dir) / ".gitignore"
            gitignore_path.write_text(
                "# creator note\ncustom-thing/\n", encoding="utf-8"
            )

            ensure_gitignore(project_dir)
            lines = gitignore_path.read_text(encoding="utf-8").splitlines()

        self.assertIn("# creator note", lines)
        self.assertIn("custom-thing/", lines)
        for line in (
            ".env",
            "runs/*/videos/",
            "runs/*/frames/",
            "runs/*/prompts/",
            "setup-answers.json",
        ):
            self.assertIn(line, lines)


class WeeklyConfigTests(NoNetworkTestCase):
    def test_weekly_defaults(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["lookback_days"], 14)
        self.assertEqual(DEFAULT_CONFIG["briefs"], 20)
        self.assertEqual(DEFAULT_CONFIG["min_outlier_ratio"], 2.0)
        self.assertEqual(DEFAULT_CONFIG["carry_weeks"], 2)
        self.assertEqual(DEFAULT_CONFIG["fill_ideas"], 8)
        self.assertEqual(DEFAULT_CONFIG["auto_scripts"], 3)

    def test_zero_turns_off_carry_and_fill(self) -> None:
        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {"competitors": ["a"], "carry_weeks": 0, "fill_ideas": 0},
            )
            config = load_config(project_dir)
        self.assertEqual((config["carry_weeks"], config["fill_ideas"]), (0, 0))

    def test_rejects_bad_weekly_values(self) -> None:
        bad = [
            {"min_outlier_ratio": 0},
            {"min_outlier_ratio": "2"},
            {"carry_weeks": -1},
            {"carry_weeks": 1.5},
            {"fill_ideas": True},
            {"auto_scripts": 0},
            {"auto_scripts": 2.5},
        ]
        for override in bad:
            with self.subTest(override=override), temp_project() as project_dir:
                _write_config(project_dir, dict({"competitors": ["a"]}, **override))
                with self.assertRaises(ConfigError):
                    load_config(project_dir)


class PaidPartnershipConfigTests(NoNetworkTestCase):
    def test_filter_defaults_to_on(self) -> None:
        self.assertIs(DEFAULT_CONFIG["exclude_paid_partnerships"], True)

    def test_filter_can_be_turned_off(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acme"], "exclude_paid_partnerships": False})
            self.assertIs(load_config(project_dir)["exclude_paid_partnerships"], False)

    def test_rejects_a_value_that_is_not_a_boolean(self) -> None:
        for value in ("no", 0, None):
            with self.subTest(value=value):
                with temp_project() as project_dir:
                    _write_config(
                        project_dir, {"competitors": ["acme"], "exclude_paid_partnerships": value}
                    )
                    with self.assertRaises(ConfigError) as caught:
                        load_config(project_dir)
                self.assertIn("exclude_paid_partnerships", str(caught.exception))


class ReadRulesTests(NoNetworkTestCase):
    def test_read_rules_empty_vs_present(self) -> None:
        with temp_project() as project_dir:
            self.assertEqual(read_rules(project_dir), "")

            contentos_dir(project_dir).mkdir(parents=True, exist_ok=True)
            rules_path = contentos_dir(project_dir) / "rules.md"
            rules_path.write_text(
                "\n".join(
                    [
                        "# creator corrections",
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


class DiscoverConfigTests(NoNetworkTestCase):
    def test_discover_defaults(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["discover_min_followers"], 10000)
        self.assertEqual(DEFAULT_CONFIG["discover_candidates"], 25)
        self.assertEqual(DEFAULT_CONFIG["discover_shortlist"], 20)
        self.assertEqual(DEFAULT_CONFIG["discover_min_views"], 5000)
        self.assertEqual(DEFAULT_CONFIG["discover_post_every_days"], 14)

    def test_min_followers_zero_turns_the_floor_off(self) -> None:
        with temp_project() as project:
            _write_config(project, {"discover_min_followers": 0})
            self.assertEqual(load_discovery_config(project)["discover_min_followers"], 0)

    def test_rejects_bad_discover_values(self) -> None:
        bad = [
            ({"discover_shortlist": 0}, "discover_shortlist"),
            ({"discover_shortlist": -1}, "discover_shortlist"),
            ({"discover_shortlist": 2.5}, "discover_shortlist"),
            ({"discover_shortlist": True}, "discover_shortlist"),
            ({"discover_min_views": 0}, "discover_min_views"),
            ({"discover_min_views": -1}, "discover_min_views"),
            ({"discover_min_views": "5000"}, "discover_min_views"),
            ({"discover_post_every_days": 0}, "discover_post_every_days"),
            ({"discover_post_every_days": 6}, "discover_post_every_days"),
            ({"discover_post_every_days": 91}, "discover_post_every_days"),
            ({"discover_post_every_days": 7.5}, "discover_post_every_days"),
            ({"discover_min_followers": -1}, "discover_min_followers"),
            ({"discover_min_followers": 1.5}, "discover_min_followers"),
        ]
        for override, key in bad:
            with self.subTest(override=override), temp_project() as project:
                _write_config(project, override)
                with self.assertRaises(ConfigError) as ctx:
                    load_discovery_config(project)
                self.assertIn(key, str(ctx.exception))

    def test_the_cadence_dial_runs_from_7_to_90(self) -> None:
        # Below 7 the bar needs more reels than the 15 scraped per creator.
        for days in (7, 90):
            with self.subTest(days=days), temp_project() as project:
                _write_config(project, {"discover_post_every_days": days})
                self.assertEqual(load_discovery_config(project)["discover_post_every_days"], days)
        for days in (6, 91):
            with self.subTest(days=days), temp_project() as project:
                _write_config(project, {"discover_post_every_days": days})
                with self.assertRaises(ConfigError) as ctx:
                    load_discovery_config(project)
                self.assertEqual(str(ctx.exception), "discover_post_every_days must be a whole number from 7 to 90")

    def test_min_views_may_be_a_fraction(self) -> None:
        with temp_project() as project:
            _write_config(project, {"discover_min_views": 2500.5})
            self.assertEqual(load_discovery_config(project)["discover_min_views"], 2500.5)

    def test_check_discovery_config_allows_no_competitors(self) -> None:
        check_discovery_config(dict(DEFAULT_CONFIG))
        with self.assertRaises(ConfigError):
            check_discovery_config(dict(DEFAULT_CONFIG, discover_shortlist=0))


class UpdateConfigKeysTests(NoNetworkTestCase):
    def test_merges_and_keeps_every_other_key(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["a"], "briefs": 7, "my_note": "keep"})
            merged = update_config_keys(project, {"discover_min_followers": 50000})
            on_disk = read_json(contentos_dir(project) / "config.json")
        expected = {"competitors": ["a"], "briefs": 7, "my_note": "keep", "discover_min_followers": 50000}
        self.assertEqual(on_disk, expected)
        self.assertEqual(merged, expected)

    def test_refuses_a_bad_value_and_writes_nothing(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["a"]})
            with self.assertRaises(ConfigError):
                update_config_keys(project, {"discover_shortlist": 0})
            self.assertEqual(read_json(contentos_dir(project) / "config.json"), {"competitors": ["a"]})

    def test_needs_an_existing_config(self) -> None:
        with temp_project() as project:
            with self.assertRaises(ConfigError):
                update_config_keys(project, {"discover_shortlist": 5})


if __name__ == "__main__":
    unittest.main()
