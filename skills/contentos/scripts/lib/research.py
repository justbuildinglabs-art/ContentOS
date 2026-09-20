"""Stage 1 -- research: Apify scrape -> normalize -> baselines -> outliers.

`run_research` is the one entry point that composes every module already
reviewed for this pipeline: `lib/apify.py` (REST client, cost estimate,
`FixtureTransport`/`HttpTransport`), `lib/instagram.py`
(`normalize_dataset`), `lib/outliers.py` (`compute_baselines`,
`score_reel`, `select_outliers`), `lib/video.py` (`download_selected`,
spec step 7 -- video + cover downloads with backfill), `lib/frames.py`
(`frames_for_selected`, spec step 8 -- keyframes for the
content-director subagent), `lib/transcribe.py` (`transcripts_for_selected`,
0.3.0 -- a transcript per selected reel, right after keyframes), and
`lib/store.py` (run directory bookkeeping). See the design spec's "Stage 1 -- research" section for
the full step-by-step flow this module drives and "Config defaults"
for the config keys read here. `no_download`, when true, skips
`download_selected`, `frames_for_selected`, and
`transcripts_for_selected`, so every `selected`/`backfill` reel's
`video_status`/`frames_status`/`transcript_status` stay `"pending"` and
the `videos`/`frames`/`transcripts` summaries are all `None`.

`contentos.py`'s `research` subcommand is the only caller; it loads
config with `store.load_config`, resolves keys with `env.resolve_keys`,
and maps each `ResearchError` subclass here to the matching exit code
from the design spec's "Skill" exit-code table (`store.ConfigError` and
`store.RunNotFound`, which this module lets propagate unchanged, map to
exit 2 there too).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib import apify, frames, history, instagram, outliers, store, transcribe, video
from lib.env import Keys
from lib.http import HTTPError

# Repo root / "fixtures": four directories up from this file
# (lib -> scripts -> contentos -> skills -> repo root).
FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures"
REELS_FIXTURE_NAME = "apify_reels_sample.json"
PROFILES_FIXTURE_NAME = "apify_profiles_sample.json"

# `now` fallback for --mock, so a mock run's lookback-window math (and
# therefore which fixture reels get selected) stays reproducible as the
# fixture data ages relative to the real calendar date.
MOCK_NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)

# stages.research["status"] values.
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

# video_status / frames_status placeholder every selected/backfill reel
# carries until download_selected / frames_for_selected run below (both
# skipped, leaving this in place, when --no-download is set).
PENDING = "pending"


class ResearchError(Exception):
    """Base for every research-stage failure the CLI maps to an exit code."""

    def __init__(self, message: str, exit_code: int, payload: Optional[dict] = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.payload = payload


class CostCapExceeded(ResearchError):
    """The cost estimate exceeds `apify_max_charge_usd` (exit 6)."""

    def __init__(self, payload: dict) -> None:
        message = (
            f"estimated cost ${payload['total_usd']:.4f} exceeds "
            f"apify_max_charge_usd ${payload['cap_usd']:.4f}"
        )
        super().__init__(message, 6, payload)


class ConfirmationRequired(ResearchError):
    """`--estimate-only`, or missing `--yes` (exit 3): the estimate, unconfirmed."""

    def __init__(self, payload: dict) -> None:
        super().__init__("confirmation required; re-run with --yes to proceed", 3, payload)


class MissingKey(ResearchError):
    """No APIFY_API_TOKEN resolved and not `--mock` (exit 4)."""

    def __init__(self) -> None:
        message = "APIFY_API_TOKEN not found; run diagnose to locate it, or pass --mock"
        super().__init__(message, 4, None)


class NothingToResume(ResearchError):
    """`--resume` named a run with no recorded Apify runs to re-poll (exit 2).

    `init_run` seeds `run.json`'s `apify_runs` as `{}`; it is only filled
    in (both entries at once, see `_start_runs`) after the initial POSTs
    succeed. A run whose very first attempt failed before that point --
    or whose `run.json` was hand-edited -- has nothing for `--resume` to
    re-poll; this is a usage error, not an upstream failure, since no
    network call has been attempted yet.
    """

    def __init__(self, run_id: str) -> None:
        message = f"{run_id}: no recorded Apify runs to resume; start a new research run"
        super().__init__(message, 2, None)


class UpstreamFailure(ResearchError):
    """An Apify run or dataset fetch failed (exit 5)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 5, None)


def _default_log(message: str) -> None:
    """Default `log`: one line per call, to stderr."""
    print(message, file=sys.stderr)


def _load_fixtures() -> Tuple[List[dict], List[dict]]:
    """Load the two sample Apify dataset files `--mock` serves."""
    reels = json.loads((FIXTURES_DIR / REELS_FIXTURE_NAME).read_text(encoding="utf-8"))
    profiles = json.loads((FIXTURES_DIR / PROFILES_FIXTURE_NAME).read_text(encoding="utf-8"))
    return reels, profiles


def _default_transport(mock: bool) -> apify.Transport:
    """The transport `run_research` uses when the caller supplies none."""
    if mock:
        reels_items, details_items = _load_fixtures()
        return apify.FixtureTransport(reels_items, details_items)
    return apify.HttpTransport()


def _estimate_payload(estimate: apify.CostEstimate, cfg: Dict[str, Any], n_accounts: int) -> Dict[str, Any]:
    """Build the JSON-able estimate every `ResearchError` subclass carries."""
    cap = cfg["apify_max_charge_usd"]
    return {
        "accounts": n_accounts,
        "reels_per_account": cfg["reels_per_account"],
        "reels_usd": estimate.reels_usd,
        "details_usd": estimate.details_usd,
        "transcripts_usd": estimate.transcripts_usd,
        "total_usd": estimate.total_usd,
        "max_items": estimate.max_items,
        "cap_usd": cap,
        "within_cap": estimate.total_usd <= cap,
    }


def check_gates(
    payload: Dict[str, Any], mock: bool, yes: bool, estimate_only: bool, keys: Optional[Keys]
) -> None:
    """Raise the matching `ResearchError`, in the exact order the spec pins down.

    1. Over `apify_max_charge_usd` -> `CostCapExceeded`.
    2. `--estimate-only`, or missing `--yes` -> `ConfirmationRequired`.
    3. Not `--mock` and no resolved key -> `MissingKey`.
    """
    if not payload["within_cap"]:
        raise CostCapExceeded(payload)
    if estimate_only or not yes:
        raise ConfirmationRequired(payload)
    if not mock and not (keys and keys.apify):
        raise MissingKey()


def _run_ref_from_record(record: Dict[str, Any]) -> apify.RunRef:
    """Rebuild a `RunRef` to re-poll from a `run.json` `apify_runs` entry.

    `status` is a placeholder: `wait_for_run` polls immediately and
    replaces it with whatever the transport actually reports.
    """
    return apify.RunRef(id=record["id"], status="READY", dataset_id=record["dataset_id"])


def _start_runs(
    token: str,
    accounts: List[str],
    cfg: Dict[str, Any],
    estimate: apify.CostEstimate,
    transport: apify.Transport,
) -> Tuple[apify.RunRef, apify.RunRef, Dict[str, Any]]:
    """Start the reels and details Apify runs; return both plus their record.

    The record is the exact shape `run.json`'s `apify_runs` stores, so the
    caller can persist it immediately -- before polling -- for `--resume`.
    """
    reels_input = apify.build_reels_input(
        accounts, cfg["reels_per_account"], cfg["baseline_lookback_days"]
    )
    details_input = apify.build_details_input(accounts)
    cap = cfg["apify_max_charge_usd"]
    timeout_s = cfg["apify_timeout_s"]

    reels_run = apify.start_run(token, reels_input, cap, estimate.max_items, timeout_s, transport)
    details_run = apify.start_run(token, details_input, cap, estimate.max_items, timeout_s, transport)

    record = {
        "reels": {"id": reels_run.id, "dataset_id": reels_run.dataset_id, "input": reels_input},
        "details": {
            "id": details_run.id, "dataset_id": details_run.dataset_id, "input": details_input
        },
    }
    return reels_run, details_run, record


def _poll_and_fetch(
    token: str,
    reels_run: apify.RunRef,
    details_run: apify.RunRef,
    cfg: Dict[str, Any],
    transport: apify.Transport,
    log: Callable[[str], None],
) -> Tuple[List[dict], List[dict], List[str]]:
    """Poll both runs to completion, then page through both datasets.

    Returns `(reel_items, profile_items, warnings)`; `warnings` names any
    run that came back partial (Apify's own TIMED-OUT, or this side's
    `apify_timeout_s` elapsing first).
    """
    poll_s = cfg["poll_interval_s"]
    timeout_s = cfg["apify_timeout_s"]
    warnings: List[str] = []

    reels_run = apify.wait_for_run(token, reels_run, poll_s, timeout_s, transport, log=log)
    if reels_run.partial:
        warnings.append(f"reels run {reels_run.id} did not finish before timeout; results may be partial")

    details_run = apify.wait_for_run(token, details_run, poll_s, timeout_s, transport, log=log)
    if details_run.partial:
        warnings.append(
            f"details run {details_run.id} did not finish before timeout; results may be partial"
        )

    log(f"fetching dataset items for {reels_run.id} and {details_run.id}")
    reel_items = list(apify.iter_dataset_items(token, reels_run.dataset_id, transport))
    profile_items = list(apify.iter_dataset_items(token, details_run.dataset_id, transport))
    return reel_items, profile_items, warnings


def _score_all(
    reels: List[Dict[str, Any]],
    profiles: Dict[str, Dict[str, Any]],
    baselines: Dict[str, outliers.Baseline],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Score every normalized reel against its account's Baseline."""
    none_baseline = outliers.Baseline(
        median=0.0, n=0, confidence=outliers.CONFIDENCE_NONE, metric=outliers.METRIC_PLAYS
    )
    scored = []
    for reel in reels:
        account = (reel.get("ownerUsername") or "").lower()
        baseline = baselines.get(account, none_baseline)
        scored.append(outliers.score_reel(reel, baseline, profiles.get(account), cfg))
    return scored


def _pending(reel: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of `reel` carrying the download/keyframe placeholders."""
    return dict(reel, video_status=PENDING, frames_status=PENDING, transcript_status=PENDING)


def _baselines_payload(
    accounts: List[str], baselines: Dict[str, outliers.Baseline]
) -> Dict[str, Dict[str, Any]]:
    """Baselines keyed by handle as given in config, skipping accounts with none.

    `compute_baselines` only produces a Baseline for an account that has
    at least one normalized reel, so an account Apify never found (or
    that scraped zero reels) is simply absent here.
    """
    payload = {}
    for handle in accounts:
        baseline = baselines.get(handle.lower())
        if baseline is not None:
            payload[handle] = {
                "median": baseline.median,
                "n": baseline.n,
                "confidence": baseline.confidence,
                "metric": baseline.metric,
            }
    return payload


def _top_reel(reels_for_account: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The reel with the highest `outlier_ratio`, or None if none is scored."""
    ranked = [reel for reel in reels_for_account if reel.get("outlier_ratio") is not None]
    if not ranked:
        return None
    return max(ranked, key=lambda reel: reel["outlier_ratio"])


def _print_summary(
    accounts: List[str],
    account_status: Dict[str, str],
    scored: List[Dict[str, Any]],
    baselines: Dict[str, outliers.Baseline],
    format_accounts: List[str],
) -> None:
    """Print `handle | kind | status | reels | median | confidence | top shortCode | ratio` per account.

    `kind` is `instagram.SOURCE_KIND_FORMAT` when `handle` (case-
    insensitively) is one of `format_accounts`, else
    `instagram.SOURCE_KIND_NICHE` -- the same rule `instagram.
    normalize_dataset` tags each reel/profile with.
    """
    by_account: Dict[str, List[Dict[str, Any]]] = {}
    for reel in scored:
        by_account.setdefault((reel.get("ownerUsername") or "").lower(), []).append(reel)

    format_set = {handle.lower() for handle in format_accounts}

    for handle in accounts:
        key = handle.lower()
        kind = instagram.SOURCE_KIND_FORMAT if key in format_set else instagram.SOURCE_KIND_NICHE
        reels_for_account = by_account.get(key, [])
        baseline = baselines.get(key)
        top = _top_reel(reels_for_account)
        median_str = f"{baseline.median:g}" if baseline else "-"
        confidence_str = baseline.confidence if baseline else "-"
        top_shortcode = top["shortCode"] if top else "-"
        ratio_str = f"{top['outlier_ratio']:.2f}" if top else "-"
        print(
            f"{handle} | {kind} | {account_status.get(handle, 'unknown')} | {len(reels_for_account)} | "
            f"{median_str} | {confidence_str} | {top_shortcode} | {ratio_str}"
        )


def run_research(
    project: Path,
    cfg: Dict[str, Any],
    keys: Keys,
    mock: bool,
    yes: bool,
    estimate_only: bool,
    resume: Optional[str],
    transport: Optional[apify.Transport] = None,
    log: Optional[Callable[[str], None]] = None,
    now: Optional[datetime] = None,
    no_download: bool = False,
) -> Dict[str, Any]:
    """Run Stage 1 end to end, including keyframes, and return the RESULT summary.

    `no_download`, when true, skips `video.download_selected`,
    `frames.frames_for_selected`, and
    `transcribe.transcripts_for_selected` entirely: every
    `selected`/`backfill` reel's `video_status`/`frames_status`/
    `transcript_status` stay `"pending"` (as `_pending` first wrote them)
    and the returned/recorded `videos`/`frames`/`transcripts` summaries
    are all `None`. Raises a `ResearchError`
    subclass for every condition the CLI maps to a non-zero exit code
    (cost cap, confirmation, missing key, upstream failure).
    `store.ConfigError`/`store.RunNotFound` propagate unchanged, for the
    CLI to map to exit 2.
    """
    if log is None:
        log = _default_log
    project = Path(project)
    # Spec step 1: the accounts are competitors followed by
    # format_accounts, in config order -- never deduplicated,
    # interleaved, or reordered.
    accounts: List[str] = list(cfg["competitors"]) + list(cfg["format_accounts"])

    # 0.3.0: the paid transcript fallback is priced in only when it would
    # actually run (lib/transcribe.py's `estimate_usd`), so it counts
    # against apify_max_charge_usd through the same gate below.
    estimate = apify.estimate_cost(
        len(accounts), cfg["reels_per_account"], transcripts_usd=transcribe.estimate_usd(cfg)
    )
    payload = _estimate_payload(estimate, cfg, len(accounts))
    check_gates(payload, mock, yes, estimate_only, keys)

    mode = "mock" if mock else "live"
    if now is None:
        now = MOCK_NOW if mock else datetime.now(timezone.utc)
    if transport is None:
        transport = _default_transport(mock)
    token = keys.apify if (keys and keys.apify) else "mock"

    store.ensure_gitignore(project)

    if resume:
        run_dir = store.resolve_run(project, resume)
        run_data = store.read_json(run_dir / "run.json")
        apify_runs = run_data.get("apify_runs") or {}
        if "reels" not in apify_runs or "details" not in apify_runs:
            raise NothingToResume(run_dir.name)
        reels_run = _run_ref_from_record(apify_runs["reels"])
        details_run = _run_ref_from_record(apify_runs["details"])
        mode = run_data.get("mode", mode)
        started_at = run_data.get("created_at") or datetime.now(timezone.utc).isoformat()
        log(f"resuming run {run_dir.name}: re-polling recorded Apify runs")
    else:
        run_dir = store.init_run(project, cfg, mode)
        started_at = datetime.now(timezone.utc).isoformat()
        log(f"starting run {run_dir.name}: launching reels and details Apify runs")
        reels_run = details_run = None  # assigned inside the try block below

    try:
        if not resume:
            reels_run, details_run, apify_runs_record = _start_runs(
                token, accounts, cfg, estimate, transport
            )
            store.update_run(run_dir, apify_runs=apify_runs_record)

        reel_items, profile_items, run_warnings = _poll_and_fetch(
            token, reels_run, details_run, cfg, transport, log
        )
    except (apify.ApifyRunFailed, HTTPError, OSError) as exc:
        store.update_run(run_dir, stages={"research": {"status": STATUS_FAILED, "error": str(exc)}})
        raise UpstreamFailure(str(exc)) from exc

    log(f"normalizing {len(reel_items)} reel items and {len(profile_items)} profile items")
    reels, profiles, account_status = instagram.normalize_dataset(
        reel_items, profile_items, accounts, format_handles=cfg["format_accounts"]
    )
    baselines = outliers.compute_baselines(reels, cfg["min_reels_for_median"])
    scored = _score_all(reels, profiles, baselines, cfg)
    briefed = history.briefed_shortcodes(project, exclude_run_id=run_dir.name)
    selection = outliers.select_outliers(scored, cfg, now, already_briefed=briefed)

    account_warnings = [
        f"{handle}: {status}"
        for handle, status in account_status.items()
        if status != instagram.STATUS_OK
    ]
    warnings = run_warnings + account_warnings
    for warning in warnings:
        log(warning)

    store.write_json_atomic(run_dir / "01-reels.json", scored)
    store.write_json_atomic(run_dir / "01-profiles.json", profiles)
    outliers_doc = {
        "selected": [_pending(reel) for reel in selection.selected],
        "backfill": [_pending(reel) for reel in selection.backfill],
        "excluded": selection.excluded,
        "account_status": account_status,
        "baselines": _baselines_payload(accounts, baselines),
    }
    store.write_json_atomic(run_dir / "02-outliers.json", outliers_doc)

    # Step 7: download videos + covers immediately, while CDN URLs are
    # freshest. `download_selected` rewrites 02-outliers.json itself
    # (per-reel video_status/cover_status, and any backfill promotions),
    # so nothing further here touches that file. --no-download leaves
    # every reel exactly as `_pending` wrote it above, and also skips
    # step 8 below -- there is no video yet for ffmpeg to read.
    videos_summary: Optional[Dict[str, int]] = None
    frames_summary: Optional[Dict[str, int]] = None
    transcripts_summary: Optional[Dict[str, int]] = None
    if not no_download:
        video_report = video.download_selected(
            run_dir, outliers_doc, cfg, mock, fixtures_dir=FIXTURES_DIR, log=log
        )
        videos_summary = {
            "ok": len(video_report.ok),
            "failed": len(video_report.skipped),
            "promoted": len(video_report.replaced_from_backfill),
        }
        warnings = warnings + video_report.warnings

        # Step 8: keyframes for whatever download_selected just landed on
        # disk (including any reel `too_large`/failed/etc -- frames.py
        # itself decides `no_video` for those). `frames_for_selected`
        # rewrites 02-outliers.json again, same reasoning as step 7.
        frame_statuses = frames.frames_for_selected(
            run_dir, outliers_doc, cfg, mock=mock, fixtures_dir=FIXTURES_DIR, log=log
        )
        frames_summary = {"ok": 0, "cover_only": 0, "failed": 0, "no_video": 0}
        for frame_status in frame_statuses.values():
            frames_summary[frame_status] = frames_summary.get(frame_status, 0) + 1

        # 0.3.0: a transcript per selected reel, right after keyframes.
        # Never raises for one reel; rewrites 02-outliers.json itself.
        log("transcribing selected reels")
        transcript_statuses = transcribe.transcripts_for_selected(
            run_dir,
            outliers_doc,
            cfg,
            mock=mock,
            fixtures_dir=FIXTURES_DIR,
            token=keys.apify if (keys and keys.apify) else None,
            transport=transport,
            log=log,
        )
        transcripts_summary = transcribe.count(transcript_statuses)

    status = STATUS_PARTIAL if run_warnings else STATUS_OK
    finished_at = datetime.now(timezone.utc).isoformat()
    updated = store.update_run(
        run_dir,
        stages={
            "research": {
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "reels_total": len(scored),
                "selected": len(selection.selected),
                "backfill": len(selection.backfill),
                "excluded": len(selection.excluded),
                "videos": videos_summary,
                "frames": frames_summary,
                "transcripts": transcripts_summary,
            }
        },
        costs={
            "apify": {
                "estimate_usd": estimate.total_usd,
                "transcripts_usd": estimate.transcripts_usd,
                "max_items": estimate.max_items,
            }
        },
        warnings=warnings,
    )

    _print_summary(accounts, account_status, scored, baselines, cfg["format_accounts"])

    result = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "mode": mode,
        "status": status,
        "accounts": len(accounts),
        "format_accounts": len(cfg["format_accounts"]),
        "reels_total": len(scored),
        "selected": len(selection.selected),
        "backfill": len(selection.backfill),
        "excluded": len(selection.excluded),
        "videos": videos_summary,
        "frames": frames_summary,
        "transcripts": transcripts_summary,
        "warnings": updated.get("warnings", warnings),
    }
    print("RESULT " + json.dumps(result))
    return result
