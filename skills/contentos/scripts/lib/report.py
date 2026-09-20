"""`report` and `status`: the creator-facing wrap-up for a run.

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

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib import agents, store

# Sections whose placeholders only repeat or explain the ones in the lines
# the creator films; listing them again would double the to-do list.
_NOTE_SECTIONS = ("Production notes", "What changed vs source")

# Beats table columns, in the order the writer's template lays them out.
_BEAT_COLUMNS = ("visual", "spoken", "on screen")

_TOKEN_RE = re.compile(r"\[NEED[^\]]*\]")


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


def _reel_states(run_dir: Path) -> List[Dict[str, Any]]:
    """Per-reel pipeline state for every `selected` reel, in selection order.

    Four keys per reel -- `shortCode`, `video_status`, `frames_status`,
    `analysis_status` -- and nothing else. This is deliberately narrow:
    it is what the orchestrator needs to decide which reels are worth
    dispatching the director at, and it keeps it from having to read
    `02-outliers.json`, which carries the scraped captions and comments
    straight into its own context. A status the pipeline has not written
    yet is `None`. A run with no `02-outliers.json` (or an unreadable
    one) has no reels yet, which is an empty list, not an error.
    """
    try:
        outliers = store.read_json(Path(run_dir) / "02-outliers.json")
    except (OSError, ValueError):
        return []
    selected = outliers.get("selected") if isinstance(outliers, dict) else None
    if not isinstance(selected, list):
        return []
    return [
        {
            "shortCode": reel.get("shortCode"),
            "video_status": reel.get("video_status"),
            "frames_status": reel.get("frames_status"),
            "analysis_status": reel.get("analysis_status"),
        }
        for reel in selected
        if isinstance(reel, dict)
    ]


def _strip_markdown(text: str) -> str:
    """`text` without bold markers and the `Spoken:`/`On-screen text:` labels."""
    text = text.replace("**", "").strip()
    for label in ("Spoken:", "On-screen text:"):
        if text.startswith(label):
            text = text[len(label):].strip()
    return text


def placeholder_contexts(script_text: str) -> List[Dict[str, Any]]:
    """Every `[NEED ...]` in a script, with where it sits and the line it is in.

    Returns `[{where, text, tokens}]` in script order. `where` is the
    section name, or for a Beats table row `"Beats <t>, <column>"`, so the
    creator can find the exact line. The notes sections are skipped (they
    repeat the same placeholders), and an identical `(where, text)` pair
    is listed once. Front matter is ignored.
    """
    body = script_text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        body = body[end + 4:] if end != -1 else ""
    found: List[Dict[str, Any]] = []
    section = "Script"

    by_text: Dict[str, Dict[str, Any]] = {}

    def add(where: str, text: str) -> None:
        tokens = _TOKEN_RE.findall(text)
        if not tokens:
            return
        # The hook usually reappears word for word in the first beat row.
        # One line to fill, two places it shows: list it once, name both.
        if text in by_text:
            item = by_text[text]
            if where not in item["where"].split(" and "):
                item["where"] += f" and {where}"
            return
        item = {"where": where, "text": text, "tokens": tokens}
        by_text[text] = item
        found.append(item)

    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section in _NOTE_SECTIONS or "[NEED" not in line:
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            beat = cells[0] if cells else ""
            for index, cell in enumerate(cells[1:]):
                column = _BEAT_COLUMNS[index] if index < len(_BEAT_COLUMNS) else f"column {index + 2}"
                add(f"{section} {beat}, {column}", cell)
        else:
            add(section, _strip_markdown(line.lstrip("- ")))
    return found


def _rel(run_dir: Path, path_value: Optional[str]) -> str:
    """`path_value` relative to the run directory when it sits inside it, else as given."""
    if not path_value:
        return "-"
    try:
        return str(Path(path_value).resolve().relative_to(Path(run_dir).resolve()))
    except ValueError:
        return str(path_value)


def _lines_word(count: int) -> str:
    """`"1 line"` or `"<n> lines"`."""
    return f"{count} line" if count == 1 else f"{count} lines"


def _read_qa(state: Dict[str, Any]) -> Dict[str, Any]:
    """The brief's latest QA JSON, or `{}` when there is none to read."""
    if not state.get("qa_path"):
        return {}
    try:
        doc = store.read_json(Path(state["qa_path"]))
    except (ValueError, OSError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _script_placeholders(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`placeholder_contexts` for the brief's latest script; `[]` without one."""
    if not state.get("script_path"):
        return []
    try:
        text = Path(state["script_path"]).read_text(encoding="utf-8")
    except OSError:
        return []
    return placeholder_contexts(text)


def next_steps(run_dir: Path, states: List[Dict[str, Any]]) -> List[str]:
    """The creator's to-do list for this run, most useful first.

    Ready scripts come first, then the briefs that need the creator's
    call, then the pipeline steps still to run.
    """
    run_id = Path(run_dir).name
    ready: List[str] = []
    human: List[str] = []
    pipeline: List[str] = []
    for state in states:
        brief_id = state["brief_id"]
        script = _rel(run_dir, state.get("script_path"))
        if state["status"] == "pass":
            count = len(_script_placeholders(state))
            if count:
                ready.append(f"{brief_id} passed QA. Fill in {_lines_word(count)} of placeholders in {script}, then film it.")
            else:
                ready.append(f"{brief_id} passed QA and is ready to film: {script}.")
        elif state.get("needs_human") and (state.get("revision") or 0) >= 2:
            human.append(
                f"{brief_id} is final after revision 2 and still needs you. Read its QA notes, "
                f"supply what they ask for in {script}, then film it or skip it."
            )
        elif state.get("needs_human"):
            human.append(
                f"{brief_id} needs your call. Answer its intake questions "
                f"(`intake --run {run_id} --brief {brief_id}`), then run revision 2."
            )
        elif state["status"] == "revise":
            pipeline.append(f"{brief_id}: run its one revision, then QA it again.")
        elif state["status"] == "written":
            pipeline.append(f"{brief_id}: run QA on {script}.")
        elif state["status"] == "pending":
            pipeline.append(f"{brief_id}: write the script.")
    return ready + human + pipeline


def render_status_text(run_dir: Path) -> str:
    """A short plain-text summary of a run, for `status --text`."""
    run_dir = Path(run_dir)
    run_data = store.read_json(run_dir / "run.json")
    states = _brief_states(run_dir)
    stages = run_data.get("stages") or {}
    lines = [f"Run {run_data.get('run_id')} ({run_data.get('mode')})"]
    if stages:
        lines.append("Stages: " + ", ".join(f"{name} {stages[name].get('status')}" for name in sorted(stages)))
    else:
        lines.append("Stages: none yet")
    if states:
        lines.append("Briefs:")
        for state in states:
            count = len(_script_placeholders(state))
            extra = f", {_lines_word(count)} to fill" if count else ""
            lines.append(f"- {state['brief_id']} {state['status']}{extra}")
    else:
        lines.append("Briefs: none yet")
    steps = next_steps(run_dir, states)
    lines.append("Next:")
    lines.extend(f"- {step}" for step in steps) if steps else lines.append("- nothing left in this run")
    return "\n".join(lines) + "\n"


def status(run_dir: Path) -> Dict[str, Any]:
    """The machine-readable state `status --run <id>` prints.

    `{run_id, mode, stages, briefs, reels, costs, warnings}`, where
    `briefs` is `agents.brief_state` for every ranked brief, in ranked
    order, and `reels` is `_reel_states` for every `selected` reel, in
    selection order.
    """
    run_dir = Path(run_dir)
    run_data = store.read_json(run_dir / "run.json")
    return {
        "run_id": run_data.get("run_id"),
        "mode": run_data.get("mode"),
        "stages": run_data.get("stages") or {},
        "briefs": _brief_states(run_dir),
        "reels": _reel_states(run_dir),
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

    lines.append("## What to do next")
    lines.append("")
    steps = next_steps(run_dir, states)
    if steps:
        lines.extend(f"- [ ] {step}" for step in steps)
    else:
        lines.append("Nothing left in this run.")
    lines.append("")

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
                script=_rel(run_dir, state["script_path"]),
            )
        )
    if not states:
        lines.append("No briefs yet.")
    lines.append("")

    lines.append("## Scores")
    lines.append("")
    bar = (run_data.get("config") or {}).get("qa_pass_threshold", store.DEFAULT_CONFIG["qa_pass_threshold"])
    any_scores = False
    for state in states:
        qa = _read_qa(state)
        scores = qa.get("scores") if isinstance(qa.get("scores"), dict) else {}
        checks = qa.get("checks") if isinstance(qa.get("checks"), dict) else {}
        if not qa:
            continue
        any_scores = True
        numeric = {name: value for name, value in scores.items() if isinstance(value, (int, float))}
        under = sorted((item for item in numeric.items() if item[1] < bar), key=lambda item: item[1])
        over = [item for item in numeric.items() if item[1] >= bar]
        parts = []
        if under:
            parts.append(f"under {bar}: " + ", ".join(f"{name} {value}" for name, value in under))
        if over:
            parts.append(f"{bar} or more: " + ", ".join(f"{name} {value}" for name, value in over))
        score_text = ". ".join(parts) or "no scores"
        failed = [name for name, value in checks.items() if value == "fail"]
        line = f"- {state['brief_id']} (r{state['revision']}, {state['verdict']}). {score_text}."
        if failed:
            line += " failed checks: " + ", ".join(failed)
        lines.append(line)
    if not any_scores:
        lines.append("No QA reviews yet.")
    lines.append("")

    lines.append("## Placeholders to fill")
    lines.append("")
    any_placeholders = False
    for state in states:
        contexts = _script_placeholders(state)
        if not contexts:
            continue
        any_placeholders = True
        lines.append(
            f"- {state['brief_id']}: {_lines_word(len(contexts))} to fill in {_rel(run_dir, state['script_path'])}"
        )
        for item in contexts:
            lines.append(f"  - {item['where']}: {item['text']}")
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
