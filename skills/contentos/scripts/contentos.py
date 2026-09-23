"""ContentOS CLI entry point.

The deterministic half of the four-stage pipeline (design spec, "Stage
1" through "Stage 4"): `diagnose`, `setup`, `research`, `frames`,
`transcribe`, `direct-prompt`, `synth-prompt`, `rank`, `intake`, `write-prompt`,
`qa-prompt`, `verify`, `report`, `status`, `sync-plugin-key`, `mark`, `history`. The SKILL.md
orchestrator dispatches the `contentos:content-director`,
`contentos:script-writer`, and `contentos:qa-reviewer` subagents around
these commands; nothing here calls a model.

Every subcommand is registered in HANDLERS, keyed by subcommand name.
Invoke one with `python3 contentos.py <subcommand> --project <dir>
[--mock] ...`; `--project` defaults to the current directory. Exit
codes are the shared constants in `lib/codes.py`: 0 ok, 1 subcommand
not implemented (none are, today; `is_stub` and `_stub_handler` stay in
place for a subcommand a future task adds ahead of its real handler),
2 usage, 3 confirmation required, 4 missing key, 5 upstream failure,
6 cost cap, 7 verification failed (see the design spec's "Global
Constraints" for what each one means for the caller).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from lib import (
    agents, apify, codes, direct, discover, env, frames, history, report, report_html, research,
    setup, store, transcribe,
)

SUBCOMMANDS = [
    "diagnose",
    "setup",
    "discover",
    "accounts",
    "research",
    "frames",
    "transcribe",
    "direct-prompt",
    "synth-prompt",
    "rank",
    "intake",
    "write-prompt",
    "qa-prompt",
    "verify",
    "report",
    "status",
    "sync-plugin-key",
    "mark",
    "history",
]


def skill_root() -> Path:
    """Return the skills/contentos directory this script lives under."""
    return Path(__file__).resolve().parent.parent


def plugin_root() -> Path:
    """Return the plugin/repo root that the marketplace installs."""
    return skill_root().parent.parent


def plugin_version() -> str:
    """Read the version string out of .claude-plugin/plugin.json."""
    manifest_path = plugin_root() / ".claude-plugin" / "plugin.json"
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    return manifest["version"]


def _stub_handler(name: str) -> Callable[[argparse.Namespace], int]:
    """Build the not-implemented-yet handler for a single subcommand."""

    def handler(_args: argparse.Namespace) -> int:
        print(f"{name}: not implemented", file=sys.stderr)
        return codes.EXIT_STUB

    handler.is_stub = True
    return handler


def _diagnose_handler(args: argparse.Namespace) -> int:
    """Run the creator-facing pre-flight check and print its JSON report.

    `--live` additionally validates the resolved key against Apify at
    zero cost (`GET /users/me`, via `apify.check_token`). Without the
    flag `apify_live` is always `null`; with it, `false` when no key
    resolved at all so a live check is never attempted with nothing to
    check. Either way this always exits 0 -- diagnose reports, it never
    fails.
    """
    project_dir = args.project.resolve()
    result = env.diagnose(
        project_dir,
        mock=args.mock,
        skill_root=str(skill_root()),
    )

    apify_live: Optional[bool] = None
    if args.live:
        keys = env.resolve_keys(project_dir)
        if keys.apify:
            apify_live = apify.check_token(keys.apify, apify.HttpTransport())
        else:
            apify_live = False
    result["apify_live"] = apify_live

    print(json.dumps(result, indent=2))
    return codes.EXIT_OK


def _sync_plugin_key_handler(_args: argparse.Namespace) -> int:
    """Copy the /plugin Apify setting to where later commands can read it.

    The plugin's SessionStart hook (`hooks/hooks.json`) runs this, not
    the creator. Claude Code hands plugin settings to hooks only, never
    to commands run through the Bash tool, so this is the one place the
    setting is visible (`env.sync_plugin_option`). A SessionStart hook's
    stdout lands in Claude's context, so nothing is printed there:
    warnings go to stderr, and this always exits 0 so a failure never
    blocks the creator's session.

    Outside a hook it changes nothing. Claude Code sets
    `CLAUDE_PLUGIN_ROOT` for plugin hooks only; without it the option is
    invisible, which would read as "setting cleared" and delete a good
    copy.
    """
    if not os.environ.get("CLAUDE_PLUGIN_ROOT"):
        print(
            "sync-plugin-key only runs from the ContentOS SessionStart hook; nothing changed.",
            file=sys.stderr,
        )
        return codes.EXIT_OK
    for warning in env.sync_plugin_option():
        print(warning, file=sys.stderr)
    return codes.EXIT_OK


def _setup_handler(args: argparse.Namespace) -> int:
    """Write `.contentos/` from the creator's answers file, then print `diagnose`.

    Every refusal (`setup.SetupError`) is a usage error: a missing or
    unreadable answers file, an answers file that is not a JSON object,
    an empty competitor list, or an existing `creator.md` without
    `--force`. The message goes to stderr and nothing is written. On
    success the stdout JSON is exactly what `diagnose` prints (with
    `apify_live` null, since setup never calls Apify), so the skill can
    read the creator's new state from one place.
    """
    project_dir = args.project.resolve()
    try:
        answers = setup.load_answers(args.answers_file)
        setup.run_setup(project_dir, answers, references_dir(), force=args.force)
    except setup.SetupError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code

    result = env.diagnose(project_dir, mock=args.mock, skill_root=str(skill_root()))
    result["apify_live"] = None
    print(json.dumps(result, indent=2))
    return codes.EXIT_OK


def _research_handler(args: argparse.Namespace) -> int:
    """Run Stage 1 research end to end and print its per-account summary + RESULT line.

    Loads config and resolves keys, then delegates to
    `research.run_research`. Every `research.ResearchError` subclass maps
    to its own `exit_code`: its `payload` (the cost estimate), when
    present, is printed to stdout as indented JSON; the message always
    goes to stderr. `store.ConfigError` (bad/missing config) and
    `store.RunNotFound` (an unresolvable `--resume` id) both map to
    EXIT_USAGE, message on stderr.
    """
    project_dir = args.project.resolve()
    try:
        cfg = store.load_config(project_dir)
        keys = env.resolve_keys(project_dir)
        research.run_research(
            project_dir,
            cfg,
            keys,
            mock=args.mock,
            yes=args.yes,
            estimate_only=args.estimate_only,
            resume=args.resume,
            no_download=args.no_download,
        )
    except research.ResearchError as exc:
        if exc.payload is not None:
            print(json.dumps(exc.payload, indent=2))
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (store.ConfigError, store.RunNotFound) as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    return codes.EXIT_OK


def _split_list(value: Optional[str]) -> List[str]:
    """Split a comma-separated flag value; a missing flag is an empty list."""
    return [part for part in (value or "").split(",")]


def _accounts_handler(args: argparse.Namespace) -> int:
    """Replace the competitor and format-account lists of a set-up project (0.5.0)."""
    format_accounts = None if args.format_accounts is None else _split_list(args.format_accounts)
    try:
        result = setup.run_accounts(
            args.project.resolve(), _split_list(args.competitors), format_accounts
        )
    except setup.SetupError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    print(json.dumps(result, indent=2))
    return codes.EXIT_OK


def _discover_handler(args: argparse.Namespace) -> int:
    """Find creators who are winning in the niche (design spec, "0.6.0 changes").

    Works before `setup` has run. Exit codes are the research stage's: the
    estimate goes to stdout as indented JSON whenever an error carries
    one, and the message to stderr. On success it prints the table, then
    the `RESULT {...}` line.
    """
    project_dir = args.project.resolve()
    try:
        cfg = store.load_discovery_config(project_dir)
        keys = env.resolve_keys(project_dir)
        doc = discover.run_discover(
            project_dir,
            cfg,
            keys,
            hashtags=_split_list(args.hashtags),
            keywords=_split_list(args.keywords),
            seeds=_split_list(args.seeds),
            handles_file=args.handles_file,
            mock=args.mock,
            yes=args.yes,
            estimate_only=args.estimate_only,
        )
    except research.ResearchError as exc:
        if exc.payload is not None:
            print(json.dumps(exc.payload, indent=2))
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except discover.DiscoverError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except store.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    print(discover.render_table(doc))
    print("RESULT " + json.dumps(discover.result_line(doc, project_dir)))
    return codes.EXIT_OK


def _frames_handler(args: argparse.Namespace) -> int:
    """Cut keyframes for one run's selected reels, or re-run to fill gaps.

    `--refresh-expired` first re-scrapes and re-downloads any `expired`
    or `blocked` video before extracting -- Instagram's CDN answers an
    expired signed URL with 403, which `lib/http.py` records as
    `blocked` -- (real mode only; `--mock` never touches the network at
    all). Prints the JSON frame-status summary
    `frames.run_frames` returns and exits 0. An unresolvable `--run`
    (`store.RunNotFound`) or a run with no `02-outliers.json` yet
    (`frames.OutliersMissing`) both exit 2, message on stderr --
    matching every other subcommand's usage-error contract.
    """
    project_dir = args.project.resolve()
    try:
        cfg = store.load_config(project_dir)
        keys = env.resolve_keys(project_dir)
        result = frames.run_frames(
            project_dir,
            args.run,
            cfg,
            keys,
            mock=args.mock,
            refresh_expired=args.refresh_expired,
            fixtures_dir=research.FIXTURES_DIR,
        )
    except (store.ConfigError, store.RunNotFound, frames.OutliersMissing) as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    print(json.dumps(result))
    return codes.EXIT_OK


def _transcribe_handler(args: argparse.Namespace) -> int:
    """Backfill transcripts for one run's selected reels, like `frames` does.

    Prints the JSON summary `transcribe.run_transcribe` returns and exits
    0; a reel that could not be transcribed is a status in that summary,
    never an error. `--mock` copies fixture transcripts and never runs
    whisper or calls Apify. An unresolvable `--run`, a run with no
    `02-outliers.json` yet, or a bad config exits 2, message on stderr.
    """
    project_dir = args.project.resolve()
    try:
        cfg = store.load_config(project_dir)
        # The Apify fallback is paid, so it asks first, like research does:
        # print the estimate and exit 3 until the creator passes --yes.
        if not args.mock and not args.yes and transcribe.apify_planned(cfg):
            print(json.dumps({"transcripts_usd": transcribe.estimate_usd(cfg),
                              "cap_usd": cfg["apify_max_charge_usd"]}))
            print("confirmation required; re-run with --yes to spend on Apify transcripts", file=sys.stderr)
            return codes.EXIT_CONFIRM
        keys = env.resolve_keys(project_dir)
        result = transcribe.run_transcribe(
            project_dir,
            args.run,
            cfg,
            keys.apify,
            mock=args.mock,
            fixtures_dir=research.FIXTURES_DIR,
        )
    except (store.ConfigError, store.RunNotFound, transcribe.OutliersMissing) as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    print(json.dumps(result))
    return codes.EXIT_OK


def references_dir() -> Path:
    """Return the skills/contentos/references directory the prompts cite."""
    return skill_root() / "references"


def _direct_prompt_handler(args: argparse.Namespace) -> int:
    """Print the `content-director` dispatch prompt for one selected reel.

    Every refusal (`direct.DirectError`) carries its own exit code: 2
    for an unresolvable run, a reel that is not selected, a reel with
    no keyframes, or a missing `creator.md`. The prompt itself goes to
    stdout for the skill to hand to the subagent.
    """
    project_dir = args.project.resolve()
    try:
        prompt = direct.run_direct_prompt(
            project_dir, args.run, args.shortcode, references_dir()
        )
    except direct.DirectError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    print(prompt)
    return codes.EXIT_OK


def _synth_prompt_handler(args: argparse.Namespace) -> int:
    """Print the one set-level synthesis dispatch prompt for `03-patterns.md`."""
    project_dir = args.project.resolve()
    try:
        cfg = store.load_config(project_dir)
        prompt = direct.run_synth_prompt(project_dir, args.run, references_dir(), cfg=cfg)
    except direct.DirectError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except store.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    print(prompt)
    return codes.EXIT_OK


def _verify_write(project_dir: Path, args: argparse.Namespace) -> int:
    """`verify --stage write`: check one script file, print `ok ...` or its problems."""
    if not args.brief:
        print("verify --stage write needs --brief", file=sys.stderr)
        return codes.EXIT_USAGE
    run_dir = store.resolve_run(project_dir, args.run)
    cfg = store.load_config(project_dir)
    revision = args.revision if args.revision is not None else 0
    path = agents.script_path(run_dir, args.brief, revision)
    check = agents.verify_script(path, references_dir(), cfg["length_tolerance"])
    for warning in check.warnings:
        print(warning, file=sys.stderr)
    if check.errors:
        print("\n".join(check.errors), file=sys.stderr)
        return codes.EXIT_VERIFY
    print(
        f"ok {path.resolve()} words={check.word_count} "
        f"read_time_s={check.read_time_s} placeholders={len(check.placeholders)} "
        f"placeholder_ratio={agents.placeholder_ratio(len(check.placeholders), check.word_count)}"
    )
    return codes.EXIT_OK


def _verify_qa(project_dir: Path, args: argparse.Namespace) -> int:
    """`verify --stage qa`: check one QA JSON file, print `ok ...` or its problems."""
    if not args.brief:
        print("verify --stage qa needs --brief", file=sys.stderr)
        return codes.EXIT_USAGE
    run_dir = store.resolve_run(project_dir, args.run)
    cfg = store.load_config(project_dir)
    revision = args.revision
    if revision is None:
        revision = agents.default_qa_revision(run_dir, args.brief)
    path = agents.qa_path(run_dir, args.brief, revision)
    problems = agents.verify_qa(path, cfg["qa_pass_threshold"])
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return codes.EXIT_VERIFY
    verdict = store.read_json(path).get("verdict")
    print(f"ok {path.resolve()} verdict={verdict}")
    return codes.EXIT_OK


def _verify_facts(project_dir: Path, args: argparse.Namespace) -> int:
    """`verify --stage facts`: every bullet in `04-facts/<B>.md` has an https source."""
    if not args.brief:
        print("verify --stage facts needs --brief", file=sys.stderr)
        return codes.EXIT_USAGE
    run_dir = store.resolve_run(project_dir, args.run)
    path = agents.facts_path(run_dir, args.brief)
    problems, count = agents.verify_facts(path)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return codes.EXIT_VERIFY
    print(f"ok {path.resolve()} facts={count}")
    return codes.EXIT_OK


def _verify_handler(args: argparse.Namespace) -> int:
    """Verify one stage's output file, printing `ok <path> ...` or its problems.

    `direct` and `synth` are Stage 2's own checks (`lib/direct.py`);
    `facts`, `write`, and `qa` are Stage 3/4's (`lib/agents.py`).
    """
    project_dir = args.project.resolve()
    try:
        if args.stage == "direct":
            path = direct.verify_direct(project_dir, args.run, args.shortcode)
            print(f"ok {path}")
            return codes.EXIT_OK
        if args.stage == "synth":
            path = direct.verify_synth(project_dir, args.run)
            print(f"ok {path}")
            return codes.EXIT_OK
        if args.stage == "facts":
            return _verify_facts(project_dir, args)
        if args.stage == "write":
            return _verify_write(project_dir, args)
        return _verify_qa(project_dir, args)
    except direct.DirectError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except agents.AgentsError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (store.RunNotFound, store.ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE


def _intake_handler(args: argparse.Namespace) -> int:
    """Print the intake question list for one brief (stdout, exit 0).

    The orchestrator asks the creator these questions and writes the
    answers to `runs/<id>/04-intake/<B>.md`. An unresolvable run or a
    brief not in `03-briefs.json` exits 2, message on stderr.
    """
    project_dir = args.project.resolve()
    try:
        run_dir = store.resolve_run(project_dir, args.run)
        text = agents.intake_questions(project_dir, run_dir, args.brief)
    except store.RunNotFound as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    except agents.AgentsError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    print(text)
    return codes.EXIT_OK


def _write_prompt_handler(args: argparse.Namespace) -> int:
    """Print the `script-writer` dispatch prompt for one brief.

    Every refusal (`agents.AgentsError`) carries its own exit code: 2
    for an unresolvable run, a brief not in `03-briefs.json`, a
    missing `creator.md`, a revision past 2, or a revision 2 the brief
    has not unlocked (it must be needs_human and have intake answers).
    """
    project_dir = args.project.resolve()
    try:
        run_dir = store.resolve_run(project_dir, args.run)
        cfg = store.load_config(project_dir)
        prompt = agents.write_prompt(
            project_dir,
            run_dir,
            args.brief,
            revision=args.revision if args.revision is not None else 0,
            references_dir=references_dir(),
            cfg=cfg,
        )
    except (store.RunNotFound, store.ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    except agents.AgentsError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    print(prompt)
    return codes.EXIT_OK


def _qa_prompt_handler(args: argparse.Namespace) -> int:
    """Print the `qa-reviewer` dispatch prompt for one brief's script.

    `--revision` defaults to the highest revision with a written
    script -- the one QA has not reviewed yet.
    """
    project_dir = args.project.resolve()
    try:
        run_dir = store.resolve_run(project_dir, args.run)
        cfg = store.load_config(project_dir)
        revision = args.revision
        if revision is None:
            revision = agents.default_qa_revision(run_dir, args.brief)
        prompt = agents.qa_prompt(
            project_dir, run_dir, args.brief, revision, references_dir=references_dir(), cfg=cfg
        )
    except (store.RunNotFound, store.ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    except agents.AgentsError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    print(prompt)
    return codes.EXIT_OK


def _report_handler(args: argparse.Namespace) -> int:
    """Render this run's `report.md`, write it, and print its path."""
    project_dir = args.project.resolve()
    try:
        run_dir = store.resolve_run(project_dir, args.run)
    except store.RunNotFound as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    report_path = run_dir / "report.md"
    report_path.write_text(report.render_report(run_dir), encoding="utf-8")
    print(report_path.resolve())
    if args.html:
        print(report_html.write_report_html(run_dir).resolve())
    return codes.EXIT_OK


def _status_handler(args: argparse.Namespace) -> int:
    """Print this run's machine-readable state as JSON."""
    project_dir = args.project.resolve()
    try:
        run_dir = store.resolve_run(project_dir, args.run)
    except store.RunNotFound as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    if args.text:
        print(report.render_status_text(run_dir), end="")
    else:
        print(json.dumps(report.status(run_dir), indent=2))
    return codes.EXIT_OK


def _mark_handler(args: argparse.Namespace) -> int:
    """Record that a brief was filmed, posted, or skipped in `.contentos/log.json`."""
    project_dir = args.project.resolve()
    try:
        entry = history.mark(project_dir, args.run, args.brief, args.state, url=args.url)
    except (store.RunNotFound, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    print(json.dumps(entry))
    return codes.EXIT_OK


def _history_handler(args: argparse.Namespace) -> int:
    """Write `.contentos/history.md`, one row per run, and print its path."""
    project_dir = args.project.resolve()
    path = store.contentos_dir(project_dir) / "history.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(history.render_history(project_dir), encoding="utf-8")
    print(path.resolve())
    return codes.EXIT_OK


def _rank_handler(args: argparse.Namespace) -> int:
    """Rank one run's analyses into briefs and print the JSON summary.

    `--mock` seeds the fixture analyses and patterns first, so a mock
    run reaches `briefs.md` with no subagent dispatched. A run with no
    valid analysis exits 2, as does a bad config or an unresolvable
    run, and so does a re-rank of a run that already has scripts or
    `log.json` marks, unless `--force`.
    """
    project_dir = args.project.resolve()
    try:
        cfg = store.load_config(project_dir)
        result = direct.run_rank(
            project_dir, args.run, cfg, mock=args.mock, force=args.force
        )
    except direct.DirectError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except store.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    print(json.dumps(result))
    return codes.EXIT_OK


HANDLERS: Dict[str, Callable[[argparse.Namespace], int]] = {
    name: _stub_handler(name) for name in SUBCOMMANDS
}
HANDLERS["diagnose"] = _diagnose_handler
HANDLERS["setup"] = _setup_handler
HANDLERS["discover"] = _discover_handler
HANDLERS["accounts"] = _accounts_handler
HANDLERS["research"] = _research_handler
HANDLERS["frames"] = _frames_handler
HANDLERS["transcribe"] = _transcribe_handler
HANDLERS["direct-prompt"] = _direct_prompt_handler
HANDLERS["synth-prompt"] = _synth_prompt_handler
HANDLERS["rank"] = _rank_handler
HANDLERS["intake"] = _intake_handler
HANDLERS["verify"] = _verify_handler
HANDLERS["write-prompt"] = _write_prompt_handler
HANDLERS["qa-prompt"] = _qa_prompt_handler
HANDLERS["report"] = _report_handler
HANDLERS["status"] = _status_handler
HANDLERS["sync-plugin-key"] = _sync_plugin_key_handler
HANDLERS["mark"] = _mark_handler
HANDLERS["history"] = _history_handler


def is_stub(name: str) -> bool:
    """Return whether HANDLERS[name] is still the not-implemented stub."""
    return getattr(HANDLERS[name], "is_stub", False)


def build_parser() -> argparse.ArgumentParser:
    """Build the contentos.py argument parser and its subcommands."""
    parser = argparse.ArgumentParser(prog="contentos.py")
    parser.add_argument("--version", action="version", version=plugin_version())

    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in SUBCOMMANDS:
        sub = subparsers.add_parser(name)
        sub.add_argument("--project", type=Path, default=Path.cwd())
        sub.add_argument("--mock", action="store_true")
        if name == "diagnose":
            sub.add_argument("--live", action="store_true")
        if name == "setup":
            sub.add_argument("--answers-file", type=Path, required=True)
            sub.add_argument("--force", action="store_true")
        if name == "accounts":
            sub.add_argument("--competitors", required=True)
            sub.add_argument("--format-accounts", default=None)
        if name == "discover":
            sub.add_argument("--keywords", default=None)
            sub.add_argument("--hashtags", default=None)
            sub.add_argument("--seeds", default=None)
            sub.add_argument("--handles-file", type=Path, default=None)
            sub.add_argument("--yes", action="store_true")
            sub.add_argument("--estimate-only", action="store_true")
        if name == "research":
            sub.add_argument("--yes", action="store_true")
            sub.add_argument("--estimate-only", action="store_true")
            sub.add_argument("--no-download", action="store_true")
            sub.add_argument("--resume", default=None)
        if name == "frames":
            sub.add_argument("--run", required=True)
            sub.add_argument("--refresh-expired", action="store_true")
        if name == "transcribe":
            sub.add_argument("--yes", action="store_true")
            sub.add_argument("--run", required=True)
        if name in (
            "direct-prompt", "synth-prompt", "rank", "intake", "verify",
            "write-prompt", "qa-prompt", "report", "status",
        ):
            sub.add_argument("--run", required=True)
        if name == "rank":
            sub.add_argument("--force", action="store_true")
        if name == "report":
            sub.add_argument("--html", action="store_true")
        if name == "status":
            sub.add_argument("--text", action="store_true")
        if name == "mark":
            sub.add_argument("--run", required=True)
            sub.add_argument("--brief", required=True)
            sub.add_argument("--state", required=True, choices=list(history.LOG_STATES))
            sub.add_argument("--url", default=None)
        if name == "direct-prompt":
            sub.add_argument("--shortcode", required=True)
        if name == "intake":
            sub.add_argument("--brief", required=True)
        if name in ("write-prompt", "qa-prompt"):
            sub.add_argument("--brief", required=True)
            sub.add_argument("--revision", type=int, default=None)
        if name == "verify":
            sub.add_argument("--stage", required=True, choices=["direct", "synth", "facts", "write", "qa"])
            sub.add_argument("--shortcode", default=None)
            sub.add_argument("--brief", default=None)
            sub.add_argument("--revision", type=int, default=None)

    return parser


def main(argv: List[str]) -> int:
    """Parse argv and dispatch to the matching subcommand handler.

    Returns the process exit code instead of calling sys.exit, so callers
    (including tests) can invoke this in-process. argparse itself raises
    SystemExit for usage errors and for --version/--help; that is caught
    here and turned into a plain return value.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.EXIT_USAGE

    handler = HANDLERS[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
