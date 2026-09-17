"""Tests for the `setup` subcommand and `lib/setup.py`.

`setup` is the one command a founder runs before anything else. It turns
the short answers the skill collected into the three files every later
stage reads: `.contentos/product.md` (built from
`references/product-template.md`), `.contentos/config.json`
(`store.DEFAULT_CONFIG` plus the normalized competitor handles), and
`.contentos/rules.md`. See the design spec's "Reference files" row for
`product-template.md` and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-18-brief.md`
for the interface.

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
from lib import store  # noqa: E402

REFERENCES_DIR = SKILL_DIR / "references"
PRODUCT_TEMPLATE = REFERENCES_DIR / "product-template.md"
SAMPLE_ANSWERS = REPO_ROOT / "fixtures" / "setup-answers.sample.json"

RULES_COMMENT = (
    "# One correction per line. ContentOS appends these to the writer and QA prompts."
)

# A complete answers file, one value per key so a test can tell which
# answer landed in which section.
FULL_ANSWERS = {
    "product_name": "Sprout, a habit tracker that holds one habit at a time.",
    "one_liner": "Sprout is the habit app that only lets you track one habit.",
    "target_user": "people who have deleted four habit apps in the last year",
    "frustration": "I always quit on day four and the app just turns red at me",
    "objection": "another habit app I will abandon in a week",
    "core_features": ["One habit at a time", "A streak that never resets to zero"],
    "demo_moment": "The Add button greys out until day 14",
    "allowed_claims": ["Logging one habit takes under five seconds"],
    "forbidden_claims": ["No health outcomes", "No income promises"],
    "proof_assets": ["The App Store rating screenshot"],
    "voice_on": ["dry", "direct", "warm"],
    "voice_off": ["peppy", "clinical", "salesy"],
    "off_limits_words": ["hustle", "grind", "unlock"],
    "cta": "Download Sprout and set up one habit tonight",
    "hashtag_seeds": ["#habittracker", "#sprout"],
    "competitors": ["sproutapp", "habitlab"],
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
        for line in PRODUCT_TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    return [heading for heading in headings if heading != "Sources"]


def _headings(text: str) -> list:
    """The `## ` headings of a rendered product.md, in order."""
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def _section(text: str, heading: str) -> str:
    """The body of one `## <heading>` section of a rendered product.md."""
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
    def test_setup_writes_product_config_rules_gitignore(self) -> None:
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
            self.assertTrue(report["product_md"])
            self.assertTrue(report["config_json"])
            self.assertTrue(report["rules_md"])

            contentos_dir = store.contentos_dir(project)
            product = (contentos_dir / "product.md").read_text(encoding="utf-8")
            config = json.loads((contentos_dir / "config.json").read_text(encoding="utf-8"))
            rules = (contentos_dir / "rules.md").read_text(encoding="utf-8")
            gitignore = (contentos_dir / ".gitignore").read_text(encoding="utf-8")

            # Same headings as the template, same order, minus Sources.
            self.assertEqual(_headings(product), _template_headings())
            self.assertNotIn("## Sources", product)

            # Answered sections carry the founder's words.
            self.assertEqual(_section(product, "One-liner"), FULL_ANSWERS["one_liner"])
            self.assertEqual(
                _section(product, "Core features"),
                "- One habit at a time\n- A streak that never resets to zero",
            )
            self.assertEqual(
                _section(product, "Competitors"), "- sproutapp\n- habitlab"
            )
            self.assertEqual(_section(product, "CTA"), FULL_ANSWERS["cta"])
            self.assertIn("- The App Store rating screenshot", _section(product, "Proof assets"))
            self.assertIn("#habittracker", _section(product, "Hashtag seeds"))

            # Audience profile and Brand voice keep the template's
            # sub-bullet labels; answered ones carry the answer, the rest
            # read TODO.
            audience = _section(product, "Audience profile")
            self.assertIn(
                "- Who specifically: people who have deleted four habit apps in the last year",
                audience,
            )
            self.assertIn(
                "- Their number one frustration, in their own words: "
                "I always quit on day four and the app just turns red at me",
                audience,
            )
            self.assertIn(
                "- Top 3 objections: another habit app I will abandon in a week", audience
            )
            self.assertIn("- What they already tried: TODO", audience)
            self.assertIn("- The fear behind it: TODO", audience)

            voice = _section(product, "Brand voice")
            self.assertIn("- 3 adjectives it is: dry, direct, warm", voice)
            self.assertIn("- 3 adjectives it is not: peppy, clinical, salesy", voice)
            self.assertIn("- 10 off-limits words: hustle, grind, unlock", voice)
            self.assertIn("- Sentence rules: TODO", voice)
            self.assertIn("- 3 sample sentences: TODO", voice)

            # config.json is DEFAULT_CONFIG plus the handles.
            expected = dict(store.DEFAULT_CONFIG, competitors=["sproutapp", "habitlab"])
            self.assertEqual(config, expected)
            # It must load back through the real validator.
            self.assertEqual(store.load_config(project)["competitors"], ["sproutapp", "habitlab"])

            self.assertEqual(rules.strip(), RULES_COMMENT)
            for line in (".env", "runs/*/videos/", "runs/*/frames/", "setup-answers.json"):
                with self.subTest(gitignore_line=line):
                    self.assertIn(line, gitignore)

    def test_setup_refuses_overwrite_without_force(self) -> None:
        with temp_project() as project:
            answers_file = _write_answers(project, FULL_ANSWERS)
            code, _out, _err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )
            self.assertEqual(code, 0)

            contentos_dir = store.contentos_dir(project)
            product_path = contentos_dir / "product.md"
            rules_path = contentos_dir / "rules.md"
            product_path.write_text("# hand edited\n", encoding="utf-8")
            rules_path.write_text(RULES_COMMENT + "\nnever say hustle\n", encoding="utf-8")

            code, out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("product.md", err)
            self.assertIn("--force", err)
            # Nothing was touched.
            self.assertEqual(product_path.read_text(encoding="utf-8"), "# hand edited\n")

            # --force rewrites product.md but never rules.md.
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
            self.assertIn("## One-liner", product_path.read_text(encoding="utf-8"))
            self.assertIn("never say hustle", rules_path.read_text(encoding="utf-8"))

    def test_setup_force_preserves_tuned_config_and_replaces_competitors(self) -> None:
        # A founder who tuned their thresholds must not lose them because
        # they re-ran setup to change the competitor list. `--force`
        # rewrites product.md, keeps every existing config key, and
        # replaces only `competitors`.
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

            new_answers = dict(FULL_ANSWERS, competitors=["@DailyWins", "ghostaccount"])
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
            self.assertEqual(after["apify_max_charge_usd"], 12.5)
            self.assertEqual(after["briefs"], 2)
            self.assertEqual(after["qa_pass_threshold"], 10)
            self.assertEqual(after["reels_per_account"], 45)
            self.assertEqual(after["my_own_note"], "keep me")

            # product.md is rewritten with the new handles.
            product = (store.contentos_dir(project) / "product.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                _section(product, "Competitors"), "- dailywins\n- ghostaccount"
            )

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
            expected = dict(store.DEFAULT_CONFIG, competitors=["sproutapp", "habitlab"])
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8")), expected
            )

    def test_setup_rejects_non_instagram_urls_and_handles_with_spaces(self) -> None:
        # Anything that is not an instagram.com URL or a bare handle is a
        # typo, not a competitor. Refuse it by name and write nothing,
        # rather than scraping a handle the founder never meant.
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
                        (store.contentos_dir(project) / "product.md").exists()
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
            product = (store.contentos_dir(project) / "product.md").read_text(encoding="utf-8")
            self.assertEqual(
                _section(product, "Competitors"),
                "- sproutapp\n- habitlab\n- dailywins\n- ghostaccount",
            )

    def test_setup_keeps_unanswered_sections_as_headings(self) -> None:
        with temp_project() as project:
            answers_file = _write_answers(project, MINIMAL_ANSWERS)

            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(answers_file)]
            )

            self.assertEqual(code, 0, err)
            product = (store.contentos_dir(project) / "product.md").read_text(encoding="utf-8")

            self.assertEqual(_headings(product), _template_headings())
            # Every section but Competitors is unanswered, and each one
            # keeps the template's guidance followed by the to-do line.
            for heading in _template_headings():
                if heading == "Competitors":
                    continue
                with self.subTest(heading=heading):
                    section = _section(product, heading)
                    self.assertIn("TODO", section)
            self.assertIn(
                "One sentence that explains the product to someone who has never heard of it.",
                _section(product, "One-liner"),
            )
            self.assertTrue(_section(product, "One-liner").endswith("TODO: fill this in."))

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

            empty = _write_answers(project, {"competitors": ["  ", "@"]})
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(empty)]
            )
            self.assertEqual(code, 2)
            self.assertIn("competitor", err)

            self.assertFalse((store.contentos_dir(project) / "product.md").exists())

    def test_run_setup_reports_what_it_wrote(self) -> None:
        # The skill reads `todo_sections` to tell the founder which
        # headings are still theirs to fill in.
        with temp_project() as project:
            result = setup_lib.run_setup(project, dict(FULL_ANSWERS), REFERENCES_DIR)

            self.assertEqual(result["competitors"], ["sproutapp", "habitlab"])
            self.assertEqual(
                result["product_md"], str(store.contentos_dir(project) / "product.md")
            )
            self.assertNotIn("Competitors", result["todo_sections"])
            self.assertNotIn("One-liner", result["todo_sections"])

        with temp_project() as project:
            result = setup_lib.run_setup(project, dict(MINIMAL_ANSWERS), REFERENCES_DIR)

            expected = [name for name in _template_headings() if name != "Competitors"]
            self.assertEqual(result["todo_sections"], expected)

    def test_sample_answers_drive_a_full_mock_setup(self) -> None:
        self.assertTrue(SAMPLE_ANSWERS.exists(), f"{SAMPLE_ANSWERS} is missing")
        with temp_project() as project:
            code, _out, err = _main(
                ["setup", "--project", str(project), "--answers-file", str(SAMPLE_ANSWERS)]
            )
            self.assertEqual(code, 0, err)

            config = store.load_config(project)
            self.assertEqual(
                config["competitors"],
                ["sproutapp", "habitlab", "dailywins", "ghostaccount"],
            )

            code, out, err = _main(
                ["research", "--project", str(project), "--mock", "--yes"]
            )

            self.assertEqual(code, 0, err)
            self.assertIn("RESULT ", out)
            result = json.loads(out.split("RESULT ", 1)[1].splitlines()[0])
            self.assertEqual(result["mode"], "mock")
            self.assertGreater(result["selected"], 0)
            self.assertTrue((Path(result["run_dir"]) / "02-outliers.json").exists())


if __name__ == "__main__":
    unittest.main()
