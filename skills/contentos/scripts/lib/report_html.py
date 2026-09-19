"""`report --html`: one self-contained `report.html` per run.

The same facts as `report.md`, laid out for reading rather than
scanning a terminal: what to do next, a fill-in list with the line each
placeholder sits on, the research funnel, and one section per brief with
its source, its bet, the score math, the script itself, and the QA
review. Standard library only. The page carries its own CSS, uses the
system font stack, and runs no JavaScript, so it opens from disk,
prints, and can be shared as a file. Every string from a run (titles,
scripts, QA notes) is HTML-escaped.

Nothing here decides anything: states come from `lib/agents.py`'s
`brief_state`, next steps and placeholders from `lib/report.py`. See the
design spec's "0.3.0 changes", phase 2.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib import director, report, store

# brief_score weights, from the design spec's "Stage 2 -- direct" (and
# lib/director.py's brief_score). Used only to draw the score bar.
_WEIGHTS = (("viral_proof", 0.35, "viral"), ("score_convertible", 0.25, "convertible"),
            ("score_scalable", 0.20, "scalable"), ("score_fit", 0.20, "fit"))

_SCRIPT_SECTIONS = ("Hook", "Beats", "Payoff", "CTA", "Lead magnet", "Caption")
_NOTE_SECTIONS = ("Production notes", "What changed vs source")

_CSS = """
:root{--bg:#F3F4F1;--surface:#FFFFFF;--ink:#16181D;--muted:#5A6170;--line:#DADDD6;
--accent:#2649C4;--accent-soft:#E6ECFB;--ok:#1F7A55;--ok-bg:#DDF2E7;--warn:#8A5A00;--warn-bg:#FBF1DC;
--crit:#B3261E;--crit-bg:#FBE4E2;--mute-bg:#ECEEEA;--need-bg:#FFE9A8;--need-ink:#6B4A00;
--s0:#2649C4;--s1:#1F8A70;--s2:#C98A12;--s3:#9B4DCA;
--body:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,Menlo,Consolas,monospace}
@media (prefers-color-scheme: dark){:root{--bg:#101217;--surface:#171A21;--ink:#E6E8EE;--muted:#9AA1B0;
--line:#2A2F3A;--accent:#7E9BFF;--accent-soft:#1D2540;--ok:#5FD3A0;--ok-bg:#16352A;--warn:#E4AE45;
--warn-bg:#33280F;--crit:#F08A82;--crit-bg:#3A1A18;--mute-bg:#232731;--need-bg:#4A3A0C;--need-ink:#F6D57A;
--s0:#7E9BFF;--s1:#4CC39F;--s2:#E4AE45;--s3:#C08BEA}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--body)}
.wrap{max-width:1040px;margin:0 auto;padding:32px 18px 64px}
a{color:var(--accent)}
h1,h2,h3,h4{line-height:1.2;margin:0}
h1{font-size:30px}h2{font-size:22px;margin:36px 0 12px}h3{font-size:17px;margin:18px 0 8px}
h4{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:14px 0 6px}
p{margin:0 0 10px;max-width:75ch}
code{font-family:var(--mono);font-size:.86em;background:var(--mute-bg);padding:1px 5px;border-radius:3px}
.muted{color:var(--muted)}.small{font-size:13px}
.eyebrow{font-family:var(--mono);font-size:12px;color:var(--muted);margin:0 0 8px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:1px;background:var(--line);
border:1px solid var(--line);margin-top:18px}
.kpis div{background:var(--surface);padding:12px 14px}
.kpis b{display:block;font:500 24px var(--mono)}
.kpis span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.box{background:var(--surface);border:1px solid var(--line);padding:16px 18px}
.todo li{margin:6px 0}
.pill{display:inline-block;font:500 11.5px var(--mono);padding:2px 7px;border-radius:3px;white-space:nowrap}
.pill.pass{background:var(--ok-bg);color:var(--ok)}.pill.warn{background:var(--warn-bg);color:var(--warn)}
.pill.crit{background:var(--crit-bg);color:var(--crit)}.pill.mute{background:var(--mute-bg);color:var(--muted)}
mark.need{background:var(--need-bg);color:var(--need-ink);font:500 .8em var(--mono);padding:1px 5px;border-radius:3px}
.cue{font:.72em var(--mono);color:var(--muted);border:1px solid var(--line);padding:0 4px;border-radius:3px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-size:14px}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);white-space:nowrap}
.num{text-align:right;font-family:var(--mono);white-space:nowrap}
.bar{display:flex;height:9px;min-width:110px;background:var(--mute-bg);border-radius:2px;overflow:hidden}
.bar span{display:block;height:100%}
.s0{background:var(--s0)}.s1{background:var(--s1)}.s2{background:var(--s2)}.s3{background:var(--s3)}
nav.jump{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:14px;margin-top:14px}
.brief{background:var(--surface);border:1px solid var(--line);margin-top:22px;padding:20px}
.brief>header{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;border-bottom:1px solid var(--line);
padding-bottom:12px;margin-bottom:12px}
.score{font:500 28px var(--mono)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.say{font-size:20px;font-weight:600;max-width:36ch}
.card{display:inline-block;background:#111;color:#fff;font-weight:700;padding:8px 12px;border-radius:6px;margin:4px 0 8px}
.card mark.need{font-size:.7em}
details{border-top:1px solid var(--line);padding:10px 0}
summary{cursor:pointer;font-weight:600}
.sc{display:grid;grid-template-columns:minmax(0,190px) 1fr 28px;gap:10px;align-items:center;font-size:13px}
.sc .bar span{background:var(--ok)}.sc .bar span.lo{background:var(--warn)}
ul.issues{list-style:none;padding:0}ul.issues li{margin:0 0 12px}
@media print{body{background:#fff}.brief{break-inside:avoid-page}}
"""


def _e(value: Any) -> str:
    """HTML-escape any value as text."""
    return html.escape("" if value is None else str(value))


def _inline(text: str) -> str:
    """Escape one line of script markdown and style its bold, code, and markers."""
    out = _e(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\[(NEED [^\]]+)\]", r'<mark class="need">\1</mark>', out)
    out = re.sub(r"\[(PAUSE|EMPHASIS)\]", r'<span class="cue">\1</span>', out)
    return out


def _markdown(block: str) -> str:
    """The small markdown subset scripts use: tables, bullet lists, paragraphs."""
    out: List[str] = []
    lines = block.strip("\n").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([cell.strip() for cell in lines[i].strip().strip("|").split("|")])
                i += 1
            head = rows[0]
            body = [row for row in rows[1:] if not set("".join(row)) <= set("-: ")]
            table = ["<div class='scroll'><table><thead><tr>"]
            table += [f"<th>{_inline(cell)}</th>" for cell in head]
            table.append("</tr></thead><tbody>")
            for row in body:
                table.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>")
            table.append("</tbody></table></div>")
            out.append("".join(table))
            continue
        if line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if not line.strip():
            i += 1
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("|", "- ")):
            para.append(_inline(lines[i]))
            i += 1
        out.append("<p>" + "<br>".join(para) + "</p>")
    return "\n".join(out)


def _sections(script_text: str) -> Dict[str, str]:
    """A script's `## ` sections by name, front matter dropped."""
    body = script_text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        body = body[end + 4:] if end != -1 else ""
    parts = re.split(r"^## (.+)$", body, flags=re.M)
    return {parts[k].strip(): parts[k + 1] for k in range(1, len(parts) - 1, 2)}


def _read(path: Path) -> Any:
    """JSON at `path`, or None when it is missing or unreadable."""
    try:
        return store.read_json(path)
    except (ValueError, OSError):
        return None


def _score_bar(brief: Dict[str, Any]) -> str:
    """The brief_score split into its four weighted parts, as a bar out of 10."""
    spans = []
    for index, (key, weight, label) in enumerate(_WEIGHTS):
        value = brief.get(key)
        if isinstance(value, (int, float)):
            part = weight * value
            spans.append(
                f"<span class='s{index}' style='width:{part * 10:.1f}%' title='{label} {part:.2f}'></span>"
            )
    return f"<span class='bar' role='img' aria-label='score parts'>{''.join(spans)}</span>"


def _status_pill(state: Dict[str, Any]) -> str:
    """A colored pill for a brief's status."""
    status = state["status"]
    kind = "pass" if status == "pass" else "crit" if state.get("needs_human") else "mute" if status == "pending" else "warn"
    label = "needs you" if state.get("needs_human") else status
    return f"<span class='pill {kind}'>{_e(label)}</span>"


def _research_section(run_dir: Path, run_data: Dict[str, Any]) -> str:
    """The funnel and per-account baselines. Only numbers, never scraped captions."""
    research = (run_data.get("stages") or {}).get("research") or {}
    if not research:
        return ""
    outliers = _read(run_dir / "02-outliers.json") or {}
    reasons: Dict[str, int] = {}
    for item in outliers.get("excluded") or []:
        if isinstance(item, dict):
            reasons[item.get("reason", "other")] = reasons.get(item.get("reason", "other"), 0) + 1
    cfg = run_data.get("config") or {}
    labels = {
        "outside_lookback": f"older than {cfg.get('lookback_days', '?')} days",
        "below_min_plays": f"under {cfg.get('min_plays', '?')} plays",
        "per_account_cap": f"past the cap of {cfg.get('max_per_account', '?')} per account",
        "already_briefed": "briefed in an earlier run",
    }
    funnel = [f"<li><b>{_e(research.get('reels_total', '?'))}</b> reels scored</li>"]
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        funnel.append(f"<li>minus <b>{count}</b> {_e(labels.get(reason, reason))}</li>")
    funnel.append(f"<li><b>{_e(research.get('selected', '?'))}</b> outliers kept and analyzed</li>")

    profiles_doc = _read(run_dir / "01-profiles.json")
    profiles = profiles_doc.get("profiles", profiles_doc) if isinstance(profiles_doc, dict) else profiles_doc
    followers: Dict[str, Any] = {}
    for profile in profiles if isinstance(profiles, list) else (profiles or {}).values() if isinstance(profiles, dict) else []:
        if isinstance(profile, dict) and profile.get("username"):
            followers[str(profile["username"]).lower()] = profile.get("followers")
    rows = []
    status_by_account = outliers.get("account_status") or {}
    for account, baseline in sorted((outliers.get("baselines") or {}).items()):
        if not isinstance(baseline, dict):
            continue
        count = followers.get(account)
        median = baseline.get("median")
        share = (
            f"{median / count * 100:.1f}%"
            if isinstance(median, (int, float)) and isinstance(count, (int, float)) and count
            else "-"
        )
        rows.append(
            f"<tr><td><a href='https://www.instagram.com/{_e(account)}/'>@{_e(account)}</a></td>"
            f"<td>{_e(status_by_account.get(account, '-'))}</td>"
            f"<td class='num'>{_e(f'{count:,}' if isinstance(count, int) else '-')}</td>"
            f"<td class='num'>{_e(f'{median:,.0f}' if isinstance(median, (int, float)) else '-')}</td>"
            f"<td class='num'>{share}</td></tr>"
        )
    table = ""
    if rows:
        table = (
            "<div class='scroll'><table><thead><tr><th>Account</th><th>Scrape</th><th class='num'>Followers</th>"
            "<th class='num'>Median plays</th><th class='num'>Median / followers</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div><p class='small muted'>Each reel is scored against its own account's median. "
            "A very low median share inflates that account's outlier ratios.</p>"
        )
    return (
        "<h2 id='research'>Research</h2><div class='grid'><div class='box'><ul>"
        + "".join(funnel)
        + "</ul></div><div class='box'>"
        + (table or "<p class='muted'>No account baselines recorded.</p>")
        + "</div></div>"
    )


def _brief_section(run_dir: Path, brief: Dict[str, Any], state: Dict[str, Any]) -> str:
    """One brief: source, bet, score math, script, QA."""
    brief_id = brief.get("brief_id", "")
    parts = [f"<section class='brief' id='{_e(brief_id)}'><header><div>"]
    parts.append(
        f"<p class='eyebrow'>{_e(brief_id)} · {_e(brief.get('format', '-'))} · {_e(brief.get('hook_type', '-'))} hook"
        f" · {_e(brief.get('source_kind', 'niche'))} source</p>"
    )
    parts.append(f"<h3>{_e(director.display_title(brief))}</h3>")
    if brief.get("source_url"):
        parts.append(
            f"<p class='small'>Source: <a href='{_e(brief['source_url'])}'>@{_e(brief.get('ownerUsername', ''))}"
            f" {_e(brief.get('shortCode', ''))}</a></p>"
        )
    parts.append(f"<p>{_status_pill(state)}</p></div>")
    score = brief.get("brief_score")
    parts.append(
        "<div><span class='score'>"
        + _e(f"{score:.2f}" if isinstance(score, (int, float)) else "-")
        + "</span><div class='small muted'>brief score</div>"
        + _score_bar(brief)
        + "</div></header>"
    )

    left = []
    if brief.get("transferable_mechanism"):
        left.append(f"<h4>The bet</h4><p>{_e(brief['transferable_mechanism'])}</p>")
    if brief.get("why_it_worked"):
        left.append(f"<h4>Why it worked</h4><p>{_e(brief['why_it_worked'])}</p>")
    right = []
    if brief.get("adaptation"):
        right.append(f"<h4>How it adapts to you</h4><p>{_e(brief['adaptation'])}</p>")
    if brief.get("avoid"):
        right.append(f"<h4>What not to copy</h4><p>{_e(brief['avoid'])}</p>")
    specifics = [item for item in brief.get("specifics") or [] if isinstance(item, dict)]
    if specifics:
        right.append(
            "<h4>Specifics from the source</h4><ul>"
            + "".join(
                f"<li><b>{_e(item.get('name'))}</b> <span class='muted small'>{_e(item.get('kind'))}"
                f"{', public' if item.get('public') else ''}</span> {_e(item.get('detail'))}</li>"
                for item in specifics
            )
            + "</ul>"
        )
    scores_line = ", ".join(
        f"{label} {brief[key]}" for key, _, label in _WEIGHTS if isinstance(brief.get(key), (int, float))
    )
    if scores_line:
        right.append(f"<p class='small muted'>Inputs: {_e(scores_line)}. Risk flags: "
                     f"{_e(', '.join(brief.get('risk_flags') or []) or 'none')}.</p>")
    parts.append(f"<div class='grid'><div>{''.join(left)}</div><div>{''.join(right)}</div></div>")

    script_path = state.get("script_path")
    if script_path and Path(script_path).exists():
        sections = _sections(Path(script_path).read_text(encoding="utf-8"))
        parts.append(f"<h3>Script <span class='small muted'>{_e(report._rel(run_dir, script_path))}</span></h3>")
        for name in _SCRIPT_SECTIONS:
            if name in sections:
                parts.append(f"<h4>{_e(name)}</h4>{_markdown(sections[name])}")
        for name in _NOTE_SECTIONS:
            if name in sections:
                parts.append(f"<details><summary>{_e(name)}</summary>{_markdown(sections[name])}</details>")
    else:
        parts.append("<p class='muted'>No script yet.</p>")

    qa = report._read_qa(state)
    if qa:
        parts.append(f"<h3>QA review <span class='small muted'>revision {_e(state.get('revision'))}, "
                     f"{_e(qa.get('verdict'))}</span></h3>")
        if qa.get("summary"):
            parts.append(f"<p>{_e(qa['summary'])}</p>")
        scores = qa.get("scores") if isinstance(qa.get("scores"), dict) else {}
        if scores:
            bar = []
            for name, value in scores.items():
                if isinstance(value, (int, float)):
                    low = " lo" if value < 8 else ""
                    bar.append(
                        f"<div class='sc'><span>{_e(name)}</span><span class='bar'>"
                        f"<span class='{low.strip()}' style='width:{value * 10:.0f}%'></span></span>"
                        f"<span class='num'>{_e(value)}</span></div>"
                    )
            parts.append("<div class='box'>" + "".join(bar) + "</div>")
        checks = qa.get("checks") if isinstance(qa.get("checks"), dict) else {}
        failed = [name for name, value in checks.items() if value == "fail"]
        if failed:
            parts.append("<p>Failed checks: " + " ".join(f"<span class='pill crit'>{_e(n)}</span>" for n in failed) + "</p>")
        if qa.get("strongest_line"):
            parts.append(f"<h4>Strongest line</h4><p>{_inline(str(qa['strongest_line']))}</p>")
        issues = [item for item in qa.get("issues") or [] if isinstance(item, dict)]
        if issues:
            rows = []
            for item in issues:
                severity = str(item.get("severity", ""))
                kind = "crit" if severity == "blocker" else "warn" if severity == "major" else "mute"
                rows.append(
                    f"<li><span class='pill {kind}'>{_e(severity)}</span> <b>{_e(item.get('check_or_score'))}</b>"
                    f"<p>{_inline(str(item.get('detail', '')))}</p>"
                    f"<p class='muted'>Fix: {_inline(str(item.get('fix', '')))}</p></li>"
                )
            parts.append("<h4>QA notes</h4><ul class='issues'>" + "".join(rows) + "</ul>")
    parts.append("</section>")
    return "".join(parts)


def render_report_html(run_dir: Path) -> str:
    """The full `report.html` for one run."""
    run_dir = Path(run_dir)
    run_data = store.read_json(run_dir / "run.json")
    briefs = report._briefs(run_dir)
    states = {state["brief_id"]: state for state in report._brief_states(run_dir)}
    ordered_states = [states[b["brief_id"]] for b in briefs if b.get("brief_id") in states]
    research = (run_data.get("stages") or {}).get("research") or {}
    cost = ((run_data.get("costs") or {}).get("apify") or {}).get("estimate_usd")
    placeholders = {state["brief_id"]: report._script_placeholders(state) for state in ordered_states}
    run_id = run_data.get("run_id") or run_dir.name

    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>ContentOS {_e(run_id)}</title><style>{_CSS}</style></head><body><main class='wrap'>",
        f"<p class='eyebrow'>ContentOS · run {_e(run_id)} · {_e(run_data.get('mode'))}</p>",
        "<h1>Run report</h1>",
        "<div class='kpis'>",
        f"<div><b>{_e(research.get('reels_total', '-'))}</b><span>reels scored</span></div>",
        f"<div><b>{_e(research.get('selected', '-'))}</b><span>outliers analyzed</span></div>",
        f"<div><b>{len(briefs)}</b><span>briefs</span></div>",
        f"<div><b>{sum(1 for s in ordered_states if s['status'] == 'pass')}</b><span>passed QA</span></div>",
        f"<div><b>{sum(1 for s in ordered_states if s.get('needs_human'))}</b><span>need you</span></div>",
        f"<div><b>{sum(len(v) for v in placeholders.values())}</b><span>lines to fill</span></div>",
        f"<div><b>{_e(f'${cost:.2f}' if isinstance(cost, (int, float)) else '-')}</b><span>Apify estimate</span></div>",
        "</div>",
    ]
    if briefs:
        out.append("<nav class='jump'>Jump to: " + " ".join(
            f"<a href='#{_e(b.get('brief_id'))}'>{_e(b.get('brief_id'))}</a>" for b in briefs
        ) + " <a href='#research'>Research</a></nav>")

    steps = report.next_steps(run_dir, ordered_states)
    out.append("<h2>What to do next</h2><div class='box'><ul class='todo'>")
    out.extend(f"<li>{_inline(step)}</li>" for step in steps) if steps else out.append("<li>Nothing left in this run.</li>")
    out.append("</ul></div>")

    out.append("<h2>Fill-in list</h2>")
    if any(placeholders.values()):
        out.append("<p class='small muted'>Every placeholder in the lines you film, with where it sits. "
                   "These are facts only you can supply.</p><div class='box'>")
        for brief_id, items in placeholders.items():
            if not items:
                continue
            out.append(f"<details open><summary>{_e(brief_id)}: {len(items)} to fill</summary><ul>")
            out.extend(
                f"<li><span class='small muted'>{_e(item['where'])}</span> {_inline(item['text'])}</li>"
                for item in items
            )
            out.append("</ul></details>")
        out.append("</div>")
    else:
        out.append("<p class='muted'>Nothing to fill.</p>")

    out.append("<h2>Briefs</h2>")
    if briefs:
        rows = []
        for brief in briefs:
            state = states.get(brief.get("brief_id"))
            if not state:
                continue
            score = brief.get("brief_score")
            rows.append(
                f"<tr><td><a href='#{_e(brief.get('brief_id'))}'>{_e(brief.get('brief_id'))}</a></td>"
                f"<td>{_e(director.display_title(brief))}</td><td>{_status_pill(state)}</td>"
                f"<td class='num'>{_e(f'{score:.2f}' if isinstance(score, (int, float)) else '-')}</td>"
                f"<td>{_score_bar(brief)}</td></tr>"
            )
        out.append("<div class='box scroll'><table><thead><tr><th>id</th><th>title</th><th>status</th>"
                   "<th class='num'>score</th><th>viral / convertible / scalable / fit</th></tr></thead><tbody>"
                   + "".join(rows) + "</tbody></table></div>")
        for brief in briefs:
            state = states.get(brief.get("brief_id"))
            if state:
                out.append(_brief_section(run_dir, brief, state))
    else:
        out.append("<p class='muted'>No briefs yet.</p>")

    out.append(_research_section(run_dir, run_data))
    out.append(
        "<p class='small muted' style='margin-top:40px'>Generated by ContentOS from this run's files. "
        "The Apify cost is an estimate; Apify bills on results actually returned.</p>"
        "</main></body></html>"
    )
    return "\n".join(out) + "\n"


def write_report_html(run_dir: Path) -> Path:
    """Write `report.html` into the run directory and return its path."""
    path = Path(run_dir) / "report.html"
    path.write_text(render_report_html(run_dir), encoding="utf-8")
    return path
