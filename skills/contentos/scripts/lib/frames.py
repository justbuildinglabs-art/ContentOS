"""Stage 1 -- research, step 8: keyframes for the content-director subagent.

Claude accepts images, not video (design spec, "External API facts":
JPEG/PNG/GIF/WebP only), so the director reads a handful of JPEG
keyframes per reel instead of the mp4 itself. `frames_for_selected` is
called from `lib/research.py`'s research tail right after
`video.download_selected` (same run, same freshly-downloaded files),
and again, standalone, by `contentos.py`'s `frames --run <id>`
subcommand (`run_frames` below) -- mainly to retry reels whose video
was still expired when step 7 got to them, or to back-fill frames for
a run that skipped downloads entirely (`research --no-download`).

`extract_frames` shells out to `ffmpeg` once per timestamp;
`ffmpeg_available` gates whether that is even attempted. Without
ffmpeg, `frames_for_selected` falls back to the single `cover.jpg`
`video.download_selected` already saved. See the design spec's "Stage
1 -- research" step 8 for the algorithm and "Risks and mitigations"
for why cover-only is an acceptable degraded mode.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from lib import apify, http, store, video
from lib.env import Keys

# frame_times' four fixed early timestamps -- always attempted first,
# before any evenly-spaced fill (design spec step 8: "t = 0.0, 1.0,
# 2.0, 3.0 s then evenly to duration - 0.5 s"). Frames this early are
# the ones most likely to carry the hook.
_FIXED_TIMES = (0.0, 1.0, 2.0, 3.0)

_STATUS_OK = "ok"
_STATUS_FAILED = "failed"
_STATUS_COVER_ONLY = "cover_only"
_STATUS_NO_VIDEO = "no_video"
_STATUS_EXPIRED = "expired"

_FFMPEG_HINT = (
    "ffmpeg not found: install it (brew install ffmpeg) for keyframes; "
    "using cover images only"
)


def _default_log(message: str) -> None:
    """Default `log`: one line per call, to stderr (mirrors lib/research.py)."""
    print(message, file=sys.stderr)


def ffmpeg_available() -> bool:
    """Whether an `ffmpeg` binary is on PATH."""
    return shutil.which("ffmpeg") is not None


def _round_half_up(value: float, ndigits: int) -> float:
    """Round `value` to `ndigits` decimals, ties rounding away from zero.

    Python's own `round()` rounds ties to even (`round(3.125, 2) ==
    3.12`), which does not match the evenly-spaced timestamps this
    module's docstrings and tests pin down (e.g. `frame_times(4.0,
    8)`'s `3.13`, not `3.12`). `Decimal` is built from `repr(value)` --
    the float's own shortest round-tripping decimal string, not its
    exact (long) binary expansion -- so a "nice" value like `3.125`
    rounds the way it visually looks like it should.
    """
    quantum = Decimal(1).scaleb(-ndigits)
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def frame_times(duration_s: Optional[float], n: int) -> List[float]:
    """`n` extraction timestamps (seconds) for a reel `duration_s` long.

    Design spec step 8: always start with `0.0, 1.0, 2.0, 3.0`, each
    kept only if it lands at least half a second before the reel ends
    (`end = max(duration_s - 0.5, 0.0)` -- the same safety margin so
    `-ss` never seeks past the last decodable frame). Only once all
    four of those fit (i.e. `end` itself is past `3.0`) are the
    remaining `n - 4` slots spread evenly between `3.0` and `end`,
    landing exactly on `end`; a short reel that cannot fit all four
    fixed points is never padded further -- there is no safe stretch
    left to spread across. `duration_s is None` (duration unknown)
    uses the fixed points only, since without a known length there is
    no safe `end` to spread the rest across either.

    Every value is rounded to 2 decimals (`_round_half_up`),
    deduplicated, sorted, and capped at `n` entries -- never more than
    `n`, and never fewer than however many fixed points fit.
    """
    if duration_s is None:
        return list(_FIXED_TIMES[:n])

    end = max(duration_s - 0.5, 0.0)
    points: List[float] = [0.0]
    for candidate in _FIXED_TIMES[1:]:
        if candidate < end:
            points.append(candidate)

    if len(points) == len(_FIXED_TIMES) and len(points) < n:
        remaining = n - len(points)
        last = points[-1]
        step = (end - last) / remaining
        for i in range(1, remaining + 1):
            points.append(last + step * i)

    rounded = [_round_half_up(p, 2) for p in points]
    deduped: List[float] = []
    for value in rounded:
        if value not in deduped:
            deduped.append(value)
    deduped.sort()
    return deduped[:n]


@dataclass
class FramesResult:
    """Outcome of one `extract_frames` call.

    `status` is `ok` (at least one frame file exists once every
    timestamp has been attempted) or `failed` (none does). `cover_only`
    is never produced here -- it is `frames_for_selected`'s own
    fallback for when ffmpeg is not installed at all, a decision
    `extract_frames` (always assumes ffmpeg is present) never makes.
    """

    status: str  # "ok" | "cover_only" | "failed"
    paths: List[Path]


def _scale_filter(long_edge: int) -> str:
    """The `-vf` value that fits a frame inside `long_edge` without upscaling.

    `min(iw, L)` / `min(ih, L)` on whichever side is the long edge means
    a reel already smaller than `long_edge` (the fixture's 360x640,
    say) is left at its native size -- this `scale` filter never
    upscales, matching the design spec's "resized to a 1024 px long
    edge" (a cap, not a target). The other side is `-2`: ffmpeg's "keep
    the aspect ratio, round to the nearest even number" (JPEG's block
    encoder needs even dimensions).
    """
    return (
        f"scale='if(gt(iw,ih),min(iw,{long_edge}),-2)':"
        f"'if(gt(iw,ih),-2,min(ih,{long_edge}))'"
    )


def extract_frames(
    mp4: Path,
    out_dir: Path,
    times: List[float],
    long_edge: int,
    runner: Callable[..., Any] = subprocess.run,
) -> FramesResult:
    """Extract one JPEG per entry of `times` from `mp4` into `out_dir`.

    Frame `i` (1-based, in `times` order) is written to
    `out_dir/fNN.jpg`. Idempotent: a frame whose file already exists
    (non-empty) is left alone and never re-extracted (design spec:
    "Idempotent: skips existing frames"), so re-running `frames --run
    <id>` after a partial failure only fills the gaps. Each remaining
    frame is its own `ffmpeg -y -loglevel error -ss <t> -i <mp4>
    -frames:v 1 -vf scale=... -q:v 4 <out>` call -- one timestamp per
    process, so one bad seek never takes the others down with it -- and
    a non-zero return code, or a missing/empty output file afterward,
    just skips that frame. `FileNotFoundError` from `runner` (no
    `ffmpeg` binary at all -- `ffmpeg_available` should have already
    ruled this out for any real caller) stops immediately and reports
    `failed`, rather than repeating the same missing-binary error for
    every remaining timestamp. The result is `ok` once at least one
    frame file exists on disk by the time this returns (freshly written
    here, or already there from a prior run), else `failed`.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = _scale_filter(long_edge)
    paths: List[Path] = []

    for index, t in enumerate(times, start=1):
        out = out_dir / f"f{index:02d}.jpg"
        if not (out.exists() and out.stat().st_size > 0):
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{t:.2f}",
                "-i", str(mp4),
                "-frames:v", "1",
                "-vf", scale,
                "-q:v", "4",
                str(out),
            ]
            try:
                result = runner(cmd, check=False, capture_output=True)
            except FileNotFoundError:
                return FramesResult(status=_STATUS_FAILED, paths=paths)
            if result.returncode != 0:
                continue
        if out.exists() and out.stat().st_size > 0:
            paths.append(out)

    status = _STATUS_OK if paths else _STATUS_FAILED
    return FramesResult(status=status, paths=paths)


def _copy_fixture_frames(fixtures_dir: Path, out_dir: Path) -> None:
    """Copy the 8 committed sample frames into `out_dir` for `--mock`.

    Mirrors `video._mock_copy`'s "never re-copy an already-downloaded
    file" contract, frame by frame, so a second `--mock` run (or a
    `frames --run` re-run) leaves any already-copied frame untouched.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_dir = Path(fixtures_dir) / "frames" / "sample"
    for index in range(1, 9):
        name = f"f{index:02d}.jpg"
        dest = out_dir / name
        if dest.exists() and dest.stat().st_size > 0:
            continue
        shutil.copyfile(src_dir / name, dest)


def frames_for_selected(
    run_dir: Path,
    outliers: Dict[str, Any],
    cfg: Dict[str, Any],
    mock: bool = False,
    fixtures_dir: Optional[Path] = None,
    runner: Callable[..., Any] = subprocess.run,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, str]:
    """Cut keyframes for every `selected` reel; rewrite `02-outliers.json`.

    `outliers` is the loaded `02-outliers.json` dict (as
    `video.download_selected` left it); its `selected` reels are
    mutated in place (`frames_status` set on each) and the whole dict
    is rewritten back to `<run_dir>/02-outliers.json` atomically, once,
    at the end -- callers must pass the dict, not a copy, if they want
    to keep reading it afterward.

    Per reel: a `video_status` that is not `"ok"` (no video was ever
    downloaded, or it failed/expired/was too large) skips ffmpeg
    entirely and records `"no_video"` -- there is nothing to extract
    from. Otherwise, `--mock` copies the 8 committed sample frames
    (`_copy_fixture_frames`) and always records `"ok"`; with a real
    video, `ffmpeg_available()` decides between running
    `extract_frames` (using that call's own `ok`/`failed` status) and,
    when ffmpeg is not installed, falling back to whatever
    `video.cover_path` already saved -- `"cover_only"` when that cover
    exists, else `"failed"`. The missing-ffmpeg install hint
    (`_FFMPEG_HINT`) is logged at most once per call to this function,
    never once per reel.
    """
    if log is None:
        log = _default_log
    run_dir = Path(run_dir)
    selected: List[Dict[str, Any]] = outliers["selected"]
    statuses: Dict[str, str] = {}
    hinted = False

    for reel in selected:
        shortcode = reel["shortCode"]
        out_dir = run_dir / "frames" / shortcode

        if reel.get("video_status") != _STATUS_OK:
            status = _STATUS_NO_VIDEO
        elif mock:
            _copy_fixture_frames(fixtures_dir, out_dir)
            status = _STATUS_OK
        elif ffmpeg_available():
            times = frame_times(reel.get("duration_s"), cfg["frames_per_reel"])
            result = extract_frames(
                video.video_path(run_dir, shortcode),
                out_dir,
                times,
                cfg["frame_long_edge_px"],
                runner,
            )
            status = result.status
        else:
            if not hinted:
                log(_FFMPEG_HINT)
                hinted = True
            status = _STATUS_COVER_ONLY if video.cover_path(run_dir, shortcode).exists() else _STATUS_FAILED

        reel["frames_status"] = status
        statuses[shortcode] = status

    store.write_json_atomic(run_dir / "02-outliers.json", outliers)
    return statuses


class OutliersMissing(Exception):
    """Raised when `<run_dir>/02-outliers.json` does not exist yet.

    `frames --run <id>` needs Stage 1's `selected`/`backfill` reels and
    their `video_status`; a run that has not gotten that far has
    nothing for this to extract from.
    """


def run_frames(
    project: Path,
    run_ref: str,
    cfg: Dict[str, Any],
    keys: Keys,
    mock: bool = False,
    refresh_expired: bool = False,
    transport: Optional[apify.Transport] = None,
    downloader: Callable[..., http.DownloadResult] = http.download_to_file,
    fixtures_dir: Optional[Path] = None,
    runner: Callable[..., Any] = subprocess.run,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Run `contentos.py frames --run <id|latest> [--refresh-expired]`.

    Resolves `run_ref` (a run id, or `"latest"`) via `store.resolve_run`
    -- `store.RunNotFound` propagates for `contentos.py` to map to exit
    2 -- then loads that run's `02-outliers.json`, raising
    `OutliersMissing` (also exit 2) when research has not produced one
    yet.

    `refresh_expired`, when true and not `--mock`, re-scrapes every
    `selected` reel whose `video_status` is exactly `"expired"` via
    `video.refresh_video_url` (one Apify call per expired reel), then
    downloads the URL it returns over the same `video_path`, capped at
    `cfg["max_video_mb"]`, updating `video_status` in place. A reel
    `refresh_video_url` cannot get a fresh URL for (it returns `None`,
    already logged there) is simply left `"expired"`, which
    `frames_for_selected` below then records as `"no_video"` -- exactly
    like any other non-`"ok"` `video_status`.

    Either way, `frames_for_selected` does the real extraction work and
    its own atomic rewrite of `02-outliers.json`, covering both any
    refreshed `video_status` values and the new `frames_status` values
    in that one write. Returns the JSON-able summary `contentos.py`
    prints: `{"run_id", "frames": {"ok", "cover_only", "failed",
    "no_video"}}`.
    """
    if log is None:
        log = _default_log
    project = Path(project)
    run_dir = store.resolve_run(project, run_ref)

    outliers_path = run_dir / "02-outliers.json"
    if not outliers_path.exists():
        raise OutliersMissing(
            f"{run_dir.name}: no 02-outliers.json in this run; run `research` first"
        )
    outliers_doc = store.read_json(outliers_path)

    if refresh_expired and not mock:
        if transport is None:
            transport = apify.HttpTransport()
        token = keys.apify if (keys and keys.apify) else ""
        max_bytes = cfg["max_video_mb"] * 1024 * 1024
        for reel in outliers_doc.get("selected", []):
            if reel.get("video_status") != _STATUS_EXPIRED:
                continue
            shortcode = reel["shortCode"]
            fresh_url = video.refresh_video_url(token, shortcode, transport, log=log)
            if fresh_url:
                dest = video.video_path(run_dir, shortcode)
                reel["video_status"] = downloader(fresh_url, dest, max_bytes).status

    statuses = frames_for_selected(
        run_dir, outliers_doc, cfg, mock=mock, fixtures_dir=fixtures_dir, runner=runner, log=log
    )

    counts = {_STATUS_OK: 0, _STATUS_COVER_ONLY: 0, _STATUS_FAILED: 0, _STATUS_NO_VIDEO: 0}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1

    return {"run_id": run_dir.name, "frames": counts}
