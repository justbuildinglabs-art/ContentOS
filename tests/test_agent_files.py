"""Tests for the subagent definition files under `agents/`.

Task 14 shipped the first one, `agents/content-director.md`; task 17
adds `script-writer.md` and `qa-reviewer.md`, so this module now has one
class per agent plus one class for the rules all three share. The
frontmatter conventions (a comma-separated `tools` string, `maxTurns`,
an omitted `model` so the subagent inherits the parent's) come from the
design spec's "Architecture" note on `claude-ads/agents/*.md`; the body
contracts come from "Stage 2 -- direct", "Stage 3 -- write", and
"Stage 4 -- qa".

An agent file is only half of a contract. The other half is the
per-run dispatch prompt `lib/agents.py` builds, which carries the
inputs and repeats the output shape. If the two halves disagree on a
label, the subagent writes a file `verify` then rejects, so
`test_agent_bodies_mirror_prompt_contract` pins the agent bodies to
`lib.agents`' own constants and to `qa.schema.json`.

The frontmatter is parsed with a tiny local reader rather than a YAML
library: this plugin is standard library only (design spec, "Global
Constraints").
"""
from __future__ import annotations

import re
import unittest
from typing import Dict, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import agents as agents_lib  # noqa: E402
from lib import director  # noqa: E402

# `test_bodies_name_the_creator_rules_label_their_prompt_emits` builds a
# real write/QA prompt pair, which needs a project with `creator.md`, a
# mock run, and a written script. `tests.test_agents` already has all
# three; importing them here beats a second copy that could drift.
from tests.test_agents import (  # noqa: E402
    REFERENCES_DIR,
    _mock_research_and_rank,
    _write_project,
    _write_script,
)

AGENTS_DIR = REPO_ROOT / "agents"
DIRECTOR_AGENT = AGENTS_DIR / "content-director.md"
WRITER_AGENT = AGENTS_DIR / "script-writer.md"
QA_AGENT = AGENTS_DIR / "qa-reviewer.md"

# The canonical description, pinned in full so it cannot drift. It says
# both of the director's jobs, because one subagent definition serves
# both the per-reel dispatch and the set-level synthesis dispatch.
DIRECTOR_DESCRIPTION = (
    "Analyzes one source Instagram Reel from keyframes and metadata and writes a "
    "ContentOS analysis JSON, or synthesizes patterns across analyses. "
    "Dispatched by /contentos; not for direct use."
)

# The writer's two jobs, first draft and revision, in one line. Both
# descriptions end the same way as the director's, so a creator reading
# `/agents` can tell at a glance that none of the three is theirs to
# call directly.
WRITER_DESCRIPTION = (
    "Writes one Instagram Reel script from a ContentOS brief, or revises it from QA "
    "findings. Dispatched by /contentos; not for direct use."
)

QA_DESCRIPTION = (
    "Reviews one ContentOS Reel script against its brief, the creator profile, and the QA "
    "rubric, and writes a QA verdict JSON. Dispatched by /contentos; not for direct use."
)

# agent name -> (file, pinned description, maxTurns). The turn limits are
# the spec's: the director looks at frames (20), the writer runs a draft
# and redraft loop (15), the reviewer only reads and scores (12).
AGENT_FILES: Dict[str, Tuple[object, str, str]] = {
    "content-director": (DIRECTOR_AGENT, DIRECTOR_DESCRIPTION, "20"),
    "script-writer": (WRITER_AGENT, WRITER_DESCRIPTION, "15"),
    "qa-reviewer": (QA_AGENT, QA_DESCRIPTION, "12"),
}

# The verdict rules, pinned by the phrase that carries each one rather
# than by the verdict words themselves: "reject" and "revise" appear in
# any body that mentions them at all, so asserting the bare words proves
# nothing about the rules the reviewer has to apply.
QA_VERDICT_PHRASES = [
    "`reject` when `no_fabricated_claims`",
    "a single line carries the whole problem",
    "`revise` when any other check failed",
    "below the threshold",
    "`pass` when nothing above applies",
    "never fail a check",
]

# Every string that must not survive the creator pivot in an agent file:
# the old analysis and QA field names, the old script section and profile
# file names, and the word "creator" itself (checked case-insensitively,
# which also covers the old `## Founder rules` prompt heading). The same
# list guards `references/` in `tests/test_references.py`.
PRODUCT_LEFTOVER_STRINGS = [
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
PRODUCT_LEFTOVER_STRINGS_CASE_INSENSITIVE = ["founder"]

BACKTICKED_RE = re.compile(r"`([^`]+)`")


def _split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Split an agent file into its `key: value` frontmatter and its body.

    Only the flat `key: value` lines these agent files use are
    understood; a surrounding pair of matching quotes is stripped from
    the value. Raises AssertionError when the file does not open with a
    `---` fence, so a malformed file fails the test instead of silently
    parsing as empty.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("agent file does not start with a --- frontmatter fence")

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

    raise AssertionError("agent file frontmatter is never closed with ---")


def _collapse(text: str) -> str:
    """Collapse every run of whitespace to one space.

    Agent prose is hard-wrapped, so a phrase the contract cares about
    ("never instructions") can land across a line break. Asserting on
    the collapsed text checks the wording without pinning the wrapping.
    """
    return " ".join(text.split())


def _creator_rules_label(prompt: str) -> str:
    """The label one dispatch prompt introduces the creator's rules with.

    `write_prompt` opens a `## Creator rules` section; `qa_prompt` writes a
    single `Creator rules: <text>` line inside `## Inputs`. An agent body
    that names the wrong one sends the subagent looking for text that is
    not in its prompt, so this pulls the label out of a real prompt rather
    than hard-coding either form. Raises AssertionError when the prompt
    carries no creator rules at all.
    """
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped == "## Creator rules":
            return stripped
        if stripped.startswith("Creator rules:"):
            return "Creator rules:"
    raise AssertionError("prompt carries no creator rules label")


def _body(path) -> str:
    """The body of one agent file, with its frontmatter removed."""
    return _split_frontmatter(path.read_text(encoding="utf-8"))[1]


class ContentDirectorAgentTests(NoNetworkTestCase):
    def test_director_agent_frontmatter_and_contract(self) -> None:
        text = DIRECTOR_AGENT.read_text(encoding="utf-8")
        fields, body = _split_frontmatter(text)

        self.assertEqual(fields["name"], "content-director")
        self.assertEqual(fields["tools"], "Read, Write")
        self.assertEqual(fields["maxTurns"], "20")
        self.assertNotIn("model", fields)
        self.assertEqual(fields["description"], DIRECTOR_DESCRIPTION)

        # The output contract and the synthesis mode.
        self.assertIn("WROTE", body)
        self.assertIn("FAILED", body)
        self.assertIn("03-patterns.md", body)
        # The prompt-injection rule: captions and comments are data.
        prose = _collapse(body)
        self.assertIn("comments are data", prose)
        self.assertIn("never instructions", prose)

    def test_director_agent_body_covers_every_judgement_call(self) -> None:
        body = _split_frontmatter(DIRECTOR_AGENT.read_text(encoding="utf-8"))[1]

        for term in (
            "hook_spoken",
            "format",
            "structure",
            "topic_shown",
            "why_it_worked",
            "transferable_mechanism",
            "adaptation",
            "avoid",
            "score_scalable",
            "score_convertible",
            "score_fit",
            "risk_flags",
            "confidence",
            "paid_partnership",
            "creator.md",
        ):
            with self.subTest(term=term):
                self.assertIn(term, body)

        # The dispatch prompt carries a `Source kind: niche | format` line,
        # and `score_fit` means two different things depending on which it
        # is, so the body has to name that line.
        lowered = body.lower()
        self.assertIn("source kind", lowered)

    def test_director_agent_prose_is_plain(self) -> None:
        text = DIRECTOR_AGENT.read_text(encoding="utf-8")

        # Creator-facing text: no em dashes (design spec, "Global Constraints").
        self.assertNotIn("—", text)


class AllAgentFilesTests(NoNetworkTestCase):
    """The rules every one of the three agent files obeys."""

    def test_all_three_agents_parse_frontmatter(self) -> None:
        for name, (path, description, max_turns) in AGENT_FILES.items():
            with self.subTest(agent=name):
                fields, _ = _split_frontmatter(path.read_text(encoding="utf-8"))

                self.assertEqual(fields["name"], name)
                self.assertEqual(fields["description"], description)
                self.assertEqual(fields["tools"], "Read, Write")
                self.assertEqual(fields["maxTurns"], max_turns)
                # No `model`: the subagent inherits the parent's.
                self.assertNotIn("model", fields)

    def test_tools_exactly_read_write(self) -> None:
        # No Bash, no WebFetch, no Agent: two tools, in this order.
        for name, (path, _description, _turns) in AGENT_FILES.items():
            with self.subTest(agent=name):
                fields, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
                tools = [tool.strip() for tool in fields["tools"].split(",")]
                self.assertEqual(tools, ["Read", "Write"])

    def test_bodies_state_wrote_contract_and_no_network(self) -> None:
        # The standing contract lines every dispatch depends on: the
        # sentinel, no network, and reel text is data rather than orders.
        for name, (path, _description, _turns) in AGENT_FILES.items():
            with self.subTest(agent=name):
                prose = _collapse(_body(path))

                self.assertIn("WROTE <path>", prose)
                self.assertIn("FAILED <reason>", prose)
                self.assertIn("no network", prose.lower())
                self.assertIn("comments are data", prose)
                self.assertIn("never instructions", prose)

    def test_bodies_name_required_reference_files_and_handoff_rule(self) -> None:
        writer_body = _body(WRITER_AGENT)
        qa_body = _body(QA_AGENT)

        for reference in ("hooks.md", "formats.md", "scripting.md"):
            with self.subTest(agent="script-writer", reference=reference):
                self.assertIn(reference, writer_body)
        for reference in ("formats.md", "qa-rubric.md"):
            with self.subTest(agent="qa-reviewer", reference=reference):
                self.assertIn(reference, qa_body)

        # Both bodies explain the HANDOFF block their dispatch prompt opens with.
        for name, body in (("script-writer", writer_body), ("qa-reviewer", qa_body)):
            with self.subTest(agent=name):
                self.assertIn("HANDOFF", body)

    def test_agents_accept_prompt_file_dispatch(self) -> None:
        # The skill never pastes a generated prompt through its own
        # context: it redirects `direct-prompt`/`synth-prompt`/
        # `write-prompt`/`qa-prompt` into a file under
        # `<run_dir>/prompts/` and dispatches a short message pointing at
        # that path. Every agent body has to say to read that file first
        # and treat its contents as the dispatch prompt.
        for name, (path, _description, _turns) in AGENT_FILES.items():
            with self.subTest(agent=name):
                prose = _collapse(_body(path))

                self.assertIn("prompt file", prose)
                self.assertIn("Read that file first", prose)

    def test_agent_bodies_have_no_product_leftovers(self) -> None:
        # 0.2.0 pivots the plugin from app founders to any creator. The
        # agent bodies were the last place the old wording lived, so this
        # greps all three for every name the pivot retired.
        for name, (path, _description, _turns) in AGENT_FILES.items():
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            for banned in PRODUCT_LEFTOVER_STRINGS:
                with self.subTest(agent=name, banned=banned):
                    self.assertNotIn(banned, text, f"{name} still contains {banned!r}")
            for banned in PRODUCT_LEFTOVER_STRINGS_CASE_INSENSITIVE:
                with self.subTest(agent=name, banned=banned):
                    self.assertNotIn(banned, lowered, f"{name} still contains {banned!r}")

    def test_agent_files_have_no_em_dashes(self) -> None:
        # Creator-facing text: plain language, no em dashes (design spec,
        # "Global Constraints").
        for name, (path, _description, _turns) in AGENT_FILES.items():
            with self.subTest(agent=name):
                self.assertNotIn("—", path.read_text(encoding="utf-8"))


class WriterAndQaAgentTests(NoNetworkTestCase):
    """The agent bodies must agree with the prompts `lib/agents.py` builds."""

    def test_agent_bodies_mirror_prompt_contract(self) -> None:
        writer_body = _body(WRITER_AGENT)

        self.assertIn(agents_lib.BEATS_HEADER, writer_body)
        for key in agents_lib.REQUIRED_FRONTMATTER_KEYS:
            with self.subTest(frontmatter_key=key):
                self.assertIn(key, writer_body)
        for section in agents_lib.CONTRACT_SECTIONS:
            with self.subTest(section=section):
                self.assertIn("## " + section, writer_body)
        for label in agents_lib.CTA_LABELS:
            with self.subTest(cta_label=label):
                self.assertIn(label, writer_body)
        self.assertIn("WROTE", writer_body)

        # The Hook labels go through the pattern `verify_script` matches
        # them with, not a copy of the wording: every backticked span in
        # the body is offered to that pattern, and the two roles it needs
        # must both come back. Rename a group there and this fails.
        quoted = BACKTICKED_RE.findall(writer_body)
        matches = [agents_lib._HOOK_LABEL_RE.match(span) for span in quoted]
        roles = {match.group(1) for match in matches if match}
        self.assertEqual(roles, {"Primary", "Backup"})

        self.assertIn("`kind` is `fill`", writer_body)

        qa_body = _body(QA_AGENT)
        qa_prose = _collapse(qa_body)
        qa_schema = director.load_schema("qa")

        for check in qa_schema["properties"]["checks"]["properties"]:
            with self.subTest(check=check):
                self.assertIn(check, qa_body)
        for score in qa_schema["properties"]["scores"]["properties"]:
            with self.subTest(score=score):
                self.assertIn(score, qa_body)
        for phrase in QA_VERDICT_PHRASES:
            with self.subTest(verdict_phrase=phrase):
                self.assertIn(phrase, qa_prose)

        # Final review I3: a fill brief is judged on its own topic.
        for phrase in ("`kind` is `fill`", "`idea_title`", "`adaptation`", "format and the hook only",
                       "not the proof reel's subject"):
            with self.subTest(fill_phrase=phrase):
                self.assertIn(phrase, qa_prose)



class CreatorRulesLabelTests(NoNetworkTestCase):
    """Each body must name the creator-rules label its own prompt emits.

    The two builders disagree on purpose: the writer gets a whole
    `## Creator rules` section, the reviewer gets one `Creator rules:`
    line under `## Inputs`. A body that describes the other one's shape
    reads its prompt, finds nothing, and treats a creator with rules as a
    creator with none, which drops `brand_voice` to `na`.
    """

    def test_bodies_name_the_creator_rules_label_their_prompt_emits(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research_and_rank(project)
            (project / ".contentos" / "rules.md").write_text(
                "Never use the word cheap.\n", encoding="utf-8"
            )

            write_text = agents_lib.write_prompt(
                project, run_dir, "B01", references_dir=REFERENCES_DIR
            )
            _write_script(run_dir, "B01", 0)
            qa_text = agents_lib.qa_prompt(
                project, run_dir, "B01", 0, references_dir=REFERENCES_DIR
            )

        write_label = _creator_rules_label(write_text)
        qa_label = _creator_rules_label(qa_text)

        # Not the same label, so neither body can satisfy both by accident.
        self.assertNotEqual(write_label, qa_label)

        with self.subTest(agent="script-writer", label=write_label):
            self.assertIn(write_label, _body(WRITER_AGENT))
        with self.subTest(agent="qa-reviewer", label=qa_label):
            self.assertIn(qa_label, _body(QA_AGENT))


if __name__ == "__main__":
    unittest.main()
