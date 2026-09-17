"""Stage 1 -- research, step 7: download selected reels' mp4 + cover.

CDN video and cover URLs expire fastest right after scraping (design
spec, "Risks and mitigations"), so `download_selected` is called from
`lib/research.py` immediately after `02-outliers.json` is first
written, before the run's final `update_run`. For each `selected`
reel it downloads the mp4 to `video_path` and the cover jpg to
`cover_path` -- via the injected `downloader` (`lib/http.py`'s
`download_to_file` by default) against the real Apify/Instagram CDN,
or by copying the tiny committed fixture under `--mock` -- and, on any
non-`ok` video status, promotes the next reel from the `backfill` pool
in its place. `refresh_video_url` is the separate, later escape hatch
(`frames --refresh-expired`) for re-scraping a single reel whose CDN
URL has since expired or started answering 403.

See the design spec's "Stage 1 -- research" step 7 for the algorithm
and "Architecture" for the run-dir paths this module writes under.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from lib import apify, http, store
from lib.http import HTTPError

# Cap for cover-image downloads. Unlike the video (capped by the
# founder-configurable `max_video_mb`), a JPEG cover is always small,
# so this is a fixed generous ceiling rather than a config key.
COVER_MAX_BYTES = 5 * 1024 * 1024

# refresh_video_url's own Apify run: one post, one result, kept far
# under the research stage's own cost cap and timeouts since this is a
# single-reel re-scrape, not a full account run.
_REFRESH_MAX_CHARGE_USD = 0.05
_REFRESH_MAX_ITEMS = 1
_REFRESH_TIMEOUT_S = 120
_REFRESH_POLL_S = 5

_STATUS_OK = "ok"
_STATUS_TOO_LARGE = "too_large"
_STATUS_FAILED = "failed"

_WARN_OVER_HALF_FAILED = (
    "more than half of the selected reels failed to download; consider the "
    "apify/instagram-reel-scraper actor's paid downloadedVideo add-on "
    "($0.02/MB) as an alternative video source"
)


def _default_log(message: str) -> None:
    """Default `log`: one line per call, to stderr (mirrors lib/research.py).

    Duplicated rather than imported from `lib/research.py`: that module
    is `download_selected`'s caller, so importing it back here would
    make the two modules circular.
    """
    print(message, file=sys.stderr)


def video_path(run_dir: Path, shortcode: str) -> Path:
    """Return `<run_dir>/videos/<shortcode>.mp4` (design spec, "Architecture")."""
    return Path(run_dir) / "videos" / f"{shortcode}.mp4"


def cover_path(run_dir: Path, shortcode: str) -> Path:
    """Return `<run_dir>/frames/<shortcode>/cover.jpg` (design spec, "Architecture").

    Lives under `frames/<shortcode>/` rather than next to the video
    because `lib/frames.py` (Task 12) treats it as one of that reel's
    frames -- the fallback the director gets when ffmpeg is missing.
    """
    return Path(run_dir) / "frames" / shortcode / "cover.jpg"


@dataclass
class VideoReport:
    """Summary `download_selected` returns for one research run's downloads."""

    ok: List[str]  # shortCodes that ended with video_status "ok"
    skipped: List[Dict[str, str]]  # [{"shortCode": ..., "status": ...}]
    replaced_from_backfill: List[Dict[str, str]]  # [{"failed": ..., "promoted": ...}]
    warnings: List[str]


def _mock_copy(src: Path, dest: Path) -> str:
    """Copy a fixture file to `dest`, skipping an already-downloaded file.

    Mirrors `http.download_to_file`'s own "never re-download an
    existing non-empty file" contract for `--mock`, which bypasses the
    real `downloader` entirely and so would otherwise re-copy the
    fixture on every run.
    """
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return _STATUS_OK
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return _STATUS_OK


def _usable_url(url: Any) -> bool:
    """True when `url` is a string the downloader could actually fetch.

    A scrape can hand back `None`, an empty string, or a scheme-less
    host ("example.com/a.mp4") for `videoUrl`/`displayUrl`.
    `urllib.request.Request` raises `ValueError` ("unknown url type")
    on all of those, so they are screened out here rather than paid for
    with an exception mid-run.
    """
    return isinstance(url, str) and url.lower().startswith(("http://", "https://"))


def _download_status(
    downloader: Callable[..., http.DownloadResult],
    url: Any,
    dest: Path,
    max_bytes: int,
) -> str:
    """`downloader`'s status for `url`, or "failed" when it cannot be fetched.

    An unusable URL never reaches `downloader` at all; a `ValueError` or
    `TypeError` from a downloader that rejects the URL itself is mapped
    to the same `failed` status. Either way the reel simply fails like
    any other bad download -- it is promoted out of the way by the
    backfill pool -- instead of aborting `download_selected` before
    `02-outliers.json` is rewritten, which would leave every reel on
    disk stuck at `pending`.
    """
    if not _usable_url(url):
        return _STATUS_FAILED
    try:
        return downloader(url, dest, max_bytes).status
    except (ValueError, TypeError):
        return _STATUS_FAILED


def _process_reel(
    run_dir: Path,
    reel: Dict[str, Any],
    cfg: Dict[str, Any],
    mock: bool,
    downloader: Callable[..., http.DownloadResult],
    fixtures_dir: Optional[Path],
    log: Callable[[str], None],
) -> str:
    """Download one reel's video, then its cover; mutate `reel` in place.

    Returns the reel's `video_status`, the only thing
    `download_selected` needs to decide whether to promote a backfill
    reel in this one's place. A duration over `max_video_seconds`
    skips the video AND the cover entirely, without ever calling
    `downloader` (`too_large`, decided for free from data the scrape
    already returned). Otherwise both are attempted regardless of
    whether the video itself succeeded -- a valid cover is still
    useful with no video, matching `frames.py`'s cover-only fallback
    when ffmpeg is missing. A cover's own status never influences
    `video_status` or backfill promotion; it is only recorded on the
    reel (`cover_status`) and logged when it is not "ok".

    A missing or non-http(s) `videoUrl`/`displayUrl` is `failed`
    without calling `downloader`, and a `ValueError`/`TypeError` out of
    `downloader` is mapped to `failed` too (`_download_status`), so one
    malformed URL costs one reel rather than the whole run.
    """
    shortcode = reel["shortCode"]
    duration_s = reel["duration_s"]
    if duration_s is not None and duration_s > cfg["max_video_seconds"]:
        reel["video_status"] = _STATUS_TOO_LARGE
        return _STATUS_TOO_LARGE

    dest = video_path(run_dir, shortcode)
    if mock:
        video_status = _mock_copy(Path(fixtures_dir) / "sample.mp4", dest)
    else:
        max_bytes = cfg["max_video_mb"] * 1024 * 1024
        video_status = _download_status(downloader, reel.get("videoUrl"), dest, max_bytes)
    reel["video_status"] = video_status

    cover_dest = cover_path(run_dir, shortcode)
    if mock:
        cover_status = _mock_copy(Path(fixtures_dir) / "sample_cover.jpg", cover_dest)
    else:
        cover_status = _download_status(
            downloader, reel.get("displayUrl"), cover_dest, COVER_MAX_BYTES
        )
    reel["cover_status"] = cover_status
    if cover_status != _STATUS_OK:
        log(f"{shortcode}: cover download {cover_status}")

    return video_status


def download_selected(
    run_dir: Path,
    outliers: Dict[str, Any],
    cfg: Dict[str, Any],
    mock: bool,
    downloader: Callable[..., http.DownloadResult] = http.download_to_file,
    fixtures_dir: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> VideoReport:
    """Download every `selected` reel's video + cover, backfilling failures.

    `outliers` is the loaded `02-outliers.json` dict (design spec,
    "Stage 1 -- research" step 7); its `selected` and `backfill` lists
    are mutated in place and the whole dict is rewritten back to
    `<run_dir>/02-outliers.json` atomically, once, at the end --
    callers must pass the dict, not a copy, if they want to keep
    reading it afterward.

    `selected` is processed as a growing queue: whenever a reel ends
    with a non-`ok` `video_status` (`too_large`, `expired`, `blocked`,
    `failed`, ...), it stays in `selected` with that status, and the
    front of `backfill` is popped, appended to `selected`, and
    processed in its own turn later in the same loop -- until
    `backfill` runs out, after which further failures are simply left
    unpromoted. Every promotion is recorded in
    `replaced_from_backfill`; every non-`ok` reel (whether or not a
    replacement was available) is recorded in `skipped`.

    `mock` copies the two committed fixture files (`fixtures_dir /
    "sample.mp4"` / `"sample_cover.jpg"`) instead of calling
    `downloader`, and always succeeds. Real downloads cap the video at
    `cfg["max_video_mb"]` (MB -> bytes) and the cover at
    `COVER_MAX_BYTES`; an already-downloaded, non-empty file is never
    re-fetched (`downloader`'s own contract -- see
    `http.download_to_file`).

    `skipped` grows by one entry for every non-`ok` reel processed,
    including a promoted reel that goes on to fail itself -- so a
    backfill chain several reels deep can add several entries for the
    same original slot. If `len(skipped)` ends up more than half of
    `original_count` (the size of `selected` before this function
    added anything to it), one warning is added naming the
    `apify/instagram-reel-scraper` actor's paid `downloadedVideo`
    add-on as an alternative video source -- a blunt but honest signal
    that downloads are failing badly enough to need a different
    approach, regardless of how the failures were distributed.
    """
    if log is None:
        log = _default_log
    run_dir = Path(run_dir)
    selected: List[Dict[str, Any]] = outliers["selected"]
    backfill: List[Dict[str, Any]] = outliers["backfill"]
    original_count = len(selected)

    report = VideoReport(ok=[], skipped=[], replaced_from_backfill=[], warnings=[])

    index = 0
    while index < len(selected):
        reel = selected[index]
        shortcode = reel["shortCode"]
        video_status = _process_reel(run_dir, reel, cfg, mock, downloader, fixtures_dir, log)

        if video_status == _STATUS_OK:
            report.ok.append(shortcode)
        else:
            report.skipped.append({"shortCode": shortcode, "status": video_status})
            log(f"{shortcode}: video download {video_status}")
            if backfill:
                promoted = backfill.pop(0)
                selected.append(promoted)
                report.replaced_from_backfill.append(
                    {"failed": shortcode, "promoted": promoted["shortCode"]}
                )
        index += 1

    if original_count and 2 * len(report.skipped) > original_count:
        report.warnings.append(_WARN_OVER_HALF_FAILED)
        log(_WARN_OVER_HALF_FAILED)

    store.write_json_atomic(run_dir / "02-outliers.json", outliers)
    return report


def refresh_video_url(
    token: str,
    shortcode: str,
    transport: apify.Transport,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Re-scrape one reel by shortCode for a fresh, unexpired `videoUrl`.

    Runs a single-post Apify actor call (`resultsType: "posts"` against
    that one reel's own URL, `resultsLimit: 1`) capped at $0.05 and 120
    seconds -- far below the research stage's own per-account cost and
    timeout, since this replaces exactly one expired CDN link, not a
    full account scrape. Returns the first (only) item's `videoUrl`,
    or `None` when the run comes back with no items.

    Never raises: an `ApifyRunFailed` (the run terminated FAILED or
    ABORTED), an `http.HTTPError` (the REST call itself failed), or an
    `OSError` (a network-level failure) is logged and turned into
    `None`, the same "could not get a fresh URL" outcome as an empty
    dataset.
    """
    if log is None:
        log = _default_log
    actor_input = {
        "directUrls": [f"https://www.instagram.com/reel/{shortcode}/"],
        "resultsType": "posts",
        "resultsLimit": 1,
    }
    try:
        run = apify.start_run(
            token, actor_input, _REFRESH_MAX_CHARGE_USD, _REFRESH_MAX_ITEMS, _REFRESH_TIMEOUT_S, transport
        )
        run = apify.wait_for_run(token, run, _REFRESH_POLL_S, _REFRESH_TIMEOUT_S, transport, log=log)
        items = list(apify.iter_dataset_items(token, run.dataset_id, transport))
    except (apify.ApifyRunFailed, HTTPError, OSError) as exc:
        log(f"{shortcode}: refresh_video_url failed: {exc}")
        return None

    if not items:
        return None
    return items[0].get("videoUrl")
