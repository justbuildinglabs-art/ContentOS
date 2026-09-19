"""Tests for the Stage 2 director subcommands: prompts, verify, rank.

Task 14 wires `direct-prompt`, `synth-prompt`, `verify --stage
direct|synth`, and `rank` into `contentos.py` over a new
`lib/direct.py`, and ships the five fixture analyses plus
`fixtures/patterns.sample.md` that let `rank --mock` produce briefs
with no subagent in the loop. See the design spec's "Stage 2 --
direct" section and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-14-brief.md`
for the exact interface.

Every run here is a real `research --mock --yes` run in a fresh
temporary project, so the reels, their `frames_status`, and their
Stage 1 `viral_proof` are the ones the shipped fixtures actually
produce, not hand-written stand-ins. Nothing touches the network:
mock research serves `apify.FixtureTransport` and copies the committed
sample video and frames; NoNetworkTestCase is a second line of
defense.
"""
from __future__ import annotations

import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import codes, director, research, store  # noqa: E402
from lib.env import Keys  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
ANALYSES_FIXTURES_DIR = FIXTURES_DIR / "analyses"
PATTERNS_FIXTURE = FIXTURES_DIR / "patterns.sample.md"

# The fixture handles Task 8/9 designed the sample Apify files around
# (mirrors tests/test_research.py's FIXTURE_HANDLES), split into
# competitors (niche) and format_accounts the way
# fixtures/setup-answers.sample.json does: dailywins is the one format
# account, so its reels (DWN*) carry source_kind "format" and the rest
# carry "niche" (design spec, "0.2.0 changes").
FIXTURE_COMPETITORS = ["sproutapp", "habitlab", "ghostaccount"]
FIXTURE_FORMAT_ACCOUNTS = ["dailywins"]

# What `research --mock --yes` selects from those handles, in rank order
# by `outlier_ratio`. The first five are the ones `fixtures/analyses/`
# covers; HAB004 is the one selected reel whose video is `too_large`, so
# it lands on `frames_status: no_video` and is the natural
# frames-are-missing case for `direct-prompt`.
ANALYZED_SHORTCODES = ["DWN006", "HAB005", "SPA006", "DWN001", "DWN003"]
NO_FRAMES_SHORTCODE = "HAB004"

CREATOR_MD = """# Creator profile

Dana is a productivity creator for people who keep quitting their system by Wednesday.

## Payoff moments

- The week view filling in after a planning session.

## Allowed claims

- Setting up the weekly plan takes under 10 minutes.
"""


def _write_project(project: Path) -> None:
    """Write the creator state a Stage 2 command needs: config and creator.md."""
    config_dir = store.contentos_dir(project)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {"competitors": FIXTURE_COMPETITORS, "format_accounts": FIXTURE_FORMAT_ACCOUNTS}
        ),
        encoding="utf-8",
    )
    (config_dir / "creator.md").write_text(CREATOR_MD, encoding="utf-8")


def _mock_research(project: Path) -> Path:
    """Run `research --mock --yes` in `project` quietly; return its run dir."""
    cfg = store.load_config(project)
    buffer = StringIO()
    with redirect_stdout(buffer):
        result = research.run_research(
            project,
            cfg,
            Keys(apify=None, source=None, warnings=[]),
            mock=True,
            yes=True,
            estimate_only=False,
            resume=None,
            log=lambda _message: None,
        )
    return Path(result["run_dir"])


def _main(argv: Sequence[str]) -> Tuple[int, str, str]:
    """Run one contentos.py subcommand in-process; return (code, stdout, stderr)."""
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = contentos.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _copy_analysis(run_dir: Path, shortcode: str) -> Path:
    """Copy one `fixtures/analyses/<sc>.json` into this run's `03-analyses/`."""
    dest_dir = run_dir / "03-analyses"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{shortcode}.json"
    shutil.copyfile(ANALYSES_FIXTURES_DIR / f"{shortcode}.json", dest)
    return dest


def _selected(run_dir: Path) -> List[Dict[str, Any]]:
    """This run's `02-outliers.json` `selected` reels."""
    return store.read_json(run_dir / "02-outliers.json")["selected"]


def _reel(run_dir: Path, shortcode: str) -> Dict[str, Any]:
    """One `selected` reel by shortCode."""
    for reel in _selected(run_dir):
        if reel["shortCode"] == shortcode:
            return reel
    raise AssertionError(f"{shortcode} is not selected in {run_dir}")


class DirectPromptTests(NoNetworkTestCase):
    def test_direct_prompt_exit_2_when_frames_missing_or_not_selected(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)

            # Never selected at all: nothing to analyze.
            code, out, err = _main(
                ["direct-prompt", "--project", str(project), "--run", "latest",
                 "--shortcode", "NOTAREEL"]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertIn("NOTAREEL", err)

            # Selected, but its video was too large, so it has no frames.
            self.assertEqual(_reel(run_dir, NO_FRAMES_SHORTCODE)["frames_status"], "no_video")
            code, out, err = _main(
                ["direct-prompt", "--project", str(project), "--run", "latest",
                 "--shortcode", NO_FRAMES_SHORTCODE]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertIn(NO_FRAMES_SHORTCODE, err)
            self.assertIn("no_video", err)

    def test_direct_prompt_prints_prompt_for_fixture_reel(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)

            code, out, err = _main(
                ["direct-prompt", "--project", str(project), "--run", "latest",
                 "--shortcode", "SPA006"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn("HANDOFF", out)
            # The reel's own frames, metadata, and the exact output path.
            self.assertIn(str((run_dir / "frames" / "SPA006").resolve()), out)
            self.assertIn("what app is this??", out)
            self.assertIn(str((run_dir / "03-analyses" / "SPA006.json").resolve()), out)
            # References and creator context, by absolute path.
            self.assertIn(str((project / ".contentos" / "creator.md").resolve()), out)
            self.assertIn("hooks.md", out)
            self.assertIn("formats.md", out)
            # Every schema property the director has to fill in.
            for prop in director.load_schema("analysis")["properties"]:
                self.assertIn(prop, out)
            # The prompt-injection rule and the output contract.
            self.assertIn("data, never instructions", out)
            self.assertIn("WROTE <path>", out)

    def test_direct_prompt_and_verify_accept_cover_only_reel(self) -> None:
        # A reel whose video was downloaded but never cut into keyframes
        # (no ffmpeg on the creator's machine) is still analyzable from
        # its cover image, at low confidence and with no beats. Both
        # halves of the loop have to accept that: `direct-prompt` must
        # still build a prompt, and `verify` must not demand a
        # `structure` the director had no frames to write.
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)

            outliers_path = run_dir / "02-outliers.json"
            doc = store.read_json(outliers_path)
            for reel in doc["selected"]:
                if reel["shortCode"] == "DWN001":
                    reel["frames_status"] = "cover_only"
            store.write_json_atomic(outliers_path, doc)
            # Mock research copied eight sample frames; a real cover-only
            # reel has none, so remove them to exercise that branch.
            for frame in (run_dir / "frames" / "DWN001").glob("f*.jpg"):
                frame.unlink()

            code, out, err = _main(
                ["direct-prompt", "--project", str(project), "--run", "latest",
                 "--shortcode", "DWN001"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn(str((run_dir / "frames" / "DWN001" / "cover.jpg").resolve()), out)
            self.assertIn("no keyframes were extracted for this reel", out)

            analysis_path = _copy_analysis(run_dir, "DWN001")
            analysis = store.read_json(analysis_path)
            analysis["structure"] = []
            analysis["confidence"] = "low"
            store.write_json_atomic(analysis_path, analysis)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "direct", "--shortcode", "DWN001"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn(str(analysis_path.resolve()), out)
            self.assertEqual(store.read_json(analysis_path)["structure"], [])
            self.assertEqual(_reel(run_dir, "DWN001")["analysis_status"], "ok")

    def test_direct_prompt_exit_2_without_creator_md(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _mock_research(project)
            (project / ".contentos" / "creator.md").unlink()

            code, out, err = _main(
                ["direct-prompt", "--project", str(project), "--run", "latest",
                 "--shortcode", "SPA006"]
            )

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertIn("creator.md", err)

    def test_direct_prompt_exit_2_when_run_not_found(self) -> None:
        with temp_project() as project:
            _write_project(project)

            code, out, err = _main(
                ["direct-prompt", "--project", str(project), "--run", "latest",
                 "--shortcode", "SPA006"]
            )

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertTrue(err.strip())


class SynthPromptTests(NoNetworkTestCase):
    def test_synth_prompt_exit_2_without_analyses(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _mock_research(project)

            code, out, err = _main(
                ["synth-prompt", "--project", str(project), "--run", "latest"]
            )

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertTrue(err.strip())

    def test_synth_prompt_prints_prompt_listing_every_analysis(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)
            for shortcode in ANALYZED_SHORTCODES:
                _copy_analysis(run_dir, shortcode)

            code, out, err = _main(
                ["synth-prompt", "--project", str(project), "--run", "latest"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            for shortcode in ANALYZED_SHORTCODES:
                self.assertIn(
                    str((run_dir / "03-analyses" / f"{shortcode}.json").resolve()), out
                )
            for heading in director.PATTERN_HEADINGS:
                self.assertIn(f"## {heading}", out)
            self.assertIn(str((run_dir / "03-patterns.md").resolve()), out)


class VerifyDirectTests(NoNetworkTestCase):
    def test_verify_direct_passes_fixture_and_rejects_bad_enum(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)
            analysis_path = _copy_analysis(run_dir, "SPA006")

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "direct", "--shortcode", "SPA006"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn("ok ", out)
            self.assertIn(str(analysis_path.resolve()), out)
            # The coerced analysis is rewritten in place and still valid.
            rewritten = store.read_json(analysis_path)
            self.assertEqual(director.validate_analysis(rewritten), [])
            self.assertEqual(_reel(run_dir, "SPA006")["analysis_status"], "ok")

            # An analysis whose enums are invented and whose required prose is
            # missing is rejected. `coerce_analysis` repairs the enums on its
            # own (design spec: "unknown enums -> other/none/unknown"), so what
            # survives to fail verification is the prose coercion cannot invent.
            broken = {
                "hook_type": "banana",
                "format": "montage",
                "confidence": "certain",
                "brief_title": "",
                "why_it_worked": "   ",
                "transferable_mechanism": "",
                "adaptation": "",
                "avoid": "",
                "structure": [],
            }
            broken_path = run_dir / "03-analyses" / "HAB005.json"
            broken_path.write_text(json.dumps(broken), encoding="utf-8")

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "direct", "--shortcode", "HAB005"]
            )

            self.assertEqual(code, codes.EXIT_VERIFY)
            self.assertEqual(out, "")
            for field in ("brief_title", "why_it_worked", "transferable_mechanism",
                          "adaptation", "avoid", "structure"):
                self.assertIn(field, err)
            # A rejected analysis is never half-repaired on disk.
            self.assertEqual(store.read_json(broken_path)["hook_type"], "banana")
            self.assertEqual(_reel(run_dir, "HAB005")["analysis_status"], "failed")

    def test_verify_direct_coerces_unknown_enum_in_place(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)
            analysis_path = _copy_analysis(run_dir, "DWN006")
            analysis = store.read_json(analysis_path)
            analysis["hook_type"] = "banana"
            analysis["score_convertible"] = 99
            store.write_json_atomic(analysis_path, analysis)

            code, _out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "direct", "--shortcode", "DWN006"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            rewritten = store.read_json(analysis_path)
            self.assertEqual(rewritten["hook_type"], "other")
            self.assertEqual(rewritten["score_convertible"], 10)

    def test_verify_direct_exit_7_when_analysis_file_is_missing(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _mock_research(project)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "direct", "--shortcode", "SPA006"]
            )

            self.assertEqual(code, codes.EXIT_VERIFY)
            self.assertEqual(out, "")
            self.assertIn("SPA006.json", err)

    def test_verify_direct_exit_2_without_shortcode(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _mock_research(project)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest", "--stage", "direct"]
            )

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertIn("--shortcode", err)

    def test_verify_write_and_qa_stages_are_wired(self) -> None:
        # Task 16 implements these stages for real (lib/agents.py). A
        # brief that was ranked but has no script and no QA file yet
        # still gets a real, non-stub answer from each: verify --stage
        # write reads 04-scripts/<id>.r<N>.md directly and reports the
        # missing file itself (exit 7 -- design spec, "Verification":
        # "verify --stage write --brief B01 (exit 7 until a script
        # exists)"). verify --stage qa first has to pick a revision to
        # check when none is given, and with no script written at all
        # there is none to pick, so that one is a usage error (exit 2)
        # naming the brief rather than a path.
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)
            code, _out, _err = _main(
                ["rank", "--project", str(project), "--run", "latest", "--mock"]
            )
            self.assertEqual(code, codes.EXIT_OK)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "write", "--brief", "B01"]
            )
            self.assertEqual(code, codes.EXIT_VERIFY)
            self.assertEqual(out, "")
            self.assertNotIn("not implemented", err)
            self.assertIn(str((run_dir / "04-scripts" / "B01.r0.md").resolve()), err)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest",
                 "--stage", "qa", "--brief", "B01"]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertNotIn("not implemented", err)
            self.assertIn("B01", err)


class VerifySynthTests(NoNetworkTestCase):
    def test_verify_synth_passes_fixture_patterns(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)
            patterns_path = run_dir / "03-patterns.md"
            shutil.copyfile(PATTERNS_FIXTURE, patterns_path)

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest", "--stage", "synth"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(err, "")
            self.assertIn(str(patterns_path.resolve()), out)

    def test_verify_synth_exit_7_on_missing_headings(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)
            (run_dir / "03-patterns.md").write_text(
                "# Patterns\n\n## Proven hooks\n", encoding="utf-8"
            )

            code, out, err = _main(
                ["verify", "--project", str(project), "--run", "latest", "--stage", "synth"]
            )

            self.assertEqual(code, codes.EXIT_VERIFY)
            self.assertEqual(out, "")
            self.assertIn("Recurring formats", err)


class RankTests(NoNetworkTestCase):
    def test_rank_mock_writes_five_briefs_and_md(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)

            code, out, err = _main(
                ["rank", "--project", str(project), "--run", "latest", "--mock"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            summary = json.loads(out)
            self.assertEqual(summary["run_id"], run_dir.name)
            self.assertEqual(summary["analyzed"], 5)
            self.assertEqual(summary["briefs"], 5)

            # --mock seeds the analyses and the patterns synthesis itself.
            for shortcode in ANALYZED_SHORTCODES:
                self.assertTrue((run_dir / "03-analyses" / f"{shortcode}.json").exists())
            self.assertEqual(director.verify_patterns(run_dir / "03-patterns.md"), [])

            briefs_doc = store.read_json(run_dir / "03-briefs.json")
            briefs = briefs_doc["briefs"]
            self.assertEqual(len(briefs), 5)
            self.assertEqual(
                [brief["brief_id"] for brief in briefs],
                ["B01", "B02", "B03", "B04", "B05"],
            )
            self.assertEqual(sorted(brief["shortCode"] for brief in briefs),
                             sorted(ANALYZED_SHORTCODES))
            self.assertTrue(briefs_doc["ranked_at"])
            self.assertEqual(briefs_doc["analyzed"], 5)

            # dailywins is the one format account (fixtures/setup-answers.sample.json),
            # so its three reels (DWN001, DWN003, DWN006) are format-kind and
            # the other two (SPA006, HAB005) are niche. With the default
            # max_format_briefs (2) and only 5 candidates for 5 slots, the
            # cap never actually excludes anyone: the order is exactly the
            # brief_score sort, format and niche interleaved by score.
            by_shortcode = {brief["shortCode"]: brief for brief in briefs}
            for shortcode in ("DWN001", "DWN003", "DWN006"):
                self.assertEqual(by_shortcode[shortcode]["source_kind"], "format")
            for shortcode in ("SPA006", "HAB005"):
                self.assertEqual(by_shortcode[shortcode]["source_kind"], "niche")
            self.assertEqual(
                [brief["shortCode"] for brief in briefs],
                ["DWN006", "HAB005", "DWN001", "SPA006", "DWN003"],
            )

            briefs_md = (run_dir / "briefs.md").read_text(encoding="utf-8")
            headings = [line for line in briefs_md.splitlines() if line.startswith("## B")]
            self.assertEqual(len(headings), 5)
            for index, brief in enumerate(briefs, start=1):
                self.assertIn(f"## B{index:02d}: {brief['brief_title']}", briefs_md)
            self.assertNotIn("—", briefs_md)

            # Ranked highest first, and run.json records the stage.
            scores = [brief["brief_score"] for brief in briefs]
            self.assertEqual(scores, sorted(scores, reverse=True))
            stage = store.read_json(run_dir / "run.json")["stages"]["direct"]
            self.assertEqual(stage["status"], "ok")
            self.assertEqual(stage["analyzed"], 5)
            self.assertEqual(stage["briefs"], 5)
            self.assertTrue(stage["finished_at"])

    def test_rank_mock_carries_fixture_specifics_and_steps_into_briefs(self) -> None:
        # DWN006's fixture analysis carries `specifics` and `steps`; the
        # other four predate them. Both kinds must rank, and only the one
        # with specifics prints a Specifics list in briefs.md.
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)

            code, _out, _err = _main(
                ["rank", "--project", str(project), "--run", "latest", "--mock"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            briefs = store.read_json(run_dir / "03-briefs.json")["briefs"]
            by_shortcode = {brief["shortCode"]: brief for brief in briefs}

            fixture = json.loads(
                (ANALYSES_FIXTURES_DIR / "DWN006.json").read_text(encoding="utf-8")
            )
            self.assertTrue(fixture["specifics"])
            self.assertTrue(fixture["steps"])
            self.assertEqual(by_shortcode["DWN006"]["specifics"], fixture["specifics"])
            self.assertEqual(by_shortcode["DWN006"]["steps"], fixture["steps"])
            for shortcode in ("DWN001", "DWN003", "SPA006", "HAB005"):
                self.assertEqual(by_shortcode[shortcode]["specifics"], [])
                self.assertEqual(by_shortcode[shortcode]["steps"], [])

            briefs_md = (run_dir / "briefs.md").read_text(encoding="utf-8")
            self.assertEqual(briefs_md.count("Specifics:"), 1)
            self.assertEqual(briefs_md.count("Steps:"), 1)
            self.assertEqual(briefs_md.count("Bet: "), 5)
            self.assertNotIn("Hypothesis:", briefs_md)
            first_specific = fixture["specifics"][0]
            self.assertIn(f"- {first_specific['name']} ({first_specific['kind']}", briefs_md)
            self.assertNotIn("—", briefs_md)

    def test_rank_mock_applies_risk_cap_and_low_confidence_penalty(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)

            code, _out, _err = _main(
                ["rank", "--project", str(project), "--run", "latest", "--mock"]
            )
            self.assertEqual(code, codes.EXIT_OK)

            briefs = {
                brief["shortCode"]: brief
                for brief in store.read_json(run_dir / "03-briefs.json")["briefs"]
            }
            for shortcode, brief in briefs.items():
                analysis = store.read_json(run_dir / "03-analyses" / f"{shortcode}.json")
                self.assertEqual(
                    brief["brief_score"], director.brief_score(analysis, _reel(run_dir, shortcode))
                )
            # The fixtures exercise both adjustments: one capped analysis and
            # one low-confidence analysis.
            capped = [b for b in briefs.values() if "copyrighted_media" in b["risk_flags"]]
            self.assertTrue(capped)
            self.assertLessEqual(capped[0]["brief_score"], 4.0)
            low = [b for b in briefs.values() if b["confidence"] == "low"]
            self.assertTrue(low)

    def test_rank_excludes_reels_without_analysis(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)
            kept = ["DWN006", "SPA006"]
            for shortcode in kept:
                _copy_analysis(run_dir, shortcode)

            code, out, _err = _main(
                ["rank", "--project", str(project), "--run", "latest"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            summary = json.loads(out)
            self.assertEqual(summary["analyzed"], 2)
            self.assertEqual(summary["briefs"], 2)

            briefs_doc = store.read_json(run_dir / "03-briefs.json")
            self.assertEqual(
                sorted(brief["shortCode"] for brief in briefs_doc["briefs"]), sorted(kept)
            )
            skipped = {entry["shortCode"]: entry["reason"] for entry in briefs_doc["skipped"]}
            for reel in _selected(run_dir):
                shortcode = reel["shortCode"]
                if shortcode not in kept:
                    self.assertIn(shortcode, skipped)
                    self.assertTrue(skipped[shortcode])

    def test_rank_skips_an_invalid_analysis_and_names_it(self) -> None:
        with temp_project() as project:
            _write_project(project)
            run_dir = _mock_research(project)
            _copy_analysis(run_dir, "DWN006")
            (run_dir / "03-analyses" / "SPA006.json").write_text("not json", encoding="utf-8")

            code, out, err = _main(
                ["rank", "--project", str(project), "--run", "latest"]
            )

            self.assertEqual(code, codes.EXIT_OK)
            self.assertIn("SPA006", err)
            summary = json.loads(out)
            self.assertEqual(summary["analyzed"], 1)
            skipped = {entry["shortCode"]: entry["reason"] for entry in summary["skipped"]}
            self.assertIn("SPA006", skipped)

    def test_rank_exit_2_when_nothing_was_analyzed(self) -> None:
        with temp_project() as project:
            _write_project(project)
            _mock_research(project)

            code, out, err = _main(["rank", "--project", str(project), "--run", "latest"])

            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(out, "")
            self.assertTrue(err.strip())


if __name__ == "__main__":
    unittest.main()
