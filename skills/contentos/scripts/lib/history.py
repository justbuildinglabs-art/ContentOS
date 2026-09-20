"""The weekly memory: what the creator filmed and posted, and every run so far.

Three things live here, none of which touch the network:

- `.contentos/log.json`, written by `mark`, records one state per brief
  (`filmed`, `posted`, or `skipped`, plus an optional post URL). It is
  keyed `<run_id>/<brief_id>` so the same brief id in two weeks never
  collides.
- `history.md`, rendered by `render_history`, is one row per run: date,
  cost, reels, briefs, how many passed QA, how many need the creator,
  and how many were filmed and posted.
- `briefed_shortcodes` lists every source reel an earlier run already
  turned into a brief, so research can skip it (`already_briefed`)
  instead of re-picking the same outliers inside its lookback window.

See the design spec's "0.3.0 changes", phase 2.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from lib import agents, store

LOG_STATES = ("filmed", "posted", "skipped")


def log_path(project: Path) -> Path:
    """Return `<project>/.contentos/log.json`."""
    return store.contentos_dir(project) / "log.json"


def load_log(project: Path) -> Dict[str, Any]:
    """The log as `{"briefs": {...}}`; empty when the file is missing or unreadable."""
    path = log_path(project)
    if not path.exists():
        return {"briefs": {}}
    try:
        doc = store.read_json(path)
    except (ValueError, OSError):
        return {"briefs": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("briefs"), dict):
        return {"briefs": {}}
    return doc


def _briefs(run_dir: Path) -> List[Dict[str, Any]]:
    """A run's ranked briefs from `03-briefs.json`; `[]` when there are none to read."""
    path = Path(run_dir) / "03-briefs.json"
    if not path.exists():
        return []
    try:
        doc = store.read_json(path)
    except (ValueError, OSError):
        return []
    briefs = doc.get("briefs") if isinstance(doc, dict) else None
    return [brief for brief in briefs or [] if isinstance(brief, dict)]


def mark(
    project: Path,
    run_id: str,
    brief_id: str,
    state: str,
    url: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Record `state` for one brief and return the stored entry.

    Raises `store.RunNotFound` for an unknown run and `ValueError` for a
    state outside `LOG_STATES` or a brief the run never ranked. A later
    mark for the same brief replaces the earlier one.
    """
    if state not in LOG_STATES:
        raise ValueError(f"state must be one of {', '.join(LOG_STATES)}")
    run_dir = store.resolve_run(project, run_id)
    brief_ids = {brief.get("brief_id") for brief in _briefs(run_dir)}
    if brief_id not in brief_ids:
        raise ValueError(f"run {run_dir.name} has no brief {brief_id}")

    stamp = (now or datetime.now(timezone.utc)).isoformat()
    entry: Dict[str, Any] = {"state": state, "updated_at": stamp}
    if url:
        entry["url"] = url
    log = load_log(project)
    log["briefs"][f"{run_dir.name}/{brief_id}"] = entry
    store.write_json_atomic(log_path(project), log)
    return entry


def list_runs(project: Path) -> List[Path]:
    """Every run directory that has a `run.json`, newest first."""
    base = store.runs_dir(project)
    if not base.is_dir():
        return []
    runs = [entry for entry in base.iterdir() if entry.is_dir() and (entry / "run.json").exists()]
    return sorted(runs, key=lambda entry: entry.name, reverse=True)


def briefed_shortcodes(project: Path, exclude_run_id: Optional[str] = None) -> Set[str]:
    """The source reel of every brief in every run, except `exclude_run_id`'s."""
    found: Set[str] = set()
    for run_dir in list_runs(project):
        if run_dir.name == exclude_run_id:
            continue
        for brief in _briefs(run_dir):
            shortcode = brief.get("shortCode")
            if isinstance(shortcode, str) and shortcode:
                found.add(shortcode)
    return found


def _run_row(run_dir: Path, log: Dict[str, Any]) -> List[str]:
    """One history table row for a run."""
    try:
        run_data = store.read_json(run_dir / "run.json")
    except (ValueError, OSError):
        run_data = {}
    research = (run_data.get("stages") or {}).get("research") or {}
    cost = ((run_data.get("costs") or {}).get("apify") or {}).get("estimate_usd")
    briefs = _briefs(run_dir)
    passed = needs_you = filmed = posted = 0
    for brief in briefs:
        brief_id = brief.get("brief_id")
        if not brief_id:
            continue
        state = agents.brief_state(run_dir, brief_id)
        if state["status"] == "pass":
            passed += 1
        elif state["needs_human"]:
            needs_you += 1
        logged = log["briefs"].get(f"{run_dir.name}/{brief_id}", {}).get("state")
        # A posted reel was filmed first, so it counts in both columns.
        if logged in ("filmed", "posted"):
            filmed += 1
        if logged == "posted":
            posted += 1
    return [
        run_dir.name,
        str(run_data.get("created_at") or "")[:10] or "-",
        str(run_data.get("mode") or "-"),
        f"${cost:.2f}" if isinstance(cost, (int, float)) else "-",
        str(research.get("reels_total", "-")),
        str(len(briefs)),
        str(passed),
        str(needs_you),
        str(filmed),
        str(posted),
    ]


def render_history(project: Path) -> str:
    """`history.md`: one row per run, newest first, then every logged post."""
    runs = list_runs(project)
    log = load_log(project)
    lines = ["# ContentOS history", ""]
    if not runs:
        lines.append("No runs yet.")
        return "\n".join(lines) + "\n"

    header = ["run", "date", "mode", "cost", "reels", "briefs", "passed", "needs you", "filmed", "posted"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for run_dir in runs:
        lines.append("| " + " | ".join(_run_row(run_dir, log)) + " |")
    lines.append("")
    lines.append("Cost is the Apify estimate. Apify bills on results actually returned.")
    lines.append("")

    posts = sorted(
        (key, entry) for key, entry in log["briefs"].items() if entry.get("state") == "posted"
    )
    lines.append("## Posted")
    lines.append("")
    if posts:
        for key, entry in posts:
            url = entry.get("url") or "no link recorded"
            lines.append(f"- {key}: {url} (marked {str(entry.get('updated_at', ''))[:10]})")
    else:
        lines.append("Nothing marked as posted yet. Record one with `mark --state posted`.")
    return "\n".join(lines).rstrip() + "\n"
