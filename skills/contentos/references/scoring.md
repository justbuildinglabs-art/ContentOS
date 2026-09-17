# Scoring

Every number in this file is deterministic. No subagent computes any of it;
the director reads `viral_proof` as context and never recalculates it, and
nothing here is a matter of judgment. The formulas are implemented in
`lib/outliers.py` (baselines, reel scores, selection) and `lib/director.py`
(`brief_score`). This file names the symbols and works one clean example per
formula so the numbers can be checked by hand. Every rounding step below
matches the code: round to 4 decimals for a ratio, round to 2 decimals for a
0 to 10 score.

## Baselines

### compute_baselines

Every account gets a baseline: a median value plus a confidence label. Count
`n`, the account's reels that have a `plays` value at all.

- `n` at or above `min_reels_for_median` (default 8): the baseline is the
  account's own median `plays`. Confidence `ok`.
- `n` from 3 up to `min_reels_for_median`: the account's own median is
  blended with the pooled median, the median `plays` across every account's
  plays-bearing reels in the same run:
  `blended = (n * account_median + (min_reels_for_median - n) * pooled_median) / min_reels_for_median`.
  Confidence `low`.
- `n` above 0 but below 3: the baseline is the account's own median, with no
  blending. Confidence `none`. Every reel scored against a `none` baseline is
  excluded later with reason `no_baseline`.
- `n` is 0, but the account has at least one reel with a `likes` count: the
  baseline is the median of those `likes` counts instead. Confidence `low`,
  and the baseline's metric switches from `plays` to `likes`. A reel scored
  this way still has `plays` null by definition, so it is excluded later with
  reason `no_plays`. It is reported on, never selected.
- `n` is 0 and there is no `likes` count either: the baseline is 0,
  confidence `none`.

Worked example, `ok`: an account's last 8 reels all carry a `plays` count:
1000, 1000, 2000, 2000, 3000, 3000, 4000, 5000. `n` is 8, meeting
`min_reels_for_median`. The median of eight numbers is the average of the
two middle ones once sorted, 2000 and 3000, so the baseline is 2500,
confidence `ok`.

Worked example, `low` (blended): a smaller account has only 5 plays-bearing
reels: 500, 800, 1000, 1200, 1500, median 1000. The pooled median across
every account in this run is 4000. With `min_reels_for_median` at 8:
`(5 * 1000 + (8 - 5) * 4000) / 8 = (5000 + 12000) / 8 = 2125`. Confidence
`low`.

## Reel scores

Four numbers and one flag, computed for every reel against its account's
baseline.

### outlier_ratio

`outlier_ratio = value / max(baseline_median, 1)`, rounded to 4 decimals.
`value` is `plays` when the baseline's metric is `plays`, or `likes` when it
fell back to the likes metric. Null when that value is missing, or the
baseline's confidence is `none`.

Worked example: a reel with 6000 plays, baseline median 3000:
`outlier_ratio = 6000 / 3000 = 2.0`.

### reach_ratio

`reach_ratio = plays / followers`, rounded to 4 decimals. Null without a
plays count, or without a follower count above 0.

Worked example: 6000 plays, 30000 followers: `reach_ratio = 0.2`.

### engagement_rate

`engagement_rate = (likes + comments) / plays`, rounded to 4 decimals. A
missing `likes` counts as 0. Null when `plays` is missing or 0.

Worked example: 250 likes, 50 comments, 6000 plays:
`(250 + 50) / 6000 = 0.05`.

### small_account_proof

True when the account's followers are under `small_account_followers`
(default 50000) and the reel's `outlier_ratio` is at least
`outlier_threshold` (default 3.0). False whenever followers are unknown.

Worked example: an account with 20000 followers posts a reel with
`outlier_ratio` 4.0. 20000 is under 50000, and 4.0 is at least 3.0, so
`small_account_proof` is true.

### viral_proof

A single 0 to 10 number for how strongly a reel's own numbers argue for its
mechanism, built from `outlier_ratio` alone:

`viral_proof = clamp(2.5 * log2(max(outlier_ratio, 1)), 0, 10)`, plus 1 when
`small_account_proof` is true (the sum still clamped to 10), then capped at 6
when the baseline confidence is `low`, and forced to 0 when the confidence is
`none` or the ratio could not be computed at all. Rounded to 2 decimals.

Worked table, `ok` confidence, no small-account bonus:

| outlier_ratio | viral_proof |
| --- | --- |
| 3 | 3.96 |
| 8 | 7.5 |
| 16 | 10 |

The design spec's own prose rounds the first row to 4.0. The real value is
3.96 (`2.5 * log2(3) = 3.9624...`, rounded to 2 decimals); the spec's 4.0 is
an approximation of that number, not a different rule.

## Selection

### select_outliers

Every scored reel gets at most one exclusion reason, checked in this order:

1. `no_plays`: the reel has no `plays` value. This always catches a reel
   scored against a likes-fallback baseline.
2. `no_baseline`: the account's baseline confidence is `none`.
3. `outside_lookback`: the reel is older than `lookback_days` (default 90).
4. `below_min_plays`: `plays` is under `min_plays` (default 5000).

Everything else survives, sorted by `outlier_ratio` descending, ties broken
by `shortCode`. Walking that order, at most `max_per_account` (default 4)
reels per account are kept; the rest get a fifth reason, `per_account_cap`.
`outlier_threshold` plays no part in this filter; it only feeds
`small_account_proof` above. Of what remains, the first `top_k_videos`
(default 20) become `selected`, and the next `backfill_pool` (default 10)
become `backfill`. Anything ranked further down is simply not listed
anywhere: not selected, not backfill, not excluded.

Worked example: three reels survive the first four checks, each from a
different account. Reel A has `outlier_ratio` 5.0, reel B has 3.0, reel C
has 2.0. Sorted, that is A, B, C. With `top_k_videos` at 1, A becomes
`selected`. With `backfill_pool` at 1, B becomes `backfill`. C is ranked but
appears nowhere.

## Brief ranking

### brief_score

`brief_score = 0.35 * viral_proof + 0.25 * score_convertible + 0.20 * score_scalable + 0.20 * score_product_fit`.
`viral_proof` always comes from stage 1's own reel score; it is never
recomputed here. Then, in this order: the total is capped at 4.0 when the
analysis's `risk_flags` contains `copyrighted_media` or
`fake_testimonial_risk`; then 1.0 is subtracted when `confidence` is `low`;
the result is clamped to 0 to 10; then rounded to 2 decimals.

Worked example, no risk, medium confidence: `viral_proof` 8,
`score_convertible` 6, `score_scalable` 8, `score_product_fit` 8:
`0.35*8 + 0.25*6 + 0.20*8 + 0.20*8 = 2.8 + 1.5 + 1.6 + 1.6 = 7.5`.
`brief_score = 7.5`.

Worked example, risky and low confidence: all four inputs at 10,
`risk_flags` includes `copyrighted_media`, `confidence` is `low`. The
weighted sum is `3.5 + 2.5 + 2.0 + 2.0 = 10.0`, capped at 4.0 for the risk
flag, then reduced by 1.0 for low confidence: `brief_score = 3.0`.

## Sources

Written from `docs/superpowers/specs/2026-09-16-contentos-design.md`, the
"Stage 1, research" section (baselines and reel scores) and the "Stage 2,
direct" section (`brief_score`), which state these formulas. The exact
rounding and the worked examples here are checked against `lib/outliers.py`
and `lib/director.py`, not restated from the guides; none of this scoring
logic comes from the Ray Cfu guides.
