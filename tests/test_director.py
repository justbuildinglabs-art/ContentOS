"""Tests for lib/director.py: Stage 2 -- direct, and the Stage 4 qa schema.

Task 13 adds the two JSON Schema documents (`schemas/analysis.schema.json`,
`schemas/qa.schema.json`), a small stdlib validator (`validate_against`,
`validate_analysis`, `coerce_analysis`), the two subagent dispatch prompt
builders (`build_director_prompt`, `build_synth_prompt`), the patterns-file
checker (`verify_patterns`), and the deterministic ranking
(`brief_score`, `rank_briefs`, `render_briefs_md`). See the design spec's
"Stage 2 -- direct" and "Stage 4 -- qa" sections and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-13-brief.md` for the
exact interface. Task 14 wires these into the `contentos.py` CLI
(`direct-prompt`, `synth-prompt`, `rank`, `verify --stage direct|synth`);
nothing here exercises that CLI layer.

`rank_briefs` needs an absolute `frames_dir`/`analysis_path` per brief (the
design spec's ranking output contract), which is not derivable from
`analyses`/`reels` alone, so it takes `run_dir` as a fourth argument here --
the same run-dir-first convention `build_director_prompt`/`build_synth_prompt`
already use.

Run dirs are built by hand (`_write_run_dir` below) for every test except
`DirectorPromptRealRunTests`, which runs the real `--mock` research pipeline
once so the prompt builder is also proven against genuinely produced
`01-profiles.json` / `02-outliers.json` / frame files, not just hand-rolled
fixtures. NoNetworkTestCase is a second line of defense throughout; nothing
here ever touches the network either way.
"""
from __future__ import annotations

import copy
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tests.helpers import NoNetworkTestCase, REPO_ROOT, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import director, frames, research, store  # noqa: E402
from lib.env import Keys  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
REFERENCES_DIR = REPO_ROOT / "skills" / "contentos" / "references"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _cfg(**overrides: Any) -> Dict[str, Any]:
    """A valid research config dict, defaulted from store.DEFAULT_CONFIG."""
    cfg = copy.deepcopy(store.DEFAULT_CONFIG)
    cfg["competitors"] = ["acct1"]
    cfg.update(overrides)
    return cfg


def _mock_keys() -> Keys:
    """A Keys value with no resolved token, as --mock needs none."""
    return Keys(apify=None, source=None, warnings=[])


def _write_config(project: Path, overrides: Dict[str, Any]) -> None:
    """Write `<project>/.contentos/config.json` with exactly `overrides`."""
    config_dir = project / ".contentos"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(overrides), encoding="utf-8")


def _run_research_capturing(**kwargs: Any) -> Dict[str, Any]:
    """Call research.run_research in-process, swallowing its stdout prints.

    Mirrors test_frames.py's/test_video.py's own helper of the same name,
    duplicated here so this module does not reach into another test
    module's internals.
    """
    kwargs.setdefault("log", lambda _m: None)
    with redirect_stdout(StringIO()):
        return research.run_research(**kwargs)


def _scored_reel(shortcode: str, **extra: Any) -> Dict[str, Any]:
    """A minimal but complete scored 02-outliers.json `selected` reel entry.

    Carries every key `lib/outliers.py`'s `score_reel` adds on top of the
    canonical Reel shape (`lib/instagram.py`), since `build_director_prompt`'s
    metadata block and `rank_briefs`'/`brief_score`'s scoring both read from
    those.
    """
    reel: Dict[str, Any] = {
        "shortCode": shortcode,
        "url": f"https://www.instagram.com/reel/{shortcode}/",
        "ownerUsername": "acct1",
        "timestamp": "2026-08-01T00:00:00+00:00",
        "caption": "a caption",
        "hashtags": ["tag1", "tag2"],
        "mentions": [],
        "duration_s": 20.0,
        "plays": 20000,
        "plays_source": "videoPlayCount",
        "likes": 900,
        "comments": 40,
        "latestComments": [{"ownerUsername": "viewer1", "text": "love this", "likesCount": 5}],
        "musicInfo": None,
        "outlier_ratio": 4.0,
        "reach_ratio": 0.1,
        "engagement_rate": 0.05,
        "viral_proof": 6.0,
        "small_account_proof": False,
        "baseline_confidence": "ok",
        "video_status": "ok",
        "frames_status": "ok",
        "cover_status": "ok",
    }
    reel.update(extra)
    return reel


def _outliers_doc(selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A minimal 02-outliers.json document: only what build_director_prompt reads."""
    return {
        "selected": selected,
        "backfill": [],
        "excluded": [],
        "account_status": {},
        "baselines": {},
    }


def _write_run_dir(
    project_dir: Path,
    shortcode: str,
    owner: str = "acct1",
    followers: Optional[float] = 12000,
    cover_only: bool = False,
    n_frames: int = 8,
    frame_indices: Optional[List[int]] = None,
    **reel_extra: Any,
) -> Path:
    """Build a run dir by hand: just enough for build_director_prompt/rank_briefs.

    Writes `02-outliers.json` (one `selected` reel), `01-profiles.json`
    (one profile, keyed by lowercase `owner`, unless `followers` is None),
    and either placeholder `fNN.jpg` files plus `cover.jpg` (the normal
    case) or just `cover.jpg` (`cover_only=True`, mirroring
    `lib/frames.py`'s `cover_only` `frames_status`). The frame files
    written are `1..n_frames` by default, or exactly `frame_indices` when
    given -- e.g. `[1, 3, 4]` to simulate a mid-sequence extraction
    failure (`lib/frames.py`'s `extract_frames` skips a failed timestamp
    and moves on, so a real run can leave a gap in the numbering).
    """
    run_dir = store.init_run(project_dir, _cfg(), "mock")
    reel = _scored_reel(shortcode, ownerUsername=owner, **reel_extra)
    store.write_json_atomic(run_dir / "02-outliers.json", _outliers_doc([reel]))

    profiles: Dict[str, Any] = {}
    if followers is not None:
        profiles[owner.lower()] = {
            "username": owner,
            "followers": followers,
            "posts": 100,
            "verified": False,
            "private": False,
            "url": f"https://www.instagram.com/{owner}/",
        }
    store.write_json_atomic(run_dir / "01-profiles.json", profiles)

    frame_dir = run_dir / "frames" / shortcode
    frame_dir.mkdir(parents=True, exist_ok=True)
    (frame_dir / "cover.jpg").write_bytes(b"cover-bytes")
    if not cover_only:
        indices = frame_indices if frame_indices is not None else range(1, n_frames + 1)
        for i in indices:
            (frame_dir / f"f{i:02d}.jpg").write_bytes(b"frame-bytes")

    return run_dir


def _valid_analysis_raw() -> Dict[str, Any]:
    """A fully-populated, schema-valid raw analysis dict (pre-coercion)."""
    return {
        "brief_title": "Great hook",
        "hook_spoken": "You have started this four times.",
        "hook_on_screen_text": "AGAIN?",
        "hook_type": "pain_callout",
        "hook_seconds": 2.5,
        "emotion_lead": "recognition",
        "format": "talking_head",
        "structure": [{"frame": 1, "beat": "problem"}, {"frame": 4, "beat": "proof"}],
        "audio": "voiceover",
        "cta": "Try it free today.",
        "topic_shown": "a habit tracker app",
        "why_it_worked": "names the exact frustration",
        "transferable_mechanism": "pain call-out hook",
        "adaptation": "swap habits for budgeting",
        "avoid": "another generic morning routine video",
        "score_scalable": 8,
        "score_convertible": 7,
        "score_fit": 9,
        "risk_flags": ["none"],
        "confidence": "high",
    }


_QA_CHECK_NAMES = (
    "hook_first_3s", "hook_matches_brief", "payoff_present", "consistent_with_profile",
    "no_fabricated_claims", "no_fake_testimonial", "no_restricted_claims", "not_a_clone",
    "cta_present", "brand_voice", "ai_tells",
)

_QA_SCORE_NAMES = (
    "hook_scroll_stop", "hook_specificity", "hook_emotional_charge", "hook_voice_match",
    "hook_differentiation", "body_argument_clarity", "body_emotional_arc", "body_proof_density",
    "body_pacing", "cta_action_clarity", "cta_friction", "cta_momentum", "cta_urgency",
)


def _valid_qa_raw() -> Dict[str, Any]:
    """A fully-populated, schema-valid qa dict."""
    return {
        "brief_id": "B01",
        "revision": 0,
        "verdict": "pass",
        "checks": {name: "pass" for name in _QA_CHECK_NAMES},
        "scores": {name: 8 for name in _QA_SCORE_NAMES},
        "length_check": {"word_count": 70, "word_budget": 75, "within_tolerance": True},
        "filler_cut_list": [],
        "placeholders": [],
        "strongest_line": "You have started this four times.",
        "weakest_lines": [],
        "one_watch_test": "clear enough to follow with sound off",
        "spoken_flow_issues": [],
        "cringe_flags": [],
        "issues": [],
        "summary": "Solid draft, ready to film.",
        "confidence": 8,
    }


# ---------------------------------------------------------------------------
# Schema shape: draft-07, flat, no $ref, every enum exactly as specified.
# ---------------------------------------------------------------------------

_ANALYSIS_ENUMS = {
    "hook_type": [
        "bold_claim", "pain_callout", "contrarian", "story_open", "question",
        "curiosity_gap", "pov", "before_after", "challenge", "other",
    ],
    "emotion_lead": [
        "curiosity", "frustration", "desire", "fear", "awe", "anger", "hope", "humor", "recognition",
    ],
    "format": [
        "talking_head", "screen_demo", "voiceover_broll", "skit", "slideshow_text",
        "ugc_review", "tutorial", "trend_remix", "stitch", "other",
    ],
    "audio": ["voiceover", "original_dialogue", "trending_sound", "music_only", "unknown"],
    "risk_flags": [
        "copyrighted_media", "fake_testimonial_risk", "medical_claim", "financial_claim",
        "minors", "brand_ip", "none",
    ],
    "confidence": ["high", "medium", "low"],
}

_QA_ENUMS = {"verdict": ["pass", "revise", "reject"]}
_QA_ENUMS.update({f"checks.{name}": ["pass", "fail", "na"] for name in _QA_CHECK_NAMES})
_QA_ENUMS["issues.severity"] = ["blocker", "major", "minor"]


def _collect_enums(schema: Dict[str, Any], path: str = "") -> Dict[str, List[str]]:
    """Every `enum` list in `schema`, keyed by its dotted property path.

    An array's own path is reused (unchanged) for its `items`, so
    `risk_flags`' item-level enum is recorded under `risk_flags` itself,
    and `issues[].severity` is recorded under `issues.severity`.
    """
    found: Dict[str, List[str]] = {}
    if "enum" in schema:
        found[path] = list(schema["enum"])
    if schema.get("type") == "object" and "properties" in schema:
        for name, sub in schema["properties"].items():
            sub_path = f"{path}.{name}" if path else name
            found.update(_collect_enums(sub, sub_path))
    if schema.get("type") == "array" and "items" in schema:
        found.update(_collect_enums(schema["items"], path))
    return found


def _assert_no_forbidden_keys(node: Any) -> None:
    """Recursively assert neither `$ref` nor `additionalProperties` appears anywhere."""
    if isinstance(node, dict):
        assert "$ref" not in node, f"$ref found in schema node: {node}"
        assert "additionalProperties" not in node, f"additionalProperties found in schema node: {node}"
        for value in node.values():
            _assert_no_forbidden_keys(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_forbidden_keys(item)


def _schema_depth(schema: Dict[str, Any]) -> int:
    """0 for a leaf (string/integer/number/boolean, nullable or enum, or an
    array of such leaves); 1 + the deepest child for an object, or for an
    array of objects.
    """
    if schema.get("type") == "object" and "properties" in schema:
        child_depths = [_schema_depth(sub) for sub in schema["properties"].values()]
        return 1 + (max(child_depths) if child_depths else 0)
    if schema.get("type") == "array" and "items" in schema:
        return _schema_depth(schema["items"])
    return 0


def _assert_required_lists_every_property(schema: Dict[str, Any]) -> None:
    """Recursively assert every object node's `required` == its own property names."""
    if schema.get("type") == "object" and "properties" in schema:
        prop_names = sorted(schema["properties"].keys())
        required = sorted(schema.get("required", []))
        assert required == prop_names, f"required {required} != properties {prop_names}"
        for sub in schema["properties"].values():
            _assert_required_lists_every_property(sub)
    if schema.get("type") == "array" and "items" in schema:
        _assert_required_lists_every_property(schema["items"])


class SchemaShapeTests(NoNetworkTestCase):
    def test_schemas_are_flat_no_refs_and_list_every_enum(self) -> None:
        analysis_schema = director.load_schema("analysis")
        qa_schema = director.load_schema("qa")

        for schema in (analysis_schema, qa_schema):
            self.assertEqual(schema.get("$schema"), "http://json-schema.org/draft-07/schema#")
            self.assertEqual(schema.get("type"), "object")
            self.assertIn("properties", schema)
            self.assertIn("required", schema)
            _assert_no_forbidden_keys(schema)
            _assert_required_lists_every_property(schema)
            self.assertLessEqual(_schema_depth(schema), 2, schema)

        self.assertEqual(_collect_enums(analysis_schema), _ANALYSIS_ENUMS)
        self.assertEqual(_collect_enums(qa_schema), _QA_ENUMS)

    def test_schema_uses_topic_shown_adaptation_and_score_fit(self) -> None:
        schema = director.load_schema("analysis")
        properties = set(schema["properties"].keys())
        required = set(schema["required"])

        for name in ("topic_shown", "adaptation", "score_fit"):
            self.assertIn(name, properties, schema)
            self.assertIn(name, required, schema)

        for old_name in ("product_or_topic_shown", "adaptation_for_product", "score_product_fit"):
            self.assertNotIn(old_name, properties, schema)
            self.assertNotIn(old_name, required, schema)

        self.assertEqual(schema["properties"]["score_fit"]["type"], "integer")
        self.assertEqual(schema["properties"]["score_fit"]["minimum"], 0)
        self.assertEqual(schema["properties"]["score_fit"]["maximum"], 10)


# ---------------------------------------------------------------------------
# validate_against / validate_analysis
# ---------------------------------------------------------------------------


class ValidateAgainstTests(NoNetworkTestCase):
    def test_validate_flags_missing_required_and_bad_enum(self) -> None:
        schema = director.load_schema("analysis")
        obj = _valid_analysis_raw()
        del obj["brief_title"]
        obj["hook_type"] = "not_a_real_type"

        errors = director.validate_against(schema, obj)

        self.assertTrue(any("brief_title" in e for e in errors), errors)
        self.assertTrue(any("hook_type" in e for e in errors), errors)
        # A fully valid object (also exercises validate_analysis directly).
        self.assertEqual(director.validate_analysis(_valid_analysis_raw()), [])

    def test_validate_reports_path_and_bound_for_out_of_range_score(self) -> None:
        qa_schema = director.load_schema("qa")
        obj = _valid_qa_raw()
        obj["scores"]["hook_scroll_stop"] = 12

        errors = director.validate_against(qa_schema, obj)

        self.assertIn("scores.hook_scroll_stop: 12 above maximum 10", errors)
        self.assertEqual(director.validate_against(qa_schema, _valid_qa_raw()), [])

    def test_validate_flags_wrong_item_type_and_too_many_weakest_lines(self) -> None:
        qa_schema = director.load_schema("qa")
        obj = _valid_qa_raw()
        obj["weakest_lines"] = ["one", "two", "three", "four"]
        obj["issues"] = [{"check_or_score": "cta_urgency", "severity": "not_a_severity", "detail": "d", "fix": "f"}]

        errors = director.validate_against(qa_schema, obj)

        self.assertTrue(any("weakest_lines" in e for e in errors), errors)
        self.assertTrue(any("severity" in e for e in errors), errors)


# ---------------------------------------------------------------------------
# coerce_analysis
# ---------------------------------------------------------------------------


class CoerceAnalysisTests(NoNetworkTestCase):
    def test_coerce_clamps_and_maps_unknowns(self) -> None:
        raw = {
            "brief_title": "Solid hook",
            "hook_type": "not_real",
            "hook_seconds": 99,
            "emotion_lead": "sarcasm",
            "format": "cinematic",
            "structure": [
                {"frame": 1, "beat": "problem"},
                {"frame": "not-an-int", "beat": "bad frame"},
                {"frame": 2, "beat": 5},
                "not-a-dict",
            ],
            "audio": "orchestral",
            "score_scalable": 99,
            "score_convertible": -5,
            "score_fit": 7.6,
            "risk_flags": ["medical_claim", "medical_claim", "bogus_flag"],
            "confidence": "extremely high",
            # hook_spoken, hook_on_screen_text, cta, and the other required
            # strings are omitted entirely.
        }

        coerced = director.coerce_analysis(raw)

        self.assertEqual(coerced["hook_type"], "other")
        self.assertEqual(coerced["format"], "other")
        self.assertEqual(coerced["audio"], "unknown")
        self.assertEqual(coerced["emotion_lead"], "curiosity")
        self.assertEqual(coerced["confidence"], "low")
        self.assertEqual(coerced["hook_seconds"], 10.0)
        self.assertEqual(coerced["score_scalable"], 10)
        self.assertEqual(coerced["score_convertible"], 0)
        self.assertEqual(coerced["score_fit"], 8)
        self.assertEqual(coerced["structure"], [{"frame": 1, "beat": "problem"}])
        self.assertEqual(coerced["risk_flags"], ["medical_claim"])
        self.assertIsNone(coerced["hook_spoken"])
        self.assertIsNone(coerced["hook_on_screen_text"])
        self.assertIsNone(coerced["cta"])
        self.assertEqual(coerced["topic_shown"], "")
        self.assertEqual(coerced["why_it_worked"], "")
        self.assertEqual(coerced["brief_title"], "Solid hook")

        # coerce_analysis never invents extra keys, and always fills every
        # schema property, so the result is always schema-valid.
        schema = director.load_schema("analysis")
        self.assertEqual(set(coerced.keys()), set(schema["properties"].keys()))
        self.assertEqual(director.validate_against(schema, coerced), [])

    def test_coerce_missing_lists_and_empty_risk_flags_become_none(self) -> None:
        coerced = director.coerce_analysis({})

        self.assertEqual(coerced["structure"], [])
        self.assertEqual(coerced["risk_flags"], ["none"])
        self.assertEqual(coerced["hook_seconds"], 3.0)
        self.assertEqual(director.validate_against(director.load_schema("analysis"), coerced), [])

    def test_coerce_drops_none_flag_when_other_flags_present(self) -> None:
        coerced = director.coerce_analysis({"risk_flags": ["none", "brand_ip"]})
        self.assertEqual(coerced["risk_flags"], ["brand_ip"])

    def test_coerce_passes_through_a_fully_valid_analysis_unchanged(self) -> None:
        valid = _valid_analysis_raw()
        coerced = director.coerce_analysis(valid)
        self.assertEqual(coerced, valid)


# ---------------------------------------------------------------------------
# brief_score
# ---------------------------------------------------------------------------


class BriefScoreTests(NoNetworkTestCase):
    def _analysis(self, **overrides: Any) -> Dict[str, Any]:
        base = {
            "score_scalable": 8, "score_convertible": 6, "score_fit": 7,
            "risk_flags": ["none"], "confidence": "high",
        }
        base.update(overrides)
        return base

    def test_brief_score_formula_risk_cap_and_low_confidence_penalty(self) -> None:
        reel = {"viral_proof": 8.0}
        analysis = self._analysis()
        # 0.35*8 + 0.25*6 + 0.20*8 + 0.20*7 = 2.8 + 1.5 + 1.6 + 1.4 = 7.3
        self.assertEqual(director.brief_score(analysis, reel), 7.3)

        risky = self._analysis(risk_flags=["copyrighted_media"])
        # Same formula (7.3), but capped at 4.0.
        self.assertEqual(director.brief_score(risky, reel), 4.0)

        risky_but_already_low = self._analysis(
            score_scalable=2, score_convertible=2, score_fit=2,
            risk_flags=["fake_testimonial_risk"],
        )
        low_reel = {"viral_proof": 1.0}
        # 0.35*1 + 0.25*2 + 0.20*2 + 0.20*2 = 1.65, already under the 4.0
        # cap, so the cap changes nothing.
        self.assertEqual(director.brief_score(risky_but_already_low, low_reel), 1.65)

        low_confidence = self._analysis(confidence="low")
        # 7.3 - 1.0 = 6.3
        self.assertEqual(director.brief_score(low_confidence, reel), 6.3)

        both = self._analysis(risk_flags=["copyrighted_media"], confidence="low")
        # Capped at 4.0, then minus 1.0 = 3.0.
        self.assertEqual(director.brief_score(both, reel), 3.0)

        floor = self._analysis(
            score_scalable=0, score_convertible=0, score_fit=0, confidence="low"
        )
        zero_reel = {"viral_proof": 0.0}
        # 0 - 1.0 = -1.0, clamped to 0.0.
        self.assertEqual(director.brief_score(floor, zero_reel), 0.0)


# ---------------------------------------------------------------------------
# rank_briefs
# ---------------------------------------------------------------------------


class RankBriefsTests(NoNetworkTestCase):
    def _analysis(self, title: str, **overrides: Any) -> Dict[str, Any]:
        raw = {
            "brief_title": title,
            "hook_type": "pain_callout",
            "format": "talking_head",
            "emotion_lead": "recognition",
            "score_scalable": 8,
            "score_convertible": 8,
            "score_fit": 8,
            "risk_flags": ["none"],
            "confidence": "high",
            "adaptation": "swap in our onboarding flow",
            "transferable_mechanism": "pain call-out",
            "why_it_worked": "names the exact frustration",
            "avoid": "another generic demo",
        }
        raw.update(overrides)
        return director.coerce_analysis(raw)

    def test_rank_order_ids_and_hypothesis_line(self) -> None:
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")

            reel_a = _scored_reel("AAA001", viral_proof=9.0, outlier_ratio=5.0)
            reel_b = _scored_reel("BBB001", viral_proof=9.0, outlier_ratio=5.0)  # ties reel_a
            reel_c = _scored_reel("CCC001", viral_proof=2.0, outlier_ratio=1.5)
            reel_no_analysis = _scored_reel("ZZZ001", viral_proof=10.0, outlier_ratio=9.0)

            analyses = {
                "AAA001": self._analysis("A title"),
                "BBB001": self._analysis("B title"),
                "CCC001": self._analysis(
                    "C title", score_scalable=1, score_convertible=1, score_fit=1
                ),
            }
            reels = [reel_a, reel_b, reel_c, reel_no_analysis]

            briefs = director.rank_briefs(analyses, reels, 2, run_dir)

        self.assertEqual([b["brief_id"] for b in briefs], ["B01", "B02"])
        # AAA001 and BBB001 tie on both brief_score and outlier_ratio, so
        # shortCode breaks the tie ascending: "AAA001" sorts first.
        self.assertEqual([b["shortCode"] for b in briefs], ["AAA001", "BBB001"])
        ranked_codes = [b["shortCode"] for b in briefs]
        self.assertNotIn("CCC001", ranked_codes)  # cut by n=2
        self.assertNotIn("ZZZ001", ranked_codes)  # never analyzed

        brief = briefs[0]
        self.assertEqual(brief["viral_proof"], 9.0)
        self.assertEqual(brief["format"], "talking_head")
        self.assertEqual(brief["hook_type"], "pain_callout")
        self.assertEqual(brief["emotion_lead"], "recognition")
        self.assertEqual(brief["risk_flags"], ["none"])
        self.assertEqual(brief["confidence"], "high")
        self.assertEqual(brief["avoid"], "another generic demo")
        self.assertEqual(
            brief["hypothesis"],
            "If we swap in our onboarding flow using the pain call-out hook, "
            "we expect above-baseline plays because names the exact frustration",
        )
        self.assertTrue(Path(brief["frames_dir"]).is_absolute())
        self.assertTrue(Path(brief["analysis_path"]).is_absolute())
        self.assertEqual(Path(brief["frames_dir"]).name, "AAA001")
        self.assertTrue(brief["analysis_path"].replace("\\", "/").endswith("03-analyses/AAA001.json"))
        # None of these reels carry source_kind, so rank_briefs treats them
        # as niche (design spec: "a reel without one counts as niche").
        self.assertEqual(brief["source_kind"], "niche")

    def test_rank_takes_n_and_never_ranks_a_reel_without_an_analysis(self) -> None:
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")
            reels = [_scored_reel(f"S{i:03d}", viral_proof=float(i), outlier_ratio=float(i)) for i in range(1, 6)]
            analyses = {reel["shortCode"]: self._analysis(reel["shortCode"]) for reel in reels[:3]}

            briefs = director.rank_briefs(analyses, reels, 10, run_dir)

        # Only the three analyzed reels are ranked, even though n=10 asked for more.
        self.assertEqual(len(briefs), 3)
        self.assertEqual({b["shortCode"] for b in briefs}, {"S001", "S002", "S003"})

    def _kind_candidates(self) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
        """Five candidates (F1..F3 format, N1..N2 niche) with descending scores.

        Every analysis carries identical scalable/convertible/fit scores,
        so brief_score is monotonic in viral_proof alone -- the candidates
        sort exactly F1 (10), N1 (9), F2 (8), N2 (7), F3 (6), by
        construction, with no tiebreak ambiguity (outlier_ratio mirrors
        viral_proof, shortCode is already unique).
        """
        specs = [
            ("F1", "format", 10.0),
            ("N1", "niche", 9.0),
            ("F2", "format", 8.0),
            ("N2", "niche", 7.0),
            ("F3", "format", 6.0),
        ]
        reels = [
            _scored_reel(code, viral_proof=score, outlier_ratio=score, source_kind=kind)
            for code, kind, score in specs
        ]
        analyses = {code: self._analysis(code) for code, _kind, _score in specs}
        return analyses, reels

    def test_rank_caps_format_briefs_and_fills_when_niche_runs_short(self) -> None:
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")
            analyses, reels = self._kind_candidates()

            # max_format_briefs=1: the walk takes F1 (first format seen,
            # under the cap), N1, N2 (niche, taken freely) -- three briefs
            # for n=4. Niche only had two entries, so the walk falls one
            # short; the remaining slot is filled from the skipped format
            # briefs (F2, F3) in score order, which is F2, not F3. The
            # taken list is then re-sorted by brief_score, so the
            # backfilled F2 (score 8) lands ahead of N2 (score 7) rather
            # than trailing at the end where the backfill appended it.
            briefs = director.rank_briefs(analyses, reels, 4, run_dir, max_format_briefs=1)

        self.assertEqual([b["shortCode"] for b in briefs], ["F1", "N1", "F2", "N2"])
        self.assertEqual([b["brief_id"] for b in briefs], ["B01", "B02", "B03", "B04"])
        self.assertEqual([b["source_kind"] for b in briefs], ["format", "niche", "format", "niche"])
        self.assertNotIn("F3", [b["shortCode"] for b in briefs])

    def test_rank_with_zero_cap_excludes_format_briefs_unless_needed_to_fill(self) -> None:
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")
            analyses, reels = self._kind_candidates()

            # max_format_briefs=0 and n=2: the two niche briefs cover n on
            # their own, so no format brief is ever admitted.
            briefs_niche_only = director.rank_briefs(analyses, reels, 2, run_dir, max_format_briefs=0)
            self.assertEqual([b["shortCode"] for b in briefs_niche_only], ["N1", "N2"])
            self.assertTrue(all(b["source_kind"] == "niche" for b in briefs_niche_only))

            # Same zero cap, but n=3: the niche briefs run out, so the
            # single best-scoring skipped format brief (F1) fills the last
            # slot even though the cap is 0 -- the cap only limits how many
            # are taken freely during the walk, not the backfill. F1 scores
            # highest of the three (10), so the re-sort puts it first
            # rather than leaving it trailing where the backfill appended
            # it.
            briefs_with_fill = director.rank_briefs(analyses, reels, 3, run_dir, max_format_briefs=0)

        self.assertEqual([b["shortCode"] for b in briefs_with_fill], ["F1", "N1", "N2"])
        self.assertEqual(briefs_with_fill[0]["source_kind"], "format")

    def test_rank_resorts_backfilled_briefs_so_none_sits_below_a_lower_score(self) -> None:
        # Two niche briefs (scores 9, 1), three format briefs (8, 7, 6),
        # max_format_briefs=2, n=5. The walk takes N-high (9), F-high (8,
        # under the cap), F-mid (7, under the cap), N-low (1, niche is
        # always taken), then sets F-low (6) aside because the cap is
        # full. That leaves 4 taken for n=5, so F-low backfills the last
        # slot -- appended after N-low, out of score order. Without the
        # re-sort the ids would read 9, 8, 7, 1, 6; with it, a backfilled
        # brief never sits below a lower-scoring one, so the final order
        # is strictly by score: 9, 8, 7, 6, 1 (design spec, "Stage 2 --
        # direct": "then re-sort the taken briefs by brief_score ...").
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")
            specs = [
                ("N9", "niche", 9.0),
                ("N1", "niche", 1.0),
                ("F8", "format", 8.0),
                ("F7", "format", 7.0),
                ("F6", "format", 6.0),
            ]
            reels = [
                _scored_reel(code, viral_proof=score, outlier_ratio=score, source_kind=kind)
                for code, kind, score in specs
            ]
            analyses = {code: self._analysis(code) for code, _kind, _score in specs}

            briefs = director.rank_briefs(analyses, reels, 5, run_dir, max_format_briefs=2)

        self.assertEqual(
            [b["shortCode"] for b in briefs], ["N9", "F8", "F7", "F6", "N1"]
        )
        self.assertEqual(
            [b["brief_id"] for b in briefs], ["B01", "B02", "B03", "B04", "B05"]
        )


# ---------------------------------------------------------------------------
# render_briefs_md
# ---------------------------------------------------------------------------


class RenderBriefsMdTests(NoNetworkTestCase):
    def test_render_briefs_md_includes_required_sections(self) -> None:
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")
            reel = _scored_reel("AAA001", viral_proof=7.0, outlier_ratio=4.0)
            analysis = director.coerce_analysis(
                {
                    "brief_title": "Morning habit callout",
                    "hook_type": "pain_callout",
                    "format": "talking_head",
                    "emotion_lead": "recognition",
                    "score_scalable": 8, "score_convertible": 7, "score_fit": 9,
                    "risk_flags": ["none"], "confidence": "high",
                    "adaptation": "swap habits for budgeting",
                    "transferable_mechanism": "pain call-out",
                    "why_it_worked": "names the exact frustration",
                    "avoid": "another generic morning routine video",
                }
            )
            briefs = director.rank_briefs({"AAA001": analysis}, [reel], 5, run_dir)

            markdown = director.render_briefs_md(briefs)

        self.assertTrue(markdown.startswith("# Briefs"))
        self.assertIn("## B01: Morning habit callout", markdown)
        self.assertIn("acct1", markdown)
        self.assertIn(reel["url"], markdown)
        self.assertIn("pain_callout", markdown)
        self.assertIn("talking_head", markdown)
        self.assertIn("recognition", markdown)
        self.assertIn("swap habits for budgeting", markdown)
        self.assertIn("another generic morning routine video", markdown)
        self.assertIn("If we swap habits for budgeting using the pain call-out hook", markdown)
        self.assertIn(briefs[0]["frames_dir"], markdown)
        self.assertNotIn("—", markdown)  # no em dashes in creator-facing text

    def test_render_briefs_md_prints_source_kind_and_fit(self) -> None:
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")
            niche_reel = _scored_reel("AAA001", viral_proof=7.0, outlier_ratio=4.0, source_kind="niche")
            format_reel = _scored_reel(
                "BBB001", ownerUsername="formatacct", viral_proof=6.0, outlier_ratio=3.0,
                source_kind="format",
            )
            analysis = director.coerce_analysis(
                {
                    "brief_title": "Morning habit callout",
                    "hook_type": "pain_callout",
                    "format": "talking_head",
                    "emotion_lead": "recognition",
                    "score_scalable": 8, "score_convertible": 7, "score_fit": 9,
                    "risk_flags": ["none"], "confidence": "high",
                    "adaptation": "swap habits for budgeting",
                    "transferable_mechanism": "pain call-out",
                    "why_it_worked": "names the exact frustration",
                    "avoid": "another generic morning routine video",
                }
            )
            briefs = director.rank_briefs(
                {"AAA001": analysis, "BBB001": analysis}, [niche_reel, format_reel], 5, run_dir
            )

            markdown = director.render_briefs_md(briefs)

        self.assertIn(f"{niche_reel['url']} (by acct1, niche account)", markdown)
        self.assertIn(f"{format_reel['url']} (by formatacct, format account)", markdown)
        self.assertIn("fit 9", markdown)
        self.assertNotIn("product fit", markdown)


# ---------------------------------------------------------------------------
# build_director_prompt
# ---------------------------------------------------------------------------


class DirectorPromptTests(NoNetworkTestCase):
    def test_director_prompt_lists_frames_in_order_with_handoff_and_contract(self) -> None:
        with temp_project() as project_dir:
            run_dir = _write_run_dir(project_dir, "AAA001", owner="acct1", followers=12000, n_frames=4)
            schema = director.load_schema("analysis")

            prompt = director.build_director_prompt(
                run_dir, "AAA001", REFERENCES_DIR, project_dir / "creator.md", schema
            )

        self.assertIn("HANDOFF TO: content-director FROM: research (stage 1)", prompt)
        self.assertIn("take the inputs below as given", prompt)
        self.assertIn("do not re-score plays or re-select reels", prompt)
        self.assertIn("WROTE <path>", prompt)
        self.assertIn("FAILED <reason>", prompt)
        self.assertNotIn("no keyframes were extracted", prompt)  # this reel is not cover-only

        frame_dir = run_dir / "frames" / "AAA001"
        paths_in_order = [str((frame_dir / f"f{i:02d}.jpg").resolve()) for i in range(1, 5)]
        positions = [prompt.index(p) for p in paths_in_order]
        self.assertEqual(positions, sorted(positions))

        expected_output = str((run_dir / "03-analyses" / "AAA001.json").resolve())
        self.assertIn(expected_output, prompt)
        self.assertIn(str((REFERENCES_DIR / "hooks.md").resolve()), prompt)
        self.assertIn(str((REFERENCES_DIR / "formats.md").resolve()), prompt)
        self.assertIn(str((REFERENCES_DIR / "scoring.md").resolve()), prompt)

    def test_director_prompt_mentions_every_schema_property(self) -> None:
        with temp_project() as project_dir:
            run_dir = _write_run_dir(project_dir, "AAA001")
            schema = director.load_schema("analysis")

            prompt = director.build_director_prompt(
                run_dir, "AAA001", REFERENCES_DIR, project_dir / "creator.md", schema
            )

        for prop_name in schema["properties"]:
            self.assertIn(prop_name, prompt, f"missing schema property {prop_name!r} in prompt")

    def test_director_prompt_never_drops_a_frame_file_when_duration_is_unknown(self) -> None:
        # frame_times(None, 8) only ever returns its 4 fixed early points
        # (see lib/frames.py), fewer than the 8 real files this run dir
        # has on disk -- every file must still be listed, just without a
        # timestamp past the 4th.
        with temp_project() as project_dir:
            run_dir = _write_run_dir(project_dir, "AAA001", n_frames=8, duration_s=None)
            schema = director.load_schema("analysis")

            prompt = director.build_director_prompt(
                run_dir, "AAA001", REFERENCES_DIR, project_dir / "creator.md", schema
            )

        frame_dir = run_dir / "frames" / "AAA001"
        for i in range(1, 9):
            self.assertIn(str((frame_dir / f"f{i:02d}.jpg").resolve()), prompt)
        self.assertIn("timestamp unknown", prompt)

    def test_director_prompt_keeps_frame_timestamps_when_a_frame_is_missing(self) -> None:
        # extract_frames (lib/frames.py) skips a timestamp whose ffmpeg call
        # fails and moves on to the next one, so a real run can land
        # f01, f03, f04, ... on disk with no f02. Pairing timestamps to
        # files by list position (rather than each file's own parsed
        # index) would mislabel every file after the gap with the
        # timestamp meant for its missing neighbor.
        with temp_project() as project_dir:
            run_dir = _write_run_dir(
                project_dir, "AAA001", duration_s=20.0, frame_indices=[1, 3, 4, 5, 6, 7, 8]
            )
            schema = director.load_schema("analysis")

            prompt = director.build_director_prompt(
                run_dir, "AAA001", REFERENCES_DIR, project_dir / "creator.md", schema
            )

        frame_dir = run_dir / "frames" / "AAA001"
        expected_times = frames.frame_times(20.0, 8)
        f03_line = f"{(frame_dir / 'f03.jpg').resolve()} at {expected_times[2]}s"
        f08_line = f"{(frame_dir / 'f08.jpg').resolve()} at {expected_times[7]}s"

        self.assertIn(f03_line, prompt)
        self.assertIn(f08_line, prompt)
        self.assertNotIn("f02.jpg", prompt)

    def test_director_prompt_labels_frames_by_configured_frame_count(self) -> None:
        # frame_times spreads its slots evenly across the reel, so the
        # timestamp for f05 depends on how many frames were *asked for*,
        # not on how many landed on disk. When the highest-numbered
        # frames are the ones ffmpeg dropped, deriving the count from the
        # files present would shift every label after the fixed points.
        with temp_project() as project_dir:
            run_dir = _write_run_dir(
                project_dir, "AAA001", duration_s=20.0, frame_indices=[1, 2, 3, 4, 5, 6]
            )
            schema = director.load_schema("analysis")

            prompt = director.build_director_prompt(
                run_dir, "AAA001", REFERENCES_DIR, project_dir / "creator.md", schema
            )

        frame_dir = run_dir / "frames" / "AAA001"
        expected_times = frames.frame_times(20.0, 8)
        f05_line = f"{(frame_dir / 'f05.jpg').resolve()} at {expected_times[4]}s"

        self.assertIn(f05_line, prompt)

    def test_director_prompt_raises_keyerror_when_reel_not_selected(self) -> None:
        with temp_project() as project_dir:
            run_dir = _write_run_dir(project_dir, "AAA001")
            schema = director.load_schema("analysis")

            with self.assertRaises(KeyError):
                director.build_director_prompt(
                    run_dir, "NOT-THERE", REFERENCES_DIR, project_dir / "creator.md", schema
                )

    def test_director_prompt_cover_only_notes_low_confidence(self) -> None:
        with temp_project() as project_dir:
            run_dir = _write_run_dir(project_dir, "AAA001", cover_only=True)
            schema = director.load_schema("analysis")

            prompt = director.build_director_prompt(
                run_dir, "AAA001", REFERENCES_DIR, project_dir / "creator.md", schema
            )

        self.assertIn("no keyframes were extracted", prompt)
        self.assertIn("cover-only", prompt.lower())
        cover_path = str((run_dir / "frames" / "AAA001" / "cover.jpg").resolve())
        self.assertIn(cover_path, prompt)
        self.assertNotIn("f01.jpg", prompt)

    def test_director_prompt_states_source_kind_and_reads_creator_md(self) -> None:
        with temp_project() as project_dir:
            format_run_dir = _write_run_dir(project_dir, "FMT001", source_kind="format")
            niche_run_dir = _write_run_dir(project_dir, "NCH001", source_kind="niche")
            no_kind_run_dir = _write_run_dir(project_dir, "NOK001")
            schema = director.load_schema("analysis")
            creator_md = project_dir / "creator.md"

            format_prompt = director.build_director_prompt(
                format_run_dir, "FMT001", REFERENCES_DIR, creator_md, schema
            )
            niche_prompt = director.build_director_prompt(
                niche_run_dir, "NCH001", REFERENCES_DIR, creator_md, schema
            )
            no_kind_prompt = director.build_director_prompt(
                no_kind_run_dir, "NOK001", REFERENCES_DIR, creator_md, schema
            )

        self.assertIn("Source kind: format", format_prompt)
        self.assertIn("Source kind: niche", niche_prompt)
        # A reel without source_kind at all still gets a line, defaulting
        # to niche (mirrors rank_briefs' own default).
        self.assertIn("Source kind: niche", no_kind_prompt)

        for prompt in (format_prompt, niche_prompt):
            self.assertIn("score_fit", prompt)
            self.assertIn("adaptation", prompt)
            # One sentence each on what source kind means for score_fit
            # and for adaptation.
            self.assertIn("topic overlaps", prompt)
            self.assertIn("transfers", prompt)
            self.assertIn("10 to 20 percent", prompt)

        self.assertIn(f"Creator profile: {creator_md.resolve()}", format_prompt)
        self.assertNotIn("product", format_prompt.lower())
        self.assertNotIn("founder", format_prompt.lower())


class DirectorPromptRealRunTests(NoNetworkTestCase):
    def test_prompt_over_a_real_mock_research_run(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp", "habitlab", "dailywins"]})
            cfg = store.load_config(project_dir)

            result = _run_research_capturing(
                project=project_dir, cfg=cfg, keys=_mock_keys(), mock=True, yes=True,
                estimate_only=False, resume=None,
            )
            run_dir = Path(result["run_dir"])
            schema = director.load_schema("analysis")

            # DWN006 is the fixture's top-ranked selected reel with a real
            # video (video_status "ok") and full 8-frame extraction under
            # --mock (frames_status "ok") -- see task-13-report.md.
            prompt = director.build_director_prompt(
                run_dir, "DWN006", REFERENCES_DIR, project_dir / "creator.md", schema
            )

        self.assertIn("DWN006", prompt)
        self.assertIn("dailywins", prompt)
        self.assertIn("950000", prompt)  # dailywins' followers, from 01-profiles.json
        self.assertIn(str((run_dir / "frames" / "DWN006" / "f01.jpg").resolve()), prompt)


# ---------------------------------------------------------------------------
# build_synth_prompt
# ---------------------------------------------------------------------------


class SynthPromptTests(NoNetworkTestCase):
    def test_synth_prompt_lists_every_analysis_and_headings(self) -> None:
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")
            analyses_dir = run_dir / "03-analyses"
            analyses_dir.mkdir(parents=True)
            for shortcode in ("BBB001", "AAA001", "CCC001"):
                store.write_json_atomic(
                    analyses_dir / f"{shortcode}.json",
                    director.coerce_analysis({"brief_title": shortcode}),
                )

            prompt = director.build_synth_prompt(run_dir, REFERENCES_DIR, project_dir / "creator.md")

        expected_paths = [
            str((analyses_dir / f"{sc}.json").resolve()) for sc in ("AAA001", "BBB001", "CCC001")
        ]
        positions = [prompt.index(p) for p in expected_paths]
        self.assertEqual(positions, sorted(positions))  # sorted (alphabetical) order

        for heading in director.PATTERN_HEADINGS:
            self.assertIn(f"## {heading}", prompt)

        self.assertIn("HANDOFF TO: content-director (synthesis) FROM: content-director (per-reel analyses)", prompt)
        self.assertIn(str((project_dir / "creator.md").resolve()), prompt)
        self.assertIn(str((REFERENCES_DIR / "hooks.md").resolve()), prompt)
        self.assertIn(str((REFERENCES_DIR / "formats.md").resolve()), prompt)
        self.assertIn("WROTE <path>", prompt)
        self.assertIn("FAILED <reason>", prompt)
        self.assertIn(str((run_dir / "03-patterns.md").resolve()), prompt)
        self.assertNotIn("—", prompt)  # no em dashes

    def test_synth_prompt_says_for_this_creator(self) -> None:
        with temp_project() as project_dir:
            run_dir = store.init_run(project_dir, _cfg(), "mock")
            analyses_dir = run_dir / "03-analyses"
            analyses_dir.mkdir(parents=True)
            store.write_json_atomic(
                analyses_dir / "AAA001.json", director.coerce_analysis({"brief_title": "AAA001"})
            )

            prompt = director.build_synth_prompt(run_dir, REFERENCES_DIR, project_dir / "creator.md")

        self.assertIn("for this creator", prompt)
        self.assertIn("wants more from this creator or what they promote", prompt)
        self.assertNotIn("product", prompt.lower())
        self.assertNotIn("founder", prompt.lower())


# ---------------------------------------------------------------------------
# PATTERN_HEADINGS / verify_patterns
# ---------------------------------------------------------------------------


class VerifyPatternsTests(NoNetworkTestCase):
    def _valid_patterns_text(self) -> str:
        return "\n".join(
            f"## {heading}\n\nSome content about {heading.lower()}.\n" for heading in director.PATTERN_HEADINGS
        )

    def test_verify_patterns_requires_five_headings(self) -> None:
        self.assertEqual(
            director.PATTERN_HEADINGS,
            [
                "Proven hooks",
                "Recurring formats",
                "Saturated angles to avoid",
                "Structural recommendation",
                "Language bank",
            ],
        )

        with temp_project() as project_dir:
            missing_file = project_dir / "does-not-exist.md"
            self.assertTrue(director.verify_patterns(missing_file))

            empty_file = project_dir / "empty.md"
            empty_file.write_text("   \n", encoding="utf-8")
            self.assertTrue(director.verify_patterns(empty_file))

            valid_file = project_dir / "valid.md"
            valid_file.write_text(self._valid_patterns_text(), encoding="utf-8")
            self.assertEqual(director.verify_patterns(valid_file), [])

            missing_heading_file = project_dir / "missing-heading.md"
            text_without_language_bank = "\n".join(
                f"## {h}\n\ncontent\n" for h in director.PATTERN_HEADINGS if h != "Language bank"
            )
            missing_heading_file.write_text(text_without_language_bank, encoding="utf-8")
            errors = director.verify_patterns(missing_heading_file)
            self.assertTrue(any("Language bank" in e for e in errors), errors)

            out_of_order_file = project_dir / "out-of-order.md"
            reversed_headings = list(reversed(director.PATTERN_HEADINGS))
            out_of_order_text = "\n".join(f"## {h}\n\ncontent\n" for h in reversed_headings)
            out_of_order_file.write_text(out_of_order_text, encoding="utf-8")
            errors = director.verify_patterns(out_of_order_file)
            self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
