"""Tests for the `setup` subcommand and `lib/setup.py`.

`setup` is the one command a creator runs before anything else. It turns
the short answers the skill collected into the three files every later
stage reads: `.contentos/creator.md` (built from
`references/creator-template.md`), `.contentos/config.json`
(`store.DEFAULT_CONFIG` plus the normalized competitor and format-account
handles), and `.contentos/rules.md`. See the design spec's "Reference
files" row for `creator-template.md` and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-3-brief.md` for the
interface.

Nothing here touches the network: `setup` is pure file work, and the one
end-to-end test runs `research --mock`, which serves the committed Apify
fixtures. NoNetworkTestCase is the second line of defense.
"""
from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Sequence, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT, SKILL_DIR, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import setup as setup_lib  # noqa: E402
from lib import codes, store  # noqa: E402

REFERENCES_DIR = SKILL_DIR / "references"
CREATOR_TEMPLATE = REFERENCES_DIR / "creator-template.md"
SAMPLE_ANSWERS = REPO_ROOT / "fixtures" / "setup-answers.sample.json"

RULES_COMMENT = (
    "# One correction per line. ContentOS appends these to the writer and QA prompts."
)

# A complete answers file, one value per key so a test can tell which
# answer landed in which section.
FULL_ANSWERS = {
    "creator_name": "Dana Cole, a productivity creator who posts habit and planning tips",
    "one_liner": "Dana turns a messy to-do list into a system you actually stick with.",
    "pillars": ["One habit at a time", "Weekly planning resets"],
    "target_user": "people who have deleted four productivity apps in the last year",
    "frustration": "I set up a perfect system on Sunday and it falls apart by Wednesday",
    "objection": "another productivity hack I will abandon in a week",
    "offer": "a free weekly planning email with one prompt and one template",
    "offer_objection": "another newsletter that just piles up unread",
    "payoff_moments": ["The week view filling in from a blank page to a done list"],
    "allowed_claims": ["Setting up the weekly plan takes under 10 minutes"],
    "forbidden_claims": ["No income or productivity guarantees", "No medical claims"],
    "proof_assets": ["The email open-rate screenshot"],
    "voice_on": ["dry", "direct", "warm"],
    "voice_off": ["peppy", "clinical", "salesy"],
    "off_limits_words": ["hustle", "grind", "unlock"],
    "cta": "Join the free weekly planning email",
    "hashtag_seeds": ["#productivity", "#planwithme"],
    "competitors": ["sproutapp", "habitlab"],
    "format_accounts": ["dailywins"],
}

# Only the one key `run_setup` refuses to do without.
MINIMAL_ANSWERS = {"competitors": ["sproutapp"]}


def _main(argv: Sequence[str]) -> Tuple[int, str, str]:
    """Run one contentos.py subcommand in-process; return (code, stdout, stderr)."""
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = contentos.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _template_headings() -> list:
    """The template's `## ` headings, in order, without `Sources`."""
    headings = [
        line[3:].strip()
        for line in CREATOR_TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    return [heading for heading in headings if heading != "Sources"]


def _headings(text: str) -> list:
    """The `## ` headings of a rendered creator.md, in order."""
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def _section(text: str, heading: str) -> str:
    """The body of one `## <heading>` section of a rendered creator.md."""
    lines = text.splitlines()
    start = lines.index("## " + heading) + 1
    body = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


def _write_answers(project: Path, answers) -> Path:
    """Write an answers JSON file inside `project` and return its path."""
    path = project / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    return path


class SetupTests(NoNetworkTestCase):
    def test_setup_writes_creator_config_rules_gitignore(self) -> None:
        with temp_project() as project:
            answers_file = _write_answers(project, FULL_ANSWERS)

            code, out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 0, err)

            # stdout is the same JSON `diagnose` prints.
            report = json.loads(out)
            _code, diagnose_out, _err = _main(["diagnose", "--project", str(project)])
            self.assertEqual(sorted(report), sorted(json.loads(diagnose_out)))
            self.assertTrue(report["creator_md"])
            self.assertTrue(report["config_json"])
            self.assertTrue(report["rules_md"])

            contentos_dir = store.contentos_dir(project)
            creator = (contentos_dir / "creator.md").read_text(encoding="utf-8")
            config = json.loads((contentos_dir / "config.json").read_text(encoding="utf-8"))
            rules = (contentos_dir / "rules.md").read_text(encoding="utf-8")
            gitignore = (contentos_dir / ".gitignore").read_text(encoding="utf-8")

            # Same headings as the template, same order, minus Sources.
            self.assertEqual(_headings(creator), _template_headings())
            self.assertNotIn("## Sources", creator)
            self.assertTrue(creator.startswith("# Creator profile"))

            # Answered sections carry the creator's words.
            self.assertEqual(_section(creator, "One-liner"), FULL_ANSWERS["one_liner"])
            self.assertEqual(
                _section(creator, "Pillars"),
                "- One habit at a time\n- Weekly planning resets",
            )
            self.assertEqual(
                _section(creator, "Competitors"), "- sproutapp\n- habitlab"
            )
            self.assertEqual(_section(creator, "Format accounts"), "- dailywins")
            self.assertEqual(_section(creator, "CTA"), FULL_ANSWERS["cta"])
            self.assertIn(
                "- The email open-rate screenshot", _section(creator, "Proof assets")
            )
            self.assertIn("#productivity", _section(creator, "Hashtag seeds"))
            self.assertEqual(
                _section(creator, "Payoff moments"),
                "- The week view filling in from a blank page to a done list",
            )

            # What you promote renders bullets, since FULL_ANSWERS carries an offer.
            self.assertEqual(
                _section(creator, "What you promote"),
                "- What it is: a free weekly planning email with one prompt and one template\n"
                "- The objection that stops people: another newsletter that just piles up unread",
            )

            # Audience profile and Brand voice keep the template's
            # sub-bullet labels; answered ones carry the answer, the rest
            # read TODO.
            audience = _section(creator, "Audience profile")
            self.assertIn(
                "- Who specifically: people who have deleted four productivity apps "
                "in the last year",
                audience,
            )
            self.assertIn(
                "- Their number one frustration or want, in their own words: "
                "I set up a perfect system on Sunday and it falls apart by Wednesday",
                audience,
            )
            self.assertIn(
                "- Top 3 objections: another productivity hack I will abandon in a week",
                audience,
            )
            self.assertIn(
                "- What they already watch and why it falls short: TODO", audience
            )
            self.assertIn("- The fear behind it: TODO", audience)

            voice = _section(creator, "Brand voice")
            self.assertIn("- 3 adjectives it is: dry, direct, warm", voice)
            self.assertIn("- 3 adjectives it is not: peppy, clinical, salesy", voice)
            self.assertIn("- 10 off-limits words: hustle, grind, unlock", voice)
            self.assertIn("- Sentence rules: TODO", voice)
            self.assertIn("- 3 sample sentences: TODO", voice)

            # config.json is DEFAULT_CONFIG plus the handles.
            expected = dict(
                store.DEFAULT_CONFIG,
                competitors=["sproutapp", "habitlab"],
                format_accounts=["dailywins"],
            )
            self.assertEqual(config, expected)
            # It must load back through the real validator.
            loaded = store.load_config(project)
            self.assertEqual(loaded["competitors"], ["sproutapp", "habitlab"])
            self.assertEqual(loaded["format_accounts"], ["dailywins"])

            self.assertEqual(rules.strip(), RULES_COMMENT)
            for line in (".env", "runs/*/videos/", "runs/*/frames/", "setup-answers.json"):
                with self.subTest(gitignore_line=line):
                    self.assertIn(line, gitignore)

    def test_setup_blank_offer_writes_none_line_and_is_not_todo(self) -> None:
        with temp_project() as project:
            answers_file = _write_answers(project, dict(MINIMAL_ANSWERS, offer="   "))

            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 0, err)
            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                _section(creator, setup_lib.OFFER_HEADING), setup_lib.NO_OFFER_LINE
            )

        # Missing entirely (MINIMAL_ANSWERS carries no "offer" key at all)
        # behaves the same way, and the section never counts as unanswered.
        with temp_project() as project:
            result = setup_lib.run_setup(project, dict(MINIMAL_ANSWERS), REFERENCES_DIR)
            self.assertNotIn(setup_lib.OFFER_HEADING, result["todo_sections"])
            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                _section(creator, setup_lib.OFFER_HEADING), setup_lib.NO_OFFER_LINE
            )

    def test_setup_offer_renders_bullets(self) -> None:
        with temp_project() as project:
            answers = dict(
                MINIMAL_ANSWERS,
                offer="a free weekly planning email",
                offer_objection="another newsletter that piles up unread",
            )
            answers_file = _write_answers(project, answers)

            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 0, err)
            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                _section(creator, setup_lib.OFFER_HEADING),
                "- What it is: a free weekly planning email\n"
                "- The objection that stops people: another newsletter that piles up unread",
            )

            result = setup_lib.run_setup(project, answers, REFERENCES_DIR, force=True)
            self.assertNotIn(setup_lib.OFFER_HEADING, result["todo_sections"])

        # An offer with no objection still renders bullets (not the
        # no-offer line), with the objection line reading TODO, and the
        # section still does not count as unanswered.
        with temp_project() as project:
            answers = dict(MINIMAL_ANSWERS, offer="a free weekly planning email")
            result = setup_lib.run_setup(project, answers, REFERENCES_DIR)
            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )
            offer_section = _section(creator, setup_lib.OFFER_HEADING)
            self.assertIn("- What it is: a free weekly planning email", offer_section)
            self.assertIn("- The objection that stops people: TODO", offer_section)
            self.assertNotIn(setup_lib.OFFER_HEADING, result["todo_sections"])

    def test_setup_format_accounts_optional_normalized_and_deduped_against_competitors(
        self,
    ) -> None:
        with temp_project() as project:
            answers = dict(
                MINIMAL_ANSWERS,
                competitors=["sproutapp", "habitlab", "DailyWins"],
                format_accounts=[
                    "  @DailyWins ",
                    "ghostaccount",
                    "https://www.instagram.com/HabitLab/",
                    "ghostaccount",
                ],
            )
            answers_file = _write_answers(project, answers)

            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 0, err)
            config = store.load_config(project)
            self.assertEqual(config["competitors"], ["sproutapp", "habitlab", "dailywins"])
            # dailywins and habitlab are already competitors, so both drop
            # out of format_accounts (case-insensitively); only
            # ghostaccount (deduplicated) remains.
            self.assertEqual(config["format_accounts"], ["ghostaccount"])

            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                _section(creator, setup_lib.FORMAT_ACCOUNTS_HEADING), "- ghostaccount"
            )

        # format_accounts is optional, like the offer: blank/missing gets
        # an empty list in config.json, and creator.md writes the
        # no-format-accounts line instead of TODO, so the section is
        # never counted as unanswered (design spec: "when empty, setup
        # writes `None. Add accounts from any niche whose formats
        # travel.` and the section does not count as TODO").
        with temp_project() as project:
            result = setup_lib.run_setup(project, dict(MINIMAL_ANSWERS), REFERENCES_DIR)
            self.assertNotIn(setup_lib.FORMAT_ACCOUNTS_HEADING, result["todo_sections"])
            config = store.load_config(project)
            self.assertEqual(config["format_accounts"], [])
            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )
            section = _section(creator, setup_lib.FORMAT_ACCOUNTS_HEADING)
            self.assertIn(setup_lib.NO_FORMAT_ACCOUNTS_LINE, section)
            self.assertNotIn("TODO", section)

    def test_setup_refuses_overwrite_without_force(self) -> None:
        with temp_project() as project:
            answers_file = _write_answers(project, FULL_ANSWERS)
            code, _out, _err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )
            self.assertEqual(code, 0)

            contentos_dir = store.contentos_dir(project)
            creator_path = contentos_dir / "creator.md"
            rules_path = contentos_dir / "rules.md"
            creator_path.write_text("# hand edited\n", encoding="utf-8")
            rules_path.write_text(RULES_COMMENT + "\nnever say hustle\n", encoding="utf-8")

            code, out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("creator.md", err)
            self.assertIn("--force", err)
            # Nothing was touched.
            self.assertEqual(creator_path.read_text(encoding="utf-8"), "# hand edited\n")

            # --force rewrites creator.md but never rules.md.
            code, _out, err = _main(
                [
                    "setup",
                    "--project",
                    str(project),
                    "--answers-file",
                    str(answers_file),
                    "--force",
                ]
            )

            self.assertEqual(code, 0, err)
            self.assertIn("## One-liner", creator_path.read_text(encoding="utf-8"))
            self.assertIn("never say hustle", rules_path.read_text(encoding="utf-8"))

    def test_setup_force_preserves_tuned_config_and_replaces_competitors(self) -> None:
        # A creator who tuned their thresholds must not lose them because
        # they re-ran setup to change their accounts. `--force`
        # rewrites creator.md, keeps every existing config key, and
        # replaces only `competitors` and `format_accounts`.
        with temp_project() as project:
            answers_file = _write_answers(project, FULL_ANSWERS)
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )
            self.assertEqual(code, 0, err)

            config_path = store.contentos_dir(project) / "config.json"
            tuned = json.loads(config_path.read_text(encoding="utf-8"))
            tuned["apify_max_charge_usd"] = 12.5
            tuned["briefs"] = 2
            tuned["qa_pass_threshold"] = 10
            tuned["reels_per_account"] = 45
            tuned["my_own_note"] = "keep me"
            config_path.write_text(json.dumps(tuned), encoding="utf-8")

            new_answers = dict(
                FULL_ANSWERS,
                competitors=["@DailyWins", "ghostaccount"],
                format_accounts=["sproutapp"],
            )
            new_file = _write_answers(project, new_answers)
            code, _out, err = _main(
                [
                    "setup",
                    "--project",
                    str(project),
                    "--answers-file",
                    str(new_file),
                    "--force",
                ]
            )

            self.assertEqual(code, 0, err)
            after = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(after["competitors"], ["dailywins", "ghostaccount"])
            self.assertEqual(after["format_accounts"], ["sproutapp"])
            self.assertEqual(after["apify_max_charge_usd"], 12.5)
            self.assertEqual(after["briefs"], 2)
            self.assertEqual(after["qa_pass_threshold"], 10)
            self.assertEqual(after["reels_per_account"], 45)
            self.assertEqual(after["my_own_note"], "keep me")

            # creator.md is rewritten with the new handles.
            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                _section(creator, "Competitors"), "- dailywins\n- ghostaccount"
            )
            self.assertEqual(_section(creator, "Format accounts"), "- sproutapp")

        # An unreadable or missing config.json falls back to the defaults
        # plus the new competitors, rather than refusing.
        with temp_project() as project:
            answers_file = _write_answers(project, FULL_ANSWERS)
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )
            self.assertEqual(code, 0, err)
            config_path = store.contentos_dir(project) / "config.json"
            config_path.write_text("{not json", encoding="utf-8")

            code, _out, err = _main(
                [
                    "setup",
                    "--project",
                    str(project),
                    "--answers-file",
                    str(answers_file),
                    "--force",
                ]
            )

            self.assertEqual(code, 0, err)
            expected = dict(
                store.DEFAULT_CONFIG,
                competitors=["sproutapp", "habitlab"],
                format_accounts=["dailywins"],
            )
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")), expected
            )

    def test_setup_without_force_also_preserves_a_tuned_config(self) -> None:
        # Setup only refuses without --force when creator.md exists. With
        # no creator.md (a creator who deleted it, or who ran `setup`
        # after hand-writing a config) the run goes ahead, and it must
        # still not reset settings the creator tuned.
        with temp_project() as project:
            answers_file = _write_answers(project, FULL_ANSWERS)
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )
            self.assertEqual(code, 0, err)

            config_path = store.contentos_dir(project) / "config.json"
            tuned = json.loads(config_path.read_text(encoding="utf-8"))
            tuned["apify_max_charge_usd"] = 12.5
            tuned["briefs"] = 2
            tuned["qa_pass_threshold"] = 10
            tuned["my_own_note"] = "keep me"
            config_path.write_text(json.dumps(tuned), encoding="utf-8")

            (store.contentos_dir(project) / "creator.md").unlink()

            new_answers = dict(FULL_ANSWERS, competitors=["@DailyWins", "ghostaccount"])
            new_file = _write_answers(project, new_answers)
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(new_file)]
            )

            self.assertEqual(code, 0, err)
            after = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(after["competitors"], ["dailywins", "ghostaccount"])
            self.assertEqual(after["apify_max_charge_usd"], 12.5)
            self.assertEqual(after["briefs"], 2)
            self.assertEqual(after["qa_pass_threshold"], 10)
            self.assertEqual(after["my_own_note"], "keep me")

    def test_setup_rejects_non_instagram_urls_and_handles_with_spaces(self) -> None:
        # Anything that is not an instagram.com URL or a bare handle is a
        # typo, not an account. Refuse it by name and write nothing,
        # rather than scraping a handle the creator never meant.
        bad_entries = [
            "https://www.tiktok.com/@someone",
            "Sprout App",
            "https://example.com/sproutapp",
            "sprout/app",
            "sprout!app",
        ]
        for bad in bad_entries:
            with self.subTest(competitor=bad):
                with temp_project() as project:
                    answers_file = _write_answers(
                        project, dict(MINIMAL_ANSWERS, competitors=["sproutapp", bad])
                    )

                    code, out, err = _main(
                        [
                            "setup",
                            "--project",
                            str(project),
                            "--answers-file",
                            str(answers_file),
                        ]
                    )

                    self.assertEqual(code, 2)
                    self.assertEqual(out, "")
                    self.assertIn(bad, err)
                    self.assertFalse(
                        (store.contentos_dir(project) / "creator.md").exists()
                    )
                    self.assertFalse(
                        (store.contentos_dir(project) / "config.json").exists()
                    )

    def test_setup_normalizes_handles(self) -> None:
        answers = dict(
            MINIMAL_ANSWERS,
            competitors=[
                "  @SproutApp ",
                "https://www.instagram.com/HabitLab/",
                "http://instagram.com/dailywins/reels/?hl=en",
                "sproutapp",
                "@habitlab",
                "   ",
                "instagram.com/ghostaccount",
            ],
        )
        with temp_project() as project:
            answers_file = _write_answers(project, answers)

            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 0, err)
            config = store.load_config(project)
            self.assertEqual(
                config["competitors"],
                ["sproutapp", "habitlab", "dailywins", "ghostaccount"],
            )
            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                _section(creator, "Competitors"),
                "- sproutapp\n- habitlab\n- dailywins\n- ghostaccount",
            )

    def test_setup_keeps_unanswered_sections_as_headings(self) -> None:
        with temp_project() as project:
            answers_file = _write_answers(project, MINIMAL_ANSWERS)

            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 0, err)
            creator = (store.contentos_dir(project) / "creator.md").read_text(
                encoding="utf-8"
            )

            self.assertEqual(_headings(creator), _template_headings())
            # Every section but Competitors, What you promote, and Format
            # accounts is unanswered, and each one keeps the template's
            # guidance followed by the to-do line. What you promote and
            # Format accounts are both optional: a blank answer writes a
            # "none" line instead of TODO, and neither counts as
            # unanswered (see
            # test_setup_blank_offer_writes_none_line_and_is_not_todo and
            # test_setup_format_accounts_optional_normalized_and_deduped_against_competitors).
            for heading in _template_headings():
                if heading in (
                    setup_lib.COMPETITORS_HEADING,
                    setup_lib.OFFER_HEADING,
                    setup_lib.FORMAT_ACCOUNTS_HEADING,
                ):
                    continue
                with self.subTest(heading=heading):
                    section = _section(creator, heading)
                    self.assertIn("TODO", section)
            self.assertEqual(
                _section(creator, setup_lib.OFFER_HEADING), setup_lib.NO_OFFER_LINE
            )
            self.assertIn(
                setup_lib.NO_FORMAT_ACCOUNTS_LINE,
                _section(creator, setup_lib.FORMAT_ACCOUNTS_HEADING),
            )
            self.assertIn(
                "One sentence that explains what you do to someone who has never "
                "heard of you.",
                _section(creator, "One-liner"),
            )
            self.assertTrue(_section(creator, "One-liner").endswith("TODO: fill this in."))

    def test_setup_inventory_fills_the_inventory_section(self) -> None:
        answers = dict(FULL_ANSWERS)
        answers["inventory"] = [
            "Notion weekly template, used every Sunday since 2024",
            "  ",
            "Paper habit card, 31 squares",
        ]
        with temp_project() as project:
            result = setup_lib.run_setup(project, answers, REFERENCES_DIR)
            creator = Path(result["creator_md"]).read_text(encoding="utf-8")

            headings = _headings(creator)
            self.assertIn("Inventory", headings)
            self.assertEqual(headings.index("Inventory"), headings.index("Proof assets") + 1)
            self.assertEqual(
                _section(creator, "Inventory"),
                "- Notion weekly template, used every Sunday since 2024\n"
                "- Paper habit card, 31 squares",
            )
            self.assertNotIn("Inventory", result["todo_sections"])

    def test_setup_without_inventory_leaves_it_todo(self) -> None:
        with temp_project() as project:
            result = setup_lib.run_setup(project, dict(FULL_ANSWERS), REFERENCES_DIR)
            creator = Path(result["creator_md"]).read_text(encoding="utf-8")
            self.assertIn("Inventory", result["todo_sections"])
            self.assertTrue(_section(creator, "Inventory").endswith(setup_lib.TODO_LINE))

    def test_setup_lead_magnet_is_appended_to_allowed_claims(self) -> None:
        answers = dict(FULL_ANSWERS)
        answers["lead_magnet"] = "  a one-page weekly reset checklist  "
        with temp_project() as project:
            result = setup_lib.run_setup(project, answers, REFERENCES_DIR)
            creator = Path(result["creator_md"]).read_text(encoding="utf-8")
            self.assertEqual(
                _section(creator, "Allowed claims"),
                "- Setting up the weekly plan takes under 10 minutes\n"
                "- The CTA guide: a one-page weekly reset checklist",
            )

        # A lead magnet alone answers Allowed claims.
        answers = dict(MINIMAL_ANSWERS, lead_magnet="the free template pack")
        with temp_project() as project:
            result = setup_lib.run_setup(project, answers, REFERENCES_DIR)
            creator = Path(result["creator_md"]).read_text(encoding="utf-8")
            self.assertEqual(
                _section(creator, "Allowed claims"), "- The CTA guide: the free template pack"
            )
            self.assertNotIn("Allowed claims", result["todo_sections"])

        # A blank lead magnet adds nothing.
        answers = dict(FULL_ANSWERS, lead_magnet="   ")
        with temp_project() as project:
            result = setup_lib.run_setup(project, answers, REFERENCES_DIR)
            creator = Path(result["creator_md"]).read_text(encoding="utf-8")
            self.assertNotIn("The CTA guide", creator)

    def test_setup_rejects_a_bad_answers_file_or_no_competitors(self) -> None:
        with temp_project() as project:
            missing = project / "nope.json"
            code, out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(missing)]
            )
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn(str(missing), err)

            broken = project / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(broken)]
            )
            self.assertEqual(code, 2)
            self.assertTrue(err.strip())

            not_an_object = _write_answers(project, ["sproutapp"])
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(not_an_object)]
            )
            self.assertEqual(code, 2)
            self.assertTrue(err.strip())

            not_a_handle = _write_answers(project, {"competitors": ["  ", "@"]})
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(not_a_handle)]
            )
            self.assertEqual(code, 2)
            self.assertIn("Instagram handle", err)

            no_competitors = _write_answers(project, {"competitors": []})
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(no_competitors)]
            )
            self.assertEqual(code, 2)
            self.assertIn("competitor", err)
            self.assertIn("niche", err)

            self.assertFalse((store.contentos_dir(project) / "creator.md").exists())

    def test_run_setup_reports_what_it_wrote(self) -> None:
        # The skill reads `todo_sections` to tell the creator which
        # headings are still theirs to fill in.
        with temp_project() as project:
            result = setup_lib.run_setup(project, dict(FULL_ANSWERS), REFERENCES_DIR)

            self.assertEqual(result["competitors"], ["sproutapp", "habitlab"])
            self.assertEqual(result["format_accounts"], ["dailywins"])
            self.assertEqual(
                result["creator_md"], str(store.contentos_dir(project) / "creator.md")
            )
            self.assertNotIn("Competitors", result["todo_sections"])
            self.assertNotIn("One-liner", result["todo_sections"])
            self.assertNotIn(setup_lib.OFFER_HEADING, result["todo_sections"])

        with temp_project() as project:
            result = setup_lib.run_setup(project, dict(MINIMAL_ANSWERS), REFERENCES_DIR)

            expected = [
                name
                for name in _template_headings()
                if name
                not in (
                    setup_lib.COMPETITORS_HEADING,
                    setup_lib.OFFER_HEADING,
                    setup_lib.FORMAT_ACCOUNTS_HEADING,
                )
            ]
            self.assertEqual(result["todo_sections"], expected)
            self.assertEqual(result["format_accounts"], [])

    def test_sample_answers_drive_a_full_mock_setup(self) -> None:
        self.assertTrue(SAMPLE_ANSWERS.exists(), f"{SAMPLE_ANSWERS} is missing")
        with temp_project() as project:
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(SAMPLE_ANSWERS)]
            )
            self.assertEqual(code, 0, err)

            config = store.load_config(project)
            self.assertEqual(
                config["competitors"], ["sproutapp", "habitlab", "ghostaccount"]
            )
            self.assertEqual(config["format_accounts"], ["dailywins"])

            code, out, err = _main(
                ["research", "--project", str(project), "--mock", "--yes"]
            )

            self.assertEqual(code, 0, err)
            self.assertIn("RESULT ", out)
            result = json.loads(out.split("RESULT ", 1)[1].splitlines()[0])
            self.assertEqual(result["mode"], "mock")
            self.assertGreater(result["selected"], 0)
            self.assertTrue((Path(result["run_dir"]) / "02-outliers.json").exists())


class AccountsCommandTests(NoNetworkTestCase):
    def _set_up(self, project: Path) -> None:
        code, _out, err = _main(
            ["setup", "--project", str(project), "--answers-file", str(SAMPLE_ANSWERS), "--mock"]
        )
        self.assertEqual(code, codes.EXIT_OK, err)

    def test_replaces_both_lists_and_keeps_everything_else(self) -> None:
        with temp_project() as project:
            self._set_up(project)
            config_dir = store.contentos_dir(project)
            config = store.read_json(config_dir / "config.json")
            config["briefs"] = 7
            store.write_json_atomic(config_dir / "config.json", config)
            before = (config_dir / "creator.md").read_text(encoding="utf-8")

            code, out, _err = _main(
                ["accounts", "--project", str(project),
                 "--competitors", "@FocusFern,https://www.instagram.com/planwithpia/,focusfern",
                 "--format-accounts", "webwillow,planwithpia"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(
                json.loads(out),
                {"competitors": ["focusfern", "planwithpia"], "format_accounts": ["webwillow"]},
            )
            config = store.load_config(project)
            self.assertEqual(config["competitors"], ["focusfern", "planwithpia"])
            self.assertEqual(config["format_accounts"], ["webwillow"])
            self.assertEqual(config["briefs"], 7)

            after = (config_dir / "creator.md").read_text(encoding="utf-8")
            self.assertEqual(_section(after, "Competitors"), "- focusfern\n- planwithpia")
            self.assertEqual(_section(after, "Format accounts"), "- webwillow")
            self.assertEqual(_headings(after), _headings(before))
            for heading in _headings(before):
                if heading not in ("Competitors", "Format accounts"):
                    self.assertEqual(_section(after, heading), _section(before, heading))

    def test_format_accounts_are_kept_when_the_flag_is_absent(self) -> None:
        with temp_project() as project:
            self._set_up(project)
            kept = store.load_config(project)["format_accounts"]
            before = (store.contentos_dir(project) / "creator.md").read_text(encoding="utf-8")

            code, _out, _err = _main(
                ["accounts", "--project", str(project), "--competitors", "focusfern"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(store.load_config(project)["format_accounts"], kept)
            after = (store.contentos_dir(project) / "creator.md").read_text(encoding="utf-8")
            self.assertEqual(_section(after, "Format accounts"), _section(before, "Format accounts"))

    def test_an_empty_format_list_clears_it(self) -> None:
        with temp_project() as project:
            self._set_up(project)
            _main(["accounts", "--project", str(project), "--competitors", "focusfern",
                   "--format-accounts", ""])
            self.assertEqual(store.load_config(project)["format_accounts"], [])
            after = (store.contentos_dir(project) / "creator.md").read_text(encoding="utf-8")
            self.assertEqual(_section(after, "Format accounts"), setup_lib.NO_FORMAT_ACCOUNTS_LINE)

    def test_refuses_and_changes_nothing(self) -> None:
        with temp_project() as project:
            self._set_up(project)
            config_path = store.contentos_dir(project) / "config.json"
            before = config_path.read_text(encoding="utf-8")
            for competitors in (" , ", "https://www.tiktok.com/@someone"):
                with self.subTest(competitors=competitors):
                    code, _out, err = _main(
                        ["accounts", "--project", str(project), "--competitors", competitors]
                    )
                    self.assertEqual(code, codes.EXIT_USAGE)
                    self.assertTrue(err.strip())
                    self.assertEqual(config_path.read_text(encoding="utf-8"), before)

    def test_refuses_before_setup(self) -> None:
        with temp_project() as project:
            code, _out, err = _main(
                ["accounts", "--project", str(project), "--competitors", "focusfern"]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertIn("setup", err)


if __name__ == "__main__":
    unittest.main()
