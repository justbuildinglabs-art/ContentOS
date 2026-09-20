"""Tests for lib/outliers.py: baselines, scoring, and outlier selection.

Task 10's `research` command (and later `viral_proof` in brief ranking)
trusts this module's numbers, so these tests pin the exact formulas from
the design spec's "Stage 1 -- research" steps 4-6, not just "it returns
something". `test_fixture_outliers_selected` also runs the two
`fixtures/apify_*_sample.json` files (Task 8) through the real
`instagram.normalize_dataset` -> `compute_baselines` -> `score_reel` ->
`select_outliers` pipeline, so a change to either module or fixture that
breaks the fixtures' designed-outlier story fails loudly here.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from typing import Any, Dict

from tests.helpers import NoNetworkTestCase, REPO_ROOT

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import instagram, outliers  # noqa: E402
from lib.store import DEFAULT_CONFIG  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"

# Matches test_instagram.py's convention: tests needing "now" for lookback
# math use 2026-09-16 UTC explicitly rather than the real current time.
NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _reel(**overrides: Any) -> Dict[str, Any]:
    """A minimal, valid canonical Reel dict; override fields per test.

    Mirrors `instagram.normalize_reel`'s output shape (Task 8) since
    that is what `compute_baselines`/`score_reel` consume -- see the
    design spec's "Stage 1 -- research" step 3 for the canonical Reel
    fields. `plays` defaults comfortably above DEFAULT_CONFIG's
    `min_plays` and `timestamp` defaults comfortably inside its
    `lookback_days`, so an unmodified `_reel()` survives every
    `select_outliers` filter unless a test deliberately breaks one.
    """
    reel: Dict[str, Any] = {
        "shortCode": "SC000",
        "url": "https://www.instagram.com/reel/SC000/",
        "ownerUsername": "someaccount",
        "timestamp": "2026-09-01T12:00:00+00:00",
        "caption": "",
        "hashtags": [],
        "mentions": [],
        "plays": 10000,
        "plays_source": "videoPlayCount",
        "likes": 500,
        "comments": 50,
        "duration_s": 20.0,
        "videoUrl": "https://example.invalid/videos/SC000.mp4",
        "displayUrl": "https://example.invalid/covers/SC000.jpg",
        "isPinned": False,
        "latestComments": [],
        "musicInfo": None,
    }
    reel.update(overrides)
    return reel


def _scored_reel(**overrides: Any) -> Dict[str, Any]:
    """A minimal, valid already-scored Reel dict for select_outliers tests.

    Layers the keys `score_reel` adds on top of `_reel`'s canonical
    shape, defaulted so an unmodified `_scored_reel()` survives every
    filter; override per test (e.g. `plays=None` for `no_plays`).
    """
    reel = _reel()
    reel.update(
        {
            "baseline_median": 1000.0,
            "baseline_n": 10,
            "baseline_confidence": "ok",
            "baseline_metric": "plays",
            "outlier_ratio": 1.0,
            "reach_ratio": None,
            "engagement_rate": None,
            "small_account_proof": False,
            "viral_proof": 0.0,
        }
    )
    reel.update(overrides)
    return reel


def _cfg(**overrides: Any) -> Dict[str, Any]:
    """DEFAULT_CONFIG with per-test overrides.

    score_reel/select_outliers read several config keys (lookback_days,
    min_plays, max_per_account, top_k_videos, backfill_pool,
    small_account_followers, outlier_threshold); starting from the real
    defaults means a test only has to name the keys it cares about.
    """
    cfg = dict(DEFAULT_CONFIG)
    # 0.3.0 selection (90-day window, no ratio floor) so pre-0.4.0 tests
    # keep their meaning; 0.4.0 tests override these explicitly.
    cfg.update({"lookback_days": 90, "min_outlier_ratio": 0.0})
    cfg.update(overrides)
    return cfg


def _load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class ComputeBaselinesTests(NoNetworkTestCase):
    def test_median_uses_only_reels_with_plays(self) -> None:
        reels = [
            _reel(shortCode="P1", ownerUsername="acct", plays=1000),
            _reel(shortCode="P2", ownerUsername="acct", plays=3000),
            _reel(shortCode="P3", ownerUsername="acct", plays=2000),
            # No plays at all: must not count toward n or the median.
            _reel(shortCode="P4", ownerUsername="acct", plays=None, plays_source=None),
        ]

        baselines = outliers.compute_baselines(reels, min_n=3)

        self.assertEqual(
            baselines["acct"],
            outliers.Baseline(median=2000.0, n=3, confidence="ok", metric="plays"),
        )

    def test_low_n_blends_pooled_median_and_marks_low(self) -> None:
        min_n = 8
        reels = (
            [_reel(shortCode=f"S{i}", ownerUsername="small", plays=1000) for i in range(4)]
            + [_reel(shortCode=f"B{i}", ownerUsername="big", plays=9000) for i in range(8)]
        )

        baselines = outliers.compute_baselines(reels, min_n=min_n)

        # pooled = median of all 12 plays values (four 1000s, eight 9000s) = 9000
        # blended = (4*1000 + (8-4)*9000) / 8 = 5000.0
        self.assertEqual(
            baselines["small"],
            outliers.Baseline(median=5000.0, n=4, confidence="low", metric="plays"),
        )
        # The account that already meets min_n is "ok", not blended.
        self.assertEqual(
            baselines["big"],
            outliers.Baseline(median=9000.0, n=8, confidence="ok", metric="plays"),
        )

    def test_none_confidence_under_three_reels(self) -> None:
        reels = [
            _reel(shortCode="O1", ownerUsername="one_reel", plays=800),
            _reel(shortCode="T1", ownerUsername="two_reels", plays=500),
            _reel(shortCode="T2", ownerUsername="two_reels", plays=700),
        ]

        baselines = outliers.compute_baselines(reels, min_n=8)

        self.assertEqual(
            baselines["one_reel"],
            outliers.Baseline(median=800.0, n=1, confidence="none", metric="plays"),
        )
        self.assertEqual(
            baselines["two_reels"],
            outliers.Baseline(median=600.0, n=2, confidence="none", metric="plays"),
        )

    def test_likes_fallback_when_account_has_no_plays(self) -> None:
        reels = [
            _reel(
                shortCode="L1",
                ownerUsername="likes_only",
                plays=None,
                plays_source=None,
                likes=100,
            ),
            _reel(
                shortCode="L2",
                ownerUsername="likes_only",
                plays=None,
                plays_source=None,
                likes=200,
            ),
            # Nothing at all: no plays anywhere, and no likes either.
            _reel(
                shortCode="N1",
                ownerUsername="nothing",
                plays=None,
                plays_source=None,
                likes=None,
            ),
            _reel(
                shortCode="N2",
                ownerUsername="nothing",
                plays=None,
                plays_source=None,
                likes=None,
            ),
        ]

        baselines = outliers.compute_baselines(reels, min_n=8)

        self.assertEqual(
            baselines["likes_only"],
            outliers.Baseline(median=150.0, n=0, confidence="low", metric="likes"),
        )
        self.assertEqual(
            baselines["nothing"],
            outliers.Baseline(median=0.0, n=0, confidence="none", metric="plays"),
        )


class ScoreReelTests(NoNetworkTestCase):
    def test_outlier_and_reach_ratios(self) -> None:
        cfg = _cfg()
        plays_baseline = outliers.Baseline(median=1000.0, n=10, confidence="ok", metric="plays")
        reel = _reel(shortCode="R1", plays=4000, likes=200, comments=50)
        profile = {"followers": 20000}

        scored = outliers.score_reel(reel, plays_baseline, profile, cfg)

        self.assertEqual(scored["outlier_ratio"], 4.0)
        self.assertEqual(scored["reach_ratio"], 0.2)
        self.assertEqual(scored["baseline_median"], 1000.0)
        self.assertEqual(scored["baseline_n"], 10)
        self.assertEqual(scored["baseline_confidence"], "ok")
        self.assertEqual(scored["baseline_metric"], "plays")
        # A copy is returned; the input reel is untouched.
        self.assertNotIn("outlier_ratio", reel)

        # When the baseline's metric is "likes", the ratio numerator is
        # `likes`, not `plays` -- but reach_ratio always keys off the
        # literal `plays` field regardless of the baseline's metric.
        likes_baseline = outliers.Baseline(median=100.0, n=0, confidence="low", metric="likes")
        likes_reel = _reel(shortCode="R2", plays=None, plays_source=None, likes=400, comments=5)
        likes_scored = outliers.score_reel(likes_reel, likes_baseline, {"followers": 5000}, cfg)

        self.assertEqual(likes_scored["outlier_ratio"], 4.0)
        self.assertEqual(likes_scored["baseline_metric"], "likes")
        self.assertIsNone(likes_scored["reach_ratio"])

    def test_reach_ratio_none_without_followers(self) -> None:
        cfg = _cfg()
        baseline = outliers.Baseline(median=1000.0, n=10, confidence="ok", metric="plays")
        reel = _reel(plays=5000)

        self.assertIsNone(outliers.score_reel(reel, baseline, None, cfg)["reach_ratio"])
        self.assertIsNone(
            outliers.score_reel(reel, baseline, {"followers": None}, cfg)["reach_ratio"]
        )
        self.assertIsNone(
            outliers.score_reel(reel, baseline, {"followers": 0}, cfg)["reach_ratio"]
        )
        self.assertEqual(
            outliers.score_reel(reel, baseline, {"followers": 2500}, cfg)["reach_ratio"], 2.0
        )

    def test_engagement_rate_guards_zero_plays(self) -> None:
        cfg = _cfg()
        baseline = outliers.Baseline(median=1000.0, n=10, confidence="ok", metric="plays")

        zero_plays = _reel(plays=0, likes=10, comments=5)
        self.assertIsNone(
            outliers.score_reel(zero_plays, baseline, None, cfg)["engagement_rate"]
        )

        no_plays = _reel(plays=None, plays_source=None, likes=10, comments=5)
        self.assertIsNone(outliers.score_reel(no_plays, baseline, None, cfg)["engagement_rate"])

        normal = _reel(plays=200, likes=50, comments=10)
        self.assertEqual(
            outliers.score_reel(normal, baseline, None, cfg)["engagement_rate"], 0.3
        )

        # `likes` missing falls back to 0 rather than propagating None.
        likes_missing = _reel(plays=100, likes=None, comments=20)
        self.assertEqual(
            outliers.score_reel(likes_missing, baseline, None, cfg)["engagement_rate"], 0.2
        )

    def test_small_account_proof(self) -> None:
        cfg = _cfg(small_account_followers=50000, outlier_threshold=3.0)
        baseline = outliers.Baseline(median=1000.0, n=10, confidence="ok", metric="plays")

        small_and_outlier = _reel(plays=5000)  # ratio 5.0 >= 3.0
        self.assertTrue(
            outliers.score_reel(small_and_outlier, baseline, {"followers": 10000}, cfg)[
                "small_account_proof"
            ]
        )

        small_but_not_outlier = _reel(plays=2000)  # ratio 2.0 < 3.0
        self.assertFalse(
            outliers.score_reel(small_but_not_outlier, baseline, {"followers": 10000}, cfg)[
                "small_account_proof"
            ]
        )

        big_account_outlier = _reel(plays=5000)  # ratio 5.0, but not a small account
        self.assertFalse(
            outliers.score_reel(big_account_outlier, baseline, {"followers": 100000}, cfg)[
                "small_account_proof"
            ]
        )

        no_followers = _reel(plays=5000)
        self.assertFalse(
            outliers.score_reel(no_followers, baseline, {"followers": None}, cfg)[
                "small_account_proof"
            ]
        )
        self.assertFalse(
            outliers.score_reel(no_followers, baseline, None, cfg)["small_account_proof"]
        )


class ViralProofTests(NoNetworkTestCase):
    def test_viral_proof_table_3_8_16_and_caps(self) -> None:
        self.assertEqual(outliers.viral_proof(3, False, "ok"), 3.96)
        self.assertEqual(outliers.viral_proof(8, False, "ok"), 7.5)
        self.assertEqual(outliers.viral_proof(16, False, "ok"), 10.0)

        # "none" confidence always scores 0.0, regardless of the ratio.
        self.assertEqual(outliers.viral_proof(16, False, "none"), 0.0)
        self.assertEqual(outliers.viral_proof(3, True, "none"), 0.0)

        # "low" confidence caps the (post-clamp) score at 6, even when the
        # raw formula would clear it; a score already under 6 is untouched.
        self.assertEqual(outliers.viral_proof(16, False, "low"), 6.0)
        self.assertEqual(outliers.viral_proof(8, False, "low"), 6.0)
        self.assertEqual(outliers.viral_proof(3, False, "low"), 3.96)

        # The small-account bonus is +1 before the 0-10 clamp...
        self.assertEqual(outliers.viral_proof(8, True, "ok"), 8.5)
        # ...and the low cap still applies after the bonus and the clamp.
        self.assertEqual(outliers.viral_proof(16, True, "low"), 6.0)

        # A reel with no computable ratio (missing value, non-"none"
        # baseline) never crashes on max(None, 1); it scores 0.0.
        self.assertEqual(outliers.viral_proof(None, False, "ok"), 0.0)
        self.assertEqual(outliers.viral_proof(None, False, "low"), 0.0)


class SelectOutliersTests(NoNetworkTestCase):
    def test_selection_filters_and_reasons(self) -> None:
        cfg = _cfg(
            lookback_days=90,
            min_plays=5000,
            max_per_account=10,
            top_k_videos=10,
            backfill_pool=10,
        )

        survivor = _scored_reel(shortCode="OK1", ownerUsername="acct")
        no_plays = _scored_reel(shortCode="NP1", ownerUsername="acct", plays=None)
        no_baseline = _scored_reel(
            shortCode="NB1", ownerUsername="acct", baseline_confidence="none"
        )
        outside_lookback = _scored_reel(
            shortCode="OL1", ownerUsername="acct", timestamp="2026-01-01T00:00:00+00:00"
        )
        below_min_plays = _scored_reel(shortCode="BM1", ownerUsername="acct", plays=1000)

        # Reason ordering: the first applicable reason wins even when a
        # reel would also trip a later check.
        no_plays_wins = _scored_reel(
            shortCode="NPW1",
            ownerUsername="acct",
            plays=None,
            baseline_confidence="none",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        no_baseline_wins = _scored_reel(
            shortCode="NBW1",
            ownerUsername="acct",
            baseline_confidence="none",
            timestamp="2026-01-01T00:00:00+00:00",
            plays=1000,
        )
        lookback_wins_over_min_plays = _scored_reel(
            shortCode="OLW1",
            ownerUsername="acct",
            timestamp="2026-01-01T00:00:00+00:00",
            plays=1000,
        )

        reels = [
            survivor,
            no_plays,
            no_baseline,
            outside_lookback,
            below_min_plays,
            no_plays_wins,
            no_baseline_wins,
            lookback_wins_over_min_plays,
        ]

        selection = outliers.select_outliers(reels, cfg, NOW)

        reasons = {item["shortCode"]: item["reason"] for item in selection.excluded}
        self.assertEqual(
            reasons,
            {
                "NP1": "no_plays",
                "NB1": "no_baseline",
                "OL1": "outside_lookback",
                "BM1": "below_min_plays",
                "NPW1": "no_plays",
                "NBW1": "no_baseline",
                "OLW1": "outside_lookback",
            },
        )
        self.assertEqual([r["shortCode"] for r in selection.selected], ["OK1"])
        self.assertEqual(selection.backfill, [])

    def test_selection_per_account_cap_and_backfill_size(self) -> None:
        cfg = _cfg(
            lookback_days=90, min_plays=0, max_per_account=3, top_k_videos=1, backfill_pool=1
        )

        # Five survivors on one account, ranked A > B > C > D > E by
        # outlier_ratio. max_per_account=3 keeps A, B, C and excludes D
        # and E with "per_account_cap". Of the three kept, top_k_videos=1
        # takes A as `selected` and backfill_pool=1 takes B as `backfill`;
        # C clears the account cap but ranks past both pools, so it is
        # simply not listed anywhere.
        reels = [
            _scored_reel(shortCode="A", ownerUsername="acct", outlier_ratio=5.0),
            _scored_reel(shortCode="B", ownerUsername="acct", outlier_ratio=4.0),
            _scored_reel(shortCode="C", ownerUsername="acct", outlier_ratio=3.0),
            _scored_reel(shortCode="D", ownerUsername="acct", outlier_ratio=2.0),
            _scored_reel(shortCode="E", ownerUsername="acct", outlier_ratio=1.0),
        ]

        selection = outliers.select_outliers(reels, cfg, NOW)

        self.assertEqual([r["shortCode"] for r in selection.selected], ["A"])
        self.assertEqual([r["shortCode"] for r in selection.backfill], ["B"])

        reasons = {item["shortCode"]: item["reason"] for item in selection.excluded}
        self.assertEqual(reasons, {"D": "per_account_cap", "E": "per_account_cap"})

        listed = (
            {r["shortCode"] for r in selection.selected}
            | {r["shortCode"] for r in selection.backfill}
            | set(reasons)
        )
        self.assertNotIn("C", listed)

    def test_selection_skips_reels_briefed_in_an_earlier_run(self) -> None:
        # A reel an earlier run already turned into a brief would win
        # again every week inside the lookback window. It is excluded
        # with its own reason and its slot goes to the next outlier.
        cfg = _cfg(lookback_days=90, min_plays=0, top_k_videos=1, backfill_pool=0)
        reels = [
            _scored_reel(shortCode="OLD", ownerUsername="acct", outlier_ratio=9.0),
            _scored_reel(shortCode="NEW", ownerUsername="acct", outlier_ratio=4.0),
        ]
        selection = outliers.select_outliers(reels, cfg, NOW, already_briefed={"OLD"})
        self.assertEqual([r["shortCode"] for r in selection.selected], ["NEW"])
        self.assertIn({"shortCode": "OLD", "reason": "already_briefed"}, selection.excluded)

        default = outliers.select_outliers(reels, cfg, NOW)
        self.assertEqual([r["shortCode"] for r in default.selected], ["OLD"])

    def test_selection_deterministic_tiebreak(self) -> None:
        cfg = _cfg(
            lookback_days=90, min_plays=0, max_per_account=10, top_k_videos=10, backfill_pool=10
        )

        # Different accounts, so the per-account cap can't interfere.
        # B, C, A share one ratio and are given out of alphabetical
        # order -- the shortCode tie-break must still sort them A, B, C.
        # Z's clearly higher ratio outranks all three regardless of its
        # own shortCode's alphabetical position.
        reels = [
            _scored_reel(shortCode="B", ownerUsername="acct-b", outlier_ratio=5.0),
            _scored_reel(shortCode="C", ownerUsername="acct-c", outlier_ratio=5.0),
            _scored_reel(shortCode="A", ownerUsername="acct-a", outlier_ratio=5.0),
            _scored_reel(shortCode="Z", ownerUsername="acct-z", outlier_ratio=9.0),
        ]

        selection = outliers.select_outliers(reels, cfg, NOW)

        self.assertEqual([r["shortCode"] for r in selection.selected], ["Z", "A", "B", "C"])

    def test_likes_baseline_reels_are_never_selected(self) -> None:
        # Controller ruling (Task 9 review, round 1): plays are the only
        # reach signal this pipeline trusts for selection. An account with
        # no plays-bearing reels at all still gets a likes-fallback
        # Baseline and a real outlier_ratio/viral_proof per reel (for the
        # research summary), but every one of its reels has plays=None by
        # construction, so select_outliers must always exclude them as
        # no_plays -- never selected, never backfilled.
        reels = [
            _reel(
                shortCode="LK1",
                ownerUsername="likes_acct",
                plays=None,
                plays_source=None,
                likes=100,
            ),
            _reel(
                shortCode="LK2",
                ownerUsername="likes_acct",
                plays=None,
                plays_source=None,
                likes=120,
            ),
            # A strong likes outlier -- would clearly win selection on
            # outlier_ratio alone if select_outliers didn't hard-exclude it.
            _reel(
                shortCode="LK3",
                ownerUsername="likes_acct",
                plays=None,
                plays_source=None,
                likes=900,
            ),
        ]

        baselines = outliers.compute_baselines(reels, min_n=8)
        baseline = baselines["likes_acct"]
        self.assertEqual(baseline.metric, "likes")
        self.assertEqual(baseline.confidence, "low")

        cfg = _cfg(
            lookback_days=90, min_plays=0, max_per_account=10, top_k_videos=10, backfill_pool=10
        )
        scored = [outliers.score_reel(reel, baseline, None, cfg) for reel in reels]

        outlier = next(reel for reel in scored if reel["shortCode"] == "LK3")
        self.assertIsNotNone(outlier["outlier_ratio"])
        self.assertGreater(outlier["outlier_ratio"], 1.0)
        self.assertGreater(outlier["viral_proof"], 0.0)

        selection = outliers.select_outliers(scored, cfg, NOW)

        reasons = {item["shortCode"]: item["reason"] for item in selection.excluded}
        for reel in scored:
            self.assertEqual(reasons.get(reel["shortCode"]), "no_plays")

        selected_and_backfill = {r["shortCode"] for r in selection.selected} | {
            r["shortCode"] for r in selection.backfill
        }
        self.assertEqual(selected_and_backfill, set())


class WeeklySelectionTests(NoNetworkTestCase):
    def test_below_min_ratio_is_excluded_after_min_plays(self) -> None:
        low = _scored_reel(shortCode="LOW", outlier_ratio=1.9)
        few = _scored_reel(shortCode="FEW", outlier_ratio=1.0, plays=10)
        ok = _scored_reel(shortCode="OK", outlier_ratio=2.0)
        cfg = _cfg(min_outlier_ratio=2.0, min_plays=100)

        selection = outliers.select_outliers([low, few, ok], cfg, NOW)

        reasons = {item["shortCode"]: item["reason"] for item in selection.excluded}
        self.assertEqual(reasons, {"LOW": "below_min_ratio", "FEW": "below_min_plays"})
        self.assertEqual([r["shortCode"] for r in selection.selected], ["OK"])

    def test_below_min_ratio_wins_over_already_briefed(self) -> None:
        low = _scored_reel(shortCode="LOW", outlier_ratio=1.5)
        selection = outliers.select_outliers(
            [low], _cfg(min_outlier_ratio=2.0), NOW, already_briefed={"LOW"}
        )
        self.assertEqual(selection.excluded, [{"shortCode": "LOW", "reason": "below_min_ratio"}])

    def test_overflow_fills_selected_before_any_slot_goes_empty(self) -> None:
        busy = [
            _scored_reel(shortCode=f"B{i}", ownerUsername="busy", outlier_ratio=10.0 - i)
            for i in range(4)
        ]
        quiet = [_scored_reel(shortCode="Q0", ownerUsername="quiet", outlier_ratio=2.5)]
        cfg = _cfg(max_per_account=2, top_k_videos=4, backfill_pool=0)

        selection = outliers.select_outliers(busy + quiet, cfg, NOW)

        # Capped list first (B0, B1, Q0), then busy's overflow (B2).
        self.assertEqual([r["shortCode"] for r in selection.selected], ["B0", "B1", "Q0", "B2"])
        self.assertEqual(selection.excluded, [{"shortCode": "B3", "reason": "per_account_cap"}])

    def test_overflow_goes_to_backfill_after_selected(self) -> None:
        busy = [
            _scored_reel(shortCode=f"B{i}", ownerUsername="busy", outlier_ratio=10.0 - i)
            for i in range(3)
        ]
        cfg = _cfg(max_per_account=1, top_k_videos=1, backfill_pool=1)

        selection = outliers.select_outliers(busy, cfg, NOW)

        self.assertEqual([r["shortCode"] for r in selection.selected], ["B0"])
        self.assertEqual([r["shortCode"] for r in selection.backfill], ["B1"])
        self.assertEqual(selection.excluded, [{"shortCode": "B2", "reason": "per_account_cap"}])


class PaidPartnershipSelectionTests(NoNetworkTestCase):
    def test_flagged_reel_is_excluded_first(self) -> None:
        # Also has no plays: paid_partnership is checked before no_plays.
        paid = _scored_reel(shortCode="PAID", paid_partnership=True, plays=None)
        ok = _scored_reel(shortCode="OK", paid_partnership=False)

        selection = outliers.select_outliers([paid, ok], _cfg(), NOW)

        self.assertEqual(selection.excluded, [{"shortCode": "PAID", "reason": "paid_partnership"}])
        self.assertEqual([r["shortCode"] for r in selection.selected], ["OK"])

    def test_flagged_reel_is_kept_when_the_filter_is_off(self) -> None:
        paid = _scored_reel(shortCode="PAID", paid_partnership=True)
        selection = outliers.select_outliers(
            [paid], _cfg(exclude_paid_partnerships=False), NOW
        )
        self.assertEqual([r["shortCode"] for r in selection.selected], ["PAID"])

    def test_reel_without_the_key_is_kept(self) -> None:
        # Reels written by a pre-0.5.0 run have no paid_partnership key.
        selection = outliers.select_outliers([_scored_reel(shortCode="OLD")], _cfg(), NOW)
        self.assertEqual([r["shortCode"] for r in selection.selected], ["OLD"])

    def test_flagged_reel_still_counts_toward_the_baseline(self) -> None:
        reels = [_reel(shortCode=f"R{i}", plays=1000, paid_partnership=(i == 0)) for i in range(8)]
        baselines = outliers.compute_baselines(reels, min_n=8)
        self.assertEqual(baselines[reels[0]["ownerUsername"]].n, 8)


class FixtureOutlierTests(NoNetworkTestCase):
    def test_fixture_outliers_selected(self) -> None:
        reel_items = _load_fixture("apify_reels_sample.json")
        profile_items = _load_fixture("apify_profiles_sample.json")
        handles = ["sproutapp", "habitlab", "dailywins"]

        reels, profiles, _account_status = instagram.normalize_dataset(
            reel_items, profile_items, handles
        )

        # min_n=8 puts every fixture account (5 or 6 reels) into "low".
        baselines = outliers.compute_baselines(reels, min_n=8)
        for handle in handles:
            self.assertEqual(baselines[handle].confidence, "low")

        fixture_cfg = _cfg(min_outlier_ratio=0.5)
        scored = [
            outliers.score_reel(
                reel,
                baselines[reel["ownerUsername"].lower()],
                profiles.get(reel["ownerUsername"].lower()),
                fixture_cfg,
            )
            for reel in reels
        ]

        selection = outliers.select_outliers(scored, fixture_cfg, NOW)

        # `selected` is already ranked by outlier_ratio descending, so the
        # first reel seen per account is that account's top pick.
        top_by_account: Dict[str, Dict[str, Any]] = {}
        for reel in selection.selected:
            account = reel["ownerUsername"].lower()
            top_by_account.setdefault(account, reel)

        for handle in handles:
            self.assertIn(handle, top_by_account, f"{handle} has no selected reel")
            top_reel = top_by_account[handle]
            acct_plays = [
                reel["plays"]
                for reel in reels
                if reel["ownerUsername"].lower() == handle and reel["plays"] is not None
            ]
            acct_median = statistics.median(acct_plays)
            self.assertGreaterEqual(
                top_reel["plays"],
                4 * acct_median,
                f"{handle}'s top selected reel ({top_reel['shortCode']}) is not "
                f">= 4x its account median ({acct_median})",
            )

        # The one fixture reel dated outside the lookback (dailywins,
        # April 2026) must be excluded specifically for that reason.
        excluded_reasons = {item["shortCode"]: item["reason"] for item in selection.excluded}
        april_reels = [
            reel
            for reel in reels
            if instagram.parse_ts(reel["timestamp"]).year == 2026
            and instagram.parse_ts(reel["timestamp"]).month == 4
        ]
        self.assertEqual(len(april_reels), 1)
        self.assertEqual(excluded_reasons.get(april_reels[0]["shortCode"]), "outside_lookback")


if __name__ == "__main__":
    import unittest

    unittest.main()
