"""Tests for skills/contentos/references/*.md: the seven reference files.

Task 2 wrote the four guide-derived files (`hooks.md`, `formats.md`,
`scripting.md`, `qa-rubric.md`) and the two example scripts under
`examples/`. Task 15 adds the remaining three (`stages.md`, `scoring.md`,
`product-template.md`) plus this test module, which checks every
reference file against the schemas and enums it must stay in sync with,
rather than pinning any file's prose word for word.

See the design spec's "Reference files" section for what each file must
contain, and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-15-brief.md` for
this task's exact file and test names.
"""
from __future__ import annotations

import re
import unittest
from typing import Dict, List

from tests.helpers import NoNetworkTestCase, REPO_ROOT

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so this import must come after it.
from lib import director  # noqa: E402

REFERENCES_DIR = REPO_ROOT / "skills" / "contentos" / "references"
EXAMPLES_DIR = REFERENCES_DIR / "examples"
AGENTS_DIR = REPO_ROOT / "agents"

# The seven files the design spec's "Reference files" table lists, minus
# examples/<format>.md (checked separately, by count and by name).
REFERENCE_FILENAMES = [
    "hooks.md",
    "formats.md",
    "scripting.md",
    "qa-rubric.md",
    "scoring.md",
    "stages.md",
    "product-template.md",
]

# v1 ships at least these two gold scripts (design spec, "Reference files").
EXAMPLE_FILENAMES = ["talking_head.md", "screen_demo.md"]

# The exact `## ` heading order the design spec's "Reference files" table
# pins down for product-template.md, with Sources appended last like every
# other reference file.
PRODUCT_TEMPLATE_SECTIONS = [
    "Product",
    "One-liner",
    "Audience profile",
    "Core features",
    "Demo moments",
    "Allowed claims",
    "Forbidden claims",
    "Proof assets",
    "Brand voice",
    "CTA",
    "Hashtag seeds",
    "Competitors",
]

# product.md section names that read as genuine section citations: each is
# two words, so a bare generic word like "Product" or "CTA" used in
# ordinary prose elsewhere in these files cannot false-positive the check
# below.
PRODUCT_SECTION_CITATION_CANDIDATES = [
    "Audience profile",
    "Core features",
    "Demo moments",
    "Allowed claims",
    "Forbidden claims",
    "Proof assets",
    "Brand voice",
    "Hashtag seeds",
]

# The subagent prompts and reference files that read product.md directly
# (design spec, "Reference files" and "Stage 2 -- direct").
PRODUCT_SECTION_CITING_FILES = [
    REFERENCES_DIR / "hooks.md",
    REFERENCES_DIR / "scripting.md",
    REFERENCES_DIR / "qa-rubric.md",
    AGENTS_DIR / "content-director.md",
]

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


def _parse_format_table(text: str) -> Dict[str, Dict[str, str]]:
    """Parse the single table under `## Format table`, keyed by `format`."""
    section = _section_text(text, "## Format table")
    rows = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    table: Dict[str, Dict[str, str]] = {}
    for row in rows[2:]:  # rows[1] is the "| --- | --- |" separator
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        record = dict(zip(header, cells))
        table[record["format"]] = record
    return table


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


class SourcesFooterTests(NoNetworkTestCase):
    def test_every_reference_file_ends_with_sources_footer(self) -> None:
        for name in REFERENCE_FILENAMES:
            path = REFERENCES_DIR / name
            headings = _headings(path.read_text(encoding="utf-8"), HEADING2_RE)
            with self.subTest(file=name):
                self.assertTrue(headings, f"{path} has no ## headings")
                self.assertEqual(headings[-1], "Sources")


class ProductTemplateTests(NoNetworkTestCase):
    def test_product_template_has_every_section(self) -> None:
        text = (REFERENCES_DIR / "product-template.md").read_text(encoding="utf-8")
        headings = _headings(text, HEADING2_RE)
        self.assertEqual(headings, PRODUCT_TEMPLATE_SECTIONS + ["Sources"])


class ProductSectionCitationTests(NoNetworkTestCase):
    def test_references_cite_only_existing_product_sections(self) -> None:
        template_headings = set(
            _headings(
                (REFERENCES_DIR / "product-template.md").read_text(encoding="utf-8"),
                HEADING2_RE,
            )
        )

        cited = set()
        for path in PRODUCT_SECTION_CITING_FILES:
            text = path.read_text(encoding="utf-8")
            for name in PRODUCT_SECTION_CITATION_CANDIDATES:
                if name in text:
                    cited.add(name)

        # A real check needs real signal: if nothing was ever cited, the
        # loop above is silently vacuous and this test would prove nothing.
        self.assertTrue(cited, "expected at least one product.md section citation")
        for name in sorted(cited):
            with self.subTest(section=name):
                self.assertIn(name, template_headings)


class ScoringMdTests(NoNetworkTestCase):
    def test_scoring_md_states_viral_proof_table(self) -> None:
        text = (REFERENCES_DIR / "scoring.md").read_text(encoding="utf-8")
        section = _section_text(text, "### viral_proof")
        for token in ("3.96", "7.5", "10"):
            with self.subTest(token=token):
                self.assertIn(token, section)


if __name__ == "__main__":
    unittest.main()
