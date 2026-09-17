"""Tests for the subagent definition files under `agents/`.

Task 14 ships the first one, `agents/content-director.md`. Later tasks
add `script-writer.md` and `qa-reviewer.md`; this module grows one
class per agent. The frontmatter conventions (a comma-separated
`tools` string, `maxTurns`, an omitted `model` so the subagent inherits
the parent's) come from the design spec's "Architecture" note on
`claude-ads/agents/*.md`; the body contract comes from "Stage 2 --
direct".

The frontmatter is parsed with a tiny local reader rather than a YAML
library: this plugin is standard library only (design spec, "Global
Constraints").
"""
from __future__ import annotations

import unittest
from typing import Dict, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT

AGENTS_DIR = REPO_ROOT / "agents"
DIRECTOR_AGENT = AGENTS_DIR / "content-director.md"


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


class ContentDirectorAgentTests(NoNetworkTestCase):
    def test_director_agent_frontmatter_and_contract(self) -> None:
        text = DIRECTOR_AGENT.read_text(encoding="utf-8")
        fields, body = _split_frontmatter(text)

        self.assertEqual(fields["name"], "content-director")
        self.assertEqual(fields["tools"], "Read, Write")
        self.assertEqual(fields["maxTurns"], "20")
        self.assertNotIn("model", fields)
        self.assertTrue(fields["description"].strip())

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


if __name__ == "__main__":
    unittest.main()
