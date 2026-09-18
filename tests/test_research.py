"""Tests for lib/research.py: the `research` command end to end (mock first).

Task 10 composes `lib/apify.py`, `lib/instagram.py`, `lib/outliers.py`, and
`lib/store.py` into `run_research` and the `contentos.py research` CLI
wiring. See the design spec's "Stage 1 -- research" section for the flow
and `.superpowers/sdd/trying-to-make-a-clever-ritchie/task-10-brief.md`
for the exact interface. Nothing here touches the network:
`apify.FixtureTransport` (mock mode) and a small scripted transport built
here (resume polling) stand in for the real Apify API; NoNetworkTestCase
is also a second line of defense.
"""
from __future__ import annotations

import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

from tests.helpers import NoNetworkTestCase, run_cli, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import apify, codes, store  # noqa: E402
from lib import research  # noqa: E402
from lib.env import Keys  # noqa: E402

# The fixture handles Task 8/9 designed the sample files around: three
# healthy accounts plus "ghostaccount", which apify_reels_sample.json
# carries as an `error: not_found` item -- exercising the per-account
# not_found path without any hand-rolled fixture data.
FIXTURE_HANDLES = ["sproutapp", "habitlab", "dailywins", "ghostaccount"]

NO_GLOBAL_ENV = {"CONTENTOS_CONFIG_DIR": ""}


def _write_config(project: Path, overrides: Dict[str, Any]) -> None:
    """Write `<project>/.contentos/config.json` with exactly `overrides`."""
    config_dir = project / ".contentos"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(overrides), encoding="utf-8")


def _mock_keys() -> Keys:
    """A Keys value with no resolved token, as `--mock` needs none."""
    return Keys(apify=None, source=None, warnings=[])


def _run_research_capturing(**kwargs: Any):
    """Call research.run_research in-process, capturing stdout and logs.

    Returns (result, stdout_text, log_lines) so tests can inspect the
    RESULT/summary output without leaking prints into the test run.
    """
    logs: List[str] = []
    kwargs.setdefault("log", logs.append)
    buffer = StringIO()
    with redirect_stdout(buffer):
        result = research.run_research(**kwargs)
    return result, buffer.getvalue(), logs


class _ScriptedTransport:
    """Returns/raises each scripted response from `.request_json` in order.

    Mirrors test_apify.py's ScriptedApifyTransport (FixtureTransport always
    reports SUCCEEDED on the first poll, which is too simple to prove
    --resume never starts a new run) but is kept local here so this
    module does not reach into another test module's internals.
    """

    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def request_json(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "json_body": json_body, "params": params}
        )
        if not self._responses:
            raise AssertionError("scripted transport exhausted")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class MockResearchTests(NoNetworkTestCase):
    def test_mock_research_writes_01_02_and_run_json(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": FIXTURE_HANDLES})
            cfg = store.load_config(project_dir)

            # This test is about the shape of 01-reels/01-profiles/
            # 02-outliers/run.json that steps 1-6 produce, not about
            # downloads (step 7, Task 11) -- no_download=True keeps every
            # reel's video_status "pending" as asserted below. See
            # tests/test_video.py for download_selected's own coverage,
            # including test_research_mock_downloads_by_default_and_
            # no_download_skips for this same CLI wiring with downloads on.
            result, stdout_text, _logs = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
                no_download=True,
            )

            run_dir = Path(result["run_dir"])
            reels_path = run_dir / "01-reels.json"
            profiles_path = run_dir / "01-profiles.json"
            outliers_path = run_dir / "02-outliers.json"
            run_json_path = run_dir / "run.json"

            for path in (reels_path, profiles_path, outliers_path, run_json_path):
                self.assertTrue(path.exists(), f"missing {path}")

            reels = store.read_json(reels_path)
            profiles = store.read_json(profiles_path)
            outliers_doc = store.read_json(outliers_path)
            run_doc = store.read_json(run_json_path)

        # Whole-pipeline numbers, pinned against the fixtures' designed
        # story (also cross-checked by test_outliers.py's
        # test_fixture_outliers_selected): 17 normalized reels across the
        # three healthy accounts (one, SPA004, has no usable play count;
        # one, DWN007, is pinned and dropped before normalization even
        # produces it), 9 pass every select_outliers filter with room
        # under the default top_k_videos=20, so none spill into backfill.
        self.assertEqual(result["mode"], "mock")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["accounts"], 4)
        self.assertEqual(result["reels_total"], 17)
        self.assertEqual(result["selected"], 9)
        self.assertEqual(result["backfill"], 0)
        self.assertEqual(result["excluded"], 8)
        self.assertIn("ghostaccount: not_found", result["warnings"])

        self.assertEqual(len(reels), 17)
        self.assertNotIn("video_status", reels[0])

        self.assertIn("sproutapp", profiles)
        self.assertIn("habitlab", profiles)
        self.assertIn("dailywins", profiles)
        self.assertNotIn("ghostaccount", profiles)

        self.assertEqual(
            set(outliers_doc),
            {"selected", "backfill", "excluded", "account_status", "baselines"},
        )
        self.assertEqual(len(outliers_doc["selected"]), 9)
        self.assertEqual(outliers_doc["backfill"], [])
        self.assertEqual(len(outliers_doc["excluded"]), 8)
        for reel in outliers_doc["selected"] + outliers_doc["backfill"]:
            self.assertEqual(reel["video_status"], "pending")
            self.assertEqual(reel["frames_status"], "pending")

        self.assertEqual(
            outliers_doc["account_status"],
            {"sproutapp": "ok", "habitlab": "ok", "dailywins": "ok", "ghostaccount": "not_found"},
        )
        self.assertEqual(set(outliers_doc["baselines"]), {"sproutapp", "habitlab", "dailywins"})
        for handle in ("sproutapp", "habitlab", "dailywins"):
            baseline = outliers_doc["baselines"][handle]
            self.assertEqual(set(baseline), {"median", "n", "confidence", "metric"})
            self.assertEqual(baseline["confidence"], "low")
            self.assertEqual(baseline["metric"], "plays")

        research_stage = run_doc["stages"]["research"]
        self.assertEqual(research_stage["status"], "ok")
        self.assertEqual(research_stage["reels_total"], 17)
        self.assertEqual(research_stage["selected"], 9)
        self.assertEqual(research_stage["backfill"], 0)
        self.assertEqual(research_stage["excluded"], 8)
        self.assertIn("started_at", research_stage)
        self.assertIn("finished_at", research_stage)
        self.assertEqual(run_doc["costs"]["apify"]["max_items"], 120)
        self.assertEqual(run_doc["apify_runs"]["reels"]["id"], "mock-reels")
        self.assertEqual(run_doc["apify_runs"]["details"]["id"], "mock-details")
        self.assertIn("ghostaccount: not_found", run_doc["warnings"])

        # A per-account summary line precedes the final RESULT line.
        for handle in FIXTURE_HANDLES:
            self.assertIn(handle, stdout_text)

    def test_partial_account_failure_still_writes_and_warns(self) -> None:
        # ghostaccount's `error: not_found` fixture item is an account-level
        # failure, not an Apify run failure: the pipeline must still run to
        # completion, write every output file, and warn about that one
        # account rather than aborting the whole research stage.
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": FIXTURE_HANDLES})
            cfg = store.load_config(project_dir)

            result, _stdout_text, _logs = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
            )

            run_dir = Path(result["run_dir"])
            outliers_doc = store.read_json(run_dir / "02-outliers.json")
            run_doc = store.read_json(run_dir / "run.json")

        self.assertEqual(result["status"], "ok")
        self.assertIn("ghostaccount: not_found", result["warnings"])
        self.assertEqual(outliers_doc["account_status"]["ghostaccount"], "not_found")
        self.assertEqual(run_doc["stages"]["research"]["status"], "ok")
        self.assertIn("ghostaccount: not_found", run_doc["warnings"])
        # No reel can belong to an account Apify never found.
        self.assertTrue(
            all(
                reel["ownerUsername"].lower() != "ghostaccount"
                for reel in outliers_doc["selected"] + outliers_doc["backfill"]
            )
        )

    def test_resume_repolls_recorded_run_ids(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acct1"]})
            cfg = store.load_config(project_dir)
            run_dir = store.init_run(project_dir, cfg, "mock")

            reels_input = apify.build_reels_input(
                ["acct1"], cfg["reels_per_account"], cfg["baseline_lookback_days"]
            )
            details_input = apify.build_details_input(["acct1"])
            store.update_run(
                run_dir,
                apify_runs={
                    "reels": {"id": "run-reels-1", "dataset_id": "ds-reels-1", "input": reels_input},
                    "details": {
                        "id": "run-details-1", "dataset_id": "ds-details-1", "input": details_input
                    },
                },
            )

            reel_item = {
                "shortCode": "ZZZ001",
                "url": "https://www.instagram.com/reel/ZZZ001/",
                "caption": "test",
                "hashtags": [],
                "mentions": [],
                "ownerUsername": "acct1",
                "timestamp": "2026-09-10T00:00:00.000Z",
                "likesCount": 100,
                "commentsCount": 5,
                "videoPlayCount": 9000,
                "videoViewCount": 9500,
                "videoDuration": 15.0,
                "videoUrl": "https://example.invalid/videos/ZZZ001.mp4",
                "displayUrl": "https://example.invalid/covers/ZZZ001.jpg",
                "productType": "clips",
                "isPinned": False,
                "latestComments": [],
                "musicInfo": None,
            }
            profile_item = {
                "username": "acct1",
                "followersCount": 5000,
                "postsCount": 10,
                "verified": False,
                "private": False,
            }

            transport = _ScriptedTransport(
                [
                    {"data": {"id": "run-reels-1", "status": "SUCCEEDED", "defaultDatasetId": "ds-reels-1"}},
                    {
                        "data": {
                            "id": "run-details-1", "status": "SUCCEEDED", "defaultDatasetId": "ds-details-1"
                        }
                    },
                    [reel_item],
                    [profile_item],
                ]
            )

            result, _stdout_text, _logs = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=run_dir.name,
                transport=transport,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["run_id"], run_dir.name)

        post_calls = [c for c in transport.calls if c["method"] == "POST"]
        self.assertEqual(post_calls, [], "resume must never start a new Apify run")

        dataset_urls = {
            c["url"] for c in transport.calls if c["url"].startswith(f"{apify.API_BASE}/datasets/")
        }
        self.assertEqual(
            dataset_urls,
            {
                f"{apify.API_BASE}/datasets/ds-reels-1/items",
                f"{apify.API_BASE}/datasets/ds-details-1/items",
            },
        )
        poll_urls = [c["url"] for c in transport.calls if c["url"].startswith(f"{apify.API_BASE}/actor-runs/")]
        self.assertEqual(
            poll_urls,
            [f"{apify.API_BASE}/actor-runs/run-reels-1", f"{apify.API_BASE}/actor-runs/run-details-1"],
        )


class FormatAccountsTests(NoNetworkTestCase):
    """format_accounts: scraped after competitors, tagged source_kind, counted in RESULT."""

    def test_research_scrapes_competitors_then_format_accounts_and_estimates_on_the_total(
        self,
    ) -> None:
        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {"competitors": ["sproutapp", "habitlab"], "format_accounts": ["dailywins"]},
            )

            # The estimate (printed on the exit-3 confirmation path) must
            # already be on the total of 3 accounts, not just the 2
            # competitors.
            code, out, _err = run_cli(
                ["research", "--mock", "--project", str(project_dir)], env=NO_GLOBAL_ENV
            )
            self.assertEqual(code, codes.EXIT_CONFIRM)
            estimate_payload = json.loads(out)
            self.assertEqual(estimate_payload["accounts"], 3)
            self.assertEqual(estimate_payload["max_items"], 3 * 30)
            self.assertEqual(estimate_payload["reels_usd"], round(3 * 30 * 0.0027, 4))

            cfg = store.load_config(project_dir)
            result, _stdout_text, _logs = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
                no_download=True,
            )

            run_dir = Path(result["run_dir"])
            run_doc = store.read_json(run_dir / "run.json")

        self.assertEqual(result["accounts"], 3)
        self.assertEqual(result["format_accounts"], 1)

        # Order in the Apify inputs is competitors then format_accounts,
        # in config order -- never the reverse, never interleaved.
        expected_urls = [
            "https://www.instagram.com/sproutapp/",
            "https://www.instagram.com/habitlab/",
            "https://www.instagram.com/dailywins/",
        ]
        self.assertEqual(run_doc["apify_runs"]["reels"]["input"]["directUrls"], expected_urls)
        self.assertEqual(run_doc["apify_runs"]["details"]["input"]["directUrls"], expected_urls)

    def test_outliers_entries_carry_source_kind(self) -> None:
        # Acceptance: research --mock --yes on a config with
        # format_accounts: ["dailywins"] writes source_kind: "format" on
        # every dailywins reel, and "niche" on every other account's.
        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {"competitors": ["sproutapp", "habitlab"], "format_accounts": ["dailywins"]},
            )
            cfg = store.load_config(project_dir)

            result, _stdout_text, _logs = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
                no_download=True,
            )

            run_dir = Path(result["run_dir"])
            reels = store.read_json(run_dir / "01-reels.json")
            profiles = store.read_json(run_dir / "01-profiles.json")
            outliers_doc = store.read_json(run_dir / "02-outliers.json")

        dailywins_reels = [reel for reel in reels if reel["ownerUsername"] == "dailywins"]
        other_reels = [reel for reel in reels if reel["ownerUsername"] != "dailywins"]
        self.assertTrue(dailywins_reels)
        self.assertTrue(other_reels)
        self.assertTrue(all(reel["source_kind"] == "format" for reel in dailywins_reels))
        self.assertTrue(all(reel["source_kind"] == "niche" for reel in other_reels))

        self.assertEqual(profiles["dailywins"]["source_kind"], "format")
        self.assertEqual(profiles["sproutapp"]["source_kind"], "niche")
        self.assertEqual(profiles["habitlab"]["source_kind"], "niche")

        # 02-outliers.json's selected/backfill entries are copies of
        # scored reels, so source_kind must flow through to them too.
        for reel in outliers_doc["selected"] + outliers_doc["backfill"]:
            expected_kind = "format" if reel["ownerUsername"] == "dailywins" else "niche"
            self.assertEqual(reel["source_kind"], expected_kind)

    def test_summary_table_has_kind_column_and_result_counts_format_accounts(self) -> None:
        with temp_project() as project_dir:
            _write_config(
                project_dir,
                {"competitors": ["sproutapp", "habitlab"], "format_accounts": ["dailywins"]},
            )
            cfg = store.load_config(project_dir)

            result, stdout_text, _logs = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
                no_download=True,
            )

        self.assertEqual(result["format_accounts"], 1)

        lines = stdout_text.splitlines()
        sproutapp_line = next(line for line in lines if line.startswith("sproutapp "))
        habitlab_line = next(line for line in lines if line.startswith("habitlab "))
        dailywins_line = next(line for line in lines if line.startswith("dailywins "))

        self.assertIn(" niche ", sproutapp_line)
        self.assertIn(" niche ", habitlab_line)
        self.assertIn(" format ", dailywins_line)


class UpstreamFailureTests(NoNetworkTestCase):
    """Apify-run-level failures (as opposed to a single account's own
    not_found/private/empty/error status, covered above): a run that
    terminates FAILED becomes exit 5 and marks the stage `failed`; a run
    that comes back TIMED-OUT/partial does not fail the stage, but marks
    it `partial` and warns, still using whatever the dataset has.
    """

    def test_apify_run_failure_exits_5_and_marks_run_failed(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acct1"]})
            cfg = store.load_config(project_dir)

            transport = _ScriptedTransport(
                [
                    {"data": {"id": "run-reels-x", "status": "READY", "defaultDatasetId": "ds-reels-x"}},
                    {"data": {"id": "run-details-x", "status": "READY", "defaultDatasetId": "ds-details-x"}},
                    {"data": {"id": "run-reels-x", "status": "FAILED", "defaultDatasetId": "ds-reels-x"}},
                ]
            )

            with self.assertRaises(research.UpstreamFailure) as ctx:
                _run_research_capturing(
                    project=project_dir,
                    cfg=cfg,
                    keys=_mock_keys(),
                    mock=True,
                    yes=True,
                    estimate_only=False,
                    resume=None,
                    transport=transport,
                )

            self.assertEqual(ctx.exception.exit_code, codes.EXIT_UPSTREAM)
            self.assertIsNone(ctx.exception.payload)

            run_dir = store.resolve_run(project_dir, "latest")
            run_doc = store.read_json(run_dir / "run.json")

        # apify_runs was recorded right after starting, before the poll
        # that then failed -- exactly what makes --resume possible.
        self.assertEqual(run_doc["apify_runs"]["reels"]["id"], "run-reels-x")
        self.assertEqual(run_doc["stages"]["research"]["status"], "failed")
        self.assertIn("FAILED", run_doc["stages"]["research"]["error"])

    def test_partial_apify_run_marks_stage_partial_and_warns(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acct1"]})
            cfg = store.load_config(project_dir)

            transport = _ScriptedTransport(
                [
                    {"data": {"id": "run-reels-y", "status": "READY", "defaultDatasetId": "ds-reels-y"}},
                    {"data": {"id": "run-details-y", "status": "READY", "defaultDatasetId": "ds-details-y"}},
                    {"data": {"id": "run-reels-y", "status": "TIMED-OUT", "defaultDatasetId": "ds-reels-y"}},
                    {"data": {"id": "run-details-y", "status": "SUCCEEDED", "defaultDatasetId": "ds-details-y"}},
                    [],  # reels dataset items: none scraped before the timeout
                    [],  # details dataset items
                ]
            )

            result, _stdout_text, _logs = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
                transport=transport,
            )

            run_dir = Path(result["run_dir"])
            run_doc = store.read_json(run_dir / "run.json")
            # A run coming back partial is not a failure: outputs still get
            # written (checked inside the `with` -- temp_project deletes
            # the directory as soon as the block exits).
            reels_written = (run_dir / "01-reels.json").exists()
            outliers_written = (run_dir / "02-outliers.json").exists()

        self.assertEqual(result["status"], "partial")
        self.assertTrue(
            any("run-reels-y" in warning and "partial" in warning for warning in result["warnings"])
        )
        self.assertEqual(run_doc["stages"]["research"]["status"], "partial")
        self.assertTrue(reels_written)
        self.assertTrue(outliers_written)


class ResumeValidationTests(NoNetworkTestCase):
    def test_resume_without_recorded_runs_exits_2(self) -> None:
        # A run whose apify_runs is still exactly init_run's seed ({}) --
        # e.g. the initial POST never got far enough to record either run
        # -- has nothing for --resume to re-poll. Goes through
        # contentos.main() (not run_research directly) so this also
        # proves the real CLI handler prints the message to stderr and
        # returns exit 2, rather than letting the KeyError this guards
        # against turn into an unhandled traceback.
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acct1"]})
            cfg = store.load_config(project_dir)
            run_dir = store.init_run(project_dir, cfg, "mock")
            self.assertEqual(store.read_json(run_dir / "run.json")["apify_runs"], {})

            transport = _ScriptedTransport([])
            stdout_buffer = StringIO()
            stderr_buffer = StringIO()

            with mock.patch.dict(os.environ, NO_GLOBAL_ENV, clear=True):
                with mock.patch("lib.research._default_transport", return_value=transport):
                    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                        exit_code = contentos.main(
                            [
                                "research", "--mock", "--yes",
                                "--resume", run_dir.name,
                                "--project", str(project_dir),
                            ]
                        )

            reels_written = (run_dir / "01-reels.json").exists()

        self.assertEqual(exit_code, codes.EXIT_USAGE)
        self.assertIn(run_dir.name, stderr_buffer.getvalue())
        self.assertIn("no recorded Apify runs to resume", stderr_buffer.getvalue())
        self.assertEqual(stdout_buffer.getvalue(), "")
        self.assertEqual(transport.calls, [])
        self.assertFalse(reels_written)


class ResearchGateTests(NoNetworkTestCase):
    def test_requires_yes_exit_3_prints_estimate(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp", "habitlab"]})
            code, out, err = run_cli(
                ["research", "--mock", "--project", str(project_dir)], env=NO_GLOBAL_ENV
            )

        self.assertEqual(code, codes.EXIT_CONFIRM)
        payload = json.loads(out)
        self.assertEqual(
            payload,
            {
                "accounts": 2,
                "reels_per_account": 30,
                "reels_usd": 0.162,
                "details_usd": 0.0054,
                "total_usd": 0.1674,
                "max_items": 60,
                "cap_usd": 3.0,
                "within_cap": True,
            },
        )
        self.assertTrue(err.strip())

    def test_estimate_only_exits_3_even_with_yes(self) -> None:
        # --estimate-only stops the run even when the founder also passed
        # --yes: the two flags are independent gates in the brief's order.
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})
            code, out, _err = run_cli(
                ["research", "--mock", "--yes", "--estimate-only", "--project", str(project_dir)],
                env=NO_GLOBAL_ENV,
            )

        self.assertEqual(code, codes.EXIT_CONFIRM)
        payload = json.loads(out)
        self.assertEqual(payload["accounts"], 1)

    def test_estimate_over_cap_exits_6(self) -> None:
        with temp_project() as project_dir:
            _write_config(
                project_dir, {"competitors": ["sproutapp"], "apify_max_charge_usd": 0.01}
            )
            code, out, err = run_cli(
                ["research", "--mock", "--yes", "--project", str(project_dir)], env=NO_GLOBAL_ENV
            )

        self.assertEqual(code, codes.EXIT_COST)
        payload = json.loads(out)
        self.assertEqual(payload["cap_usd"], 0.01)
        self.assertFalse(payload["within_cap"])
        self.assertTrue(err.strip())

    def test_missing_key_exits_4_unless_mock(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})

            code, out, err = run_cli(
                ["research", "--yes", "--project", str(project_dir)], env=NO_GLOBAL_ENV
            )
            self.assertEqual(code, codes.EXIT_KEYS)
            self.assertEqual(out.strip(), "")
            self.assertTrue(err.strip())

            code, _out, err = run_cli(
                ["research", "--mock", "--yes", "--project", str(project_dir)], env=NO_GLOBAL_ENV
            )
            self.assertEqual(code, codes.EXIT_OK, err)

    def test_error_classes_carry_expected_exit_codes(self) -> None:
        estimate_payload = {"total_usd": 5.0, "cap_usd": 3.0}
        self.assertEqual(research.CostCapExceeded(estimate_payload).exit_code, codes.EXIT_COST)
        self.assertEqual(research.ConfirmationRequired({"a": 1}).exit_code, codes.EXIT_CONFIRM)
        self.assertEqual(research.MissingKey().exit_code, codes.EXIT_KEYS)
        self.assertEqual(research.UpstreamFailure("boom").exit_code, codes.EXIT_UPSTREAM)
        self.assertIsNone(research.MissingKey().payload)
        self.assertEqual(research.CostCapExceeded(estimate_payload).payload, estimate_payload)


class ResearchCliTests(NoNetworkTestCase):
    def test_result_line_is_last_and_parses(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": FIXTURE_HANDLES})
            code, out, err = run_cli(
                ["research", "--mock", "--yes", "--project", str(project_dir)], env=NO_GLOBAL_ENV
            )

        self.assertEqual(code, codes.EXIT_OK, err)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertTrue(lines)
        last_line = lines[-1]
        self.assertTrue(last_line.startswith("RESULT "), last_line)

        payload = json.loads(last_line[len("RESULT "):])
        self.assertEqual(
            set(payload),
            {
                "run_id", "run_dir", "mode", "status", "accounts", "format_accounts",
                "reels_total", "selected", "backfill", "excluded", "videos", "frames",
                "warnings",
            },
        )
        self.assertEqual(payload["mode"], "mock")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["accounts"], 4)
        self.assertEqual(payload["format_accounts"], 0)
        self.assertEqual(payload["reels_total"], 17)

    def test_cli_accepts_no_download_flag(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})
            code, _out, err = run_cli(
                ["research", "--mock", "--yes", "--no-download", "--project", str(project_dir)],
                env=NO_GLOBAL_ENV,
            )

        self.assertEqual(code, codes.EXIT_OK, err)

    def test_config_error_exits_2(self) -> None:
        with temp_project() as project_dir:
            code, out, err = run_cli(
                ["research", "--mock", "--yes", "--project", str(project_dir)], env=NO_GLOBAL_ENV
            )

        self.assertEqual(code, codes.EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())

    def test_unresolvable_resume_exits_2(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})
            code, out, err = run_cli(
                [
                    "research", "--mock", "--yes", "--resume", "does-not-exist",
                    "--project", str(project_dir),
                ],
                env=NO_GLOBAL_ENV,
            )

        self.assertEqual(code, codes.EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())


if __name__ == "__main__":
    unittest.main()
