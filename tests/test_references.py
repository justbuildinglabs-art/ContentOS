"""Tests for skills/contentos/references/*.md: the seven reference files.

Task 2 wrote the four guide-derived files (`hooks.md`, `formats.md`,
`scripting.md`, `qa-rubric.md`) and the two example scripts under
`examples/`. Task 15 added the remaining three (`stages.md`,
`scoring.md`, and the profile template) plus this test module, which
checks every reference file against the schemas and enums it must stay
in sync with, rather than pinning any file's prose word for word. Task 6
rewrote the prose and the examples for the creator pivot, so the checks
here also guard against the old app-founder vocabulary coming back.

See the design spec's "Reference files" section for what each file must
contain, and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-15-brief.md` for
this task's exact file and test names.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Dict, List

from tests.helpers import NoNetworkTestCase, REPO_ROOT

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so this import must come after it.
from lib import director  # noqa: E402

REFERENCES_DIR = REPO_ROOT / "skills" / "contentos" / "references"
EXAMPLES_DIR = REFERENCES_DIR / "examples"
AGENTS_DIR = REPO_ROOT / "agents"

# The four creator.md sections `no_fabricated_claims` traces claims to
# (design spec, "Stage 3 -- write": "Every claim must exist in creator.md,
# under Allowed claims, Proof assets, Payoff moments, or What you promote").
NO_FABRICATED_CLAIMS_SECTIONS = [
    "Allowed claims",
    "Proof assets",
    "Payoff moments",
    "What you promote",
]

# The seven files the design spec's "Reference files" table lists, minus
# examples/<format>.md (checked separately, by count and by name).
REFERENCE_FILENAMES = [
    "hooks.md",
    "formats.md",
    "scripting.md",
    "qa-rubric.md",
    "scoring.md",
    "stages.md",
    "creator-template.md",
    "specificity.md",
]

# Niches `specificity.md` must cover in its table (design spec, "0.3.0
# changes": what "specific" means per niche, niche-agnostic).
SPECIFICITY_NICHES = [
    "tech", "fitness", "cooking", "personal finance", "faith", "beauty", "parenting", "travel",
]

# v1 ships at least these two gold scripts (design spec, "Reference files").
EXAMPLE_FILENAMES = ["talking_head.md", "screen_demo.md"]

# The exact `## ` heading order the design spec's "Reference files" table
# pins down for creator-template.md, with Sources appended last like every
# other reference file.
CREATOR_TEMPLATE_SECTIONS = [
    "Creator",
    "One-liner",
    "Pillars",
    "Audience profile",
    "What you promote",
    "Payoff moments",
    "Allowed claims",
    "Forbidden claims",
    "Proof assets",
    "Brand voice",
    "CTA",
    "Hashtag seeds",
    "Competitors",
    "Format accounts",
]

# Section names the creator pivot dropped from the profile template.
# They stay in the candidate list below so the citation check keeps real
# signal: a reference file that still points a subagent at "Core
# features" or "Demo moments" is naming a heading `creator.md` does not
# have, and must fail rather than go unnoticed.
DROPPED_SECTION_NAMES = ["Product facts", "Core features", "Demo moments"]

# The subagent prompts and reference files that read creator.md directly
# (design spec, "Reference files" and "Stage 2 -- direct").
CREATOR_SECTION_CITING_FILES = [
    REFERENCES_DIR / "hooks.md",
    REFERENCES_DIR / "scripting.md",
    REFERENCES_DIR / "qa-rubric.md",
    AGENTS_DIR / "content-director.md",
]

# Every string that must not survive the creator pivot anywhere under
# references/: the old schema keys, the old script section and profile
# file names, and the word "founder" itself (checked case-insensitively,
# which also covers the old `## Founder rules` prompt heading).
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

HEADING2_RE = re.compile(r"^## (.+)$", re.MULTILINE)
HEADING3_RE = re.compile(r"^### (.+)$", re.MULTILINE)


def _headings(text: str, pattern) -> List[str]:
    """Every heading captured by `pattern`, in document order, stripped."""
    return [heading.strip() for heading in pattern.findall(text)]


def _section_text(text: str, heading_line: str) -> str:
    """The body of one heading: from `heading_line` to the next heading at
    the same level or shallower, or end of file.

    `heading_line` is the exact line to match, e.g. `"## Format table"` or
    `"### viral_proof"`. Raises AssertionError when it is not found, so a
    caller fails with a clear message instead of an IndexError downstream.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading_line:
            start = index
            break
    if start is None:
        raise AssertionError(f"heading not found: {heading_line!r}")

    level = len(heading_line) - len(heading_line.lstrip("#"))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("#"):
            this_level = len(stripped) - len(stripped.lstrip("#"))
            if this_level <= level:
                end = index
                break
    return "\n".join(lines[start:end])


def _parse_markdown_table(section_text: str) -> List[Dict[str, str]]:
    """Parse the single `| ... |` table inside one heading's body.

    `section_text` is a heading's full body as `_section_text` returns it
    (the heading line, its surrounding prose, and exactly one table). Every
    data row becomes one dict keyed by the header row's cells, in table
    order. Used for every table this module checks, so a corrupted cell
    fails on the exact row/column it lives in, not on a substring search
    that could also match unrelated prose elsewhere in the section.
    """
    rows = [line.strip() for line in section_text.splitlines() if line.strip().startswith("|")]
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    return [
        dict(zip(header, (cell.strip() for cell in row.strip("|").split("|"))))
        for row in rows[2:]  # rows[1] is the "| --- | --- |" separator
    ]


def _parse_format_table(text: str) -> Dict[str, Dict[str, str]]:
    """Parse the single table under `## Format table`, keyed by `format`."""
    section = _section_text(text, "## Format table")
    return {row["format"]: row for row in _parse_markdown_table(section)}


def _citation_candidates(template_headings: List[str]) -> List[str]:
    """The heading names distinctive enough to read as a section citation.

    Only multi-word headings from `creator-template.md` qualify: a bare
    "Creator", "Pillars", or "CTA" turns up in ordinary prose all over
    these files and would match every one of them. The dropped names are
    appended so a stale citation still trips the check.
    """
    return [name for name in template_headings if " " in name] + DROPPED_SECTION_NAMES


def _reference_paths() -> List[Path]:
    """Every markdown file under `references/`, examples included."""
    return sorted(REFERENCES_DIR.rglob("*.md"))


class ReferenceFilesExistTests(NoNetworkTestCase):
    def test_reference_files_exist_nonempty(self) -> None:
        for name in REFERENCE_FILENAMES:
            path = REFERENCES_DIR / name
            with self.subTest(file=name):
                self.assertTrue(path.exists(), f"missing {path}")
                self.assertTrue(path.read_text(encoding="utf-8").strip(), f"empty {path}")

        example_paths = sorted(EXAMPLES_DIR.glob("*.md"))
        self.assertGreaterEqual(
            len(example_paths), 2, "expected at least two gold scripts under examples/"
        )
        for name in EXAMPLE_FILENAMES:
            path = EXAMPLES_DIR / name
            with self.subTest(file=name):
                self.assertTrue(path.exists(), f"missing {path}")
                self.assertTrue(path.read_text(encoding="utf-8").strip(), f"empty {path}")


class FormatsTableTests(NoNetworkTestCase):
    def test_formats_table_covers_every_format_with_word_budget(self) -> None:
        text = (REFERENCES_DIR / "formats.md").read_text(encoding="utf-8")
        table = _parse_format_table(text)

        format_values = director.load_schema("analysis")["properties"]["format"]["enum"]
        self.assertTrue(format_values)
        for value in format_values:
            with self.subTest(format=value):
                self.assertIn(value, table)
                self.assertGreater(int(table[value]["word_budget"]), 0)
                self.assertGreater(int(table[value]["target_length_s"]), 0)


class HooksCoverageTests(NoNetworkTestCase):
    def test_hooks_cover_every_hook_type(self) -> None:
        text = (REFERENCES_DIR / "hooks.md").read_text(encoding="utf-8")
        headings = set(_headings(text, HEADING3_RE))

        hook_types = director.load_schema("analysis")["properties"]["hook_type"]["enum"]
        self.assertTrue(hook_types)
        for value in hook_types:
            with self.subTest(hook_type=value):
                self.assertIn(value, headings)


class QaRubricCoverageTests(NoNetworkTestCase):
    def test_qa_rubric_names_every_check_and_score_key_from_schema(self) -> None:
        text = (REFERENCES_DIR / "qa-rubric.md").read_text(encoding="utf-8")
        headings = set(_headings(text, HEADING3_RE))

        qa_schema = director.load_schema("qa")
        keys = list(qa_schema["properties"]["checks"]["properties"]) + list(
            qa_schema["properties"]["scores"]["properties"]
        )
        self.assertTrue(keys)
        for key in keys:
            with self.subTest(key=key):
                self.assertIn(key, headings)


class NoFabricatedClaimsSectionsTests(NoNetworkTestCase):
    def test_qa_rubric_names_all_four_evidence_sections(self) -> None:
        text = (REFERENCES_DIR / "qa-rubric.md").read_text(encoding="utf-8")
        section = _section_text(text, "### no_fabricated_claims")
        for name in NO_FABRICATED_CLAIMS_SECTIONS:
            with self.subTest(section=name):
                self.assertIn(name, section)

    def test_qa_reviewer_agent_names_all_four_evidence_sections(self) -> None:
        text = (AGENTS_DIR / "qa-reviewer.md").read_text(encoding="utf-8")
        match = re.search(
            r"- `no_fabricated_claims`:.*?(?=\n- `|\n## |\Z)", text, re.DOTALL
        )
        self.assertIsNotNone(match, "no_fabricated_claims bullet not found")
        bullet = match.group(0)
        for name in NO_FABRICATED_CLAIMS_SECTIONS:
            with self.subTest(section=name):
                self.assertIn(name, bullet)


class SourcesFooterTests(NoNetworkTestCase):
    def test_every_reference_file_ends_with_sources_footer(self) -> None:
        for name in REFERENCE_FILENAMES:
            path = REFERENCES_DIR / name
            headings = _headings(path.read_text(encoding="utf-8"), HEADING2_RE)
            with self.subTest(file=name):
                self.assertTrue(headings, f"{path} has no ## headings")
                self.assertEqual(headings[-1], "Sources")


class CreatorTemplateTests(NoNetworkTestCase):
    def test_creator_template_has_every_section(self) -> None:
        text = (REFERENCES_DIR / "creator-template.md").read_text(encoding="utf-8")
        headings = _headings(text, HEADING2_RE)
        self.assertEqual(headings, CREATOR_TEMPLATE_SECTIONS + ["Sources"])


class CreatorSectionCitationTests(NoNetworkTestCase):
    def test_references_cite_only_existing_creator_sections(self) -> None:
        template_headings = _headings(
            (REFERENCES_DIR / "creator-template.md").read_text(encoding="utf-8"),
            HEADING2_RE,
        )
        candidates = _citation_candidates(template_headings)

        cited = set()
        for path in CREATOR_SECTION_CITING_FILES:
            text = path.read_text(encoding="utf-8")
            for name in candidates:
                if name in text:
                    cited.add(name)

        # A real check needs real signal: if nothing was ever cited, the
        # loop above is silently vacuous and this test would prove nothing.
        self.assertTrue(cited, "expected at least one creator.md section citation")
        for name in sorted(cited):
            with self.subTest(section=name):
                self.assertIn(name, set(template_headings))


class ProductLeftoverTests(NoNetworkTestCase):
    def test_reference_files_have_no_product_leftovers(self) -> None:
        paths = _reference_paths()
        self.assertTrue(paths, "expected markdown files under references/")
        for path in paths:
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            name = str(path.relative_to(REFERENCES_DIR))
            for banned in PRODUCT_LEFTOVER_STRINGS:
                with self.subTest(file=name, banned=banned):
                    self.assertNotIn(banned, text, f"{name} still contains {banned!r}")
            for banned in PRODUCT_LEFTOVER_STRINGS_CASE_INSENSITIVE:
                with self.subTest(file=name, banned=banned):
                    self.assertNotIn(
                        banned, lowered, f"{name} still contains {banned!r}"
                    )


class SpecificityMdTests(NoNetworkTestCase):
    def test_specificity_md_covers_every_niche_and_the_benefit_frame(self) -> None:
        text = (REFERENCES_DIR / "specificity.md").read_text(encoding="utf-8")
        self.assertNotIn("—", text)

        rows = _parse_markdown_table(_section_text(text, "## By niche"))
        niches = [row["Niche"].lower() for row in rows]
        for niche in SPECIFICITY_NICHES:
            with self.subTest(niche=niche):
                self.assertIn(niche, niches)

        frame = _section_text(text, "## Benefit frame for a proof beat").lower()
        for part in ("what it is", "who it is for", "cost", "tradeoff"):
            with self.subTest(part=part):
                self.assertIn(part, frame)

        # Every specifics kind the schema allows is explained somewhere.
        kinds = director.load_schema("analysis")["properties"]["specifics"]["items"][
            "properties"]["kind"]["enum"]
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertIn(f"`{kind}`", text)


class ScoringMdTests(NoNetworkTestCase):
    def test_scoring_md_states_viral_proof_table(self) -> None:
        text = (REFERENCES_DIR / "scoring.md").read_text(encoding="utf-8")
        section = _section_text(text, "### viral_proof")
        rows = _parse_markdown_table(section)
        table = {row["outlier_ratio"]: row["viral_proof"] for row in rows}

        # A substring search over the whole section would also match "10"
        # and "3.96" in the surrounding prose (the "0 to 10" range, the
        # "clamped to 10" clause, the "3.9624..." explanation below the
        # table), so a corrupted table row could still pass. Parsing the
        # table and comparing its own cells rules that out.
        expected = {"3": "3.96", "8": "7.5", "16": "10"}
        self.assertEqual(set(table), set(expected), "unexpected outlier_ratio rows")
        for ratio, viral_proof in expected.items():
            with self.subTest(outlier_ratio=ratio):
                self.assertEqual(table[ratio], viral_proof)

    def test_scoring_md_defines_score_fit_and_score_convertible_with_anchors(self) -> None:
        text = (REFERENCES_DIR / "scoring.md").read_text(encoding="utf-8")
        headings = set(_headings(text, HEADING3_RE))

        # The design spec's "Reference files" table: scoring.md carries
        # 10/7/4 anchors for all three director-judged scores.
        for key in ("score_convertible", "score_scalable", "score_fit"):
            with self.subTest(score=key):
                self.assertIn(key, headings)
                section = _section_text(text, f"### {key}")
                for anchor in ("10:", "7:", "4:"):
                    self.assertIn(
                        anchor, section, f"{key} is missing its {anchor} anchor"
                    )

        # score_fit reads differently for a niche reel and a format reel
        # (design spec, "Stage 2 -- direct"), so its section must say so.
        fit_section = _section_text(text, "### score_fit")
        self.assertIn("niche", fit_section)
        self.assertIn("format", fit_section)


if __name__ == "__main__":
    unittest.main()
