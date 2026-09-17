"""ContentOS CLI entry point.

Every subcommand is registered in HANDLERS, keyed by subcommand name. This
task ships each one as a stub that reports itself as not implemented; later
tasks replace one HANDLERS entry at a time with the real implementation, so
the dispatch mechanism itself never has to change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from lib import agents, apify, codes, direct, env, frames, report, research, setup, store

SUBCOMMANDS = [
    "diagnose",
    "setup",
    "research",
    "frames",
    "direct-prompt",
    "synth-prompt",
    "rank",
    "write-prompt",
    "qa-prompt",
    "verify",
    "report",
    "status",
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
    """Run the founder-facing pre-flight check and print its JSON report.

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


def _setup_handler(args: argparse.Namespace) -> int:
    """Write `.contentos/` from the founder's answers file, then print `diagnose`.

    Every refusal (`setup.SetupError`) is a usage error: a missing or
    unreadable answers file, an answers file that is not a JSON object,
    an empty competitor list, or an existing `product.md` without
    `--force`. The message goes to stderr and nothing is written. On
    success the stdout JSON is exactly what `diagnose` prints (with
    `apify_live` null, since setup never calls Apify), so the skill can
    read the founder's new state from one place.
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


def references_dir() -> Path:
    """Return the skills/contentos/references directory the prompts cite."""
    return skill_root() / "references"


def _direct_prompt_handler(args: argparse.Namespace) -> int:
    """Print the `content-director` dispatch prompt for one selected reel.

    Every refusal (`direct.DirectError`) carries its own exit code: 2
    for an unresolvable run, a reel that is not selected, a reel with
    no keyframes, or a missing `product.md`. The prompt itself goes to
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
        prompt = direct.run_synth_prompt(project_dir, args.run, references_dir())
    except direct.DirectError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
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
        f"read_time_s={check.read_time_s} placeholders={len(check.placeholders)}"
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


def _verify_handler(args: argparse.Namespace) -> int:
    """Verify one stage's output file, printing `ok <path> ...` or its problems.

    `direct` and `synth` are Stage 2's own checks (`lib/direct.py`);
    `write` and `qa` are Stage 3/4's (`lib/agents.py`).
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


def _write_prompt_handler(args: argparse.Namespace) -> int:
    """Print the `script-writer` dispatch prompt for one brief.

    Every refusal (`agents.AgentsError`) carries its own exit code: 2
    for an unresolvable run, a brief not in `03-briefs.json`, or a
    missing `product.md`.
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
    return codes.EXIT_OK


def _status_handler(args: argparse.Namespace) -> int:
    """Print this run's machine-readable state as JSON."""
    project_dir = args.project.resolve()
    try:
        run_dir = store.resolve_run(project_dir, args.run)
    except store.RunNotFound as exc:
        print(str(exc), file=sys.stderr)
        return codes.EXIT_USAGE
    print(json.dumps(report.status(run_dir), indent=2))
    return codes.EXIT_OK


def _rank_handler(args: argparse.Namespace) -> int:
    """Rank one run's analyses into briefs and print the JSON summary.

    `--mock` seeds the fixture analyses and patterns first, so a mock
    run reaches `briefs.md` with no subagent dispatched. A run with no
    valid analysis exits 2, as does a bad config or an unresolvable
    run.
    """
    project_dir = args.project.resolve()
    try:
        cfg = store.load_config(project_dir)
        result = direct.run_rank(project_dir, args.run, cfg, mock=args.mock)
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
HANDLERS["research"] = _research_handler
HANDLERS["frames"] = _frames_handler
HANDLERS["direct-prompt"] = _direct_prompt_handler
HANDLERS["synth-prompt"] = _synth_prompt_handler
HANDLERS["rank"] = _rank_handler
HANDLERS["verify"] = _verify_handler
HANDLERS["write-prompt"] = _write_prompt_handler
HANDLERS["qa-prompt"] = _qa_prompt_handler
HANDLERS["report"] = _report_handler
HANDLERS["status"] = _status_handler


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
        if name == "research":
            sub.add_argument("--yes", action="store_true")
            sub.add_argument("--estimate-only", action="store_true")
            sub.add_argument("--no-download", action="store_true")
            sub.add_argument("--resume", default=None)
        if name == "frames":
            sub.add_argument("--run", required=True)
            sub.add_argument("--refresh-expired", action="store_true")
        if name in (
            "direct-prompt", "synth-prompt", "rank", "verify",
            "write-prompt", "qa-prompt", "report", "status",
        ):
            sub.add_argument("--run", required=True)
        if name == "direct-prompt":
            sub.add_argument("--shortcode", required=True)
        if name in ("write-prompt", "qa-prompt"):
            sub.add_argument("--brief", required=True)
            sub.add_argument("--revision", type=int, default=None)
        if name == "verify":
            sub.add_argument("--stage", required=True, choices=["direct", "synth", "write", "qa"])
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
