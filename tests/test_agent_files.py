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

import unittest
from typing import Dict, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import agents as agents_lib  # noqa: E402
from lib import director  # noqa: E402

AGENTS_DIR = REPO_ROOT / "agents"
DIRECTOR_AGENT = AGENTS_DIR / "content-director.md"
WRITER_AGENT = AGENTS_DIR / "script-writer.md"
QA_AGENT = AGENTS_DIR / "qa-reviewer.md"

# The canonical description, pinned in full so it cannot drift. It says
# both of the director's jobs, because one subagent definition serves
# both the per-reel dispatch and the set-level synthesis dispatch.
DIRECTOR_DESCRIPTION = (
    "Analyzes one competitor Instagram Reel from keyframes and metadata and writes a "
    "ContentOS analysis JSON, or synthesizes patterns across analyses. "
    "Dispatched by /contentos; not for direct use."
)

# The writer's two jobs, first draft and revision, in one line. Both
# descriptions end the same way as the director's, so a founder reading
# `/agents` can tell at a glance that none of the three is theirs to
# call directly.
WRITER_DESCRIPTION = (
    "Writes one Instagram Reel script from a ContentOS brief, or revises it from QA "
    "findings. Dispatched by /contentos; not for direct use."
)

QA_DESCRIPTION = (
    "Reviews one ContentOS Reel script against its brief, the product facts, and the QA "
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

# The two Hook labels `write_prompt` demands, quoted exactly as the
# prompt writes them. `lib.agents` has no constant for these, because
# `verify_script` matches them with a regex; the literal wording lives
# in both the prompt and the agent body, so the test pins the string.
HOOK_LABELS = ["**Primary (approach: <name>)**", "**Backup (approach: <name>)**"]


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
            "why_it_worked",
            "transferable_mechanism",
            "adaptation_for_product",
            "avoid",
            "score_scalable",
            "score_convertible",
            "score_product_fit",
            "risk_flags",
            "confidence",
            "product.md",
        ):
            with self.subTest(term=term):
                self.assertIn(term, body)

    def test_director_agent_prose_is_plain(self) -> None:
        text = DIRECTOR_AGENT.read_text(encoding="utf-8")

        # Founder-facing text: no em dashes (design spec, "Global Constraints").
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

    def test_agent_files_have_no_em_dashes(self) -> None:
        # Founder-facing text: plain language, no em dashes (design spec,
        # "Global Constraints").
        for name, (path, _description, _turns) in AGENT_FILES.items():
            with self.subTest(agent=name):
                self.assertNotIn("—", path.read_text(encoding="utf-8"))


class WriterAndQaAgentTests(NoNetworkTestCase):
    """The agent bodies must agree with the prompts `lib/agents.py` builds."""

    def test_agent_bodies_mirror_prompt_contract(self) -> None:
        writer_body = _body(WRITER_AGENT)

        self.assertIn(agents_lib.BEATS_HEADER, writer_body)
        for label in HOOK_LABELS:
            with self.subTest(hook_label=label):
                self.assertIn(label, writer_body)
        for label in agents_lib.CTA_LABELS:
            with self.subTest(cta_label=label):
                self.assertIn(label, writer_body)
        self.assertIn("WROTE", writer_body)

        qa_body = _body(QA_AGENT)
        qa_schema = director.load_schema("qa")

        for check in qa_schema["properties"]["checks"]["properties"]:
            with self.subTest(check=check):
                self.assertIn(check, qa_body)
        for score in qa_schema["properties"]["scores"]["properties"]:
            with self.subTest(score=score):
                self.assertIn(score, qa_body)
        for verdict in ("reject", "revise", "pass"):
            with self.subTest(verdict=verdict):
                self.assertIn(verdict, qa_body)


if __name__ == "__main__":
    unittest.main()
