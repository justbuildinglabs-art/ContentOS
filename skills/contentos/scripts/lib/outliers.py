"""Per-account baselines, reel scoring, and outlier selection.

Given canonical Reel dicts (`lib/instagram.py`, Task 8) and their
owners' profiles, this is the deterministic core of the research stage:
`compute_baselines` builds one play-count (or likes-fallback) `Baseline`
per account, `score_reel` scores one reel against its account's
`Baseline`, and `select_outliers` filters, ranks, and splits already-
scored reels into what the content director sees. See the design
spec's "Stage 1 -- research" steps 4-6 for the formulas pinned down
here; Task 10's `research` command is the only caller, and
`viral_proof` here is also what stage 1's score feeds into brief
ranking later (never recomputed by a subagent).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import log2
from statistics import median
from typing import Any, Dict, List, Optional

from lib import instagram

# Baseline.confidence values, in the order compute_baselines prefers them.
CONFIDENCE_OK = "ok"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"

# Baseline.metric / score_reel ratio-numerator values.
METRIC_PLAYS = "plays"
METRIC_LIKES = "likes"

# select_outliers exclusion reasons, in the order they are checked.
REASON_NO_PLAYS = "no_plays"
REASON_NO_BASELINE = "no_baseline"
REASON_OUTSIDE_LOOKBACK = "outside_lookback"
REASON_BELOW_MIN_PLAYS = "below_min_plays"
REASON_PER_ACCOUNT_CAP = "per_account_cap"


@dataclass
class Baseline:
    """One account's outlier yardstick: a median plus how much to trust it."""

    median: float
    n: int
    confidence: str  # "ok" | "low" | "none"
    metric: str  # "plays" | "likes"


@dataclass
class Selection:
    """The result of `select_outliers`: ranked reels plus why others dropped."""

    selected: List[Dict[str, Any]]
    backfill: List[Dict[str, Any]]
    excluded: List[Dict[str, str]]  # [{"shortCode": ..., "reason": ...}]


def _account(reel: Dict[str, Any]) -> str:
    """The lowercase account key `compute_baselines`/`select_outliers` group by."""
    return (reel.get("ownerUsername") or "").lower()


def compute_baselines(reels: List[Dict[str, Any]], min_n: int) -> Dict[str, Baseline]:
    """Compute one Baseline per distinct account in `reels`.

    `min_n` is the founder's `min_reels_for_median` config value. For
    each account, `n` is its count of reels with `plays` not None:

    - `n >= min_n`: the account's own median plays, confidence "ok".
    - `3 <= n < min_n`: blended with the pooled median across every
      account's plays-bearing reels -- `(n*acct + (min_n-n)*pooled) /
      min_n` -- confidence "low". Falls back to the account median,
      still "low", if the pool is ever empty; in practice that cannot
      happen here, since this account alone already puts at least one
      plays-bearing reel into the pool.
    - `0 < n < 3`: the account's own median, confidence "none" -- too
      few reels to blend meaningfully.
    - `n == 0` with at least one of the account's reels carrying
      `likes`: the median of those likes, `metric="likes"`, confidence
      "low".
    - `n == 0` and no likes either: median 0.0, confidence "none".

    Medians are computed with `statistics.median`.
    """
    by_account: Dict[str, List[Dict[str, Any]]] = {}
    for reel in reels:
        by_account.setdefault(_account(reel), []).append(reel)

    all_plays = [reel["plays"] for reel in reels if reel.get("plays") is not None]
    pooled_median: Optional[float] = median(all_plays) if all_plays else None

    baselines: Dict[str, Baseline] = {}
    for account, acct_reels in by_account.items():
        plays_values = [reel["plays"] for reel in acct_reels if reel.get("plays") is not None]
        n = len(plays_values)

        if n == 0:
            likes_values = [
                reel["likes"] for reel in acct_reels if reel.get("likes") is not None
            ]
            if likes_values:
                baselines[account] = Baseline(
                    median=float(median(likes_values)),
                    n=0,
                    confidence=CONFIDENCE_LOW,
                    metric=METRIC_LIKES,
                )
            else:
                baselines[account] = Baseline(
                    median=0.0, n=0, confidence=CONFIDENCE_NONE, metric=METRIC_PLAYS
                )
            continue

        # float(): statistics.median returns the middle element as-is for
        # an odd-length list, which would otherwise silently be an int
        # here rather than the float the Baseline.median field promises.
        acct_median = float(median(plays_values))

        if n >= min_n:
            baselines[account] = Baseline(
                median=acct_median, n=n, confidence=CONFIDENCE_OK, metric=METRIC_PLAYS
            )
        elif n >= 3:
            if pooled_median is None:
                blended = acct_median
            else:
                blended = (n * acct_median + (min_n - n) * pooled_median) / min_n
            baselines[account] = Baseline(
                median=blended, n=n, confidence=CONFIDENCE_LOW, metric=METRIC_PLAYS
            )
        else:
            baselines[account] = Baseline(
                median=acct_median, n=n, confidence=CONFIDENCE_NONE, metric=METRIC_PLAYS
            )

    return baselines


def viral_proof(
    outlier_ratio: Optional[float], small_account_proof: bool, confidence: str
) -> float:
    """Compress one reel's outlier ratio into a single 0-10 "how viral" score.

    A "none"-confidence baseline -- or a ratio that could not be
    computed at all (e.g. the reel's own value is missing even though
    its account's baseline is usable) -- always scores 0.0, since
    `2.5*log2(max(ratio, 1))` has nothing meaningful to work from
    either way. Otherwise: `2.5*log2(max(ratio, 1))`, +1 when
    `small_account_proof`, clamped to 0-10, then capped at 6 when
    `confidence` is "low". Rounded to 2 decimals.
    """
    if confidence == CONFIDENCE_NONE or outlier_ratio is None:
        return 0.0

    raw = 2.5 * log2(max(outlier_ratio, 1))
    if small_account_proof:
        raw += 1

    clamped = max(0.0, min(10.0, raw))
    if confidence == CONFIDENCE_LOW:
        clamped = min(clamped, 6.0)

    return round(clamped, 2)


def score_reel(
    reel: Dict[str, Any],
    baseline: Baseline,
    profile: Optional[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Score one reel against its account's Baseline.

    Returns a copy of `reel` (never mutated) with nine added keys:
    `baseline_median, baseline_n, baseline_confidence, baseline_metric,
    outlier_ratio, reach_ratio, engagement_rate, small_account_proof,
    viral_proof`. `profile` is the reel owner's normalized profile dict
    (or None when no profile was scraped for that account).
    """
    plays = reel.get("plays")
    likes = reel.get("likes")
    comments = reel.get("comments") or 0
    followers = profile.get("followers") if profile else None

    value = plays if baseline.metric == METRIC_PLAYS else likes
    if value is None or baseline.confidence == CONFIDENCE_NONE:
        outlier_ratio: Optional[float] = None
    else:
        outlier_ratio = round(value / max(baseline.median, 1), 4)

    if plays is not None and followers is not None and followers > 0:
        reach_ratio: Optional[float] = round(plays / followers, 4)
    else:
        reach_ratio = None

    if plays is not None and plays > 0:
        engagement_rate: Optional[float] = round(((likes or 0) + comments) / plays, 4)
    else:
        engagement_rate = None

    small_account_proof = bool(
        followers is not None
        and followers < cfg["small_account_followers"]
        and outlier_ratio is not None
        and outlier_ratio >= cfg["outlier_threshold"]
    )

    scored = dict(reel)
    scored.update(
        {
            "baseline_median": baseline.median,
            "baseline_n": baseline.n,
            "baseline_confidence": baseline.confidence,
            "baseline_metric": baseline.metric,
            "outlier_ratio": outlier_ratio,
            "reach_ratio": reach_ratio,
            "engagement_rate": engagement_rate,
            "small_account_proof": small_account_proof,
            "viral_proof": viral_proof(outlier_ratio, small_account_proof, baseline.confidence),
        }
    )
    return scored


def _exclusion_reason(
    reel: Dict[str, Any], cfg: Dict[str, Any], cutoff: datetime
) -> Optional[str]:
    """The first applicable exclusion reason for one already-scored reel, if any.

    Checked in order -- a reel gets at most one reason, even when it
    would also trip a later check.
    """
    if reel.get("plays") is None:
        return REASON_NO_PLAYS
    if reel.get("baseline_confidence") == CONFIDENCE_NONE:
        return REASON_NO_BASELINE
    if instagram.parse_ts(reel["timestamp"]) < cutoff:
        return REASON_OUTSIDE_LOOKBACK
    if reel["plays"] < cfg["min_plays"]:
        return REASON_BELOW_MIN_PLAYS
    return None


def select_outliers(reels: List[Dict[str, Any]], cfg: Dict[str, Any], now: datetime) -> Selection:
    """Filter, rank, and split already-scored reels into a Selection.

    `reels` must already carry the keys `score_reel` adds. `now` is a
    UTC-aware datetime. Reasons are applied in this order per reel:
    `no_plays`, `no_baseline`, `outside_lookback` (older than
    `now - lookback_days`), `below_min_plays`. Survivors are sorted by
    `outlier_ratio` descending, then `shortCode` ascending; walking that
    order, at most `max_per_account` reels per account are kept (the
    rest excluded as `per_account_cap`). The first `top_k_videos` of
    what remains become `selected`, the next `backfill_pool` become
    `backfill`; anything ranked past that is simply not listed anywhere
    (`outlier_threshold` is not a filter here -- only ranking/display).
    """
    cutoff = now - timedelta(days=cfg["lookback_days"])

    excluded: List[Dict[str, str]] = []
    survivors: List[Dict[str, Any]] = []
    for reel in reels:
        reason = _exclusion_reason(reel, cfg, cutoff)
        if reason is None:
            survivors.append(reel)
        else:
            excluded.append({"shortCode": reel["shortCode"], "reason": reason})

    survivors.sort(key=lambda reel: (-reel["outlier_ratio"], reel["shortCode"]))

    max_per_account = cfg["max_per_account"]
    per_account_counts: Dict[str, int] = {}
    capped: List[Dict[str, Any]] = []
    for reel in survivors:
        account = _account(reel)
        count = per_account_counts.get(account, 0)
        if count < max_per_account:
            capped.append(reel)
            per_account_counts[account] = count + 1
        else:
            excluded.append({"shortCode": reel["shortCode"], "reason": REASON_PER_ACCOUNT_CAP})

    top_k = cfg["top_k_videos"]
    backfill_pool = cfg["backfill_pool"]
    selected = capped[:top_k]
    backfill = capped[top_k : top_k + backfill_pool]

    return Selection(selected=selected, backfill=backfill, excluded=excluded)
