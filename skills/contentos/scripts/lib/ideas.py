"""The weekly ideas ledger, `.contentos/ideas.json` (design spec, "0.4.0 changes").

One entry per outlier idea ever shown, keyed by its source shortCode.
Only `rank` writes it: it forgets the run it is re-ranking, closes
entries from what other runs recorded, offers the open ones as carried
ideas, and records the briefs it just ranked. Format fill ideas are
never recorded; each week's synthesis makes fresh ones.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from lib import agents, history, store

LEDGER_VERSION = 1
REEL_SNAPSHOT_KEYS = (
    "shortCode", "url", "ownerUsername", "source_kind", "timestamp", "plays",
    "outlier_ratio", "viral_proof",
)
_LOG_CLOSES = ("filmed", "posted", "skipped")


def _empty() -> Dict[str, Any]:
    return {"version": LEDGER_VERSION, "ideas": {}}


def ledger_path(project: Path) -> Path:
    """Return `<project>/.contentos/ideas.json`."""
    return store.contentos_dir(project) / "ideas.json"


def _valid_pair(pair: Any) -> bool:
    return (
        isinstance(pair, dict)
        and isinstance(pair.get("run_id"), str)
        and isinstance(pair.get("brief_id"), str)
    )


def load_ledger(project: Path) -> Dict[str, Any]:
    """The ledger; an empty one when the file is missing or unreadable, like `log.json`.

    A hand-edited ledger must never crash `rank`, so an entry that is
    not an object is dropped, and so is every `shown` pair that is not
    an object with a string `run_id` and `brief_id` (a `shown` that is
    not a list becomes `[]`, which `forget_run` then drops).
    """
    path = ledger_path(project)
    if not path.exists():
        return _empty()
    try:
        doc = store.read_json(path)
    except (ValueError, OSError):
        return _empty()
    if not isinstance(doc, dict) or not isinstance(doc.get("ideas"), dict):
        return _empty()
    entries = {}
    for key, entry in doc["ideas"].items():
        if not isinstance(entry, dict):
            continue
        shown = entry.get("shown")
        entry["shown"] = [pair for pair in shown if _valid_pair(pair)] if isinstance(shown, list) else []
        entries[key] = entry
    doc["ideas"] = entries
    return doc


def save_ledger(project: Path, ledger: Dict[str, Any]) -> None:
    store.write_json_atomic(ledger_path(project), ledger)


def forget_run(ledger: Dict[str, Any], run_id: str) -> None:
    """Drop `run_id`'s shown pairs, and any entry left with none, so a re-rank never double-counts."""
    for key in list(ledger["ideas"]):
        entry = ledger["ideas"][key]
        entry["shown"] = [pair for pair in entry.get("shown", []) if pair.get("run_id") != run_id]
        if not entry["shown"]:
            del ledger["ideas"][key]


def _closed_reason(
    project: Path,
    entry: Dict[str, Any],
    log: Dict[str, Any],
    run_ids: List[str],
    run_id: str,
    carry_weeks: int,
) -> Any:
    for pair in entry.get("shown", []):
        run_dir = store.run_dir(project, pair["run_id"])
        if run_dir.is_dir() and agents.latest_script_revision(run_dir, pair["brief_id"]) is not None:
            return "scripted"
    for pair in entry.get("shown", []):
        logged = log["briefs"].get(f"{pair['run_id']}/{pair['brief_id']}")
        state = logged.get("state") if isinstance(logged, dict) else None
        if state in _LOG_CLOSES:
            return state
    first_run = entry.get("first_run")
    first_run = first_run if isinstance(first_run, str) else ""
    if sum(1 for other in run_ids if first_run < other < run_id) >= carry_weeks:
        return "expired"
    return None


def close_entries(project: Path, ledger: Dict[str, Any], run_id: str, carry_weeks: int) -> None:
    """Close each open entry from scripts, `log.json` marks, and its age in runs.

    `run_id` is the run being ranked. Past a script or a mark, an open
    entry expires once at least `carry_weeks` runs (folders with a
    `run.json`, `history.list_runs`) sit strictly between its
    `first_run` and `run_id`, whether or not those runs showed it. So an
    idea a full list cut still expires on time, and `carry_weeks` 0
    expires every open entry.
    """
    log = history.load_log(project)
    run_ids = [run_dir.name for run_dir in history.list_runs(project)]
    for entry in ledger["ideas"].values():
        if entry.get("closed") is None:
            entry["closed"] = _closed_reason(project, entry, log, run_ids, run_id, carry_weeks)


def carry_candidates(ledger: Dict[str, Any], run_id: str) -> List[Dict[str, Any]]:
    """Open entries first found before `run_id`, each with `weeks_carried` added, in key order."""
    found = []
    for key in sorted(ledger["ideas"]):
        entry = ledger["ideas"][key]
        if entry.get("closed") is None and entry.get("first_run", "") < run_id:
            found.append(dict(entry, weeks_carried=len(entry.get("shown", []))))
    return found


def record_briefs(
    ledger: Dict[str, Any],
    run_id: str,
    briefs: List[Dict[str, Any]],
    reels: Dict[str, Dict[str, Any]],
) -> None:
    """Add this run's shown pairs; new ideas also get an entry with a reel snapshot."""
    for brief in briefs:
        kind = brief.get("kind")
        shortcode = brief.get("shortCode")
        if kind not in ("new", "carried") or not shortcode:
            continue
        pair = {"run_id": run_id, "brief_id": brief["brief_id"]}
        entry = ledger["ideas"].get(shortcode)
        if entry is None:
            reel = reels.get(shortcode, {})
            entry = {
                "idea_title": brief.get("idea_title"),
                "brief_title": brief.get("brief_title"),
                "first_run": run_id,
                "shown": [],
                "analysis_path": brief.get("analysis_path"),
                "frames_dir": brief.get("frames_dir"),
                "reel": {key: reel.get(key) for key in REEL_SNAPSHOT_KEYS},
                "closed": None,
            }
            ledger["ideas"][shortcode] = entry
        entry["shown"].append(pair)
