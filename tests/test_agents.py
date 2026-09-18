"""Tests for the Stage 3/4 plumbing: write/qa prompts, verifiers, brief state.

Task 16 wires `write-prompt`, `qa-prompt`, `verify --stage write|qa`,
`report`, and `status` into `contentos.py` over a new `lib/agents.py`
(this module) and `lib/report.py` (`tests/test_report.py`), plus the
two gold fixtures `fixtures/script.sample.md` and
`fixtures/qa.sample.json` that let the write/QA loop run end to end
with no subagent in it. See the design spec's "Stage 3 -- write" and
"Stage 4 -- qa" sections and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-16-brief.md` for
the exact interface.

Every run here is a real `research --mock --yes` plus `rank --mock` in
a fresh temporary project, so the ranked briefs (`B01` is always the
`DWN006` reel, `format: screen_demo`) are the ones the shipped fixtures
actually produce, not hand-written stand-ins -- `fixtures/script.sample.md`
is built to match this exact brief. Nothing touches the network:
NoNetworkTestCase is a second line of defense on top of mock research.
"""
from __future__ import annotations

import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import agents, codes, director, research, store  # noqa: E402
from lib.env import Keys  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
REFERENCES_DIR = REPO_ROOT / "skills" / "contentos" / "references"
SCRIPT_FIXTURE = FIXTURES_DIR / "script.sample.md"
QA_FIXTURE = FIXTURES_DIR / "qa.sample.json"

# The fixture handles Task 8/9 designed the sample Apify files around
# (mirrors tests/test_direct_commands.py's FIXTURE_HANDLES). `research
# --mock` plus `rank --mock` always ranks the DWN006 reel first, so
# `fixtures/script.sample.md` is built for B01/DWN006/screen_demo.
FIXTURE_HANDLES = ["sproutapp", "habitlab", "dailywins", "ghostaccount"]

PRODUCT_MD = """# Product

Sprout is a habit tracker for people who keep quitting on day four.

## Demo moments

- The streak screen filling in after a check-in.

## Allowed claims

- Logging one habit takes under five seconds.
"""

# Deliberately broken on four axes at once: a missing section (no
# Demo moment / Production notes / What changed vs source), a Beats
# table with no separator row and only 2 data rows, a Primary hook
# spoken line at 26 words, and a word count over the 75-word
# talking_head budget by more than 10 percent.
BAD_SCRIPT = """---
brief_id: B01
format: talking_head
target_length_s: 30
word_budget: 75
hypothesis: Some hypothesis text without a colon in it.
source_shortcode: XYZ001
revision: 0
---

## Hook

**Primary (approach: bold claim)**
Spoken: This hook line is written on purpose to run past the twenty five word ceiling so the verifier check fails here for certain no matter what.
On-screen text: Too many words.

**Backup (approach: bold claim)**
Spoken: Same approach as the primary which should not be allowed at all.
On-screen text: Same approach too.

## Beats

| t | [VISUAL CUE] | spoken / VO | on-screen text |
| 0:03 | a shot | {ninety_words} | more words here |
| 0:10 | a shot | short | short |

## CTA

**Primary (direct ask)**
Do the thing.

**Backup (open loop)**
Wonder about it.

## Caption

Some caption text.
#one #two #three
""".format(ninety_words=" ".join(["word"] * 90))


def _write_project(project: Path) -> None:
    """Write the founder state Stage 3/4 commands need: config and product.md."""
    config_dir = store.contentos_dir(project)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps({"competitors": FIXTURE_HANDLES}), encoding="utf-8"
    )
    (config_dir / "product.md").write_text(PRODUCT_MD, encoding="utf-8")


def _main(argv: Sequence[str]) -> Tuple[int, str, str]:
    """Run one contentos.py subcommand in-process; return (code, stdout, stderr)."""
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = contentos.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _mock_research_and_rank(project: Path) -> Path:
    """`research --mock --yes` then `rank --mock`, quietly; return the run dir."""
    cfg = store.load_config(project)
    buffer = StringIO()
    with redirect_stdout(buffer):
        result = research.run_research(
            project,
            cfg,
            Keys(apify=None, source=None, warnings=[]),
            mock=True,
            yes=True,
            estimate_only=False,
            resume=None,
            log=lambda _message: None,
        )
    run_dir = Path(result["run_dir"])
    code, _out, err = _main(
        ["rank", "--project", str(project), "--run", "latest", "--mock"]
    )
    assert code == codes.EXIT_OK, err
    return run_dir


def _brief(run_dir: Path, brief_id: str) -> Dict[str, Any]:
    """One ranked brief by id, from this run's 03-briefs.json."""
    doc = store.read_json(run_dir / "03-briefs.json")
    for brief in doc["briefs"]:
        if brief["brief_id"] == brief_id:
            return brief
    raise AssertionError(f"{brief_id} is not ranked in {run_dir}")


def _write_script(run_dir: Path, brief_id: str, revision: int, source: Path = SCRIPT_FIXTURE) -> Path:
    """Copy the gold script fixture into this run's 04-scripts/, at `revision`."""
    dest = agents.script_path(run_dir, brief_id, revision)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return dest


def _write_qa(run_dir: Path, brief_id: str, revision: int, source: Path = QA_FIXTURE) -> Path:
    """Copy the gold QA fixture into this run's 05-qa/, at `revision`."""
    dest = agents.qa_path(run_dir, brief_id, revision)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return dest


def _dump_json(path: Path, obj: Any) -> Path:
    """Write `obj` as JSON to `path`, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


class WordBudgetForTests(NoNetworkTestCase):
    def test_known_format_returns_target_length_and_word_budget(self) -> None:
        self.assertEqual(agents.word_budget_for("screen_demo", REFERENCES_DIR), (40, 100))
        self.assertEqual(agents.word_budget_for("talking_head", REFERENCES_DIR), (30, 75))
        self.assertEqual(agents.word_budget_for("slideshow_text", REFERENCES_DIR), (20, 50))

    def test_unknown_format_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            agents.word_budget_for("not_a_format", REFERENCES_DIR)


class WritePromptTests(NoNetworkTestCase):
    def test_write_prompt_contains_abs_paths_handoff_budget_and_contract(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            brief = _brief(run_dir, "B01")
            self.assertEqual(brief["format"], "screen_demo")

            prompt = agents.write_prompt(
                project, run_dir, "B01", revision=0, references_dir=REFERENCES_DIR
            )

            self.assertIn("HANDOFF TO: script-writer FROM: content-director (rank)", prompt)
            # Absolute paths to every input.
            self.assertIn(str((run_dir / "03-briefs.json").resolve()), prompt)
            self.assertIn(str(Path(brief["analysis_path"]).resolve()), prompt)
            self.assertIn(str(Path(brief["frames_dir"]).resolve()), prompt)
            self.assertIn(str((project / ".contentos" / "product.md").resolve()), prompt)
            self.assertIn(str((REFERENCES_DIR / "hooks.md").resolve()), prompt)
            self.assertIn(str((REFERENCES_DIR / "formats.md").resolve()), prompt)
            self.assertIn(str((REFERENCES_DIR / "scripting.md").resolve()), prompt)
            self.assertIn(str((REFERENCES_DIR / "examples" / "screen_demo.md").resolve()), prompt)
            # The budget, from formats.md's screen_demo row.
            self.assertIn("Target length: 40 seconds.", prompt)
            self.assertIn("Word budget: 100 words.", prompt)
            self.assertIn("[VISUAL CUE]", prompt)
            # The output contract.
            self.assertIn(
                "brief_id, format, target_length_s, word_budget, hypothesis, "
                "source_shortcode, revision",
                prompt,
            )
            for section in agents.CONTRACT_SECTIONS:
                self.assertIn(f"## {section}", prompt)
            self.assertIn("**Primary (approach: <name>)**", prompt)
            self.assertIn("**Backup (approach: <name>)**", prompt)
            self.assertIn(agents.BEATS_HEADER, prompt)
            self.assertIn("**Primary (direct ask)**", prompt)
            self.assertIn("**Backup (open loop)**", prompt)
            self.assertIn("5 to 8 hashtags", prompt)
            # The exact output path and the WROTE/FAILED contract.
            self.assertIn(str((run_dir / "04-scripts" / "B01.r0.md").resolve()), prompt)
            self.assertIn("WROTE <path>", prompt)
            self.assertIn("FAILED <reason>", prompt)


    def test_write_prompt_prefers_a_project_example_over_the_shipped_one(self) -> None:
        # The plugin's references/ directory is replaced wholesale on
        # update, so a founder's gold script has to live in their own
        # project to survive.
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            self.assertEqual(_brief(run_dir, "B01")["format"], "screen_demo")

            shipped = REFERENCES_DIR / "examples" / "screen_demo.md"
            self.assertTrue(shipped.exists())

            project_example = project / ".contentos" / "examples" / "screen_demo.md"
            project_example.parent.mkdir(parents=True, exist_ok=True)
            project_example.write_text("# my own gold script\n", encoding="utf-8")

            prompt = agents.write_prompt(
                project, run_dir, "B01", revision=0, references_dir=REFERENCES_DIR
            )

            self.assertIn(str(project_example.resolve()), prompt)
            self.assertNotIn(str(shipped.resolve()), prompt)

    def test_write_prompt_falls_back_to_the_shipped_example(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            # A project examples/ directory that has no file for this
            # format falls through to the plugin's own example.
            (project / ".contentos" / "examples").mkdir(parents=True, exist_ok=True)
            (project / ".contentos" / "examples" / "talking_head.md").write_text(
                "# a different format\n", encoding="utf-8"
            )

            prompt = agents.write_prompt(
                project, run_dir, "B01", revision=0, references_dir=REFERENCES_DIR
            )

            self.assertIn(
                str((REFERENCES_DIR / "examples" / "screen_demo.md").resolve()), prompt
            )

    def test_write_prompt_includes_rules_only_when_nonempty(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            prompt = agents.write_prompt(project, run_dir, "B01", references_dir=REFERENCES_DIR)
            self.assertNotIn("## Founder rules", prompt)

            (project / ".contentos" / "rules.md").write_text(
                "Never use the word cheap.\n", encoding="utf-8"
            )
            prompt = agents.write_prompt(project, run_dir, "B01", references_dir=REFERENCES_DIR)
            self.assertIn("## Founder rules", prompt)
            self.assertIn("Never use the word cheap.", prompt)

    def test_revision_prompt_includes_prior_script_and_issues(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            prompt_at_zero = agents.write_prompt(
                project, run_dir, "B01", revision=0, references_dir=REFERENCES_DIR
            )
            self.assertNotIn("## Revision", prompt_at_zero)

            prompt = agents.write_prompt(
                project, run_dir, "B01", revision=1, references_dir=REFERENCES_DIR
            )

            self.assertIn("## Revision", prompt)
            self.assertIn("This is revision 1.", prompt)
            self.assertIn(str((run_dir / "04-scripts" / "B01.r0.md").resolve()), prompt)
            self.assertIn(str((run_dir / "05-qa" / "B01.r0.json").resolve()), prompt)
            self.assertIn("Fix only what QA flagged", prompt)
            self.assertIn(str((run_dir / "04-scripts" / "B01.r1.md").resolve()), prompt)

    def test_write_prompt_exit_2_for_unknown_brief_and_missing_product_md(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            with self.assertRaises(agents.AgentsError) as ctx:
                agents.write_prompt(project, run_dir, "B99", references_dir=REFERENCES_DIR)
            self.assertEqual(ctx.exception.exit_code, codes.EXIT_USAGE)

            (project / ".contentos" / "product.md").unlink()
            with self.assertRaises(agents.AgentsError) as ctx:
                agents.write_prompt(project, run_dir, "B01", references_dir=REFERENCES_DIR)
            self.assertEqual(ctx.exception.exit_code, codes.EXIT_USAGE)


class QaPromptTests(NoNetworkTestCase):
    def test_qa_prompt_contains_abs_paths_handoff_thresholds_and_schema(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            script_file = _write_script(run_dir, "B01", 0)

            prompt = agents.qa_prompt(project, run_dir, "B01", 0, references_dir=REFERENCES_DIR)

            self.assertIn("HANDOFF TO: qa-reviewer FROM: script-writer", prompt)
            self.assertIn(str(script_file.resolve()), prompt)
            self.assertIn(str((run_dir / "03-briefs.json").resolve()), prompt)
            self.assertIn(str((REFERENCES_DIR / "formats.md").resolve()), prompt)
            self.assertIn(str((REFERENCES_DIR / "qa-rubric.md").resolve()), prompt)
            # The rubric asks the reviewer to enforce scripting.md's banned
            # vocabulary list, so scripting.md has to be an input it can read.
            self.assertIn(str((REFERENCES_DIR / "scripting.md").resolve()), prompt)
            self.assertIn("qa_pass_threshold: 8", prompt)
            self.assertIn("length_tolerance: 0.1", prompt)
            self.assertIn("Word budget: 100 words", prompt)
            for prop in director.load_schema("qa")["properties"]:
                self.assertIn(prop, prompt)
            self.assertIn(str((run_dir / "05-qa" / "B01.r0.json").resolve()), prompt)
            self.assertIn("WROTE <path>", prompt)
            self.assertIn("JSON only", prompt)

    def test_qa_prompt_exit_2_when_script_missing_at_that_revision(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            with self.assertRaises(agents.AgentsError) as ctx:
                agents.qa_prompt(project, run_dir, "B01", 0, references_dir=REFERENCES_DIR)
            self.assertEqual(ctx.exception.exit_code, codes.EXIT_USAGE)


class VerifyScriptTests(NoNetworkTestCase):
    def test_verify_script_passes_fixture(self) -> None:
        with temp_project() as project:
            run_dir = project / "run"
            path = _write_script(run_dir, "B01", 0)

            check = agents.verify_script(path, REFERENCES_DIR, 0.10)

            self.assertEqual(check.errors, [])
            self.assertEqual(check.warnings, [])
            self.assertEqual(check.word_count, 103)
            self.assertEqual(check.spoken_words, 82)
            self.assertEqual(check.read_time_s, 32.8)
            self.assertEqual(check.placeholders, ["[NEED NUMBER]"])
            self.assertEqual(check.frontmatter["format"], "screen_demo")
            self.assertEqual(check.frontmatter["brief_id"], "B01")

    def test_verify_script_reports_missing_section_bad_table_long_hook_and_over_budget(self) -> None:
        with temp_project() as project:
            path = project / "bad.md"
            path.write_text(BAD_SCRIPT, encoding="utf-8")

            check = agents.verify_script(path, REFERENCES_DIR, 0.10)

            joined = "\n".join(check.errors)
            self.assertIn("sections: expected", joined)
            self.assertIn("Demo moment", joined)
            self.assertIn("Beats: missing the separator row", joined)
            self.assertIn("only 2 data row", joined)
            self.assertIn("Hook: Primary spoken line has", joined)
            self.assertIn("25 or more", joined)
            self.assertIn("over the 75-word budget", joined)

    def test_verify_script_collects_placeholders_without_failing(self) -> None:
        with temp_project() as project:
            run_dir = project / "run"
            path = agents.script_path(run_dir, "B01", 0)
            path.parent.mkdir(parents=True)
            text = SCRIPT_FIXTURE.read_text(encoding="utf-8").replace(
                "The real difference, on screen.", "[NEED NUMBER] more, on screen."
            )
            path.write_text(text, encoding="utf-8")

            check = agents.verify_script(path, REFERENCES_DIR, 0.10)

            self.assertEqual(check.errors, [])
            self.assertEqual(check.placeholders, ["[NEED NUMBER]", "[NEED NUMBER]"])

    def test_verify_script_missing_file_reports_one_error(self) -> None:
        check = agents.verify_script(Path("/no/such/script.md"), REFERENCES_DIR, 0.10)
        self.assertEqual(len(check.errors), 1)
        self.assertIn("no script file was written", check.errors[0])

    def test_verify_script_revision_must_match_filename(self) -> None:
        with temp_project() as project:
            run_dir = project / "run"
            # Copy the r0 fixture to a path claiming r1: the frontmatter
            # (revision: 0) now disagrees with the filename (.r1.md).
            path = _write_script(run_dir, "B01", 1)

            check = agents.verify_script(path, REFERENCES_DIR, 0.10)

            self.assertTrue(any("does not match" in error for error in check.errors))


class ExamplesPassVerifyScriptTests(NoNetworkTestCase):
    def test_examples_pass_verify_script(self) -> None:
        examples_dir = REFERENCES_DIR / "examples"
        example_paths = sorted(examples_dir.glob("*.md"))
        self.assertGreaterEqual(len(example_paths), 2)
        for path in example_paths:
            with self.subTest(example=path.name):
                check = agents.verify_script(path, REFERENCES_DIR, 0.10)
                self.assertEqual(check.errors, [], f"{path.name}: {check.errors}")


class VerifyQaTests(NoNetworkTestCase):
    def test_verify_qa_passes_fixture(self) -> None:
        problems = agents.verify_qa(QA_FIXTURE, 8)
        self.assertEqual(problems, [])

    def test_verify_qa_rejects_pass_with_failed_check_or_low_score(self) -> None:
        base = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
        with temp_project() as project:
            failed_check = dict(base)
            failed_check["checks"] = dict(base["checks"], no_fabricated_claims="fail")
            path = _dump_json(project / "qa_failed_check.json", failed_check)
            problems = agents.verify_qa(path, 8)
            self.assertTrue(problems)

            low_score = dict(base)
            low_score["scores"] = dict(base["scores"], hook_scroll_stop=5)
            path = _dump_json(project / "qa_low_score.json", low_score)
            problems = agents.verify_qa(path, 8)
            self.assertTrue(problems)

    def test_verify_qa_accepts_reject_with_a_failed_check(self) -> None:
        base = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
        with temp_project() as project:
            reject = dict(base)
            reject["verdict"] = "reject"
            reject["checks"] = dict(base["checks"], no_fabricated_claims="fail")
            path = _dump_json(project / "qa_reject.json", reject)
            self.assertEqual(agents.verify_qa(path, 8), [])

    def test_verify_qa_rejects_reject_with_no_failed_check(self) -> None:
        base = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
        with temp_project() as project:
            bad_reject = dict(base)
            bad_reject["verdict"] = "reject"
            path = _dump_json(project / "qa_bad_reject.json", bad_reject)
            self.assertTrue(agents.verify_qa(path, 8))

    def test_verify_qa_missing_file_reports_one_error(self) -> None:
        problems = agents.verify_qa(Path("/no/such/qa.json"), 8)
        self.assertEqual(len(problems), 1)

    def test_verify_qa_schema_errors_short_circuit_consistency_checks(self) -> None:
        base = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
        with temp_project() as project:
            malformed = dict(base)
            malformed["scores"] = dict(base["scores"])
            del malformed["scores"]["hook_scroll_stop"]
            path = _dump_json(project / "qa_malformed.json", malformed)
            problems = agents.verify_qa(path, 8)
            self.assertTrue(any("hook_scroll_stop" in problem for problem in problems))


class BriefStateTests(NoNetworkTestCase):
    def test_pending_when_no_script(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            state = agents.brief_state(run_dir, "B01")

            self.assertEqual(state["status"], "pending")
            self.assertIsNone(state["revision"])
            self.assertIsNone(state["script_path"])
            self.assertIsNone(state["qa_path"])
            self.assertIsNone(state["verdict"])
            self.assertEqual(state["placeholders"], [])
            self.assertFalse(state["needs_human"])
            self.assertEqual(state["title"], _brief(run_dir, "B01")["brief_title"])

    def test_written_when_script_but_no_qa(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)

            state = agents.brief_state(run_dir, "B01")

            self.assertEqual(state["status"], "written")
            self.assertEqual(state["revision"], 0)
            self.assertEqual(state["placeholders"], ["[NEED NUMBER]"])
            self.assertIsNone(state["verdict"])

    def test_pass_status_and_verdict_come_from_qa(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)
            _write_qa(run_dir, "B01", 0)

            state = agents.brief_state(run_dir, "B01")

            self.assertEqual(state["status"], "pass")
            self.assertEqual(state["verdict"], "pass")
            self.assertFalse(state["needs_human"])

    def test_needs_human_after_revise_at_revision_1(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            revise_qa = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
            revise_qa["verdict"] = "revise"
            revise_qa["scores"] = dict(revise_qa["scores"], hook_scroll_stop=5)

            _write_script(run_dir, "B01", 0)
            _dump_json(agents.qa_path(run_dir, "B01", 0), revise_qa)
            _write_script(run_dir, "B01", 1)
            revise_qa_1 = dict(revise_qa)
            revise_qa_1["revision"] = 1
            _dump_json(agents.qa_path(run_dir, "B01", 1), revise_qa_1)

            state = agents.brief_state(run_dir, "B01")

            self.assertTrue(state["needs_human"])
            self.assertEqual(state["status"], "needs_human")
            self.assertEqual(state["verdict"], "revise")
            self.assertEqual(state["revision"], 1)

    def test_needs_human_when_two_revise_verdicts_exist_even_after_a_later_pass(self) -> None:
        # A brief that somehow accumulated a third script revision (past
        # the one-revision policy) whose QA came back pass: the *latest*
        # verdict alone would read as fine, but two earlier revise
        # verdicts on this brief still mean a human should look at it.
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            revise_qa = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
            revise_qa["verdict"] = "revise"
            revise_qa["scores"] = dict(revise_qa["scores"], hook_scroll_stop=5)

            _write_script(run_dir, "B01", 0)
            _dump_json(agents.qa_path(run_dir, "B01", 0), revise_qa)
            _write_script(run_dir, "B01", 1)
            revise_qa_1 = dict(revise_qa)
            revise_qa_1["revision"] = 1
            _dump_json(agents.qa_path(run_dir, "B01", 1), revise_qa_1)
            _write_script(run_dir, "B01", 2)
            pass_qa_2 = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
            pass_qa_2["revision"] = 2
            _dump_json(agents.qa_path(run_dir, "B01", 2), pass_qa_2)

            state = agents.brief_state(run_dir, "B01")

            self.assertEqual(state["verdict"], "pass")
            self.assertTrue(state["needs_human"])
            self.assertEqual(state["status"], "needs_human")


class CliWiringTests(NoNetworkTestCase):
    def test_write_prompt_cli_prints_prompt_naming_output_path(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            code, out, err = _main(
                ["write-prompt", "--project", str(project), "--run", "latest", "--brief", "B01"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn(str((run_dir / "04-scripts" / "B01.r0.md").resolve()), out)

    def test_write_prompt_cli_exit_2_for_unknown_brief(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _mock_research_and_rank(project)

            code, out, err = _main(
                ["write-prompt", "--project", str(project), "--run", "latest", "--brief", "B99"]
            )

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertIn("B99", err)

    def test_verify_write_cli_exit_7_then_ok(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "write", "--brief", "B01"]
            )
            self.assertEqual(code, codes.EXIT_VERIFY)
            self.assertEqual(out, "")
            self.assertIn("no script file was written", err)

            _write_script(run_dir, "B01", 0)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "write", "--brief", "B01"]
            )
            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn("ok ", out)
            self.assertIn("words=103", out)
            self.assertIn("read_time_s=32.8", out)
            self.assertIn("placeholders=1", out)

    def test_qa_prompt_cli_defaults_to_highest_script_revision(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)

            code, out, err = _main(
                ["qa-prompt", "--project", str(project), "--run", "latest", "--brief", "B01"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn(str((run_dir / "05-qa" / "B01.r0.json").resolve()), out)

    def test_qa_prompt_cli_exit_2_without_a_script(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _mock_research_and_rank(project)

            code, out, err = _main(
                ["qa-prompt", "--project", str(project), "--run", "latest", "--brief", "B01"]
            )

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertTrue(err.strip())

    def test_verify_qa_cli_exit_7_then_ok_with_verdict(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)

            broken = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
            broken["checks"] = dict(broken["checks"], no_fabricated_claims="fail")
            _dump_json(agents.qa_path(run_dir, "B01", 0), broken)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "qa", "--brief", "B01"]
            )
            self.assertEqual(code, codes.EXIT_VERIFY)
            self.assertEqual(out, "")

            _write_qa(run_dir, "B01", 0)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "qa", "--brief", "B01"]
            )
            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn("verdict=pass", out)


if __name__ == "__main__":
    unittest.main()
