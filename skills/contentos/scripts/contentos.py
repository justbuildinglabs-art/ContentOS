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
from typing import Callable, Dict, List

from lib import codes, env

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

    return handler


def _diagnose_handler(args: argparse.Namespace) -> int:
    """Run the founder-facing pre-flight check and print its JSON report."""
    project_dir = args.project.resolve()
    result = env.diagnose(
        project_dir,
        mock=args.mock,
        skill_root=str(skill_root()),
    )
    print(json.dumps(result, indent=2))
    return codes.EXIT_OK


HANDLERS: Dict[str, Callable[[argparse.Namespace], int]] = {
    name: _stub_handler(name) for name in SUBCOMMANDS
}
HANDLERS["diagnose"] = _diagnose_handler


def build_parser() -> argparse.ArgumentParser:
    """Build the contentos.py argument parser and its subcommands."""
    parser = argparse.ArgumentParser(prog="contentos.py")
    parser.add_argument("--version", action="version", version=plugin_version())

    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in SUBCOMMANDS:
        sub = subparsers.add_parser(name)
        sub.add_argument("--project", type=Path, default=Path.cwd())
        sub.add_argument("--mock", action="store_true")

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
