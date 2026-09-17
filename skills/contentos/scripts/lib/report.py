"""`report` and `status`: the founder-facing wrap-up for a run.

Both read a run directory that `write`/`qa` dispatches (Stage 3/4) have
already populated, and neither touches a subagent or the network.
`render_report` is markdown for a human; `status` is the same
information as a plain dict, for a script or the skill to poll. See the
design spec's "Stage 4 -- qa" for the `report`/`status` contract.

Per-brief state is `lib/agents.py`'s `brief_state`; this module is only
the layout around it -- the summary counts, the tables, and the
placeholder/needs-human/costs sections `contentos.py`'s `report --run`
and `status --run` handlers print.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from lib import agents, store


def _briefs(run_dir: Path) -> List[Dict[str, Any]]:
    """This run's ranked briefs from `03-briefs.json`, in ranked order; `[]` when there are none."""
    briefs_path = Path(run_dir) / "03-briefs.json"
    if not briefs_path.exists():
        return []
    try:
        doc = store.read_json(briefs_path)
    except (ValueError, OSError):
        return []
    return doc.get("briefs") or []


def _brief_states(run_dir: Path) -> List[Dict[str, Any]]:
    """`agents.brief_state` for every brief in this run, in ranked order."""
    return [agents.brief_state(run_dir, brief["brief_id"]) for brief in _briefs(run_dir)]


def _qa_confidence(qa_path_value: Any) -> Any:
    """The `confidence` field of a brief's latest QA JSON, or `"-"` when there is none to read."""
    if not qa_path_value:
        return "-"
    try:
        return store.read_json(Path(qa_path_value)).get("confidence", "-")
    except (ValueError, OSError):
        return "-"


def _qa_summary(state: Dict[str, Any]) -> str:
    """One line describing the latest QA review for a `needs_human` brief."""
    if not state["qa_path"]:
        return "no QA review was ever written"
    try:
        qa = store.read_json(Path(state["qa_path"]))
    except (ValueError, OSError):
        return f"could not read {state['qa_path']}"
    summary = qa.get("summary")
    return summary if summary else f"verdict: {qa.get('verdict')}"


def status(run_dir: Path) -> Dict[str, Any]:
    """The machine-readable state `status --run <id>` prints.

    `{run_id, mode, stages, briefs, costs, warnings}`, where `briefs`
    is `agents.brief_state` for every ranked brief, in ranked order.
    """
    run_dir = Path(run_dir)
    run_data = store.read_json(run_dir / "run.json")
    return {
        "run_id": run_data.get("run_id"),
        "mode": run_data.get("mode"),
        "stages": run_data.get("stages") or {},
        "briefs": _brief_states(run_dir),
        "costs": run_data.get("costs") or {},
        "warnings": run_data.get("warnings") or [],
    }


def render_report(run_dir: Path) -> str:
    """Render this run's `report.md`: summary, briefs, placeholders, needs-human, costs, warnings."""
    run_dir = Path(run_dir)
    run_data = store.read_json(run_dir / "run.json")
    briefs = _briefs(run_dir)
    titles = {brief["brief_id"]: brief.get("brief_title", "") for brief in briefs}
    states = _brief_states(run_dir)

    lines: List[str] = [f"# ContentOS report: {run_data.get('run_id')}", ""]

    lines.append("## Summary")
    lines.append("")
    lines.append(f"Mode: {run_data.get('mode')}")
    lines.append("")
    lines.append("Stages:")
    stages = run_data.get("stages") or {}
    if stages:
        for name in sorted(stages):
            lines.append(f"- {name}: {stages[name].get('status')}")
    else:
        lines.append("- no stages recorded yet")
    lines.append("")
    counts: Dict[str, int] = {}
    for state in states:
        counts[state["status"]] = counts.get(state["status"], 0) + 1
    lines.append("Briefs by status:")
    if counts:
        for status_name in sorted(counts):
            lines.append(f"- {status_name}: {counts[status_name]}")
    else:
        lines.append("- no briefs yet")
    lines.append("")

    lines.append("## Briefs")
    lines.append("")
    lines.append("| id | title | status | verdict | confidence | latest script |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for state in states:
        lines.append(
            "| {id} | {title} | {status} | {verdict} | {confidence} | {script} |".format(
                id=state["brief_id"],
                title=titles.get(state["brief_id"], ""),
                status=state["status"],
                verdict=state["verdict"] or "-",
                confidence=_qa_confidence(state["qa_path"]),
                script=state["script_path"] or "-",
            )
        )
    if not states:
        lines.append("No briefs yet.")
    lines.append("")

    lines.append("## Placeholders to fill")
    lines.append("")
    any_placeholders = False
    for state in states:
        if state["placeholders"]:
            any_placeholders = True
            lines.append(f"- {state['brief_id']}:")
            for placeholder in state["placeholders"]:
                lines.append(f"  - {placeholder}")
    if not any_placeholders:
        lines.append("None.")
    lines.append("")

    lines.append("## Needs a human")
    lines.append("")
    needing = [state for state in states if state["needs_human"]]
    if needing:
        for state in needing:
            lines.append(f"- {state['brief_id']}: {_qa_summary(state)}")
    else:
        lines.append("None.")
    lines.append("")

    lines.append("## Costs and timings")
    lines.append("")
    costs = run_data.get("costs") or {}
    if costs:
        for name in sorted(costs):
            value = costs[name]
            if isinstance(value, dict):
                detail = ", ".join(f"{key}: {value[key]}" for key in sorted(value))
                lines.append(f"- {name}: {detail}")
            else:
                lines.append(f"- {name}: {value}")
    else:
        lines.append("No costs recorded.")
    for name in sorted(stages):
        finished = stages[name].get("finished_at") or stages[name].get("started_at")
        if finished:
            lines.append(f"- {name} finished at {finished}")
    lines.append("")

    lines.append("## Warnings")
    lines.append("")
    warnings = run_data.get("warnings") or []
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("None.")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"
