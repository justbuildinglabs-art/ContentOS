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
# Claude Code hands plugin settings to hook processes only, never to
# commands run through the Bash tool. The SessionStart hook copies the
# setting into this file in the global config dir (`sync_plugin_option`),
# and resolve_keys reads it back as the plugin option.
PLUGIN_OPTION_FILE = "plugin-option.env"


@dataclass
class Keys:
    """Result of resolving the creator's Apify key from every source."""

    apify: Optional[str]
    source: Optional[str]  # "env" | "plugin_option" | "project_env" | "global_env"
    warnings: List[str]


def load_env_file(path: Path, warnings: Optional[List[str]] = None) -> Dict[str, str]:
    """Parse a simple `KEY=VALUE` env file into a dict.

    Blank lines and lines starting with `#` are skipped. A leading
    `export ` is stripped first, since that is how the line reads when a
    creator copies it out of their shell profile. A value's matching
    surrounding quotes (single or double) are stripped. Keys whose value
    is empty (after quote-stripping) are dropped entirely rather than
    kept as "".

    A file that cannot be read at all -- not valid UTF-8, a directory in
    the file's place, permissions -- yields `{}` plus one line appended
    to `warnings` when a list was passed. Key resolution has three other
    sources to fall back on, so an unreadable `.env` is never a reason
    to end the whole command in a traceback.
    """
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        if warnings is not None:
            warnings.append(f"{path} could not be read and was ignored: {exc}")
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
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
    When the key is absent, fall back to `environ["HOME"]` -- but a
    missing (or empty) HOME means "no global config file" too, the same
    as CONTENTOS_CONFIG_DIR="". resolve_keys must never raise just
    because of the environment's shape, so this never falls back to
    Path.home().
    """
    if "CONTENTOS_CONFIG_DIR" in environ:
        override = environ["CONTENTOS_CONFIG_DIR"]
        if override == "":
            return None
        return Path(override)
    home = environ.get("HOME")
    if not home:
        return None
    return Path(home) / ".config" / "contentos"


def sync_plugin_option(environ: Mapping[str, str] = os.environ) -> List[str]:
    """Copy the /plugin setting into `PLUGIN_OPTION_FILE`, for the SessionStart hook.

    A set option is written as one `APIFY_API_TOKEN=...` line, mode 600,
    replacing any older copy in one step. An unset or blank option
    removes the copy, so clearing the setting in /plugin clears the key
    too. Clean mode (no global config dir) does nothing. The creator's own
    global `.env` is never touched.

    Returns warnings rather than raising, and no warning ever contains
    the key: a failed hook must never block a session or leak the key.
    """
    config_dir = _global_config_dir(environ)
    if config_dir is None:
        return []
    target = config_dir / PLUGIN_OPTION_FILE
    value = environ.get(PLUGIN_OPTION_NAME, "").strip()

    try:
        if "\n" in value or "\r" in value:
            if target.exists():
                target.unlink()
            return [
                "The Apify API token in /plugin spans more than one line, so ContentOS "
                "ignored it. Paste it again as a single line."
            ]
        if not value:
            if target.exists():
                target.unlink()
            return []

        config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = config_dir / (PLUGIN_OPTION_FILE + ".tmp")
        if temp.exists():
            temp.unlink()
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                "# Written by the ContentOS SessionStart hook from your /plugin setting.\n"
                "# Change the key in /plugin, not here: this file is rewritten every session.\n"
                "{0}={1}\n".format(KEY_NAME, value)
            )
        os.replace(str(temp), str(target))
    except OSError as exc:
        return [
            "Could not copy the /plugin Apify token to {0}: {1}".format(
                target, exc.strerror or type(exc).__name__
            )
        ]
    return []


def resolve_keys(project_dir: Path, environ: Mapping[str, str] = os.environ) -> Keys:
    """Resolve APIFY_API_TOKEN, trying each source in precedence order.

    Precedence: process env -> plugin option -> project `.env` -> global
    `.env`. The plugin option is the `CLAUDE_PLUGIN_OPTION_*` variable
    when present, else the SessionStart hook's copy of it in
    `PLUGIN_OPTION_FILE`. An empty string at any source falls through to
    the next one.
    File-permission warnings are collected for every env file that
    exists (project and global), whether or not it holds the key, and
    independent of which source ultimately wins. An env file that
    cannot be read adds its own warning and is otherwise skipped
    (`load_env_file`), so a corrupt `.env` never stops the later
    sources from being tried.
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

    config_dir = _global_config_dir(environ)

    if config_dir is not None:
        option_path = config_dir / PLUGIN_OPTION_FILE
        if option_path.exists():
            warning = check_file_permissions(option_path)
            if warning:
                warnings.append(warning)
            option_values = load_env_file(option_path, warnings)
            if apify is None and option_values.get(KEY_NAME):
                apify = option_values[KEY_NAME]
                source = "plugin_option"

    project_env_path = project_dir / ".contentos" / ".env"
    if project_env_path.exists():
        warning = check_file_permissions(project_env_path)
        if warning:
            warnings.append(warning)
        project_values = load_env_file(project_env_path, warnings)
        if apify is None and project_values.get(KEY_NAME):
            apify = project_values[KEY_NAME]
            source = "project_env"

    if config_dir is not None:
        global_env_path = config_dir / ".env"
        if global_env_path.exists():
            warning = check_file_permissions(global_env_path)
            if warning:
                warnings.append(warning)
            global_values = load_env_file(global_env_path, warnings)
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

    `whisper` says whether a whisper-cpp binary is on PATH;
    `whisper_model` is the model file transcripts would use (config
    `whisper_model`, else env, else the default path), or None when
    there is none on disk. Both come from `lib/transcribe.py`, the only
    module that knows about whisper. An unreadable or invalid config
    just means no configured model path here: diagnose never fails.
    """
    # Imported here, not at the top: transcribe -> video -> apify is a
    # long chain for the key-resolution helpers every command imports.
    from lib import store, transcribe

    keys = resolve_keys(project_dir, environ)
    contentos_dir = project_dir / ".contentos"
    try:
        cfg = store.load_config(project_dir)
    except Exception:  # noqa: BLE001 -- diagnose reports, it never fails
        cfg = {}
    model = transcribe.find_model(cfg, environ)

    return {
        "apify": bool(keys.apify),
        "apify_source": keys.source,
        "project_dir": str(project_dir),
        "creator_md": (contentos_dir / "creator.md").exists(),
        "rules_md": (contentos_dir / "rules.md").exists(),
        "config_json": (contentos_dir / "config.json").exists(),
        "python": platform.python_version(),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "whisper": transcribe.find_whisper() is not None,
        "whisper_model": str(model) if model is not None else None,
        "skill_root": str(skill_root),
        "env_perms_ok": not keys.warnings,
        "warnings": keys.warnings,
        "mock": mock,
    }
