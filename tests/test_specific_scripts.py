"""Tests for the 0.3.0 writer and QA side of "specific scripts".

Covers the design spec's "0.3.0 changes": `intake --run --brief`, the
fact sheet check `verify --stage facts`, the new write/QA prompt inputs
(the brief's intake answers, its fact sheet, `references/specificity.md`
when present), the three claim tiers, the `min_specifics` rule, the QA
schema's new `body_specificity` score and `not_generic`/`facts_sourced`
checks, the one extra revision a filled intake unlocks after
`needs_human`, and the `placeholder_ratio` field on `verify --stage
write`.

Every run is a real `research --mock --yes` plus `rank --mock` in a
fresh temporary project (see `tests/test_agents.py`). Brief specifics
are written into `03-briefs.json` by hand here, standing in for the
director slice that fills them.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from tests.helpers import NoNetworkTestCase, temp_project
from tests.test_agents import (
    QA_FIXTURE,
    REFERENCES_DIR,
    SCRIPT_FIXTURE,
    _dump_json,
    _main,
    _mock_research_and_rank,
    _write_project,
    _write_qa,
    _write_script,
)

from lib import agents, codes, director, store  # noqa: E402

SPECIFICS: List[Dict[str, Any]] = [
    {
        "kind": "tool",
        "name": "Todoist",
        "detail": "the task app on screen",
        "evidence": "frame 2",
        "public": True,
    },
    {
        "kind": "number",
        "name": "3 hours saved a week",
        "detail": "the source creator's own result",
        "evidence": "transcript 0:12",
        "public": False,
    },
    {
        "kind": "repo",
        "name": "obsidian-tasks",
        "detail": "open source plugin",
        "evidence": "caption",
        "public": True,
    },
]

INVENTORY_MD = """

## Inventory

- Paper week card, 7 boxes, used every Sunday since 2024
- Notion weekly template
"""


def _add_specifics(run_dir: Path, brief_id: str, specifics: List[Dict[str, Any]]) -> None:
    """Write `specifics` into one brief of this run's 03-briefs.json."""
    path = run_dir / "03-briefs.json"
    doc = store.read_json(path)
    for brief in doc["briefs"]:
        if brief["brief_id"] == brief_id:
            brief["specifics"] = specifics
    path.write_text(json.dumps(doc), encoding="utf-8")


def _add_inventory(project: Path) -> None:
    creator = store.contentos_dir(project) / "creator.md"
    creator.write_text(creator.read_text(encoding="utf-8") + INVENTORY_MD, encoding="utf-8")


def _qa_doc(verdict: str, revision: int) -> Dict[str, Any]:
    """A schema-valid QA dict with a verdict its own fields agree with."""
    doc = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
    doc["revision"] = revision
    doc["verdict"] = verdict
    if verdict == "revise":
        doc["scores"] = dict(doc["scores"], body_proof_density=5)
        doc["issues"] = [
            {
                "check_or_score": "body_proof_density",
                "severity": "major",
                "detail": "The proof beat rests on [NEED NUMBER].",
                "fix": "Use the real number from the creator.",
            }
        ]
    if verdict == "reject":
        doc["checks"] = dict(doc["checks"], no_fabricated_claims="fail")
    return doc


def _make_needs_human(run_dir: Path, brief_id: str = "B01") -> None:
    """r0 revise, r1 revise: the brief is needs_human."""
    _write_script(run_dir, brief_id, 0)
    _dump_json(agents.qa_path(run_dir, brief_id, 0), _qa_doc("revise", 0))
    _write_script(run_dir, brief_id, 1)
    _dump_json(agents.qa_path(run_dir, brief_id, 1), _qa_doc("revise", 1))


def _write_intake(run_dir: Path, brief_id: str = "B01") -> Path:
    path = agents.intake_path(run_dir, brief_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Answers\n\n- Time: 9 minutes\n", encoding="utf-8")
    return path


def _write_facts(run_dir: Path, text: str, brief_id: str = "B01") -> Path:
    path = agents.facts_path(run_dir, brief_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class IntakeTests(NoNetworkTestCase):
    def test_intake_questions_cover_specifics_inventory_and_format(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _add_inventory(project)
            run_dir = _mock_research_and_rank(project)
            _add_specifics(run_dir, "B01", SPECIFICS)

            text = agents.intake_questions(project, run_dir, "B01")

            # The main named thing and the inventory that could replace it.
            self.assertIn("Todoist", text)
            self.assertIn("Paper week card, 7 boxes, used every Sunday since 2024", text)
            self.assertIn("Notion weekly template", text)
            # Every public specific gets a keep question.
            self.assertIn("obsidian-tasks", text)
            self.assertIn("keep", text.lower())
            # The source creator's own claim asks for the creator's version.
            self.assertIn("3 hours saved a week", text)
            # Numbers the format needs, with the blank-is-fine rule.
            self.assertIn("Leave blank if you do not know", text)
            self.assertIn("How long", text)
            # Where the answers go, and their shape.
            self.assertIn(str(agents.intake_path(run_dir, "B01").resolve()), text)
            self.assertIn("## Answers", text)
            self.assertNotIn("—", text)
            # Deterministic.
            self.assertEqual(text, agents.intake_questions(project, run_dir, "B01"))

    def test_intake_works_for_an_older_brief_with_no_specifics_or_inventory(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            text = agents.intake_questions(project, run_dir, "B01")

            self.assertIn("Leave blank if you do not know", text)
            self.assertIn("## Answers", text)

    def test_intake_titles_a_fill_brief_by_its_idea_title(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            path = run_dir / "03-briefs.json"
            doc = store.read_json(path)
            doc["briefs"][0].update(
                {"kind": "fill", "idea_title": "My own Sunday week card", "brief_title": "Source format"}
            )
            path.write_text(json.dumps(doc), encoding="utf-8")

            text = agents.intake_questions(project, run_dir, "B01")

            self.assertEqual(text.splitlines()[0], "# Intake for B01: My own Sunday week card")

    def test_intake_cli_exit_0_and_exit_2_for_missing_brief_or_run(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            code, out, err = _main(
                ["intake", "--project", str(project), "--run", "latest", "--brief", "B01"]
            )
            self.assertEqual(code, codes.EXIT_OK, err)
            self.assertIn(str(agents.intake_path(run_dir, "B01").resolve()), out)

            code, out, err = _main(
                ["intake", "--project", str(project), "--run", "latest", "--brief", "B99"]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertIn("B99", err)

            code, out, err = _main(
                ["intake", "--project", str(project), "--run", "nope", "--brief", "B01"]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertTrue(err.strip())


class VerifyFactsTests(NoNetworkTestCase):
    def _verify(self, project: Path) -> Any:
        return _main(
            ["verify", "--project", str(project), "--run", "latest",
             "--stage", "facts", "--brief", "B01"]
        )

    def test_verify_facts_ok_counts_bullets(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            path = _write_facts(
                run_dir,
                "# Facts for B01\n\n"
                "- Todoist has a free plan. Source: https://todoist.com/pricing. Checked 2026-09-18.\n"
                "- obsidian-tasks is open source. Source: https://github.com/obsidian-tasks-group/obsidian-tasks. Checked 2026-09-18.\n",
            )

            code, out, err = self._verify(project)

            self.assertEqual(code, codes.EXIT_OK, err)
            self.assertEqual(out.strip(), f"ok {path.resolve()} facts=2")

    def test_verify_facts_exit_7_for_missing_file_or_unsourced_bullet(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)

            code, out, err = self._verify(project)
            self.assertEqual(code, codes.EXIT_VERIFY)
            self.assertEqual(out, "")
            self.assertIn("04-facts", err)

            _write_facts(
                run_dir,
                "- Todoist has a free plan. Source: https://todoist.com/pricing. Checked 2026-09-18.\n"
                "- Todoist has 40 million users. Source: http://example.com. Checked 2026-09-18.\n"
                "- A fact with no source at all.\n",
            )
            code, out, err = self._verify(project)
            self.assertEqual(code, codes.EXIT_VERIFY)
            self.assertEqual(out, "")
            self.assertIn("40 million users", err)
            self.assertIn("no source at all", err)
            self.assertNotIn("free plan", err)

    def test_verify_facts_needs_brief(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _mock_research_and_rank(project)
            code, _out, err = _main(
                ["verify", "--project", str(project), "--run", "latest", "--stage", "facts"]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertIn("--brief", err)


class PromptInputTests(NoNetworkTestCase):
    def test_prompts_list_intake_and_facts_only_when_present(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)

            write_text = agents.write_prompt(project, run_dir, "B01", references_dir=REFERENCES_DIR)
            qa_text = agents.qa_prompt(project, run_dir, "B01", 0, references_dir=REFERENCES_DIR)
            for text in (write_text, qa_text):
                self.assertNotIn("04-intake", text)
                self.assertNotIn("04-facts", text)

            intake = _write_intake(run_dir)
            facts = _write_facts(
                run_dir, "- A fact. Source: https://example.com/a. Checked 2026-09-18.\n"
            )
            write_text = agents.write_prompt(project, run_dir, "B01", references_dir=REFERENCES_DIR)
            qa_text = agents.qa_prompt(project, run_dir, "B01", 0, references_dir=REFERENCES_DIR)
            for text in (write_text, qa_text):
                self.assertIn(f"Intake answers: {intake.resolve()}", text)
                self.assertIn(f"Fact sheet: {facts.resolve()}", text)

    def test_prompts_list_specificity_md_only_when_present(self) -> None:
        with temp_project() as project, tempfile.TemporaryDirectory() as tmp:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)
            refs = Path(tmp) / "references"
            shutil.copytree(REFERENCES_DIR, refs)
            if (refs / "specificity.md").exists():
                (refs / "specificity.md").unlink()

            write_text = agents.write_prompt(project, run_dir, "B01", references_dir=refs)
            qa_text = agents.qa_prompt(project, run_dir, "B01", 0, references_dir=refs)
            self.assertNotIn("specificity.md", write_text)
            self.assertNotIn("specificity.md", qa_text)

            (refs / "specificity.md").write_text("# Specificity\n", encoding="utf-8")
            write_text = agents.write_prompt(project, run_dir, "B01", references_dir=refs)
            qa_text = agents.qa_prompt(project, run_dir, "B01", 0, references_dir=refs)
            self.assertIn(str((refs / "specificity.md").resolve()), write_text)
            self.assertIn(str((refs / "specificity.md").resolve()), qa_text)

    def test_prompts_state_three_tiers_min_specifics_and_rule_precedence(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)
            cfg = dict(store.DEFAULT_CONFIG, min_specifics=5)

            write_text = agents.write_prompt(
                project, run_dir, "B01", references_dir=REFERENCES_DIR, cfg=cfg
            )
            qa_text = agents.qa_prompt(
                project, run_dir, "B01", 0, references_dir=REFERENCES_DIR, cfg=cfg
            )
            for text in (write_text, qa_text):
                self.assertIn("About the creator:", text)
                self.assertIn("About the world:", text)
                self.assertIn("Never:", text)
                self.assertIn("public: true", text)
                self.assertIn("Forbidden claims", text)
                self.assertIn("at least 5 concrete named items", text)
                self.assertIn("Placeholders are only for facts about the creator", text)
                self.assertIn("outrank the default offer placement", text)
                self.assertNotIn("—", text)
            self.assertIn("min_specifics: 5", qa_text)
            # A placeholder no longer earns full proof credit.
            self.assertNotIn("never lower a score", qa_text)
            self.assertIn("not_generic", qa_text)
            self.assertIn("facts_sourced", qa_text)


class QaSpecificitySchemaTests(NoNetworkTestCase):
    def test_schema_requires_the_new_keys(self) -> None:
        # A new review always fills them; verify_qa lets a pre-0.3.0
        # file that has none of them through (see the next test).
        schema = director.load_schema("qa")
        checks = schema["properties"]["checks"]
        scores = schema["properties"]["scores"]
        for key in ("not_generic", "facts_sourced"):
            self.assertIn(key, checks["properties"])
            self.assertIn(key, checks["required"])
        self.assertIn("body_specificity", scores["properties"])
        self.assertIn("body_specificity", scores["required"])

    def test_verify_qa_flags_a_new_file_missing_only_some_new_keys(self) -> None:
        base = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
        with temp_project() as project:
            partial = dict(base)
            partial["checks"] = dict(base["checks"], not_generic="pass")
            problems = agents.verify_qa(_dump_json(project / "partial.json", partial), 8)
            self.assertTrue(any("facts_sourced" in problem for problem in problems), problems)
            self.assertTrue(any("body_specificity" in problem for problem in problems), problems)

    def test_verify_qa_accepts_old_files_and_new_keys(self) -> None:
        # The shipped fixture predates 0.3.0 and has none of the new keys.
        self.assertEqual(agents.verify_qa(QA_FIXTURE, 8), [])
        base = json.loads(QA_FIXTURE.read_text(encoding="utf-8"))
        with temp_project() as project:
            new = dict(base)
            new["checks"] = dict(base["checks"], not_generic="pass", facts_sourced="pass")
            new["scores"] = dict(base["scores"], body_specificity=9)
            self.assertEqual(agents.verify_qa(_dump_json(project / "new.json", new), 8), [])

            generic = dict(new)
            generic["checks"] = dict(new["checks"], not_generic="fail")
            self.assertTrue(agents.verify_qa(_dump_json(project / "generic.json", generic), 8))

            vague = dict(new)
            vague["scores"] = dict(new["scores"], body_specificity=7)
            self.assertTrue(agents.verify_qa(_dump_json(project / "vague.json", vague), 8))

            vague_revise = dict(vague, verdict="revise")
            self.assertEqual(
                agents.verify_qa(_dump_json(project / "vague_revise.json", vague_revise), 8), []
            )


class RevisionTwoTests(NoNetworkTestCase):
    def test_revision_2_refused_unless_needs_human(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)
            _write_qa(run_dir, "B01", 0)
            _write_intake(run_dir)

            with self.assertRaises(agents.AgentsError) as ctx:
                agents.write_prompt(
                    project, run_dir, "B01", revision=2, references_dir=REFERENCES_DIR
                )
            self.assertEqual(ctx.exception.exit_code, codes.EXIT_USAGE)
            self.assertIn("needs a human", str(ctx.exception))

    def test_revision_2_refused_without_intake(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _make_needs_human(run_dir)

            with self.assertRaises(agents.AgentsError) as ctx:
                agents.write_prompt(
                    project, run_dir, "B01", revision=2, references_dir=REFERENCES_DIR
                )
            self.assertEqual(ctx.exception.exit_code, codes.EXIT_USAGE)
            self.assertIn(str(agents.intake_path(run_dir, "B01").resolve()), str(ctx.exception))

            code, out, err = _main(
                ["write-prompt", "--project", str(project), "--run", "latest",
                 "--brief", "B01", "--revision", "2"]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertIn("intake", err)

    def test_revision_3_is_always_refused(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _make_needs_human(run_dir)
            _write_intake(run_dir)
            with self.assertRaises(agents.AgentsError) as ctx:
                agents.write_prompt(
                    project, run_dir, "B01", revision=3, references_dir=REFERENCES_DIR
                )
            self.assertEqual(ctx.exception.exit_code, codes.EXIT_USAGE)

    def test_revision_2_prompt_carries_r1_issues_and_intake(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _make_needs_human(run_dir)
            intake = _write_intake(run_dir)

            prompt = agents.write_prompt(
                project, run_dir, "B01", revision=2, references_dir=REFERENCES_DIR
            )

            self.assertIn("This is revision 2.", prompt)
            self.assertIn(str(agents.script_path(run_dir, "B01", 1).resolve()), prompt)
            self.assertIn(str(agents.qa_path(run_dir, "B01", 1).resolve()), prompt)
            self.assertIn("The proof beat rests on [NEED NUMBER].", prompt)
            self.assertIn("Use the real number from the creator.", prompt)
            self.assertIn(str(intake.resolve()), prompt)
            self.assertIn(str(agents.script_path(run_dir, "B01", 2).resolve()), prompt)
            self.assertIn("no revision 3", prompt)

    def test_revision_2_after_a_reject_uses_the_rejected_revision(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)
            _dump_json(agents.qa_path(run_dir, "B01", 0), _qa_doc("reject", 0))
            _write_intake(run_dir)

            prompt = agents.write_prompt(
                project, run_dir, "B01", revision=2, references_dir=REFERENCES_DIR
            )
            self.assertIn(str(agents.script_path(run_dir, "B01", 0).resolve()), prompt)
            self.assertIn(str(agents.qa_path(run_dir, "B01", 0).resolve()), prompt)

    def test_qa_prompt_revision_2_gated_the_same_way(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _make_needs_human(run_dir)
            _write_script(run_dir, "B01", 2)

            with self.assertRaises(agents.AgentsError) as ctx:
                agents.qa_prompt(project, run_dir, "B01", 2, references_dir=REFERENCES_DIR)
            self.assertEqual(ctx.exception.exit_code, codes.EXIT_USAGE)

            _write_intake(run_dir)
            prompt = agents.qa_prompt(project, run_dir, "B01", 2, references_dir=REFERENCES_DIR)
            self.assertIn(str(agents.qa_path(run_dir, "B01", 2).resolve()), prompt)

            # Re-running QA r2 after its verdict landed is still allowed.
            _dump_json(agents.qa_path(run_dir, "B01", 2), _qa_doc("pass", 2))
            agents.qa_prompt(project, run_dir, "B01", 2, references_dir=REFERENCES_DIR)

    def test_state_after_a_revision_2_verdict(self) -> None:
        for verdict, status, needs_human in (
            ("pass", "pass", False),
            ("revise", "needs_human", True),
            ("reject", "needs_human", True),
        ):
            with self.subTest(verdict=verdict), temp_project() as project:
                _write_project(project)
                run_dir = _mock_research_and_rank(project)
                _make_needs_human(run_dir)
                _write_intake(run_dir)
                _write_script(run_dir, "B01", 2)
                _dump_json(agents.qa_path(run_dir, "B01", 2), _qa_doc(verdict, 2))

                state = agents.brief_state(run_dir, "B01")

                self.assertEqual(state["revision"], 2)
                self.assertEqual(state["verdict"], verdict)
                self.assertEqual(state["status"], status)
                self.assertEqual(state["needs_human"], needs_human)


class PlaceholderRatioTests(NoNetworkTestCase):
    def test_verify_write_appends_placeholder_ratio(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            _write_script(run_dir, "B01", 0)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "write", "--brief", "B01"]
            )

            self.assertEqual(code, codes.EXIT_OK, err)
            # 1 placeholder in 105 counted words.
            self.assertTrue(
                out.strip().endswith("placeholders=1 placeholder_ratio=1.0"), out
            )

    def test_placeholder_ratio_helper(self) -> None:
        self.assertEqual(agents.placeholder_ratio(0, 0), 0.0)
        self.assertEqual(agents.placeholder_ratio(3, 80), 3.8)
        self.assertEqual(agents.placeholder_ratio(1, 105), 1.0)


AGENTS_DIR = REFERENCES_DIR.parent.parent.parent / "agents"


class WriterAndQaDocsTests(NoNetworkTestCase):
    def _read(self, path: Path) -> str:
        return " ".join(path.read_text(encoding="utf-8").split())

    def test_writer_docs_state_tiers_specificity_and_new_inputs(self) -> None:
        for path in (AGENTS_DIR / "script-writer.md", REFERENCES_DIR / "scripting.md"):
            text = self._read(path)
            with self.subTest(file=path.name):
                for phrase in (
                    "About the creator",
                    "About the world",
                    "Never",
                    "public: true",
                    "intake",
                    "fact sheet",
                    "min_specifics",
                    "Inventory",
                    "Placeholders are only for facts about the creator",
                    "outrank the default offer placement",
                    "revision 2",
                ):
                    self.assertIn(phrase, text)
                self.assertNotIn("\u2014", text)

    def test_qa_docs_define_the_new_score_and_checks(self) -> None:
        for path in (AGENTS_DIR / "qa-reviewer.md", REFERENCES_DIR / "qa-rubric.md"):
            text = self._read(path)
            with self.subTest(file=path.name):
                for phrase in (
                    "body_specificity",
                    "not_generic",
                    "facts_sourced",
                    "min_specifics",
                    "fact sheet",
                    "intake",
                    "A placeholder is not proof",
                    "never fails `payoff_present`",
                ):
                    self.assertIn(phrase, text)
                self.assertNotIn("never lower a score", text)
                self.assertNotIn("\u2014", text)


if __name__ == "__main__":
    unittest.main()


_LEAD_MAGNET = """## Lead magnet

Keyword: PLAN
Title: The Sunday one-prompt planning sheet
- The one prompt, word for word
- Where to paste it and how long it takes
- The week view template shown in the reel

"""


def _script_with_lead_magnet(section: str = _LEAD_MAGNET, keyword_in_cta: bool = True) -> str:
    """The gold fixture script with a `## Lead magnet` section after the CTA."""
    text = SCRIPT_FIXTURE.read_text(encoding="utf-8")
    if keyword_in_cta:
        text = text.replace(
            "One prompt, not another pile-up. Join the free weekly email,",
            "Comment PLAN for the sheet. Join the free weekly email,",
        )
    return text.replace("## Caption", section + "## Caption", 1)


class LeadMagnetTests(NoNetworkTestCase):
    """0.3.0 follow-up: each script suggests its own lead magnet."""

    def _check(self, text: str) -> "agents.ScriptCheck":
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "B01.r0.md"
            path.write_text(text, encoding="utf-8")
            return agents.verify_script(path, REFERENCES_DIR, 0.10)

    def test_a_script_with_a_lead_magnet_passes(self) -> None:
        self.assertEqual(self._check(_script_with_lead_magnet()).errors, [])

    def test_a_script_without_one_still_passes(self) -> None:
        self.assertEqual(self._check(SCRIPT_FIXTURE.read_text(encoding="utf-8")).errors, [])

    def test_lead_magnet_needs_a_keyword_a_title_and_three_to_seven_items(self) -> None:
        bad = {
            "no keyword": _LEAD_MAGNET.replace("Keyword: PLAN\n", ""),
            "no title": _LEAD_MAGNET.replace("Title: The Sunday one-prompt planning sheet\n", ""),
            "two items": _LEAD_MAGNET.replace("- The week view template shown in the reel\n", ""),
        }
        for name, section in bad.items():
            with self.subTest(case=name):
                errors = self._check(_script_with_lead_magnet(section)).errors
                self.assertTrue(any(e.startswith("Lead magnet:") for e in errors), errors)

    def test_the_primary_cta_must_use_the_lead_magnet_keyword(self) -> None:
        errors = self._check(_script_with_lead_magnet(keyword_in_cta=False)).errors
        self.assertTrue(any("PLAN" in e and "CTA" in e for e in errors), errors)

    def test_write_prompt_asks_for_a_lead_magnet_and_allows_reasonable_claims(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            text = agents.write_prompt(project, run_dir, "B01", references_dir=REFERENCES_DIR)
        self.assertIn("## Lead magnet", text)
        self.assertIn("Keyword:", text)
        self.assertIn("reasonable claim", text)
        self.assertIn("never invent a number about the creator's own results", text)
        self.assertNotIn("—", text)
