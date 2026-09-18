"""End-to-end coverage of the mock pipeline, driven through the CLI.

Task 19 is the last task in the ContentOS build. Every stage already has
its own unit tests (test_research.py, test_direct_commands.py,
test_agents.py, test_report.py, ...), each exercising its module
directly or through one or two CLI calls. None of them prove that the
whole thing actually runs end to end, in order, purely through
`contentos.main()`, the way a founder's `/contentos run --mock` would:
`setup` writes founder state from the fixture answers, `research --mock
--yes` seeds the fixture video and frames with no network, `rank --mock`
seeds the fixture analyses and `03-patterns.md` (standing in for the
content-director subagent), `verify` accepts and coerces every one of
them, a second `rank` without `--mock` proves ranking is idempotent over
what is already on disk, `write-prompt`/`qa-prompt` name the exact
output paths the fixture script and QA files are copied to (standing in
for the script-writer and qa-reviewer subagents), and `report`/`status`
read back the finished run. See the design spec's "Verification" step 2
and "Architecture" section, and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-19-brief.md`'s
"Resolutions of ambiguities" for the exact command sequence this
replays.

`_run_pipeline` is the one place that sequence is written; both tests
below call it and make their own assertions on top of the one real run
it produces, so the eleven-command sequence itself is never duplicated.
Nothing here touches the network: `--mock` never calls Apify, and
NoNetworkTestCase is a second line of defense, the same as every other
test module in this package.
"""
from __future__ import annotations

import json
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple
from unittest import mock

from tests.helpers import NoNetworkTestCase, REPO_ROOT, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import codes  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
SETUP_ANSWERS_FIXTURE = FIXTURES_DIR / "setup-answers.sample.json"
SCRIPT_FIXTURE = FIXTURES_DIR / "script.sample.md"
QA_FIXTURE = FIXTURES_DIR / "qa.sample.json"

# CONTENTOS_CONFIG_DIR="" tells lib/env.py's resolve_keys to skip
# ~/.config/contentos/.env entirely. research --mock never needs a real
# key, but unlike test_agents.py/test_direct_commands.py (which call
# lib/research.py directly with a hand-built Keys() and so never touch
# key resolution at all), this module drives the real `research`
# subcommand, which does call resolve_keys. This keeps the test from
# depending on whatever, if anything, happens to live in the machine's
# own home directory.
_NO_GLOBAL_ENV = {"CONTENTOS_CONFIG_DIR": ""}


def _main(argv: Sequence[str]) -> Tuple[int, str, str]:
    """Run one contentos.py subcommand in-process; return (code, stdout, stderr)."""
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = contentos.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _require_ok(code: int, out: str, err: str, step: str) -> None:
    """Raise a clear AssertionError, naming the step and showing stderr, on non-zero exit."""
    if code != codes.EXIT_OK:
        raise AssertionError(
            "step `{0}` exited {1} (expected 0)\n"
            "--- stderr ---\n{2}\n"
            "--- stdout ---\n{3}".format(step, code, err, out)
        )


def _result_line(stdout: str) -> Dict[str, Any]:
    """Parse research's `RESULT {...}` stdout line for `run_id` and `run_dir`."""
    for line in reversed(stdout.splitlines()):
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    raise AssertionError("no RESULT line in research's stdout:\n{0}".format(stdout))


def _run_pipeline(project: Path) -> Dict[str, Any]:
    """Drive the full mock pipeline through `contentos.main()`, step by step.

    Exactly the sequence
    `.superpowers/sdd/trying-to-make-a-clever-ritchie/task-19-brief.md`'s
    "Resolutions of ambiguities" pins down: setup from the fixture
    answers, mock research, mock rank (seeds the fixture analyses and
    03-patterns.md), verify every seeded analysis plus the synthesis, a
    second, non-mock rank (idempotent over the now-verified analyses),
    write-prompt + the fixture script + verify, qa-prompt + the fixture
    QA file + verify, report, and status.

    Every step's exit code is checked immediately, with a message naming
    the failing command and showing its stderr, so a real defect in the
    pipeline fails loudly at the step that broke rather than several
    steps later on a missing file. Returns `{project, run_id, run_dir,
    analyses, script_path, qa_path, report_path, status}` for the
    caller's own assertions.
    """
    project = Path(project)

    with mock.patch.dict(os.environ, _NO_GLOBAL_ENV):
        code, out, err = _main(
            ["setup", "--project", str(project), "--answers-file", str(SETUP_ANSWERS_FIXTURE)]
        )
        _require_ok(code, out, err, "setup --answers-file")

        code, out, err = _main(["research", "--project", str(project), "--mock", "--yes"])
        _require_ok(code, out, err, "research --mock --yes")
        result = _result_line(out)
        run_dir = Path(result["run_dir"])

        code, out, err = _main(["rank", "--project", str(project), "--run", "latest", "--mock"])
        _require_ok(code, out, err, "rank --run latest --mock")

        analyses = sorted(path.stem for path in (run_dir / "03-analyses").glob("*.json"))
        if not analyses:
            raise AssertionError(
                "rank --mock seeded no 03-analyses/*.json files in {0}".format(run_dir)
            )

        for shortcode in analyses:
            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "direct", "--shortcode", shortcode]
            )
            _require_ok(code, out, err, "verify --stage direct --shortcode {0}".format(shortcode))

        code, out, err = _main(
            ["verify", "--project", str(project), "--run", "latest", "--stage", "synth"]
        )
        _require_ok(code, out, err, "verify --stage synth")

        code, out, err = _main(["rank", "--project", str(project), "--run", "latest"])
        _require_ok(code, out, err, "rank --run latest (idempotency check, no --mock)")
        reranked = json.loads(out)
        if reranked["analyzed"] != len(analyses) or reranked["briefs"] != len(analyses):
            raise AssertionError(
                "rank --run latest without --mock re-ranked to {0}, expected "
                "analyzed=briefs={1}".format(reranked, len(analyses))
            )

        code, out, err = _main(
            ["write-prompt", "--project", str(project), "--run", "latest", "--brief", "B01"]
        )
        _require_ok(code, out, err, "write-prompt --brief B01")
        script_path = run_dir / "04-scripts" / "B01.r0.md"
        if str(script_path.resolve()) not in out:
            raise AssertionError(
                "write-prompt --brief B01 did not name {0} in its prompt:\n{1}".format(
                    script_path.resolve(), out
                )
            )
        script_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT_FIXTURE, script_path)

        code, out, err = _main(
            ["verify", "--project", str(project), "--run", "latest",
             "--stage", "write", "--brief", "B01"]
        )
        _require_ok(code, out, err, "verify --stage write --brief B01")

        code, out, err = _main(
            ["qa-prompt", "--project", str(project), "--run", "latest", "--brief", "B01"]
        )
        _require_ok(code, out, err, "qa-prompt --brief B01")
        qa_path = run_dir / "05-qa" / "B01.r0.json"
        if str(qa_path.resolve()) not in out:
            raise AssertionError(
                "qa-prompt --brief B01 did not name {0} in its prompt:\n{1}".format(
                    qa_path.resolve(), out
                )
            )
        qa_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(QA_FIXTURE, qa_path)

        code, out, err = _main(
            ["verify", "--project", str(project), "--run", "latest",
             "--stage", "qa", "--brief", "B01"]
        )
        _require_ok(code, out, err, "verify --stage qa --brief B01")

        code, out, err = _main(["report", "--project", str(project), "--run", "latest"])
        _require_ok(code, out, err, "report --run latest")
        report_path = run_dir / "report.md"
        if str(report_path.resolve()) not in out:
            raise AssertionError(
                "report --run latest did not print {0}:\n{1}".format(report_path.resolve(), out)
            )

        code, out, err = _main(["status", "--project", str(project), "--run", "latest"])
        _require_ok(code, out, err, "status --run latest")
        status = json.loads(out)

    return {
        "project": project,
        "run_id": result["run_id"],
        "run_dir": run_dir,
        "analyses": analyses,
        "script_path": script_path,
        "qa_path": qa_path,
        "report_path": report_path,
        "status": status,
    }


class FullMockPipelineTests(NoNetworkTestCase):
    def test_full_mock_pipeline_produces_every_stage_file(self) -> None:
        with temp_project() as project:
            outcome = _run_pipeline(project)
            run_dir = outcome["run_dir"]

            for name in (
                "run.json", "01-reels.json", "01-profiles.json", "02-outliers.json",
                "03-patterns.md", "03-briefs.json", "briefs.md", "report.md",
            ):
                self.assertTrue((run_dir / name).exists(), "missing {0}".format(run_dir / name))

            self.assertTrue(
                list(run_dir.glob("videos/*.mp4")), "no videos/*.mp4 under {0}".format(run_dir)
            )
            self.assertTrue(
                list(run_dir.glob("frames/*/f01.jpg")),
                "no frames/*/f01.jpg under {0}".format(run_dir),
            )
            self.assertTrue(
                list(run_dir.glob("frames/*/cover.jpg")),
                "no frames/*/cover.jpg under {0}".format(run_dir),
            )

            # rank --mock seeds exactly the five gold fixture analyses
            # (fixtures/analyses/*.json); design spec, "Verification" step 2.
            self.assertEqual(len(outcome["analyses"]), 5, outcome["analyses"])
            self.assertTrue((run_dir / "04-scripts" / "B01.r0.md").exists())
            self.assertTrue((run_dir / "05-qa" / "B01.r0.json").exists())

            for name in ("creator.md", "config.json", "rules.md", ".gitignore"):
                path = project / ".contentos" / name
                self.assertTrue(path.exists(), "missing {0}".format(path))

            brief_b01 = next(
                brief for brief in outcome["status"]["briefs"] if brief["brief_id"] == "B01"
            )
            self.assertEqual(brief_b01["status"], "pass")

            report_text = outcome["report_path"].read_text(encoding="utf-8")
            self.assertIn("[NEED NUMBER]", report_text)


class PipelineNeverTouchesNetworkTests(NoNetworkTestCase):
    def test_pipeline_never_touches_network(self) -> None:
        with temp_project() as project:
            with mock.patch(
                "urllib.request.urlopen", side_effect=AssertionError("network disabled in tests")
            ) as urlopen_mock, mock.patch(
                "urllib.request.OpenerDirector.open",
                side_effect=AssertionError("network disabled in tests"),
            ) as opener_mock:
                _run_pipeline(project)

            self.assertFalse(urlopen_mock.called)
            self.assertFalse(opener_mock.called)


if __name__ == "__main__":
    unittest.main()
