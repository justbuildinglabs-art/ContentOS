"""Tests for `skills/contentos/SKILL.md` and `README.md`.

SKILL.md is the orchestrator Claude follows when a founder types
`/contentos ...`. It is the one file in this plugin that has to describe
the CLI exactly as implemented: a command, a flag, or a file name that
drifts from `contentos.py` sends the founder (or Claude) at something
that is not there. So these tests check the frontmatter the runtime
reads, the parts of the body the design spec pins down (the root probe,
the foreground-Bash rule, the four dispatch loops), and every command
line in the file against the real argument parser.

README.md is the other founder-facing document, so the install lines,
the key locations, the ffmpeg note, the per-run cost, and the credits to
the three guides are checked here too.

Both files are parsed with a tiny local frontmatter reader rather than a
YAML library: this plugin is standard library only (design spec, "Global
Constraints").
"""
from __future__ import annotations

import re
import unittest
from typing import Dict, List, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT, SKILL_DIR

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import apify  # noqa: E402

SKILL_MD = SKILL_DIR / "SKILL.md"
README = REPO_ROOT / "README.md"

ARGUMENT_HINT = (
    "setup | run [--auto] [--yes] | research | direct | write [B01 B02] | "
    "qa [B01] | status | diagnose [--mock]"
)
ALLOWED_TOOLS = (
    "Bash, Read, Write, Glob, AskUserQuestion, "
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
# each named by the phrase both founder-facing documents use for it.
KEY_LOCATIONS = [
    "APIFY_API_TOKEN",
    "plugin setting",
    ".contentos/.env",
    "~/.config/contentos/.env",
]

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
            with self.subTest(subcommand=name):
                self.assertIn(name, mentioned, f"SKILL.md never runs {name}")

    def test_skill_has_no_em_dashes(self) -> None:
        # Founder-facing text: plain language, no em dashes (design spec,
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

        # The cost line quotes the number the estimator really produces
        # for ten accounts at the default 30 reels each.
        estimate = apify.estimate_cost(10, 30)
        self.assertIn("${0:.2f}".format(estimate.total_usd), text)

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

        # The commands table names every founder-facing command.
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

        # Founder-facing text: no em dashes.
        self.assertNotIn("—", text)


if __name__ == "__main__":
    unittest.main()
