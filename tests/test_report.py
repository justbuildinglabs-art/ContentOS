"""Tests for `lib/report.py`: `render_report` and `status`.

Task 16 wires `report --run <id>` and `status --run <id>` into
`contentos.py` over `lib/report.py`, which is mostly a layout around
`lib/agents.py`'s `brief_state` (`tests/test_agents.py` covers that
function's own rules). These tests build a run directory by hand --
`run.json` plus a `03-briefs.json` and matching `04-scripts/`/`05-qa/`
files for five briefs, one per status `brief_state` can return -- since
a real mock research run only ever ranks one brief per status
combination it happens to produce, and report rendering does not
depend on anything research- or director-specific.
"""
from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple

from tests.helpers import NoNetworkTestCase, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import agents, codes, report, store  # noqa: E402

_FAKE_SCRIPT = """---
brief_id: {brief_id}
format: talking_head
target_length_s: 30
word_budget: 75
hypothesis: test hypothesis for report rendering
source_shortcode: TEST001
revision: {revision}
---

## Hook

A placeholder script body for report tests. {placeholder}
"""


def _main(argv: Sequence[str]) -> Tuple[int, str, str]:
    """Run one contentos.py subcommand in-process; return (code, stdout, stderr)."""
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = contentos.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _new_run(project: Path, competitors: Sequence[str] = ("a",)) -> Path:
    """A fresh mock run directory with nothing but `run.json` yet."""
    config_dir = store.contentos_dir(project)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps({"competitors": list(competitors)}), encoding="utf-8"
    )
    cfg = store.load_config(project)
    return store.init_run(project, cfg, mode="mock")


def _write_script_text(run_dir: Path, brief_id: str, revision: int, placeholder: str = "") -> Path:
    """A minimal script file at `run_dir`'s `04-scripts/<brief_id>.r<revision>.md`."""
    path = agents.script_path(run_dir, brief_id, revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _FAKE_SCRIPT.format(brief_id=brief_id, revision=revision, placeholder=placeholder),
        encoding="utf-8",
    )
    return path


def _write_qa(run_dir: Path, brief_id: str, revision: int, verdict: str, **extra: Any) -> Path:
    """A minimal QA file at `run_dir`'s `05-qa/<brief_id>.r<revision>.json`."""
    path = agents.qa_path(run_dir, brief_id, revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: Dict[str, Any] = {
        "brief_id": brief_id,
        "revision": revision,
        "verdict": verdict,
        "confidence": 6,
        "summary": f"{brief_id} r{revision}: {verdict} for testing.",
    }
    doc.update(extra)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _build_run_with_every_status(project: Path) -> Path:
    """A run with one brief in each of `brief_state`'s five statuses, plus needs_human.

    B01 pass (one placeholder), B02 revise, B03 reject, B04 needs_human
    (revise at revision 1, with a placeholder on the latest script),
    B05 pending (no script at all).
    """
    run_dir = _new_run(project)
    store.update_run(
        run_dir,
        stages={
            "research": {"status": "ok"},
            "direct": {"status": "ok", "finished_at": "2026-09-17T00:00:00+00:00"},
        },
        costs={"apify": {"estimate_usd": 0.33}},
        warnings=["ghostaccount: not_found"],
    )
    store.write_json_atomic(
        run_dir / "03-briefs.json",
        {
            "briefs": [
                {"brief_id": "B01", "brief_title": "Pass brief"},
                {"brief_id": "B02", "brief_title": "Revise brief"},
                {"brief_id": "B03", "brief_title": "Reject brief"},
                {"brief_id": "B04", "brief_title": "Needs human brief"},
                {"brief_id": "B05", "brief_title": "Pending brief"},
            ],
            "ranked_at": "2026-09-17T00:00:00+00:00",
            "analyzed": 5,
            "skipped": [],
        },
    )

    _write_script_text(run_dir, "B01", 0, placeholder="[NEED NUMBER]")
    _write_qa(run_dir, "B01", 0, "pass")

    _write_script_text(run_dir, "B02", 0, placeholder="[NEED NAME]")
    _write_qa(run_dir, "B02", 0, "revise")

    _write_script_text(run_dir, "B03", 0)
    _write_qa(run_dir, "B03", 0, "reject")

    _write_script_text(run_dir, "B04", 0)
    _write_qa(run_dir, "B04", 0, "revise")
    _write_script_text(run_dir, "B04", 1, placeholder="[NEED SCREENSHOT]")
    _write_qa(run_dir, "B04", 1, "revise")

    # B05: pending. No script, no QA.

    store.write_json_atomic(
        run_dir / "02-outliers.json",
        {
            "selected": [
                {
                    "shortCode": "AAA001",
                    "caption": "a caption the orchestrator must never have to read",
                    "video_status": "ok",
                    "frames_status": "ok",
                    "analysis_status": "ok",
                },
                {
                    "shortCode": "BBB002",
                    "caption": "another caption",
                    "video_status": "failed",
                    "frames_status": "no_video",
                },
            ],
            "backfill": [],
            "excluded": [],
            "account_status": {},
            "baselines": {},
        },
    )

    return run_dir


class RenderReportTests(NoNetworkTestCase):
    def test_report_lists_pass_revise_reject_needs_human_and_placeholders(self) -> None:
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)

            text = report.render_report(run_dir)

            self.assertIn(f"# ContentOS report: {run_dir.name}", text)
            self.assertIn("## Summary", text)
            self.assertIn("Mode: mock", text)

            self.assertIn("## Briefs", text)
            self.assertIn("| B01 | Pass brief | pass | pass |", text)
            self.assertIn("| B02 | Revise brief | revise | revise |", text)
            self.assertIn("| B03 | Reject brief | reject | reject |", text)
            self.assertIn("| B04 | Needs human brief | needs_human | revise |", text)
            self.assertIn("| B05 | Pending brief | pending | - |", text)

            self.assertIn("## Placeholders to fill", text)
            self.assertIn("B01:", text)
            self.assertIn("[NEED NUMBER]", text)
            self.assertIn("B02:", text)
            self.assertIn("[NEED NAME]", text)
            self.assertIn("B04:", text)
            self.assertIn("[NEED SCREENSHOT]", text)

            self.assertIn("## Needs a human", text)
            self.assertIn("B04: B04 r1: revise for testing.", text)
            # A reject is final too, so it needs a human even though its
            # status column keeps saying "reject" (SKILL.md, the write/QA
            # loop: "A second `revise`, or any `reject`. Final.").
            self.assertIn("B03: B03 r0: reject for testing.", text)

            self.assertIn("## Costs and timings", text)
            self.assertIn("apify", text)

            self.assertIn("## Warnings", text)
            self.assertIn("ghostaccount: not_found", text)

            self.assertNotIn("—", text)  # no em dashes anywhere in creator-facing text

    def test_report_marks_none_when_nothing_needs_a_human_or_has_placeholders(self) -> None:
        with temp_project() as project:
            run_dir = _new_run(project)
            store.write_json_atomic(
                run_dir / "03-briefs.json",
                {
                    "briefs": [{"brief_id": "B01", "brief_title": "Clean brief"}],
                    "ranked_at": "t",
                    "analyzed": 1,
                    "skipped": [],
                },
            )
            _write_script_text(run_dir, "B01", 0)
            _write_qa(run_dir, "B01", 0, "pass")

            text = report.render_report(run_dir)

            self.assertIn("## Placeholders to fill", text)
            self.assertIn("## Needs a human", text)
            sections = text.split("## ")
            placeholders_section = next(s for s in sections if s.startswith("Placeholders to fill"))
            needs_human_section = next(s for s in sections if s.startswith("Needs a human"))
            self.assertIn("None.", placeholders_section)
            self.assertIn("None.", needs_human_section)

    def test_report_handles_a_run_with_no_briefs_yet(self) -> None:
        with temp_project() as project:
            run_dir = _new_run(project)

            text = report.render_report(run_dir)

            self.assertIn("No briefs yet.", text)
            self.assertIn("- no briefs yet", text)


class StatusTests(NoNetworkTestCase):
    def test_status_json_shape(self) -> None:
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)

            result = report.status(run_dir)

            self.assertEqual(
                set(result.keys()),
                {"run_id", "mode", "stages", "briefs", "costs", "warnings", "reels"},
            )
            self.assertEqual(result["run_id"], run_dir.name)
            self.assertEqual(result["mode"], "mock")
            self.assertIsInstance(result["stages"], dict)
            self.assertIsInstance(result["costs"], dict)
            self.assertIsInstance(result["warnings"], list)

            self.assertIsInstance(result["briefs"], list)
            self.assertEqual(len(result["briefs"]), 5)
            expected_keys = {
                "brief_id", "title", "revision", "script_path", "qa_path",
                "verdict", "placeholders", "needs_human", "status",
            }
            for state in result["briefs"]:
                self.assertEqual(set(state.keys()), expected_keys)

            by_id = {state["brief_id"]: state for state in result["briefs"]}
            self.assertEqual(by_id["B01"]["status"], "pass")
            self.assertEqual(by_id["B02"]["status"], "revise")
            self.assertEqual(by_id["B03"]["status"], "reject")
            self.assertTrue(by_id["B03"]["needs_human"])
            self.assertEqual(by_id["B04"]["status"], "needs_human")
            self.assertTrue(by_id["B04"]["needs_human"])
            self.assertFalse(by_id["B01"]["needs_human"])
            self.assertEqual(by_id["B05"]["status"], "pending")
            self.assertIsNone(by_id["B05"]["revision"])

            # `reels` lets the orchestrator find analyzable reels without
            # reading 02-outliers.json, which carries raw captions and
            # comments straight into its own context.
            self.assertEqual(
                result["reels"],
                [
                    {
                        "shortCode": "AAA001",
                        "video_status": "ok",
                        "frames_status": "ok",
                        "analysis_status": "ok",
                    },
                    {
                        "shortCode": "BBB002",
                        "video_status": "failed",
                        "frames_status": "no_video",
                        "analysis_status": None,
                    },
                ],
            )
            self.assertEqual(
                list(result["reels"][0].keys()),
                ["shortCode", "video_status", "frames_status", "analysis_status"],
            )

    def test_status_reels_is_empty_without_an_outliers_file(self) -> None:
        with temp_project() as project:
            run_dir = _new_run(project)

            self.assertEqual(report.status(run_dir)["reels"], [])


class ReportCliTests(NoNetworkTestCase):
    def test_report_cli_writes_report_md_and_prints_its_path(self) -> None:
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)

            code, out, err = _main(["report", "--project", str(project), "--run", "latest"])

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            report_path = run_dir / "report.md"
            self.assertEqual(out.strip(), str(report_path.resolve()))
            self.assertTrue(report_path.exists())
            self.assertIn("# ContentOS report", report_path.read_text(encoding="utf-8"))

    def test_report_cli_exit_2_when_run_not_found(self) -> None:
        with temp_project() as project:
            code, out, err = _main(["report", "--project", str(project), "--run", "latest"])

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertTrue(err.strip())

    def test_status_cli_prints_json(self) -> None:
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)

            code, out, err = _main(["status", "--project", str(project), "--run", "latest"])

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            result = json.loads(out)
            self.assertEqual(result["run_id"], run_dir.name)
            self.assertEqual(len(result["briefs"]), 5)

    def test_status_cli_exit_2_when_run_not_found(self) -> None:
        with temp_project() as project:
            code, out, err = _main(["status", "--project", str(project), "--run", "latest"])

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
