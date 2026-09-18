"""Stage 2 -- direct: the four director subcommands, end to end.

`lib/director.py` holds the deterministic pieces of Stage 2 (schemas,
prompt builders, the patterns check, the ranking formula). This module
is the layer above it: everything the four `contentos.py` subcommands
need in order to turn a run directory plus a creator's project into
either a dispatch prompt, a verified analysis, or a set of ranked
briefs. `contentos.py`'s handlers stay thin -- resolve `--project`,
call one function here, print what it returns -- exactly like the
`research` and `frames` handlers do.

- `run_direct_prompt` / `run_synth_prompt` return the text the skill
  hands to the `contentos:content-director` subagent (design spec,
  "Stage 2 -- direct"). Both refuse, with exit 2, when the run, the
  reel, its keyframes, or `creator.md` are not there to build a prompt
  from; a prompt that points at files the subagent cannot read is worse
  than no prompt at all.
- `verify_direct` / `verify_synth` are the `verify --stage
  direct|synth` half. `verify_direct` coerces the subagent's JSON,
  re-validates it, checks the handful of things coercion cannot repair
  (empty prose, no beats), records `analysis_status` on the reel, and
  either rewrites the coerced analysis in place or exits 7 with every
  problem on stderr, so the skill can re-dispatch once and then mark
  the reel failed.
- `run_rank` is the deterministic `rank` subcommand: collect every
  valid analysis, rank them with `director.rank_briefs`, write
  `03-briefs.json` and `briefs.md`, and record the stage in `run.json`.
  `--mock` first seeds `03-analyses/` and `03-patterns.md` from the
  committed fixtures, so a mock run reaches briefs without dispatching
  a single subagent.

Nothing here touches the network, and nothing writes outside the run
directory it was pointed at.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib import codes, director, store

# Repo root / "fixtures": four directories up from this file (lib ->
# scripts -> contentos -> skills -> repo root). The same directory
# `lib/research.py` serves `--mock` research from; `rank --mock` reads
# `analyses/<shortCode>.json` and `patterns.sample.md` out of it.
FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures"
ANALYSES_FIXTURES_SUBDIR = "analyses"
PATTERNS_FIXTURE_NAME = "patterns.sample.md"

# frames_status values a reel can be analyzed from. Anything else
# (`pending`, `failed`, `no_video`) means the director would have no
# image to read, so `direct-prompt` refuses rather than dispatching a
# subagent that can only fail.
ANALYZABLE_FRAMES_STATUSES = ("ok", "cover_only")

# 02-outliers.json per-reel field `verify --stage direct` records, so
# `rank` (and the skill) can tell a reel that was never analyzed from
# one whose analysis was rejected.
ANALYSIS_STATUS_OK = "ok"
ANALYSIS_STATUS_FAILED = "failed"

# Analysis fields that must carry real prose. `coerce_analysis` fills a
# missing string with "", which passes the schema but leaves the brief
# empty, so these are checked separately (see `_content_problems`).
REQUIRED_TEXT_FIELDS = (
    "brief_title",
    "why_it_worked",
    "transferable_mechanism",
    "adaptation",
    "avoid",
)


class DirectError(Exception):
    """A Stage 2 failure, carrying the exit code `contentos.py` returns.

    `exit_code` is 2 for every usage problem (no such run, no such
    reel, no frames, no `creator.md`, nothing to rank) and 7 for a
    verification failure. The message is what goes to stderr; a
    verification failure joins one line per problem with newlines, so
    the creator (and the skill's re-dispatch) sees all of them at once.
    """

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _default_log(message: str) -> None:
    """Default `log`: one line per call, to stderr (mirrors lib/research.py)."""
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Shared lookups
# ---------------------------------------------------------------------------


def _resolve_run(project: Path, run_ref: str) -> Path:
    """`store.resolve_run`, with `RunNotFound` turned into a usage error."""
    try:
        return store.resolve_run(Path(project), run_ref)
    except store.RunNotFound as exc:
        raise DirectError(f"{exc}: no such run; run `research` first", codes.EXIT_USAGE) from exc


def _read_outliers(run_dir: Path) -> Dict[str, Any]:
    """This run's `02-outliers.json`, or a usage error naming what is missing."""
    outliers_path = run_dir / "02-outliers.json"
    if not outliers_path.exists():
        raise DirectError(
            f"{run_dir.name}: no 02-outliers.json in this run; run `research` first",
            codes.EXIT_USAGE,
        )
    try:
        doc = store.read_json(outliers_path)
    except (ValueError, OSError) as exc:
        raise DirectError(f"{outliers_path}: cannot read: {exc}", codes.EXIT_USAGE) from exc
    if not isinstance(doc, dict):
        raise DirectError(f"{outliers_path}: expected a JSON object", codes.EXIT_USAGE)
    return doc


def _selected_reel(outliers_doc: Dict[str, Any], shortcode: str, run_name: str) -> Dict[str, Any]:
    """The `selected` reel named `shortcode`, or a usage error."""
    for reel in outliers_doc.get("selected") or []:
        if reel.get("shortCode") == shortcode:
            return reel
    raise DirectError(
        f"{shortcode}: not in this run's selected reels ({run_name}); "
        "only selected reels are analyzed",
        codes.EXIT_USAGE,
    )


def _creator_md(project: Path) -> Path:
    """`<project>/.contentos/creator.md`, or a usage error when it is missing."""
    creator_md = store.contentos_dir(Path(project)) / "creator.md"
    if not creator_md.exists():
        raise DirectError(
            f"{creator_md}: no creator.md; run: /contentos setup", codes.EXIT_USAGE
        )
    return creator_md


def _frames_status(run_dir: Path, shortcode: str) -> Optional[str]:
    """This reel's `frames_status`, or None when the run cannot say.

    Only `verify` uses this, and only to decide whether to require
    beats, so a run whose `02-outliers.json` is missing or unreadable
    answers None (do not require beats) rather than failing the whole
    verification over bookkeeping this check can do without.
    """
    outliers_path = run_dir / "02-outliers.json"
    if not outliers_path.exists():
        return None
    try:
        doc = store.read_json(outliers_path)
    except (ValueError, OSError):
        return None
    if not isinstance(doc, dict):
        return None
    for reel in doc.get("selected") or []:
        if reel.get("shortCode") == shortcode:
            return reel.get("frames_status")
    return None


def _record_analysis_status(run_dir: Path, shortcode: str, status: str) -> None:
    """Record `analysis_status` on one `selected` reel in `02-outliers.json`.

    A run with no `02-outliers.json` (or one that does not list this
    reel) is left alone rather than raising: `verify` has already
    decided the analysis's fate by the time this is called, and failing
    the whole command over the bookkeeping write would throw that
    result away.
    """
    outliers_path = run_dir / "02-outliers.json"
    if not outliers_path.exists():
        return
    try:
        doc = store.read_json(outliers_path)
    except (ValueError, OSError):
        return
    if not isinstance(doc, dict):
        return

    changed = False
    for reel in doc.get("selected") or []:
        if reel.get("shortCode") == shortcode:
            reel["analysis_status"] = status
            changed = True
    if changed:
        store.write_json_atomic(outliers_path, doc)


# ---------------------------------------------------------------------------
# direct-prompt / synth-prompt
# ---------------------------------------------------------------------------


def run_direct_prompt(
    project: Path,
    run_ref: str,
    shortcode: str,
    references_dir: Path,
) -> str:
    """The `direct-prompt --run <id|latest> --shortcode <sc>` prompt text.

    Refuses with exit 2 (a `DirectError`) when the run does not
    resolve, the run has no `02-outliers.json` yet, the reel is not in
    `selected`, its `frames_status` is neither `ok` nor `cover_only`
    (there would be no image for the director to read), or the creator
    has no `creator.md`. Otherwise returns
    `director.build_director_prompt`'s text for `contentos.py` to print
    to stdout.
    """
    run_dir = _resolve_run(project, run_ref)
    outliers_doc = _read_outliers(run_dir)
    reel = _selected_reel(outliers_doc, shortcode, run_dir.name)

    frames_status = reel.get("frames_status")
    if frames_status not in ANALYZABLE_FRAMES_STATUSES:
        raise DirectError(
            f"{shortcode}: frames_status is {frames_status!r}, not ok or cover_only; "
            "run `frames` for this run first",
            codes.EXIT_USAGE,
        )

    creator_md = _creator_md(project)
    return director.build_director_prompt(
        run_dir,
        shortcode,
        Path(references_dir),
        creator_md,
        director.load_schema("analysis"),
    )


def run_synth_prompt(project: Path, run_ref: str, references_dir: Path) -> str:
    """The `synth-prompt --run <id>` prompt text for the one synthesis dispatch.

    Refuses with exit 2 when the run does not resolve, when
    `03-analyses/` holds no `*.json` files yet (there is nothing to
    synthesize from), or when `creator.md` is missing.
    """
    run_dir = _resolve_run(project, run_ref)
    analyses = sorted((run_dir / "03-analyses").glob("*.json"))
    if not analyses:
        raise DirectError(
            f"{run_dir.name}: no analyses in 03-analyses/; run the director dispatches first",
            codes.EXIT_USAGE,
        )
    creator_md = _creator_md(project)
    return director.build_synth_prompt(run_dir, Path(references_dir), creator_md)


# ---------------------------------------------------------------------------
# verify --stage direct|synth
# ---------------------------------------------------------------------------


def _read_analysis(path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Read one analysis file; return `(object or None, problems)`.

    A missing file, unreadable content, and a top-level value that is
    not an object are each one problem line and no object, so the caller
    reports them and stops rather than coercing `None` into an analysis
    full of empty strings. `ValueError` covers both ways the content can
    be unreadable: `json.JSONDecodeError` for bad JSON and
    `UnicodeDecodeError` for bytes that are not UTF-8 at all (a
    subagent that wrote an image, say). Both subclass `ValueError`, so
    one clause catches them without naming either.
    """
    if not path.exists():
        return None, [f"{path}: no analysis file was written"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return None, [f"{path}: cannot read as JSON: {exc}"]
    if not isinstance(raw, dict):
        return None, [f"{path}: expected a JSON object at the top level"]
    return raw, []


def _content_problems(analysis: Dict[str, Any], frames_status: Optional[str]) -> List[str]:
    """The checks `coerce_analysis` cannot make pass on its own.

    Coercion fills a missing string with `""` and a malformed
    `structure` with `[]`: both satisfy the schema, and both leave the
    creator with an empty brief. So every field a brief actually
    renders must hold real text, and a reel that has real keyframes
    (`frames_status: ok`) must come back with at least one beat. A
    `cover_only` reel is exempt: one still image cannot be broken into
    beats.
    """
    problems: List[str] = []
    for field in REQUIRED_TEXT_FIELDS:
        value = analysis.get(field)
        if not (isinstance(value, str) and value.strip()):
            problems.append(f"{field}: must not be empty")
    if frames_status == "ok" and not analysis.get("structure"):
        problems.append("structure: must have at least one beat for a reel with keyframes")
    return problems


def verify_direct(project: Path, run_ref: str, shortcode: Optional[str]) -> Path:
    """Verify, coerce, and rewrite one `03-analyses/<sc>.json` in place.

    Reads the file the `content-director` subagent claims to have
    written, repairs it with `director.coerce_analysis` (clamped
    scores, unknown enums mapped to their safe fallback, malformed
    lists dropped), re-validates the repaired object against
    `schemas/analysis.schema.json`, and then applies the checks
    coercion cannot satisfy (see `_content_problems`).

    On success the coerced analysis replaces the file atomically, the
    reel is marked `analysis_status: "ok"`, and the path is returned
    for `contentos.py` to print. On any problem the file is left
    exactly as the subagent wrote it (a half-repaired analysis on disk
    would be harder to debug than the original), the reel is marked
    `analysis_status: "failed"`, and a `DirectError` carrying every
    problem line and exit code 7 is raised.
    """
    run_dir = _resolve_run(project, run_ref)
    if not shortcode:
        raise DirectError("verify --stage direct needs --shortcode", codes.EXIT_USAGE)

    analysis_path = run_dir / "03-analyses" / f"{shortcode}.json"
    raw, problems = _read_analysis(analysis_path)

    coerced: Optional[Dict[str, Any]] = None
    if raw is not None:
        coerced = director.coerce_analysis(raw)
        problems.extend(director.validate_analysis(coerced))
        problems.extend(_content_problems(coerced, _frames_status(run_dir, shortcode)))

    if coerced is None or problems:
        _record_analysis_status(run_dir, shortcode, ANALYSIS_STATUS_FAILED)
        raise DirectError("\n".join(problems), codes.EXIT_VERIFY)

    store.write_json_atomic(analysis_path, coerced)
    _record_analysis_status(run_dir, shortcode, ANALYSIS_STATUS_OK)
    return analysis_path


def verify_synth(project: Path, run_ref: str) -> Path:
    """Check this run's `03-patterns.md` for the five required headings.

    Delegates to `director.verify_patterns` (missing file, empty file,
    a missing heading, or headings out of order) and raises a
    `DirectError` with exit code 7 carrying one line per problem.
    Returns the patterns path on success.
    """
    run_dir = _resolve_run(project, run_ref)
    patterns_path = run_dir / "03-patterns.md"
    problems = director.verify_patterns(patterns_path)
    if problems:
        raise DirectError("\n".join(problems), codes.EXIT_VERIFY)
    return patterns_path


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


def _seed_mock_analyses(
    run_dir: Path,
    selected: List[Dict[str, Any]],
    fixtures_dir: Path,
    log: Callable[[str], None],
) -> None:
    """Copy the committed fixture analyses and patterns into this run.

    `rank --mock` is what makes a demo run reach `briefs.md` with no
    subagent in the loop: every `selected` reel that has a
    `fixtures/analyses/<shortCode>.json` gets it copied into
    `03-analyses/`, and `fixtures/patterns.sample.md` becomes
    `03-patterns.md`. Both copies skip anything already on disk, so a
    real analysis from an actual dispatch is never overwritten by a
    fixture.
    """
    analyses_dir = run_dir / "03-analyses"
    fixtures_analyses = Path(fixtures_dir) / ANALYSES_FIXTURES_SUBDIR
    for reel in selected:
        shortcode = reel.get("shortCode")
        if not shortcode:
            continue
        source = fixtures_analyses / f"{shortcode}.json"
        dest = analyses_dir / f"{shortcode}.json"
        if not source.exists() or dest.exists():
            continue
        analyses_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        log(f"{shortcode}: seeded the fixture analysis (mock)")

    patterns_dest = run_dir / "03-patterns.md"
    patterns_source = Path(fixtures_dir) / PATTERNS_FIXTURE_NAME
    if patterns_source.exists() and not patterns_dest.exists():
        shutil.copyfile(patterns_source, patterns_dest)
        log("seeded the fixture 03-patterns.md (mock)")


def _collect_analyses(
    run_dir: Path,
    selected: List[Dict[str, Any]],
    log: Callable[[str], None],
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    """Every selected reel's valid analysis, plus why each other one was skipped.

    A reel is skipped when `verify --stage direct` already rejected it
    (`analysis_status: "failed"`), when no analysis file exists, when
    the file cannot be parsed, or when the coerced object still fails
    the schema. Each skip is one stderr line and one
    `{"shortCode", "reason"}` entry in `03-briefs.json`, so nothing
    disappears from the run without a reason the creator can read.
    """
    analyses: Dict[str, Dict[str, Any]] = {}
    skipped: List[Dict[str, str]] = []

    def skip(shortcode: str, reason: str) -> None:
        skipped.append({"shortCode": shortcode, "reason": reason})
        log(f"{shortcode}: skipped, {reason}")

    for reel in selected:
        shortcode = reel.get("shortCode")
        if not shortcode:
            continue
        if reel.get("analysis_status") == ANALYSIS_STATUS_FAILED:
            skip(shortcode, "its analysis failed verification")
            continue

        path = run_dir / "03-analyses" / f"{shortcode}.json"
        if not path.exists():
            # Named without the path: a reel that was simply never
            # dispatched is the common case here, and one absolute path
            # per un-analyzed reel would bury the real problems.
            skip(shortcode, "no analysis file was written")
            continue

        raw, problems = _read_analysis(path)
        if raw is None:
            skip(shortcode, problems[0])
            continue

        coerced = director.coerce_analysis(raw)
        errors = director.validate_analysis(coerced)
        if errors:
            skip(shortcode, "; ".join(errors))
            continue
        analyses[shortcode] = coerced

    return analyses, skipped


def run_rank(
    project: Path,
    run_ref: str,
    cfg: Dict[str, Any],
    mock: bool = False,
    fixtures_dir: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Run `contentos.py rank --run <id|latest> [--mock]`.

    Deterministic end to end: no model call, no network. Collects every
    `selected` reel's valid analysis, ranks them with
    `director.rank_briefs` (the design spec's `brief_score` formula,
    top `cfg["briefs"]`), writes `03-briefs.json` and `briefs.md`, and
    records the `direct` stage in `run.json`. `--mock` first seeds the
    fixture analyses and patterns (see `_seed_mock_analyses`).

    Raises a `DirectError` with exit code 2 when the run does not
    resolve, when it has no `02-outliers.json`, or when not one
    selected reel has a valid analysis -- there is nothing to rank, and
    an empty `briefs.md` would read like a real (empty) result.
    Returns the JSON-able summary `contentos.py` prints:
    `{"run_id", "analyzed", "briefs", "skipped"}`.
    """
    if log is None:
        log = _default_log
    run_dir = _resolve_run(project, run_ref)
    outliers_doc = _read_outliers(run_dir)
    selected = outliers_doc.get("selected") or []

    if mock:
        _seed_mock_analyses(
            run_dir, selected, FIXTURES_DIR if fixtures_dir is None else Path(fixtures_dir), log
        )

    analyses, skipped = _collect_analyses(run_dir, selected, log)
    if not analyses:
        raise DirectError(
            f"{run_dir.name}: no valid analysis in 03-analyses/; "
            "run the director dispatches first",
            codes.EXIT_USAGE,
        )

    briefs = director.rank_briefs(
        analyses, selected, cfg["briefs"], run_dir, max_format_briefs=cfg["max_format_briefs"]
    )
    ranked_at = datetime.now(timezone.utc).isoformat()

    store.write_json_atomic(
        run_dir / "03-briefs.json",
        {
            "briefs": briefs,
            "ranked_at": ranked_at,
            "analyzed": len(analyses),
            "skipped": skipped,
        },
    )
    (run_dir / "briefs.md").write_text(director.render_briefs_md(briefs), encoding="utf-8")

    store.update_run(
        run_dir,
        stages={
            "direct": {
                "status": "ok",
                "analyzed": len(analyses),
                "briefs": len(briefs),
                "finished_at": ranked_at,
            }
        },
    )

    return {
        "run_id": run_dir.name,
        "analyzed": len(analyses),
        "briefs": len(briefs),
        "skipped": skipped,
    }
