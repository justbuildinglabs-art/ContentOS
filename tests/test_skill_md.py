"""Tests for `skills/contentos/SKILL.md` and `README.md`.

SKILL.md is the orchestrator Claude follows when a creator types
`/contentos ...`. It is the one file in this plugin that has to describe
the CLI exactly as implemented: a command, a flag, or a file name that
drifts from `contentos.py` sends the creator (or Claude) at something
that is not there. So these tests check the frontmatter the runtime
reads, the parts of the body the design spec pins down (the root probe,
the foreground-Bash rule, the four dispatch loops), and every command
line in the file against the real argument parser.

README.md is the other creator-facing document, so the install lines,
the key locations, the ffmpeg note, the per-run cost, and the credits to
the three guides are checked here too.

Both files were written for app founders in 0.1.x. 0.2.0 makes ContentOS
a tool for any creator, so these tests also hold the rename shut: no
`product.md`, no `founder`, and a README that positions the plugin for
creators and walks through one run.

Both files are parsed with a tiny local frontmatter reader rather than a
YAML library: this plugin is standard library only (design spec, "Global
Constraints").
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import unittest
from typing import Dict, List, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT, SKILL_DIR, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import apify, env, setup as setup_lib  # noqa: E402

SKILL_MD = SKILL_DIR / "SKILL.md"
README = REPO_ROOT / "README.md"

ARGUMENT_HINT = (
    "setup | discover | run [--auto] [--yes] | research | direct | write [B01 B02] | "
    "qa [B01] | status | diagnose [--mock]"
)
ALLOWED_TOOLS = (
    "Bash, Read, Write, Glob, AskUserQuestion, WebSearch, WebFetch, "
    "Agent(contentos:content-director, contentos:script-writer, contentos:qa-reviewer)"
)

# The probe chain Step 0 walks, in order (design spec, "Skill").
PROBE_LOCATIONS = [
    "${CLAUDE_SKILL_DIR}",
    "${CLAUDE_PLUGIN_ROOT}/skills/contentos",
    "$HOME/.claude/plugins/cache/*/contentos/*/skills/contentos",
    "$HOME/.claude/plugins/marketplaces/*/skills/contentos",
    "./skills/contentos",
]

# The four dispatch loops, named by a phrase each one owns.
DISPATCH_LOOPS = [
    "direct-prompt",
    "synth-prompt",
    "write-prompt",
    "qa-prompt",
]

# The four places the Apify key can live (design spec, "Key resolution"),
# each named by the phrase both creator-facing documents use for it.
KEY_LOCATIONS = [
    "APIFY_API_TOKEN",
    "plugin setting",
    ".contentos/.env",
    "~/.config/contentos/.env",
]

# The keys the skill writes into `.contentos/setup-answers.json`
# (design spec, "Skill"). `_answer_keys_setup_reads` checks this list
# against the keys `lib/setup.py` really looks at, so the interview and
# the command that consumes it can never drift apart.
SETUP_ANSWER_KEYS = [
    "creator_name",
    "one_liner",
    "pillars",
    "target_user",
    "frustration",
    "objection",
    "offer",
    "offer_objection",
    "payoff_moments",
    "allowed_claims",
    "forbidden_claims",
    "proof_assets",
    "voice_on",
    "voice_off",
    "off_limits_words",
    "cta",
    "hashtag_seeds",
    "competitors",
    "format_accounts",
    "inventory",
    "lead_magnet",
]

# Names from the 0.1.x product-and-founder vocabulary. After 0.2.0 none
# of them may appear in either creator-facing file (design spec, "Global
# Constraints": "The rename is complete").
PRODUCT_LEFTOVERS = [
    "product_fit",
    "demo_present",
    "consistent_with_product",
    "Demo moment",
    "product.md",
    "product-template",
    "adaptation_for_product",
    "product_or_topic_shown",
    "Founder rules",
]

# The invented creator the README's walkthrough follows. Invented on
# purpose: nothing in a creator-facing file claims anything about a real
# person or a real product.
WALKTHROUGH_CREATOR = "Mara"

GUIDE_TITLES = [
    "The 20-Agent Script System: How to Build an AI Writing Pipeline That Actually "
    "Produces Good Work",
    "How to Build a 5-Agent Content Pipeline That Writes, Edits, and Publishes for You",
    "How to Make Your Writing Not Sound Like AI",
]

# `python3 "$CONTENTOS_ROOT/scripts/contentos.py" <subcommand> ...` up to
# the end of the line or the next table cell: one match per documented
# command line. The subcommand group is lowercase letters and dashes, so
# the Step 0 probe's `[ -f "$dir/scripts/contentos.py" ]` and the
# template line's `<cmd>` placeholder simply do not match.
COMMAND_RE = re.compile(r"contentos\.py\"?\s+([a-z][a-z-]*)([^\n|]*)")
FLAG_RE = re.compile(r"(--[a-z][a-z-]*)")
# Run by hooks/hooks.json, never by the skill: from the Bash tool the
# plugin option is invisible, so running it there would be meaningless.
HOOK_ONLY_SUBCOMMANDS = ["sync-plugin-key"]


def _split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Split a markdown file into its `key: value` frontmatter and its body.

    Only the flat `key: value` lines SKILL.md uses are understood; a
    surrounding pair of matching quotes is stripped from the value.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("SKILL.md does not start with a --- frontmatter fence")

    fields: Dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return fields, "\n".join(lines[index + 1:])
        key, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fields[key.strip()] = value

    raise AssertionError("SKILL.md frontmatter is never closed with ---")


def _collapse(text: str) -> str:
    """Collapse every run of whitespace to one space (prose is hard-wrapped)."""
    return " ".join(text.split())


def _subparser_options() -> Dict[str, List[str]]:
    """Every subcommand's real long options, read off the real parser."""
    parser = contentos.build_parser()
    choices = None
    for action in parser._actions:  # noqa: SLF001 - argparse exposes no public API
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            choices = action.choices
            break
    if choices is None:
        raise AssertionError("could not find the subparsers action on the CLI parser")

    options: Dict[str, List[str]] = {}
    for name, subparser in choices.items():
        flags = []
        for action in subparser._actions:  # noqa: SLF001
            flags.extend(
                option for option in action.option_strings if option.startswith("--")
            )
        options[name] = flags
    return options


def _answer_keys_setup_reads() -> set:
    """Every answer key `lib/setup.py` reads, from the module itself."""
    keys = set(setup_lib.TEXT_SECTIONS.values())
    keys.update(setup_lib.LIST_SECTIONS.values())
    for mapping in setup_lib.BULLET_SECTIONS.values():
        keys.update(mapping.values())
    # The offer block and the lead magnet line are rendered by hand, not
    # through a section map.
    keys.update({"offer", "offer_objection", "lead_magnet"})
    return keys


class SkillFrontmatterTests(NoNetworkTestCase):
    def test_skill_frontmatter_fields(self) -> None:
        fields, body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))

        self.assertEqual(fields["name"], "contentos")
        self.assertEqual(fields["argument-hint"], ARGUMENT_HINT)
        self.assertEqual(fields["allowed-tools"], ALLOWED_TOOLS)
        self.assertEqual(fields["disable-model-invocation"], "true")

        # The description says what it does and when it runs.
        description = fields["description"]
        self.assertTrue(description.strip())
        self.assertIn("/contentos", description)

        self.assertTrue(body.strip(), "SKILL.md has no body")


class SkillBodyTests(NoNetworkTestCase):
    def test_skill_contains_root_probe_timeout_rule_and_four_loops(self) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        prose = _collapse(body)

        # Step 0: the probe chain, in order, ending in a clear stop.
        self.assertIn("CONTENTOS_ROOT", body)
        position = -1
        for location in PROBE_LOCATIONS:
            with self.subTest(probe=location):
                found = body.find(location, position + 1)
                self.assertNotEqual(found, -1, f"{location} is not in the probe chain")
                self.assertGreater(found, position, f"{location} is out of order")
                position = found
        self.assertIn("scripts/contentos.py", body)
        self.assertIn("ERROR", body)

        # Foreground Bash, with the 10-minute timeout, never backgrounded.
        self.assertIn("timeout: 600000", body)
        self.assertIn("Never use `run_in_background`", prose)

        # The four dispatch loops, each with its command and its verify.
        for command in DISPATCH_LOOPS:
            with self.subTest(loop=command):
                self.assertIn(command, body)
        for stage in ("--stage direct", "--stage synth", "--stage write", "--stage qa"):
            with self.subTest(verify=stage):
                self.assertIn(stage, body)

        # Each generated prompt goes to a file; the subagent reads the file.
        self.assertIn("prompts/", body)
        self.assertIn(
            "Your dispatch prompt is in the file", body
        )
        for agent in (
            "contentos:content-director",
            "contentos:script-writer",
            "contentos:qa-reviewer",
        ):
            with self.subTest(agent=agent):
                self.assertIn(agent, body)

        # Batching, one revision, and the exit-7 re-dispatch.
        self.assertIn("parallel_agents", body)
        self.assertIn("--revision 1", body)
        self.assertIn("exit 7", prose)

        # The re-dispatch appends the verify errors to the prompt file
        # through the shell, so the prompt never enters this context.
        self.assertIn("## Fix these problems", body)
        self.assertIn('2> "$RUN_DIR/prompts/verify-', body)
        self.assertIn("printf '\\n## Fix these problems\\n'", body)

        # The RESULT line carries run_id and run_dir, and `<run_dir>`
        # everywhere below means that absolute path.
        self.assertIn("carries both `run_id` and `run_dir`", prose)
        self.assertIn("`<run_dir>` below is that absolute", prose)

        # `--run` takes `latest`, and a standalone stage never re-runs research.
        self.assertIn("`--run` accepts a run id or the word `latest`", prose)
        self.assertIn("Never start a new research run", prose)

        # The mock shortcut is stated where the loops start, not after them.
        mock_note = body.index("--run <run_id> --mock")
        director_step = body.index("4. **Director loop")
        self.assertLess(mock_note, director_step)

        # The failure table covers every non-zero exit code the CLI returns.
        for code in ("| 2 |", "| 3 |", "| 4 |", "| 5 |", "| 6 |", "| 7 |"):
            with self.subTest(exit_code=code):
                self.assertIn(code, body)

        # The rules.md offer and the final message.
        self.assertIn("rules.md", body)
        self.assertIn("report.md", body)

    def test_skill_commands_exist_in_cli(self) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        options = _subparser_options()

        mentioned = set()
        for match in COMMAND_RE.finditer(body):
            name, rest = match.group(1), match.group(2)
            with self.subTest(command=name):
                self.assertIn(name, contentos.SUBCOMMANDS, f"no such subcommand: {name}")
            mentioned.add(name)
            for flag in FLAG_RE.findall(rest):
                with self.subTest(command=name, flag=flag):
                    self.assertIn(flag, options[name], f"{name} has no {flag}")

        for name in contentos.SUBCOMMANDS:
            if name in HOOK_ONLY_SUBCOMMANDS:
                continue
            with self.subTest(subcommand=name):
                self.assertIn(name, mentioned, f"SKILL.md never runs {name}")

    def test_skill_never_runs_hook_only_subcommands(self) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        mentioned = {match.group(1) for match in COMMAND_RE.finditer(body)}

        for name in HOOK_ONLY_SUBCOMMANDS:
            with self.subTest(subcommand=name):
                self.assertIn(name, contentos.SUBCOMMANDS)
                self.assertNotIn(name, mentioned)

    def test_skill_setup_flow_asks_about_offer_and_format_accounts_and_writes_new_keys(
        self,
    ) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        # The body is hard-wrapped, so phrases are matched against the
        # collapsed prose rather than against the raw lines.
        collapsed = _collapse(body)
        prose = collapsed.lower()

        # Four rounds, in order, and still plain chat questions.
        position = -1
        for round_number in (1, 2, 3, 4):
            marker = "**Round {0},".format(round_number)
            with self.subTest(round=round_number):
                found = body.find(marker, position + 1)
                self.assertNotEqual(found, -1, f"{marker} is missing")
                self.assertGreater(found, position, f"{marker} is out of order")
                position = found
        self.assertIn("not AskUserQuestion", collapsed)

        # Round 1 is the creator, round 2 the viewer, round 3 the offer,
        # round 4 the guardrails and the accounts.
        for phrase in (
            "pillars",
            "payoff",
            "what you promote",
            "is a fine answer",
            "format accounts",
            "formats travel",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, prose)

        # Round 4 also asks for hashtag seeds: optional, and the writer
        # falls back to the niche when the answer is blank.
        round4 = collapsed.split("**Round 4,", 1)[1]
        round4_prose = round4.lower()
        self.assertIn("hashtag seeds", round4_prose)
        self.assertIn("optional", round4_prose)

        # Every answer key the skill writes, and no key setup cannot read.
        self.assertEqual(set(SETUP_ANSWER_KEYS), _answer_keys_setup_reads())
        for key in SETUP_ANSWER_KEYS:
            with self.subTest(answer_key=key):
                self.assertIn("`" + key + "`", body)

        # Setup writes creator.md, and --force rewrites that file.
        self.assertIn("setup --answers-file", body)
        self.assertIn("`creator.md`", body)
        self.assertIn("--force", body)

    def test_skill_brief_choice_matches_what_rank_really_does(self) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        section = _collapse(body.split("## Choosing briefs", 1)[1])

        # Both source kinds, and the cap by its config key.
        for phrase in ("niche", "format", "`max_format_briefs`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

        # `rank_briefs` fills the remaining slots from the format briefs
        # it set aside whenever the niche briefs ran short, so the cap is
        # not a promise about the finished list. The file has to say so:
        # everything Claude tells the creator comes from the run dir.
        self.assertIn("unless too few niche reels survived analysis", section)
        self.assertIn("fills the remaining slots from the format briefs", section)

    def test_weekly_picking_and_auto_scripts(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        for needle in (
            "`Top 3`",
            "`Top 5`",
            "`All <n>`",
            "auto_scripts",
            "03-fill.json",
            "no new outliers",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertNotIn("take the top `briefs`", text)

        # Final review M6: offer each top-N only when it cuts the list,
        # name the way to stop an idea carrying over, and drop the stale
        # promise that every brief is new.
        prose = _collapse(text)
        self.assertIn("`Top 5` only when the list has more than 5 ideas", prose)
        self.assertIn("`Top 3` only when it has more than 3", prose)
        self.assertIn("`mark --state skipped`", prose)
        self.assertIn("stops that idea from carrying over", prose)
        self.assertNotIn("so the briefs are new", prose)

    def test_a_fill_brief_gets_facts_about_its_own_topic(self) -> None:
        # Final review I3: a fill brief's topic is its own idea, not the
        # proof reel's, so the fact sheet must be researched from it.
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        section = _collapse(body.split("## Intake and the fact sheet", 1)[1].split("\n## ", 1)[0])
        for phrase in ("`kind` is `fill`", "`idea_title`", "`adaptation`", "not from the proof reel"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_skill_preflight_checks_creator_md(self) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]

        # The pre-flight step reads the diagnose keys by their real names.
        self.assertIn("`creator_md`", body)
        self.assertIn("`config_json`", body)

        with temp_project() as project:
            report = env.diagnose(project, environ={"CONTENTOS_CONFIG_DIR": ""})
        for key in ("creator_md", "config_json", "apify", "ffmpeg"):
            with self.subTest(diagnose_key=key):
                self.assertIn(key, report)

    def test_skill_has_no_em_dashes(self) -> None:
        # Creator-facing text: plain language, no em dashes (design spec,
        # "Global Constraints").
        self.assertNotIn("—", SKILL_MD.read_text(encoding="utf-8"))


class ReadmeTests(NoNetworkTestCase):
    def test_readme_mentions_install_key_ffmpeg_and_credits(self) -> None:
        text = README.read_text(encoding="utf-8")
        prose = _collapse(text)

        # Install.
        self.assertIn("/plugin marketplace add <owner>/ContentOS", text)
        self.assertIn("/plugin install contentos@contentos", text)
        self.assertIn("<owner>", text)

        # Requirements.
        self.assertIn("Python 3.9", text)
        self.assertIn("ffmpeg", text)

        # The key and the four places it can live.
        for location in KEY_LOCATIONS:
            with self.subTest(key_location=location):
                self.assertIn(location, text)

        # First run.
        self.assertIn("/contentos setup", text)
        self.assertIn("/contentos run", text)
        self.assertIn("/contentos run --mock --auto", text)

        # The cost line quotes the numbers the estimator really produces
        # for eight and for ten accounts at the default 30 reels each.
        for accounts in (8, 10):
            estimate = apify.estimate_cost(accounts, 30)
            with self.subTest(accounts=accounts):
                self.assertIn("${0:.2f}".format(estimate.total_usd), text)

        # The commands table's setup row names the three files setup writes.
        self.assertIn(
            "Interview, then write `creator.md`, `config.json`, and `rules.md`", text
        )

        # Where files go, and how output improves over time.
        self.assertIn(".contentos/", text)
        self.assertIn("rules.md", text)
        self.assertIn("references/examples/", text)
        # The dispatch prompts land in the run directory and are gitignored.
        self.assertIn("prompts/", text)

        # Privacy and credits.
        self.assertIn("stay on your machine", prose)
        self.assertIn("Apify sees", prose)
        for title in GUIDE_TITLES:
            with self.subTest(guide=title):
                self.assertIn(title, prose)
        self.assertIn("Ray Cfu", text)

        # The commands table names every creator-facing command.
        for command in (
            "setup",
            "run",
            "research",
            "direct",
            "write",
            "qa",
            "status",
            "diagnose",
        ):
            with self.subTest(command=command):
                self.assertIn("/contentos " + command, text)

        # Development and license.
        self.assertIn("python3 -m unittest discover -s tests", text)
        self.assertIn("License", text)

        # Privacy: what Claude reads is the creator's own profile.
        self.assertIn("reads your creator profile and your run files", prose)

        # Creator-facing text: no em dashes.
        self.assertNotIn("—", text)

    def test_readme_has_walkthrough_and_creator_positioning(self) -> None:
        text = README.read_text(encoding="utf-8")
        prose = _collapse(text)

        # The headline is for creators, not for app founders.
        headline = _collapse(text.split("## ", 1)[0])
        self.assertIn("Claude Code plugin for creators", headline)
        self.assertNotIn("app founders", headline)

        # A worked run, following one invented creator end to end.
        self.assertIn("## How a run looks", text)
        walkthrough = text.split("## How a run looks", 1)[1]
        self.assertIn(WALKTHROUGH_CREATOR, walkthrough)
        for phrase in (
            "/contentos setup",
            "/contentos run",
            ".contentos/creator.md",
            "briefs.md",
            "report.md",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, walkthrough)

        # The new vocabulary, in the stage summary at the top.
        for phrase in ("payoff", "creator profile"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, prose.lower())


class WeeklyReleaseNoteTests(NoNetworkTestCase):
    """Final review D1, D2, M7: the 0.4.0 settings and files, stated plainly."""

    def _changelog_040(self) -> str:
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        return _collapse(text.split("## [0.4.0]", 1)[1].split("\n## [", 1)[0])

    def test_readme_and_changelog_state_the_weekly_settings_and_files(self) -> None:
        readme = _collapse(README.read_text(encoding="utf-8"))
        for name, prose in (("README", readme), ("CHANGELOG", self._changelog_040())):
            for phrase in (
                "`fill_ideas`", "default 8", "`fill_ideas` 0 turns format fill off",
                "after every real idea",
                "`carry_weeks`", "default 2", "`carry_weeks` 0 turns carry-over off",
                "03-fill.json", ".contentos/ideas.json",
                "start with an empty ideas ledger",
            ):
                with self.subTest(doc=name, phrase=phrase):
                    self.assertIn(phrase, prose)
            with self.subTest(doc=name):
                self.assertNotIn("—", prose)

    def test_readme_and_changelog_cover_discovery_and_paid_partnerships(self) -> None:
        readme = README.read_text(encoding="utf-8")
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for text, name in ((readme, "README.md"), (changelog, "CHANGELOG.md")):
            for phrase in ("/contentos discover", "exclude_paid_partnerships", "paid partnership"):
                with self.subTest(file=name, phrase=phrase):
                    self.assertIn(phrase, text)
            self.assertNotIn("—", text)
        self.assertIn("find them for me", readme)

    def test_readme_auto_row_names_auto_scripts(self) -> None:
        text = README.read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| `/contentos run` |"))
        self.assertIn("`auto_scripts`", row)
        self.assertIn("default 3", row)

    def test_readme_walkthrough_matches_the_rank_rule(self) -> None:
        text = README.read_text(encoding="utf-8")
        walkthrough = _collapse(text.split("## How a run looks", 1)[1].split("\n## ", 1)[0])
        self.assertNotIn("At most two of the twenty ideas come from format accounts", walkthrough)
        self.assertNotIn("one command replaced my whole morning routine", walkthrough)
        self.assertIn("`max_format_briefs`", walkthrough)
        self.assertIn("when niche ideas run short", walkthrough)


def _key_command(text: str) -> List[str]:
    """Every fenced bash block that saves the Apify token, stripped."""
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)
    return [block.strip() for block in blocks if "APIFY_API_TOKEN=%s" in block]


def _run_key_command(command: str, stdin: str) -> Tuple["subprocess.CompletedProcess", "object"]:
    """Run the terminal command against a throwaway HOME with `stdin` as the paste."""
    with temp_project() as home:
        env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        done = subprocess.run(
            ["bash", "-c", command],
            input=stdin,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        saved = home / ".config" / "contentos" / ".env"
        result = {
            "exists": saved.exists(),
            "text": saved.read_text(encoding="utf-8") if saved.exists() else None,
            "mode": (saved.stat().st_mode & 0o777) if saved.exists() else None,
        }
        return done, result


class ApifyKeyStepsTests(NoNetworkTestCase):
    """Adding the Apify key is a guided, step-by-step task the creator does.

    The token must never pass through a chat, so the README and the skill
    both hand the creator one terminal command that asks for it with the
    input hidden. These tests pin that the two documents carry the same
    command, that the command really does what the docs say, and that the
    skill tells Claude to hand over the steps and never touch the token.
    """

    def test_readme_and_skill_share_one_terminal_command(self) -> None:
        readme = _key_command(README.read_text(encoding="utf-8"))
        skill = _key_command(SKILL_MD.read_text(encoding="utf-8"))

        self.assertEqual(len(readme), 1, "README needs exactly one key command")
        self.assertEqual(len(skill), 1, "SKILL.md needs exactly one key command")
        self.assertEqual(readme, skill)

    def test_terminal_command_saves_the_token_with_owner_only_permissions(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("bash is not installed")
        command = _key_command(README.read_text(encoding="utf-8"))[0]

        done, saved = _run_key_command(command, "fake_test_token_123\n")

        self.assertTrue(saved["exists"])
        self.assertEqual(saved["text"], "APIFY_API_TOKEN=fake_test_token_123\n")
        self.assertEqual(saved["mode"], 0o600)
        self.assertIn("Saved.", done.stdout)
        # The paste is hidden: the token never shows in the output.
        self.assertNotIn("fake_test_token_123", done.stdout + done.stderr)

    def test_terminal_command_writes_nothing_on_empty_input(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("bash is not installed")
        command = _key_command(README.read_text(encoding="utf-8"))[0]

        done, saved = _run_key_command(command, "\n")

        self.assertFalse(saved["exists"])
        self.assertNotIn("Saved.", done.stdout)

    def test_readme_walks_through_the_key_steps(self) -> None:
        prose = _collapse(README.read_text(encoding="utf-8"))

        # Where to get the token, where to run the command, what success
        # looks like, and how to confirm it.
        self.assertIn("API & Integrations", prose)
        self.assertIn("real Terminal", prose)
        self.assertIn("cannot take typed input", prose)
        self.assertIn("Saved.", prose)
        self.assertIn("owner-only permissions", prose)
        self.assertIn("/contentos diagnose", prose)
        # The token never goes through a chat.
        self.assertIn("never paste the token into a chat", prose.lower())

    def test_skill_hands_over_the_steps_and_never_touches_the_token(self) -> None:
        prose = _collapse(SKILL_MD.read_text(encoding="utf-8"))

        self.assertIn("Never ask the creator to paste the token into the chat", prose)
        self.assertIn("never write it to a file for them", prose)
        self.assertIn("API & Integrations", prose)
        self.assertIn("real Terminal window", prose)
        self.assertIn("cannot take typed input", prose)
        self.assertIn("Saved.", prose)
        # After the creator confirms, Claude checks it live.
        self.assertIn("diagnose --live", prose)
        self.assertIn("apify_live", prose)
        # Exit 4 sends the creator to the same steps.
        self.assertIn("the key steps from Step 1", prose)


class DiscoveryFlowTests(NoNetworkTestCase):
    @staticmethod
    def _flow() -> str:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        return body.split("## The discovery flow", 1)[1].split("\n## ", 1)[0]

    def test_names_the_bar_the_sources_and_the_tiers(self) -> None:
        flow = _collapse(self._flow())
        for phrase in (
            "10,000 or more followers", "every 2 weeks", "1 in 4", "Established", "Rising",
            "site:instagram.com", "you must use it", "Never add a handle from memory",
            "--seeds", "--keywords",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, flow)
        self.assertNotIn("small_account", flow)

    def test_every_discover_command_parses(self) -> None:
        blocks = re.findall(r"```bash\n(.*?)```", self._flow(), flags=re.DOTALL)
        commands = [block for block in blocks if 'contentos.py" discover' in block]
        self.assertTrue(commands)
        parser = contentos.build_parser()
        for block in commands:
            argv = shlex.split(block.replace("\\\n", " "))
            with self.subTest(block=block):
                parser.parse_args(argv[argv.index("discover"):])

    def test_the_save_step_keeps_the_current_watch_list(self) -> None:
        flow = _collapse(self._flow())
        self.assertIn(
            "pass the current `competitors` first, then the picks", flow
        )
        self.assertIn(
            "in the answers file, after the handles the creator typed", flow
        )

    def test_a_partial_run_is_explained_and_run_again(self) -> None:
        flow = _collapse(self._flow())
        for phrase in ('"not checked in time"', '"not measured in time"', "run it again",
                       "never as not found"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, flow)

    def test_the_claude_search_is_deeper_and_free(self) -> None:
        flow = _collapse(self._flow())
        for phrase in (
            "`reason`", "`source_title`", "`followers_seen`", "creators like @", "open the 2 or 3",
            "costs no Apify credit", "no Apify key", "Never add a handle from memory",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, flow)

    def test_the_chat_can_stop_after_the_claude_search(self) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        chat = _collapse(body.split("### Discovery in the chat", 1)[1].split("\n## ", 1)[0])
        for phrase in ("with no Apify", "--check-only", "not checked"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, chat)


class CreatorRenameTests(NoNetworkTestCase):
    def test_skill_and_readme_have_no_product_leftovers(self) -> None:
        for path in (SKILL_MD, README):
            text = path.read_text(encoding="utf-8")
            for leftover in PRODUCT_LEFTOVERS:
                with self.subTest(file=path.name, leftover=leftover):
                    self.assertNotIn(leftover, text)
            # "Creator" is gone in every case: the README's credits line
            # does not contain it either, so nothing here is exempt.
            with self.subTest(file=path.name, leftover="founder"):
                self.assertNotIn("founder", text.lower())


class ControlPanelSkillTests(NoNetworkTestCase):
    def test_the_panel_is_the_default_and_the_only_background_command(self) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        self.assertIn("Never use `run_in_background`", _collapse(body))
        self.assertEqual(body.count("run_in_background: true"), 1)
        self.assertLess(body.index("### The control panel"), body.index("### Discovery in the chat"))
        for phrase in ("ui-session.json", "discovery-picks.json", '"saved": false', "--open"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body)

    @staticmethod
    def _panel() -> str:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        return _collapse(body.split("### The control panel", 1)[1].split("### Discovery in the chat", 1)[0])

    def test_the_link_comes_from_the_ui_line_first(self) -> None:
        panel = self._panel()
        self.assertIn("`UI <url>`", panel)
        self.assertIn("as a fallback", panel)
        self.assertLess(panel.index("`UI <url>`"), panel.index("ui-session.json"))

    def test_an_exit_with_no_result_line_is_explained(self) -> None:
        panel = self._panel()
        for phrase in ("no `RESULT` line", "stopped before", "open it again", "chat steps"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, panel)

    def test_search_again_is_a_loop_back_to_the_panel(self) -> None:
        panel = self._panel()
        for phrase in ('"next": "claude_search"', "`known`", "--resume", "the same way, in the background",
                       "new link", "no web search tool"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, panel)
        self.assertIn("give the creator the new link and open it", panel)

    def test_an_idle_panel_that_could_not_be_kept_starts_fresh(self) -> None:
        panel = self._panel()
        idle = panel.split('`"reason": "idle"`', 1)[1]
        for phrase in ("`warning`", "no `handoff_path`", "start a new panel"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, idle)

    def test_every_ui_command_parses(self) -> None:
        body = _split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1]
        blocks = [block for block in re.findall(r"```bash\n(.*?)```", body, flags=re.DOTALL)
                  if 'contentos.py" ui' in block]
        self.assertGreaterEqual(len(blocks), 2)
        parser = contentos.build_parser()
        for block in blocks:
            argv = shlex.split(block.replace("\\\n", " "))
            with self.subTest(block=block):
                parser.parse_args(argv[argv.index("ui"):])


class DiscoveryReleaseNoteTests(NoNetworkTestCase):
    @staticmethod
    def _changelog_060() -> str:
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        return _collapse(text.split("## [0.6.0]", 1)[1].split("\n## [", 1)[0])

    def test_readme_and_changelog_state_the_bar_the_settings_and_the_panel(self) -> None:
        readme = _collapse(README.read_text(encoding="utf-8"))
        for name, prose in (("README", readme), ("CHANGELOG", self._changelog_060())):
            for phrase in (
                "10,000", "Established", "Rising", "1 in 4", "control panel",
                "`discover_min_followers`", "`discover_min_views`",
                "`discover_post_every_days`", "`discover_shortlist`",
            ):
                with self.subTest(doc=name, phrase=phrase):
                    self.assertIn(phrase, prose)
            with self.subTest(doc=name):
                self.assertNotIn("—", prose)
        changelog = self._changelog_060()
        for phrase in ("#higgsfieldpartner", "keyword search", "discover_min_followers: 1000"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, changelog)
        self.assertIn("discover_min_followers: 1000", readme)

    def test_changelog_states_what_the_fix_wave_changed_for_creators(self) -> None:
        changelog = self._changelog_060()
        for phrase in (
            "7 to 90 days", "#twitchpartner", '"not checked in time"', '"not measured in time"',
            "instead of \"not found\"", "Reloading the control panel keeps your search",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, changelog)


class ClaudeSearchReleaseNoteTests(NoNetworkTestCase):
    @staticmethod
    def _changelog_061() -> str:
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        return _collapse(text.split("## [0.6.1]", 1)[1].split("\n## [", 1)[0])

    def test_readme_and_changelog_explain_the_two_searches(self) -> None:
        readme = _collapse(README.read_text(encoding="utf-8"))
        for name, prose in (("README", readme), ("CHANGELOG", self._changelog_061())):
            for phrase in ("Search again with Claude", "optional", "Apify scan", "free"):
                with self.subTest(file=name, phrase=phrase):
                    self.assertIn(phrase, prose)
        self.assertIn("--check-only", self._changelog_061())
        self.assertIn("--resume", self._changelog_061())


if __name__ == "__main__":
    unittest.main()
