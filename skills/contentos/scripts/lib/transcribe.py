"""Stage 1 -- research, after keyframes: transcripts for the director (0.3.0).

The only module that touches whisper. See the design spec's "0.3.0
changes", "Transcripts" bullet, for the contract this implements.

`transcripts_for_selected` is called from `lib/research.py` right after
`frames.frames_for_selected`, and again, standalone, by `contentos.py
transcribe --run <id>` (`run_transcribe` below) to backfill a run. Each
selected reel gets `runs/<id>/transcripts/<shortCode>.txt`, one line per
segment in the form `[m:ss] text`, and a `transcript_status` in
`02-outliers.json`: `ok` (local whisper, or a `--mock` fixture), `apify`
(the paid fallback), `none` (no transcript and nothing went wrong), or
`failed` (a backend ran and broke).

Backend order under config `transcripts`:

- `"auto"` (default): local whisper-cpp when it is fully available, else
  the Apify fallback when `apify_transcripts` is true, else `none`.
- `"local"`: local only.
- `"apify"`: the Apify fallback only (when `apify_transcripts` is true).
- `"off"`: `none` for every reel.

Local means all three of: a `whisper-cli` (or `whisper-cpp`) binary on
PATH, a ggml model file (`find_model`), and `ffmpeg` to pull 16 kHz mono
audio out of the mp4. whisper-cpp is optional, exactly like ffmpeg:
without it nothing fails. A single reel's failure is recorded as its
status and never raises.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from lib import apify, store, video
from lib.http import HTTPError

STATUS_OK = "ok"
STATUS_APIFY = "apify"
STATUS_NONE = "none"
STATUS_FAILED = "failed"
STATUSES = (STATUS_OK, STATUS_APIFY, STATUS_NONE, STATUS_FAILED)

MODE_AUTO = "auto"
MODE_LOCAL = "local"
MODE_APIFY = "apify"
MODE_OFF = "off"

WHISPER_BINARIES = ("whisper-cli", "whisper-cpp")
MODEL_ENV = "CONTENTOS_WHISPER_MODEL"
# Relative to $HOME.
DEFAULT_MODEL_RELPATH = Path(".cache") / "contentos" / "whisper" / "ggml-base.en.bin"

# Per-process wall clock limits. A reel is at most `max_video_seconds`
# long (180 by default); base.en on a laptop CPU runs far faster than
# real time, so these only stop a hung process.
_FFMPEG_TIMEOUT_S = 120
_WHISPER_TIMEOUT_S = 900

_LOCAL_HINT = (
    "Transcripts skipped: whisper-cpp is not set up. "
    "To add them, run: brew install whisper-cpp, then put a model at "
    "~/.cache/contentos/whisper/ggml-base.en.bin. Or set apify_transcripts to true."
)
_APIFY_OFF_HINT = (
    "Transcripts skipped: transcripts is set to apify, but apify_transcripts is false."
)
_NO_KEY_HINT = "Transcripts skipped: the Apify fallback needs an Apify key."

# `[00:01:15.000 --> 00:01:19.000]   text` -- whisper-cli's own stdout.
_STDOUT_LINE = re.compile(
    r"^\[(\d+):(\d\d):(\d\d(?:[.,]\d+)?)\s*-->\s*[^\]]*\]\s*(.*)$"
)
# `/reel/<sc>/`, `/reels/<sc>/`, `/p/<sc>/`, `/tv/<sc>/` in an Instagram URL.
_URL_SHORTCODE = re.compile(r"/(?:reels?|p|tv)/([A-Za-z0-9_-]+)")

# The Apify output field holding the transcript. The actor's output
# shape is not verified yet (see apify.build_transcript_input), so every
# plausible name is tried in order.
_TRANSCRIPT_FIELDS = ("transcript", "transcriptText", "transcription", "transcripts")
_SEGMENT_TEXT_FIELDS = ("text", "transcript", "content")
_SEGMENT_START_FIELDS = ("start", "startTime", "start_time", "offset", "from")

Segment = Tuple[float, str]


def _default_log(message: str) -> None:
    """Default `log`: one line per call, to stderr (mirrors lib/research.py)."""
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Tool discovery


def find_whisper() -> Optional[str]:
    """Path to `whisper-cli` on PATH, else `whisper-cpp`, else None."""
    for name in WHISPER_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_ffmpeg() -> Optional[str]:
    """Path to `ffmpeg` on PATH, or None (same lookup as lib/frames.py)."""
    return shutil.which("ffmpeg")


def find_model(cfg: Mapping[str, Any], environ: Mapping[str, str] = os.environ) -> Optional[Path]:
    """The ggml model file to use, or None when there is none on disk.

    Order: config `whisper_model` when non-empty, else env
    `CONTENTOS_WHISPER_MODEL` when non-empty, else
    `$HOME/.cache/contentos/whisper/ggml-base.en.bin`. The first source
    that names a path decides; that path is returned only when it is an
    existing file. No HOME means no default.
    """
    configured = (cfg.get("whisper_model") or "").strip()
    from_env = (environ.get(MODEL_ENV) or "").strip()
    if configured:
        candidate: Optional[Path] = Path(configured).expanduser()
    elif from_env:
        candidate = Path(from_env).expanduser()
    else:
        home = environ.get("HOME")
        candidate = Path(home) / DEFAULT_MODEL_RELPATH if home else None
    if candidate is not None and candidate.is_file():
        return candidate
    return None


def local_available(cfg: Mapping[str, Any], environ: Mapping[str, str] = os.environ) -> bool:
    """Whether the local backend can run: whisper, a model, and ffmpeg."""
    return bool(find_whisper() and find_model(cfg, environ) and find_ffmpeg())


def _mode(cfg: Mapping[str, Any]) -> str:
    return cfg.get("transcripts") or MODE_AUTO


def apify_planned(cfg: Mapping[str, Any], environ: Mapping[str, str] = os.environ) -> bool:
    """Whether a run with this config would use the paid Apify fallback.

    True only when `apify_transcripts` is on and the mode allows it:
    `"apify"` always (it skips the local backend on purpose), `"auto"`
    only when the local backend is unavailable. `"local"` and `"off"`
    never pay.
    """
    if not cfg.get("apify_transcripts"):
        return False
    mode = _mode(cfg)
    if mode == MODE_APIFY:
        return True
    if mode == MODE_AUTO:
        return not local_available(cfg, environ)
    return False


def estimate_usd(cfg: Mapping[str, Any], environ: Mapping[str, str] = os.environ) -> float:
    """`transcripts_usd` for the research estimate: 0.0 unless Apify is planned."""
    if not apify_planned(cfg, environ):
        return 0.0
    return apify.estimate_transcripts_cost(
        cfg["top_k_videos"], cfg["max_video_seconds"], cfg["apify_transcript_usd_per_min"]
    )


# ---------------------------------------------------------------------------
# Parsing and formatting


def format_timestamp(seconds: float) -> str:
    """`[m:ss]`, minutes unpadded, seconds truncated (75.9 -> `[1:15]`)."""
    total = max(int(seconds), 0)
    return f"[{total // 60}:{total % 60:02d}]"


def format_lines(segments: List[Segment]) -> str:
    """One `[m:ss] text` line per segment, newline-terminated."""
    return "".join(f"{format_timestamp(start)} {text}\n" for start, text in segments)


def parse_whisper_csv(text: str) -> List[Segment]:
    """Parse whisper-cli `-ocsv` output: `start,end,"text"`, times in ms.

    The header row, blank rows, rows whose start is not a number, and
    rows whose text is only whitespace are skipped. whisper.cpp escapes
    a quote inside the text as `""`, which the csv module undoes.
    """
    segments: List[Segment] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        try:
            start_ms = float(row[0])
        except ValueError:
            continue
        body = ",".join(row[2:]).strip()
        if body:
            segments.append((round(start_ms / 1000.0, 3), body))
    return segments


def parse_whisper_stdout(text: str) -> List[Segment]:
    """Parse whisper-cli's printed `[hh:mm:ss.mmm --> ...]  text` lines.

    The fallback for when the CSV file was not written but the run
    printed its segments anyway.
    """
    segments: List[Segment] = []
    for line in text.splitlines():
        match = _STDOUT_LINE.match(line.strip())
        if not match:
            continue
        hours, minutes, secs, body = match.groups()
        body = body.strip()
        if not body:
            continue
        start = int(hours) * 3600 + int(minutes) * 60 + float(secs.replace(",", "."))
        segments.append((round(start, 3), body))
    return segments


def item_shortcode(item: Mapping[str, Any]) -> Optional[str]:
    """The shortCode an Apify output item is about: its field, else its URL."""
    shortcode = item.get("shortCode") or item.get("shortcode") or item.get("code")
    if isinstance(shortcode, str) and shortcode:
        return shortcode
    for key in ("url", "inputUrl", "postUrl"):
        value = item.get(key)
        if isinstance(value, str):
            match = _URL_SHORTCODE.search(value)
            if match:
                return match.group(1)
    return None


def _segment_from(entry: Any) -> Optional[Segment]:
    if isinstance(entry, str):
        text = entry.strip()
        return (0.0, text) if text else None
    if not isinstance(entry, Mapping):
        return None
    text = ""
    for key in _SEGMENT_TEXT_FIELDS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            break
    if not text:
        return None
    start = 0.0
    for key in _SEGMENT_START_FIELDS:
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            start = float(value)
            break
    return (start, text)


def segments_from_item(item: Mapping[str, Any]) -> List[Segment]:
    """Transcript segments from one Apify output item, defensively.

    Tries each name in `_TRANSCRIPT_FIELDS`. A string becomes one `0:00`
    segment per non-empty line (the actor gives no timestamps then). A
    list may hold strings or `{text, start}`-like dicts. Anything else,
    or nothing, is `[]`.
    """
    for field in _TRANSCRIPT_FIELDS:
        value = item.get(field)
        if isinstance(value, str):
            lines = [line.strip() for line in value.splitlines()]
            return [(0.0, line) for line in lines if line]
        if isinstance(value, list):
            segments = [_segment_from(entry) for entry in value]
            return [segment for segment in segments if segment is not None]
    return []


# ---------------------------------------------------------------------------
# Backends


def transcript_path(run_dir: Path, shortcode: str) -> Path:
    """`<run_dir>/transcripts/<shortCode>.txt` (the path the director prompt reads)."""
    return Path(run_dir) / "transcripts" / f"{shortcode}.txt"


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _has_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _no_audio_track(result: Any) -> bool:
    """Whether ffmpeg failed only because the video has no audio stream.

    Instagram sometimes serves a video-only stream. There is nothing to
    hear, so that is `none`, not a transcription failure.
    """
    stderr = getattr(result, "stderr", "") or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return "does not contain any stream" in stderr or "matches no streams" in stderr


def transcribe_local(
    mp4: Path,
    out_path: Path,
    whisper: str,
    model: Path,
    ffmpeg: str = "ffmpeg",
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    """Transcribe one mp4 with whisper-cpp into `out_path`; return a status.

    ffmpeg writes 16 kHz mono PCM to a temp wav, whisper-cli reads it
    with `-ocsv` so the segments land in `<prefix>.csv`, and that CSV
    (or, failing that, whisper's printed segment lines) becomes the
    transcript. `ok` when at least one segment was written, `none` when
    whisper ran fine but heard nothing or the video has no audio track, `failed` for any process error,
    timeout, or missing binary. Never raises for those.
    """
    with tempfile.TemporaryDirectory(prefix="contentos-whisper-") as tmp:
        wav = Path(tmp) / "audio.wav"
        prefix = Path(tmp) / "out"
        ffmpeg_cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(mp4),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(wav),
        ]
        whisper_cmd = [
            whisper, "-m", str(model), "-f", str(wav),
            "-ocsv", "-of", str(prefix), "-np",
        ]
        try:
            result = runner(ffmpeg_cmd, check=False, capture_output=True, timeout=_FFMPEG_TIMEOUT_S)
            if result.returncode != 0 and _no_audio_track(result):
                return STATUS_NONE
            if result.returncode != 0 or not _has_file(wav):
                return STATUS_FAILED
            result = runner(
                whisper_cmd, check=False, capture_output=True, text=True, timeout=_WHISPER_TIMEOUT_S
            )
        except (OSError, subprocess.SubprocessError):
            return STATUS_FAILED
        if result.returncode != 0:
            return STATUS_FAILED

        csv_path = prefix.with_suffix(".csv")
        if csv_path.exists():
            segments = parse_whisper_csv(csv_path.read_text(encoding="utf-8", errors="replace"))
        else:
            stdout = getattr(result, "stdout", "") or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            segments = parse_whisper_stdout(stdout)

    if not segments:
        return STATUS_NONE
    _write_text_atomic(Path(out_path), format_lines(segments))
    return STATUS_OK


def fetch_apify_transcripts(
    token: str,
    reels: List[Dict[str, Any]],
    cfg: Mapping[str, Any],
    transport: apify.Transport,
    sleep: Callable[[float], None] = time.sleep,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, List[Segment]]:
    """One `apify~instagram-reel-scraper` run for `reels`; segments by shortCode.

    Only these reels' own URLs are sent. The run is capped at
    `apify_max_charge_usd` and `apify_timeout_s`, like the research runs.
    A shortCode missing from the result simply has no transcript.
    Raises `apify.ApifyRunFailed`, `HTTPError`, or `OSError` for the
    caller to turn into `failed` statuses.
    """
    urls = [
        reel.get("url") or f"https://www.instagram.com/reel/{reel['shortCode']}/" for reel in reels
    ]
    run = apify.start_run(
        token,
        apify.build_transcript_input(urls),
        cfg["apify_max_charge_usd"],
        len(urls),
        cfg["apify_timeout_s"],
        transport,
        runs_path=apify.TRANSCRIPT_ACTOR_RUNS_PATH,
    )
    run = apify.wait_for_run(
        token, run, cfg["poll_interval_s"], cfg["apify_timeout_s"], transport, sleep=sleep, log=log
    )
    by_shortcode: Dict[str, List[Segment]] = {}
    for item in apify.iter_dataset_items(token, run.dataset_id, transport):
        shortcode = item_shortcode(item)
        segments = segments_from_item(item)
        if shortcode and segments and shortcode not in by_shortcode:
            by_shortcode[shortcode] = segments
    return by_shortcode


def _mock_copy(fixtures_dir: Optional[Path], shortcode: str, dest: Path) -> str:
    if fixtures_dir is None:
        return STATUS_NONE
    src = Path(fixtures_dir) / "transcripts" / f"{shortcode}.txt"
    if not src.is_file():
        return STATUS_NONE
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return STATUS_OK


def transcripts_for_selected(
    run_dir: Path,
    outliers: Dict[str, Any],
    cfg: Mapping[str, Any],
    mock: bool = False,
    fixtures_dir: Optional[Path] = None,
    runner: Callable[..., Any] = subprocess.run,
    token: Optional[str] = None,
    transport: Optional[apify.Transport] = None,
    log: Optional[Callable[[str], None]] = None,
    environ: Mapping[str, str] = os.environ,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, str]:
    """Transcribe every `selected` reel; rewrite `02-outliers.json` once.

    Mutates `outliers["selected"]` in place (`transcript_status` on each
    reel) and writes the whole dict back atomically at the end, the same
    contract as `frames.frames_for_selected`. A reel whose transcript
    file already exists keeps it (status `ok`, or `apify` when that is
    what it already said), so a re-run only fills gaps. Returns
    `{shortCode: status}`.
    """
    if log is None:
        log = _default_log
    run_dir = Path(run_dir)
    selected: List[Dict[str, Any]] = outliers.get("selected", [])
    statuses: Dict[str, str] = {}
    mode = _mode(cfg)

    pending: List[Dict[str, Any]] = []
    for reel in selected:
        shortcode = reel["shortCode"]
        out = transcript_path(run_dir, shortcode)
        if mode == MODE_OFF:
            statuses[shortcode] = STATUS_NONE
        elif _has_file(out):
            previous = reel.get("transcript_status")
            statuses[shortcode] = STATUS_APIFY if previous == STATUS_APIFY else STATUS_OK
        elif mock:
            statuses[shortcode] = _mock_copy(fixtures_dir, shortcode, out)
        else:
            pending.append(reel)

    if pending:
        use_local = mode in (MODE_AUTO, MODE_LOCAL) and local_available(cfg, environ)
        if use_local:
            whisper = find_whisper() or "whisper-cli"
            model = find_model(cfg, environ)
            ffmpeg = find_ffmpeg() or "ffmpeg"
            log(f"transcribing {len(pending)} reels with whisper-cpp")
            for reel in pending:
                shortcode = reel["shortCode"]
                mp4 = video.video_path(run_dir, shortcode)
                if reel.get("video_status") != "ok" or not _has_file(mp4):
                    statuses[shortcode] = STATUS_NONE
                    continue
                try:
                    status = transcribe_local(
                        mp4, transcript_path(run_dir, shortcode), whisper, model, ffmpeg, runner
                    )
                except OSError as exc:
                    log(f"{shortcode}: transcript failed: {exc}")
                    status = STATUS_FAILED
                statuses[shortcode] = status
        elif apify_planned(cfg, environ):
            statuses.update(_apify_statuses(run_dir, pending, cfg, token, transport, sleep, log))
        else:
            if mode == MODE_APIFY:
                log(_APIFY_OFF_HINT)
            else:
                log(_LOCAL_HINT)
            for reel in pending:
                statuses[reel["shortCode"]] = STATUS_NONE

    for reel in selected:
        reel["transcript_status"] = statuses[reel["shortCode"]]
    store.write_json_atomic(run_dir / "02-outliers.json", outliers)
    return statuses


def _apify_statuses(
    run_dir: Path,
    reels: List[Dict[str, Any]],
    cfg: Mapping[str, Any],
    token: Optional[str],
    transport: Optional[apify.Transport],
    sleep: Callable[[float], None],
    log: Callable[[str], None],
) -> Dict[str, str]:
    """The Apify fallback for `reels`: write each transcript, return statuses."""
    if not token:
        log(_NO_KEY_HINT)
        return {reel["shortCode"]: STATUS_NONE for reel in reels}
    if transport is None:
        transport = apify.HttpTransport()
    log(f"fetching transcripts for {len(reels)} reels from Apify")
    try:
        found = fetch_apify_transcripts(token, reels, cfg, transport, sleep=sleep, log=log)
    except (apify.ApifyRunFailed, HTTPError, OSError, KeyError, TypeError, ValueError) as exc:
        log(f"Apify transcripts failed: {exc}")
        return {reel["shortCode"]: STATUS_FAILED for reel in reels}

    statuses: Dict[str, str] = {}
    for reel in reels:
        shortcode = reel["shortCode"]
        segments = found.get(shortcode)
        if not segments:
            statuses[shortcode] = STATUS_NONE
            continue
        try:
            _write_text_atomic(transcript_path(run_dir, shortcode), format_lines(segments))
        except OSError as exc:
            log(f"{shortcode}: could not save the transcript: {exc}")
            statuses[shortcode] = STATUS_FAILED
            continue
        statuses[shortcode] = STATUS_APIFY
    return statuses


def count(statuses: Mapping[str, str]) -> Dict[str, int]:
    """`{"ok", "apify", "none", "failed"}` counts for a status map."""
    counts = {status: 0 for status in STATUSES}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    return counts


class OutliersMissing(Exception):
    """`<run_dir>/02-outliers.json` does not exist yet (exit 2)."""


def run_transcribe(
    project: Path,
    run_ref: str,
    cfg: Mapping[str, Any],
    token: Optional[str],
    mock: bool = False,
    transport: Optional[apify.Transport] = None,
    fixtures_dir: Optional[Path] = None,
    runner: Callable[..., Any] = subprocess.run,
    log: Optional[Callable[[str], None]] = None,
    environ: Mapping[str, str] = os.environ,
) -> Dict[str, Any]:
    """Run `contentos.py transcribe --run <id|latest>`: backfill one run.

    `store.RunNotFound` propagates (exit 2). A run without
    `02-outliers.json` raises `OutliersMissing` (exit 2). Returns the
    JSON-able summary `{"run_id", "transcripts": counts}`.
    """
    run_dir = store.resolve_run(Path(project), run_ref)
    outliers_path = run_dir / "02-outliers.json"
    if not outliers_path.exists():
        raise OutliersMissing(
            f"{run_dir.name}: no 02-outliers.json in this run; run `research` first"
        )
    outliers_doc = store.read_json(outliers_path)
    statuses = transcripts_for_selected(
        run_dir,
        outliers_doc,
        cfg,
        mock=mock,
        fixtures_dir=fixtures_dir,
        runner=runner,
        token=token,
        transport=transport,
        log=log,
        environ=environ,
    )
    return {"run_id": run_dir.name, "transcripts": count(statuses)}
