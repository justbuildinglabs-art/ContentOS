"""Tests that pin the plugin's on-disk layout to the design spec.

Task 19 is the last task in the ContentOS build: every stage is
implemented and reviewed, so what is left to prove about the repository
itself is that it actually has the exact file layout the design spec's
"Architecture" section commits to -- right down to the empty
`lib/__init__.py` and the absence of the cut `lib/gemini.py` backend --
and that the plugin manifest's version and CHANGELOG.md's top entry
never drift apart. See the design spec's "Architecture" section and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-19-brief.md`.

This module never runs the CLI and never touches the network; it is a
plain filesystem/JSON check. It still subclasses NoNetworkTestCase, as
CLAUDE.md requires of every test module in this package.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import List

from tests.helpers import NoNetworkTestCase, REPO_ROOT, SKILL_DIR

REFERENCES_DIR = SKILL_DIR / "references"
SCRIPTS_DIR = SKILL_DIR / "scripts"
LIB_DIR = SCRIPTS_DIR / "lib"
SCHEMAS_DIR = SCRIPTS_DIR / "schemas"
FIXTURES_DIR = REPO_ROOT / "fixtures"
AGENTS_DIR = REPO_ROOT / "agents"

# The seven reference files (design spec, "Reference files"), and the
# two gold example scripts (v1 ships talking_head and screen_demo).
REFERENCE_FILES = [
    "hooks.md", "formats.md", "scripting.md", "qa-rubric.md",
    "scoring.md", "stages.md", "product-template.md",
]
EXAMPLE_FILES = ["talking_head.md", "screen_demo.md"]

# Every module under skills/contentos/scripts/lib/ (design spec,
# "Architecture"). __init__.py stays empty; there is no lib/gemini.py --
# that backend was cut from v1 ("Cut from v1 (YAGNI) and later").
LIB_MODULES = [
    "__init__.py", "codes.py", "env.py", "store.py", "http.py",
    "apify.py", "instagram.py", "outliers.py", "research.py",
    "video.py", "frames.py", "director.py", "direct.py",
    "agents.py", "report.py", "setup.py",
]

SCHEMA_FILES = ["analysis.schema.json", "qa.schema.json"]

AGENT_FILES = ["content-director.md", "script-writer.md", "qa-reviewer.md"]

ANALYSIS_FIXTURES = ["DWN001.json", "DWN003.json", "DWN006.json", "HAB005.json", "SPA006.json"]
FRAME_FIXTURES = ["f0{0}.jpg".format(n) for n in range(1, 9)]


def _required_paths() -> List[Path]:
    """Every path `test_plugin_layout_complete` asserts exists."""
    paths = [
        REPO_ROOT / ".claude-plugin" / "plugin.json",
        REPO_ROOT / ".claude-plugin" / "marketplace.json",
        REPO_ROOT / "hooks" / "hooks.json",
        SKILL_DIR / "SKILL.md",
        SCRIPTS_DIR / "contentos.py",
        REPO_ROOT / "README.md",
        REPO_ROOT / "CLAUDE.md",
        REPO_ROOT / "CHANGELOG.md",
        REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-09-16-contentos-design.md",
    ]
    paths += [REFERENCES_DIR / name for name in REFERENCE_FILES]
    paths += [REFERENCES_DIR / "examples" / name for name in EXAMPLE_FILES]
    paths += [LIB_DIR / name for name in LIB_MODULES]
    paths += [SCHEMAS_DIR / name for name in SCHEMA_FILES]
    paths += [AGENTS_DIR / name for name in AGENT_FILES]
    paths += [
        FIXTURES_DIR / "apify_reels_sample.json",
        FIXTURES_DIR / "apify_profiles_sample.json",
        FIXTURES_DIR / "sample.mp4",
        FIXTURES_DIR / "sample_cover.jpg",
    ]
    paths += [FIXTURES_DIR / "frames" / "sample" / name for name in FRAME_FIXTURES]
    paths += [FIXTURES_DIR / "analyses" / name for name in ANALYSIS_FIXTURES]
    paths += [
        FIXTURES_DIR / "patterns.sample.md",
        FIXTURES_DIR / "script.sample.md",
        FIXTURES_DIR / "qa.sample.json",
        FIXTURES_DIR / "setup-answers.sample.json",
    ]
    return paths


class PluginLayoutTests(NoNetworkTestCase):
    def test_plugin_layout_complete(self) -> None:
        missing = [str(path) for path in _required_paths() if not path.exists()]
        self.assertEqual(missing, [], "missing from the plugin layout: {0}".format(missing))

    def test_lib_init_is_empty(self) -> None:
        # design spec, "Global Constraints": "lib/__init__.py stays empty
        # (no eager imports)".
        self.assertEqual((LIB_DIR / "__init__.py").read_text(encoding="utf-8"), "")

    def test_no_gemini_backend(self) -> None:
        # design spec, "Cut from v1 (YAGNI) and later": "any video-model
        # backend" is cut; lib/gemini.py must never reappear.
        self.assertFalse((LIB_DIR / "gemini.py").exists())

    def test_plugin_version_matches_changelog_top_entry(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        match = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
        self.assertIsNotNone(match, "CHANGELOG.md has no top-level ## [x.y.z] entry")
        self.assertEqual(manifest["version"], match.group(1))


if __name__ == "__main__":
    unittest.main()
