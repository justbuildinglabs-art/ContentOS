"""Run directory layout, config loading, and rules for a founder project.

See the design spec's "Architecture" section for the per-founder state
tree this module owns (everything under `<project>/.contentos/`),
"Config defaults" for `DEFAULT_CONFIG`, and "Global Constraints" for the
atomic-JSON-write and Python-3.9-syntax rules every module here follows.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "competitors": [],
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
}

# Every DEFAULT_CONFIG key whose default is a plain number (this excludes
# "competitors", a list, and "video_source", a string). load_config
# requires each of these to hold a positive, non-bool int/float.
_NUMERIC_CONFIG_KEYS = tuple(
    key
    for key, default in DEFAULT_CONFIG.items()
    if isinstance(default, (int, float)) and not isinstance(default, bool)
)

_GITIGNORE_LINES = (
    ".env",
    "runs/*/videos/",
    "runs/*/frames/",
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

    for key in _NUMERIC_CONFIG_KEYS:
        value = config.get(key)
        is_positive_number = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        )
        if not is_positive_number:
            raise ConfigError(f"{key} must be a number greater than 0")

    if not 1 <= config["qa_pass_threshold"] <= 10:
        raise ConfigError("qa_pass_threshold must be between 1 and 10")

    if not 0 < config["length_tolerance"] <= 1:
        raise ConfigError("length_tolerance must be greater than 0 and at most 1")

    if config.get("video_source") != "cdn":
        raise ConfigError('video_source must be "cdn"')


def load_config(project: Path) -> Dict[str, Any]:
    """Load, merge over DEFAULT_CONFIG, and validate `.contentos/config.json`.

    Keys the file omits fall back to DEFAULT_CONFIG; keys the file has
    that DEFAULT_CONFIG does not know about are preserved as-is. Raises
    ConfigError when the file is missing, is not valid JSON, is not a
    JSON object, or fails validation.
    """
    config_path = contentos_dir(project) / "config.json"
    if not config_path.exists():
        raise ConfigError("no .contentos/config.json; run: /contentos setup")

    try:
        overrides = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid .contentos/config.json: {exc}") from exc

    if not isinstance(overrides, dict):
        raise ConfigError("invalid .contentos/config.json: must be a JSON object")

    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(overrides)
    _validate_config(config)
    return config


def new_run_id(now: Optional[datetime] = None) -> str:
    """Format `now` (default: the current local time) as a run id."""
    if now is None:
        now = datetime.now()
    return now.strftime("%Y%m%d-%H%M%S")


def resolve_run(project: Path, ref: str) -> Path:
    """Resolve a run reference (a run id, or "latest") to its directory.

    "latest" is the lexically greatest directory name under `runs/`,
    which is also the chronologically newest since run ids sort that
    way. Any other ref must name an existing directory directly under
    `runs/`. A missing or empty `runs/` directory, or an unresolvable
    ref, all raise RunNotFound(ref).
    """
    base = runs_dir(project)

    if ref == "latest":
        if not base.is_dir():
            raise RunNotFound(ref)
        names = sorted(entry.name for entry in base.iterdir() if entry.is_dir())
        if not names:
            raise RunNotFound(ref)
        return base / names[-1]

    candidate = base / ref if ref else None
    if candidate is None or not candidate.is_dir():
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
    the lines missing from an existing file, so founder edits (extra
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
    """Return the founder's rules.md, minus blank lines and `#` comments.

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
