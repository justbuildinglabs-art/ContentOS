"""APIFY_API_TOKEN resolution and the `diagnose` pre-flight check.

See the design spec's "Key resolution" section for the precedence order
this module implements, and CLAUDE.md / the spec's "Global Constraints"
for the stdlib-only, Python-3.9-syntax rules every module here follows.
"""
from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional

KEY_NAME = "APIFY_API_TOKEN"
PLUGIN_OPTION_NAME = "CLAUDE_PLUGIN_OPTION_APIFY_API_TOKEN"


@dataclass
class Keys:
    """Result of resolving the founder's Apify key from every source."""

    apify: Optional[str]
    source: Optional[str]  # "env" | "plugin_option" | "project_env" | "global_env"
    warnings: List[str]


def load_env_file(path: Path) -> Dict[str, str]:
    """Parse a simple `KEY=VALUE` env file into a dict.

    Blank lines and lines starting with `#` are skipped. A value's
    matching surrounding quotes (single or double) are stripped. Keys
    whose value is empty (after quote-stripping) are dropped entirely
    rather than kept as "".
    """
    values: Dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not value:
            continue
        values[key] = value
    return values


def check_file_permissions(path: Path) -> Optional[str]:
    """Warn when `path` is readable or writable by group/others (not 600)."""
    mode = path.stat().st_mode
    if mode & 0o077:
        return "{0} is readable by other users. Run: chmod 600 {0}".format(path)
    return None


def _global_config_dir(environ: Mapping[str, str]) -> Optional[Path]:
    """Return the global config dir, or None to mean "skip it entirely".

    `CONTENTOS_CONFIG_DIR` overrides the default `~/.config/contentos`
    location when present in `environ`; an explicit empty string means
    clean mode (used by tests so they never touch a real home directory).
    Only when the key is absent do we fall back to `environ["HOME"]`.
    """
    if "CONTENTOS_CONFIG_DIR" in environ:
        override = environ["CONTENTOS_CONFIG_DIR"]
        if override == "":
            return None
        return Path(override)
    return Path(environ["HOME"]) / ".config" / "contentos"


def resolve_keys(project_dir: Path, environ: Mapping[str, str] = os.environ) -> Keys:
    """Resolve APIFY_API_TOKEN, trying each source in precedence order.

    Precedence: process env -> plugin option -> project `.env` -> global
    `.env`. An empty string at any source falls through to the next one.
    File-permission warnings are collected for every env file that
    exists (project and global), whether or not it holds the key, and
    independent of which source ultimately wins.
    """
    warnings: List[str] = []
    apify: Optional[str] = None
    source: Optional[str] = None

    if environ.get(KEY_NAME):
        apify = environ[KEY_NAME]
        source = "env"

    if apify is None and environ.get(PLUGIN_OPTION_NAME):
        apify = environ[PLUGIN_OPTION_NAME]
        source = "plugin_option"

    project_env_path = project_dir / ".contentos" / ".env"
    if project_env_path.exists():
        warning = check_file_permissions(project_env_path)
        if warning:
            warnings.append(warning)
        project_values = load_env_file(project_env_path)
        if apify is None and project_values.get(KEY_NAME):
            apify = project_values[KEY_NAME]
            source = "project_env"

    config_dir = _global_config_dir(environ)
    if config_dir is not None:
        global_env_path = config_dir / ".env"
        if global_env_path.exists():
            warning = check_file_permissions(global_env_path)
            if warning:
                warnings.append(warning)
            global_values = load_env_file(global_env_path)
            if apify is None and global_values.get(KEY_NAME):
                apify = global_values[KEY_NAME]
                source = "global_env"

    return Keys(apify=apify, source=source, warnings=warnings)


def diagnose(
    project_dir: Path,
    environ: Mapping[str, str] = os.environ,
    mock: bool = False,
    skill_root: Optional[str] = None,
) -> dict:
    """Build the JSON-able pre-flight report the `diagnose` subcommand prints.

    `skill_root` is computed by contentos.py (which knows where it lives
    on disk) and passed in rather than imported here, so this module
    never imports contentos.py. Whatever is passed is rendered as a
    string, so the key is always present with type str.
    """
    keys = resolve_keys(project_dir, environ)
    contentos_dir = project_dir / ".contentos"

    return {
        "apify": bool(keys.apify),
        "apify_source": keys.source,
        "project_dir": str(project_dir),
        "product_md": (contentos_dir / "product.md").exists(),
        "rules_md": (contentos_dir / "rules.md").exists(),
        "config_json": (contentos_dir / "config.json").exists(),
        "python": platform.python_version(),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "skill_root": str(skill_root),
        "env_perms_ok": not keys.warnings,
        "warnings": keys.warnings,
        "mock": mock,
    }
