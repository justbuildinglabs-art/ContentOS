"""Run directory layout, config loading, and rules for a creator project.

See the design spec's "Architecture" section for the per-creator state
tree this module owns (everything under `<project>/.contentos/`),
"Config defaults" for `DEFAULT_CONFIG`, and "Global Constraints" for the
atomic-JSON-write and Python-3.9-syntax rules every module here follows.
"""
from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "competitors": [],
    "format_accounts": [],
    "max_format_briefs": 2,
    "lookback_days": 90,
    "baseline_lookback_days": 365,
    "reels_per_account": 30,
    "min_reels_for_median": 8,
    "outlier_threshold": 3.0,
    "min_plays": 5000,
    "top_k_videos": 20,
    "backfill_pool": 10,
    "max_per_account": 4,
    "small_account_followers": 50000,
    "apify_max_charge_usd": 3.0,
    "apify_timeout_s": 900,
    "poll_interval_s": 5,
    "max_video_mb": 40,
    "max_video_seconds": 180,
    "frames_per_reel": 8,
    "frame_long_edge_px": 1024,
    "briefs": 5,
    "parallel_agents": 3,
    "qa_pass_threshold": 8,
    "length_tolerance": 0.10,
    "video_source": "cdn",
    # 0.3.0: transcripts (lib/transcribe.py) and the QA specificity floor.
    "transcripts": "auto",
    "apify_transcripts": False,
    "apify_transcript_usd_per_min": 0.0,
    "whisper_model": "",
    "min_specifics": 3,
}

TRANSCRIPT_MODES = ("auto", "local", "apify", "off")

# Every DEFAULT_CONFIG key whose default is a plain number (this excludes
# "competitors" and "format_accounts", both lists, "video_source", a
# string, and "max_format_briefs", a count that is allowed to be 0 --
# unlike every other key here, which the loop below requires to be
# strictly greater than 0. load_config requires each of these to hold a
# positive, non-bool int/float.
_NUMERIC_CONFIG_KEYS = tuple(
    key
    for key, default in DEFAULT_CONFIG.items()
    if key not in ("max_format_briefs", "apify_transcript_usd_per_min")
    and isinstance(default, (int, float))
    and not isinstance(default, bool)
)

# The subset of _NUMERIC_CONFIG_KEYS that must be whole numbers. Each of
# these is a count of things, and ends up as a list slice bound, a
# `range()` argument, or a batch size: `top_k_videos`/`backfill_pool`
# (lib/outliers.py), `frames_per_reel` (lib/frames.py), `briefs`
# (lib/director.py), `reels_per_account` and `min_reels_for_median`
# (the scrape size and the baseline minimum), `max_per_account`,
# `parallel_agents`, and the two polling numbers. A float there is
# either an outright TypeError or, worse, a silently truncated answer.
#
# Everything else stays int-or-float, because each one is a measurement
# a creator could reasonably want a fraction of: thresholds
# (`outlier_threshold`, `qa_pass_threshold`, `length_tolerance`), money
# (`apify_max_charge_usd`), and sizes/limits (`min_plays`,
# `small_account_followers`, `max_video_mb`, `max_video_seconds`,
# `frame_long_edge_px`, `baseline_lookback_days`). None of those indexes
# anything; the two that reach an int-shaped API (`frame_long_edge_px`
# in an ffmpeg filter string, `baseline_lookback_days` in Apify's
# "<n> days") are formatted into text, which a float survives.
_INT_CONFIG_KEYS = (
    "reels_per_account",
    "min_reels_for_median",
    "top_k_videos",
    "backfill_pool",
    "max_per_account",
    "frames_per_reel",
    "briefs",
    "parallel_agents",
    "apify_timeout_s",
    "poll_interval_s",
    "min_specifics",
)

_GITIGNORE_LINES = (
    ".env",
    "runs/*/videos/",
    "runs/*/frames/",
    "runs/*/prompts/",
    "setup-answers.json",
)

_RUN_MODES = ("live", "mock")

# init_run collision suffixing: new_run_id has one-second resolution, so a
# second call in the same second tries "<id>-02", "<id>-03", ... up to
# this many total run dirs for one base id before giving up. The suffix
# is always zero-padded to two digits: unpadded, "-9" would lexically
# outrank "-10" even though 9 < 10, breaking resolve_run's "latest".
_MAX_RUN_ID_SUFFIX = 99

# run.json fields that update_run merges one level deep instead of
# replacing outright.
_MERGE_KEYS = ("stages", "costs", "apify_runs")

# What a run directory's name looks like: `new_run_id`'s
# "%Y%m%d-%H%M%S", plus `init_run`'s optional two-digit collision
# suffix. `resolve_run` uses it to pick "latest" only among real runs,
# so a stray directory somebody dropped in `runs/` is never handed back
# as if it were one.
_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}(-\d{2})?$")


class ConfigError(Exception):
    """Raised when `.contentos/config.json` is missing or fails validation."""


class RunNotFound(Exception):
    """Raised when a run reference does not resolve to an existing run dir."""


class RunExists(Exception):
    """Raised when every collision suffix for a run id is already taken."""


def contentos_dir(project: Path) -> Path:
    """Return `<project>/.contentos`."""
    return Path(project) / ".contentos"


def runs_dir(project: Path) -> Path:
    """Return `<project>/.contentos/runs`."""
    return contentos_dir(project) / "runs"


def run_dir(project: Path, run_id: str) -> Path:
    """Return `<project>/.contentos/runs/<run_id>`."""
    return runs_dir(project) / run_id


def _validate_config(config: Dict[str, Any]) -> None:
    """Raise ConfigError, naming the offending key, on the first bad value."""
    competitors = config.get("competitors")
    valid_competitors = (
        isinstance(competitors, list)
        and bool(competitors)
        and all(isinstance(item, str) and item.strip() for item in competitors)
    )
    if not valid_competitors:
        raise ConfigError("competitors must be a non-empty list of non-empty strings")

    format_accounts = config.get("format_accounts")
    valid_format_accounts = isinstance(format_accounts, list) and all(
        isinstance(item, str) and item.strip() for item in format_accounts
    )
    if not valid_format_accounts:
        raise ConfigError("format_accounts must be a list of non-empty strings")

    for key in _NUMERIC_CONFIG_KEYS:
        value = config.get(key)
        is_positive_number = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        )
        if not is_positive_number:
            raise ConfigError(f"{key} must be a number greater than 0")
        if key in _INT_CONFIG_KEYS and not isinstance(value, int):
            raise ConfigError(f"{key} must be a whole number greater than 0")

    # max_format_briefs is excluded from _NUMERIC_CONFIG_KEYS above
    # because, unlike every key in that loop, 0 is a legitimate value
    # (a creator who wants no format-account briefs at all) rather than
    # an error -- so it gets its own "whole number, >= 0" check here.
    max_format_briefs = config.get("max_format_briefs")
    valid_max_format_briefs = (
        isinstance(max_format_briefs, int)
        and not isinstance(max_format_briefs, bool)
        and max_format_briefs >= 0
    )
    if not valid_max_format_briefs:
        raise ConfigError("max_format_briefs must be a whole number greater than or equal to 0")

    if not 1 <= config["qa_pass_threshold"] <= 10:
        raise ConfigError("qa_pass_threshold must be between 1 and 10")

    if not 0 < config["length_tolerance"] <= 1:
        raise ConfigError("length_tolerance must be greater than 0 and at most 1")

    if config.get("video_source") != "cdn":
        raise ConfigError('video_source must be "cdn"')

    if config.get("transcripts") not in TRANSCRIPT_MODES:
        raise ConfigError('transcripts must be one of "auto", "local", "apify", "off"')
    if not isinstance(config.get("apify_transcripts"), bool):
        raise ConfigError("apify_transcripts must be true or false")
    if not isinstance(config.get("whisper_model"), str):
        raise ConfigError("whisper_model must be a path string, or empty")

    # Like max_format_briefs, 0 is legitimate here (the paid backend is
    # off), so this key is outside the positive-number loop above.
    per_min = config.get("apify_transcript_usd_per_min")
    valid_per_min = (
        isinstance(per_min, (int, float)) and not isinstance(per_min, bool) and per_min >= 0
    )
    if not valid_per_min:
        raise ConfigError("apify_transcript_usd_per_min must be a number greater than or equal to 0")
    # A paid backend with no price would estimate $0 and slip past the
    # cost cap, so it must be priced before it can be switched on.
    if config["apify_transcripts"] and per_min == 0:
        raise ConfigError(
            "apify_transcripts is on but apify_transcript_usd_per_min is 0; "
            "set it to the per-minute price shown in your Apify console"
        )


def load_config(project: Path) -> Dict[str, Any]:
    """Load, merge over DEFAULT_CONFIG, and validate `.contentos/config.json`.

    Keys the file omits fall back to DEFAULT_CONFIG; keys the file has
    that DEFAULT_CONFIG does not know about are preserved as-is. Raises
    ConfigError when the file is missing, cannot be read (not UTF-8
    text, a directory in its place, permissions), is not valid JSON, is
    not a JSON object, or fails validation. Every one of those is the
    same thing from the creator's side -- the config is not usable --
    and every caller already handles ConfigError, so none of them
    should end in a traceback.
    """
    config_path = contentos_dir(project) / "config.json"
    if not config_path.exists():
        raise ConfigError("no .contentos/config.json; run: /contentos setup")

    try:
        overrides = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # ValueError covers both json.JSONDecodeError and
        # UnicodeDecodeError, which are both subclasses of it.
        raise ConfigError(f"invalid .contentos/config.json: {exc}") from exc

    if not isinstance(overrides, dict):
        raise ConfigError("invalid .contentos/config.json: must be a JSON object")

    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(overrides)
    _validate_config(config)

    # A handle a creator hand-edited into both lists stays a competitor
    # (design spec, "Reference files" -> Format accounts): drop it from
    # format_accounts here too, case-insensitively, so a hand-edited
    # config.json behaves exactly like one `setup` wrote.
    competitors = {handle.lower() for handle in config["competitors"]}
    config["format_accounts"] = [
        handle for handle in config["format_accounts"] if handle.lower() not in competitors
    ]

    return config


def new_run_id(now: Optional[datetime] = None) -> str:
    """Format `now` (default: the current local time) as a run id."""
    if now is None:
        now = datetime.now()
    return now.strftime("%Y%m%d-%H%M%S")


def resolve_run(project: Path, ref: str) -> Path:
    """Resolve a run reference (a run id, or "latest") to its directory.

    "latest" is the lexically greatest run-id-shaped directory name
    under `runs/`, which is also the chronologically newest since run
    ids sort that way (`_RUN_ID_RE`). Directories whose names are not
    run ids are ignored, so a stray folder in `runs/` is never returned
    as the latest run.

    Any other ref must be a plain directory name -- no path separator,
    no `..`, not absolute -- that exists directly under `runs/`. A run
    ref arrives straight off the command line, so anything that could
    walk out of `runs/` is refused rather than resolved. A missing or
    empty `runs/` directory, or an unresolvable ref, all raise
    RunNotFound(ref).
    """
    base = runs_dir(project)

    if ref == "latest":
        if not base.is_dir():
            raise RunNotFound(ref)
        names = sorted(
            entry.name
            for entry in base.iterdir()
            if entry.is_dir() and _RUN_ID_RE.match(entry.name)
        )
        if not names:
            raise RunNotFound(ref)
        return base / names[-1]

    if not ref or ref in (".", "..") or Path(ref).name != ref:
        raise RunNotFound(ref)

    candidate = base / ref
    if not candidate.is_dir():
        raise RunNotFound(ref)
    return candidate


def write_json_atomic(path: Path, obj: Any) -> None:
    """Write `obj` as JSON to `path` without ever leaving a partial file.

    Creates `path`'s parent directories if needed, writes to a `.tmp`
    sibling in the same directory, then `os.replace`s it into place, so
    a reader never observes a half-written file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def read_json(path: Path) -> Any:
    """Read and parse a JSON file, such as one written by write_json_atomic."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def init_run(project: Path, config: Dict[str, Any], mode: str) -> Path:
    """Create a fresh run directory with its initial `run.json`.

    `mode` must be "live" or "mock", else ValueError. `config` is stored
    as a deep-copied snapshot, so later changes to the caller's dict
    never leak into the run's recorded config (or vice versa).

    `new_run_id()` has one-second resolution, so a second call in the
    same second would otherwise collide on the same directory and
    silently overwrite the first run's `run.json`. When `runs/<id>`
    already exists, this tries `<id>-02`, `<id>-03`, ... up to `<id>-99`
    until it finds a free name. The suffix is zero-padded to two digits
    so every suffixed id is the same width: unpadded, "-9" would
    lexically outrank "-10" even though 9 < 10, so `resolve_run(project,
    "latest")` would silently return a stale run once 10 or more
    collisions piled up. Zero-padded, every suffixed id still sorts
    lexically after the bare id and before the next second's bare id,
    so "latest" keeps working no matter how many collisions pile up.
    Raises RunExists if every suffix up to 99 is already taken.
    """
    if mode not in _RUN_MODES:
        raise ValueError(f'mode must be "live" or "mock", got {mode!r}')

    base_run_id = new_run_id()
    run_id = base_run_id
    target_dir = run_dir(project, run_id)
    suffix = 2
    while target_dir.exists():
        if suffix > _MAX_RUN_ID_SUFFIX:
            raise RunExists(base_run_id)
        run_id = f"{base_run_id}-{suffix:02d}"
        target_dir = run_dir(project, run_id)
        suffix += 1

    target_dir.mkdir(parents=True)

    run_data = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "config": copy.deepcopy(config),
        "stages": {},
        "costs": {},
        "apify_runs": {},
        "warnings": [],
    }
    write_json_atomic(target_dir / "run.json", run_data)
    return target_dir


def update_run(run_dir: Path, **fields: Any) -> Dict[str, Any]:
    """Merge `fields` into `<run_dir>/run.json` and return the new state.

    `stages`, `costs`, and `apify_runs` are merged one level deep (key
    by key) into the existing dict rather than replaced outright.
    `warnings` accepts either a single string or a list of strings and
    appends to the existing list. Every other field replaces the
    top-level value.
    """
    run_path = Path(run_dir) / "run.json"
    run_data = read_json(run_path)

    for key, value in fields.items():
        if key in _MERGE_KEYS:
            run_data.setdefault(key, {}).update(value)
        elif key == "warnings":
            existing = run_data.setdefault("warnings", [])
            existing.extend([value] if isinstance(value, str) else value)
        else:
            run_data[key] = value

    write_json_atomic(run_path, run_data)
    return run_data


def ensure_gitignore(project: Path) -> None:
    """Ensure `<project>/.contentos/.gitignore` covers every gitignored path.

    Creates `.contentos/` first if it does not exist yet. Appends only
    the lines missing from an existing file, so creator edits (extra
    entries, comments, reordering) are never overwritten; calling this
    again once every line is present changes nothing.
    """
    target_dir = contentos_dir(project)
    target_dir.mkdir(parents=True, exist_ok=True)
    gitignore_path = target_dir / ".gitignore"

    existing_lines: List[str] = []
    if gitignore_path.exists():
        existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()

    missing_lines = [line for line in _GITIGNORE_LINES if line not in existing_lines]
    if not missing_lines:
        return

    text = "\n".join(existing_lines + missing_lines) + "\n"
    gitignore_path.write_text(text, encoding="utf-8")


def read_rules(project: Path) -> str:
    """Return the creator's rules.md, minus blank lines and `#` comments.

    Returns "" when the file is missing, or holds nothing but blank
    lines and comments.
    """
    rules_path = contentos_dir(project) / "rules.md"
    if not rules_path.exists():
        return ""

    kept_lines = []
    for raw_line in rules_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)
