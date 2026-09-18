"""`setup`: turn a creator's short answers into their `.contentos/` state.

`setup` is the first command a creator runs. The skill interviews them,
writes the answers to `.contentos/setup-answers.json`, and calls this
module, which writes the four files every later stage reads:

- `creator.md`, built from `references/creator-template.md`. The
  headings and their order come from the template, so the one place that
  defines the shape of a creator file stays the template itself. A
  section the creator answered carries their words; a section they did
  not keeps the template's guidance and a `TODO` line, so the file reads
  as a to-do list rather than as finished work.
- `config.json`, `store.DEFAULT_CONFIG` with the competitor and
  format-account handles normalized (see `normalize_handle`).
- `rules.md`, one comment line, and only when it does not exist yet.
  This file is the creator's own; `setup --force` never touches it.
- `.gitignore`, through `store.ensure_gitignore`.

See the design spec's "Reference files" row for `creator-template.md`
and its "Skill" section for the `setup` flow. Nothing here touches the
network, and nothing is written until every answer has been validated.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from lib import codes, store

# The one comment line a fresh `rules.md` carries. `store.read_rules`
# drops comment lines, so an untouched rules.md reads as empty.
RULES_COMMENT = (
    "# One correction per line. ContentOS appends these to the writer and QA prompts."
)

# The line an unanswered section ends with, under the template's own
# guidance. The skill points the creator at these after setup.
TODO_LINE = "TODO: fill this in."

# What one unanswered sub-bullet of Audience profile or Brand voice says.
TODO_BULLET = "TODO"

# The template heading `setup` never copies: `creator.md` is the
# creator's own file, not a reference file with a sources footer.
SOURCES_HEADING = "Sources"

# The `## ` heading that holds the scraped niche accounts. Always
# answered: `run_setup` refuses an empty competitor list.
COMPETITORS_HEADING = "Competitors"

# The `## ` heading that holds the (optional) format accounts: accounts
# from any niche whose formats travel. Rendered like any other list
# section (empty means guidance plus TODO), except its handles are also
# deduplicated against `COMPETITORS_HEADING`: a handle in both lists
# stays a competitor (see `normalize_format_accounts`).
FORMAT_ACCOUNTS_HEADING = "Format accounts"

# The `## ` heading for the optional offer block. Unlike every other
# section, a blank answer here is not a TODO: it renders `NO_OFFER_LINE`
# instead, because "no offer" is itself a complete, valid answer.
OFFER_HEADING = "What you promote"

# What `## What you promote` reads when the creator has nothing to
# promote. This is a real answer, not a placeholder, so the section is
# never counted as unanswered.
NO_OFFER_LINE = "None. Scripts end on a follow, comment, save, or share ask."

# Sections whose answer is one sentence, rendered as a paragraph.
TEXT_SECTIONS = {
    "Creator": "creator_name",
    "One-liner": "one_liner",
    "CTA": "cta",
}

# Sections whose answer is a list, rendered one bullet per item. A
# string answer counts as a one-item list.
LIST_SECTIONS = {
    "Pillars": "pillars",
    "Payoff moments": "payoff_moments",
    "Allowed claims": "allowed_claims",
    "Forbidden claims": "forbidden_claims",
    "Proof assets": "proof_assets",
    "Hashtag seeds": "hashtag_seeds",
    COMPETITORS_HEADING: "competitors",
    FORMAT_ACCOUNTS_HEADING: "format_accounts",
}

# Sections the template writes as labeled sub-bullets. The setup
# interview only asks for some of them, so each label either carries the
# creator's answer or reads TODO; the labels and their order come from
# the template, never from here.
BULLET_SECTIONS = {
    "Audience profile": {
        "Who specifically": "target_user",
        "Their number one frustration or want, in their own words": "frustration",
        "Top 3 objections": "objection",
    },
    "Brand voice": {
        "3 adjectives it is": "voice_on",
        "3 adjectives it is not": "voice_off",
        "10 off-limits words": "off_limits_words",
    },
}

# The header of the generated file. `creator.md` is read by the
# director, the writer, and the reviewer, so it says up front what it is
# for.
CREATOR_HEADER = """# Creator profile

ContentOS reads this file at every stage. A claim, a feature, or a word that is
not written here does not exist for the writer or the reviewer. Keep each answer
short and concrete. Plain language, no em dashes. Anything marked TODO is still
yours to fill in."""

# What Instagram allows in a username, and therefore the only shape a
# competitor or format-account entry may reduce to. Anything else is a
# typo, not an account, and scraping it would spend the creator's money
# on nothing.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]+$")

# instagram.com, or any subdomain of it. `notinstagram.com` and
# `instagram.com.example.net` both fail: the optional group has to end
# in a dot, and the match is anchored at both ends.
_INSTAGRAM_HOST_RE = re.compile(r"^(?:[A-Za-z0-9.-]+\.)?instagram\.com$", re.IGNORECASE)


class SetupError(Exception):
    """A setup failure, carrying the exit code `contentos.py` returns.

    Always `codes.EXIT_USAGE` today: every way setup can fail is the
    creator (or the skill) handing it something it cannot work from.
    The attribute exists so the CLI handler never has to know that.
    """

    def __init__(self, message: str, exit_code: int = codes.EXIT_USAGE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def load_answers(path: Path) -> Dict[str, Any]:
    """Read and parse the answers JSON file `--answers-file` names.

    Raises `SetupError` (exit 2) when the file is missing, unreadable,
    not valid JSON, or not a JSON object. The skill writes this file
    itself, so a failure here means the file on disk is not what the
    skill thinks it wrote.
    """
    path = Path(path)
    if not path.exists():
        raise SetupError(f"{path}: no such answers file")
    try:
        answers = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SetupError(f"{path}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise SetupError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(answers, dict):
        raise SetupError(f"{path}: expected a JSON object of answers")
    return answers


def _url_parts(value: str) -> Tuple[Optional[str], str]:
    """Split a URL-ish account entry into `(host, path)`.

    Handles both `https://www.instagram.com/sproutapp/` and the
    scheme-less `instagram.com/sproutapp` a creator is just as likely to
    paste. Returns `(None, "")` when the entry has no path separator at
    all, which is the bare-handle case.
    """
    if "://" in value:
        parts = urlsplit(value)
        return parts.netloc, parts.path
    if "/" in value:
        host, _, path = value.partition("/")
        return host, path
    return None, ""


def normalize_handle(raw: Any) -> str:
    """Reduce one competitor or format-account answer to a bare lowercase handle.

    Accepts a bare handle (`sproutapp`, `@SproutApp`) or any
    instagram.com profile URL, with or without a scheme, a subdomain,
    extra path segments, a query string, or a fragment
    (`https://www.instagram.com/SproutApp/reels/?hl=en`). The first path
    segment is the handle; a leading `@` is stripped and the result is
    lowercased.

    Returns `""` for a blank or non-string entry, which the caller
    drops. Raises `SetupError` for anything else that is not an
    Instagram handle: a URL on another host, or a handle carrying a
    character Instagram does not allow, such as a space. Those are
    typos, and scraping them would spend the creator's Apify credit on
    an account that cannot exist.
    """
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if not value:
        return ""

    host, path = _url_parts(value)
    if host is None:
        candidate = value
    else:
        if not _INSTAGRAM_HOST_RE.match(host):
            raise SetupError(
                "{0!r} is not an Instagram account; an account is a handle "
                "like sproutapp or a link like "
                "https://www.instagram.com/sproutapp/".format(raw)
            )
        segments = [segment for segment in path.split("/") if segment]
        candidate = segments[0] if segments else ""

    candidate = candidate.lstrip("@").strip()
    if not _HANDLE_RE.match(candidate):
        raise SetupError(
            "{0!r} is not an Instagram handle; accounts use only letters, "
            "numbers, dots, and underscores".format(raw)
        )
    return candidate.lower()


def normalize_handles(raw_handles: Any) -> List[str]:
    """Normalize every account answer, dropping blanks and duplicates.

    Order is the creator's, because the research summary and the run
    report list accounts in config order and a creator scanning that
    table expects to see the order they typed. Raises `SetupError`,
    naming the entry, on the first answer that is not an Instagram
    handle or profile URL.
    """
    if not isinstance(raw_handles, list):
        return []
    handles: List[str] = []
    for raw in raw_handles:
        handle = normalize_handle(raw)
        if handle and handle not in handles:
            handles.append(handle)
    return handles


def normalize_format_accounts(raw_format_accounts: Any, competitors: List[str]) -> List[str]:
    """Normalize the format-account handles, dropping any that are also a competitor.

    A handle a creator listed under both `Competitors` and `Format
    accounts` stays a competitor: it is dropped from the format-account
    list (case-insensitively, since `normalize_handles` already
    lowercases both sides) so research never tags the same account as
    both a niche and a format account.
    """
    handles = normalize_handles(raw_format_accounts)
    competitor_set = set(competitors)
    return [handle for handle in handles if handle not in competitor_set]


def _answer_text(answers: Dict[str, Any], key: str) -> str:
    """One answer as a single stripped line, or `""` when it is not usable."""
    value = answers.get(key)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return ", ".join(item.strip() for item in value if isinstance(item, str) and item.strip())
    return ""


def _answer_list(answers: Dict[str, Any], key: str) -> List[str]:
    """One answer as a list of non-empty lines; a string counts as one item."""
    value = answers.get(key)
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def parse_template(template_text: str) -> List[Tuple[str, List[str], List[str]]]:
    """Split `creator-template.md` into `(heading, guidance, bullet labels)`.

    `guidance` is the prose under the heading, line for line as the
    template wrote it. `bullet labels` are the `- Label: hint` lines'
    labels, in order, for the sections the template writes that way.
    The `Sources` footer is dropped: it belongs to the reference file,
    not to a creator's creator.md.
    """
    sections: List[Tuple[str, List[str], List[str]]] = []
    heading: Optional[str] = None
    guidance: List[str] = []
    labels: List[str] = []

    def flush() -> None:
        if heading is not None and heading != SOURCES_HEADING:
            sections.append((heading, list(guidance), list(labels)))

    for line in template_text.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            guidance = []
            labels = []
            continue
        if heading is None:
            continue
        if line.startswith("- "):
            label = line[2:].partition(":")[0].strip()
            if label:
                labels.append(label)
            continue
        guidance.append(line)

    flush()
    return sections


def _guidance_block(guidance: List[str]) -> List[str]:
    """The template's guidance prose, trimmed of leading and trailing blanks."""
    lines = list(guidance)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _render_offer_section(heading: str, answers: Dict[str, Any]) -> Tuple[List[str], bool]:
    """Render `## What you promote`: the one section a blank answer never TODOs.

    A blank or missing `offer` renders `NO_OFFER_LINE` alone: "no
    offer" is a complete answer, not a gap. An `offer` renders both
    bullets, with `offer_objection` reading TODO when it is itself
    blank. Either way the section counts as answered.
    """
    offer = _answer_text(answers, "offer")
    if not offer:
        return ["## " + heading, NO_OFFER_LINE], True

    objection = _answer_text(answers, "offer_objection")
    bullets = [
        "- What it is: " + offer,
        "- The objection that stops people: " + (objection if objection else TODO_BULLET),
    ]
    return ["## " + heading] + bullets, True


def _render_section(
    heading: str, guidance: List[str], labels: List[str], answers: Dict[str, Any]
) -> Tuple[List[str], bool]:
    """Render one creator.md section; return its lines and whether it was answered."""
    if heading == OFFER_HEADING:
        return _render_offer_section(heading, answers)

    prose = _guidance_block(guidance)

    if heading in BULLET_SECTIONS:
        mapping = BULLET_SECTIONS[heading]
        bullets = []
        answered = False
        for label in labels:
            key = mapping.get(label)
            value = _answer_text(answers, key) if key else ""
            if value:
                answered = True
            bullets.append("- {0}: {1}".format(label, value if value else TODO_BULLET))
        lines = ["## " + heading] + prose + [""] + bullets
        return lines, answered

    if heading in LIST_SECTIONS:
        if heading == COMPETITORS_HEADING:
            # The same handles config.json gets, so the two files can
            # never disagree about which accounts a run scrapes.
            items = normalize_handles(answers.get(LIST_SECTIONS[heading]))
        elif heading == FORMAT_ACCOUNTS_HEADING:
            competitors = normalize_handles(answers.get("competitors"))
            items = normalize_format_accounts(answers.get(LIST_SECTIONS[heading]), competitors)
        else:
            items = _answer_list(answers, LIST_SECTIONS[heading])
        if items:
            return ["## " + heading] + ["- " + item for item in items], True
        return ["## " + heading] + prose + ["", TODO_LINE], False

    if heading in TEXT_SECTIONS:
        value = _answer_text(answers, TEXT_SECTIONS[heading])
        if value:
            return ["## " + heading, value], True
        return ["## " + heading] + prose + ["", TODO_LINE], False

    # A heading the template grew that `setup` does not ask about yet.
    return ["## " + heading] + prose + ["", TODO_LINE], False


def render_creator_md(
    template_text: str, answers: Dict[str, Any]
) -> Tuple[str, List[str]]:
    """Build `creator.md` from the template and the creator's answers.

    Returns the file's text and the list of headings left as TODO, in
    template order, so the skill can point the creator at them.
    """
    blocks = [CREATOR_HEADER]
    todo: List[str] = []

    for heading, guidance, labels in parse_template(template_text):
        lines, answered = _render_section(heading, guidance, labels, answers)
        if not answered:
            todo.append(heading)
        blocks.append("\n".join(lines).rstrip())

    return "\n\n".join(blocks).rstrip() + "\n", todo


def _config_for(
    config_path: Path, competitors: List[str], format_accounts: List[str]
) -> Dict[str, Any]:
    """Build the `config.json` to write: defaults, or the tuned file kept.

    A creator who raised `apify_max_charge_usd`, dropped `briefs` to 2,
    or moved `qa_pass_threshold` must not lose that because they re-ran
    setup to change their accounts. Whenever `config.json` exists and
    parses as a JSON object, every key it holds is kept, including keys
    ContentOS does not know about, and only `competitors` and
    `format_accounts` are replaced. This holds with or without
    `--force`: `--force` is about overwriting `creator.md`, and setup
    can reach this point without it whenever `creator.md` is absent.
    Anything else (no file yet, or a file that is not a readable JSON
    object) falls back to `store.DEFAULT_CONFIG` plus the handles.
    """
    try:
        existing = store.read_json(config_path)
    except (ValueError, OSError):
        existing = None
    if isinstance(existing, dict):
        config = copy.deepcopy(existing)
    else:
        config = copy.deepcopy(store.DEFAULT_CONFIG)
    config["competitors"] = competitors
    config["format_accounts"] = format_accounts
    return config


def run_setup(
    project: Path,
    answers: Dict[str, Any],
    references_dir: Path,
    force: bool = False,
) -> Dict[str, Any]:
    """Write a creator's `.contentos/` state from their answers.

    Refuses, with exit 2, when `answers` is not a JSON object, when no
    usable competitor handle survives normalization, or when
    `creator.md` already exists and `force` is false. Nothing is
    written until every one of those has passed, so a refused setup
    leaves the project exactly as it found it.

    `force` rewrites `creator.md`. It never rewrites `rules.md`: those
    lines are the creator's own corrections, and re-running setup must
    not throw them away. `config.json` is rewritten either way, keeping
    every setting the creator had tuned and replacing only
    `competitors` and `format_accounts` (see `_config_for`).

    Returns `{project, creator_md, config_json, rules_md, gitignore,
    competitors, format_accounts, todo_sections}`.
    """
    project = Path(project)
    if not isinstance(answers, dict):
        raise SetupError("answers must be a JSON object")

    handles = normalize_handles(answers.get("competitors"))
    if not handles:
        raise SetupError(
            "no competitor accounts; setup needs 3 to 8 Instagram handles in your niche"
        )
    format_accounts = normalize_format_accounts(answers.get("format_accounts"), handles)

    contentos_dir = store.contentos_dir(project)
    creator_path = contentos_dir / "creator.md"
    config_path = contentos_dir / "config.json"
    rules_path = contentos_dir / "rules.md"

    if creator_path.exists() and not force:
        raise SetupError(
            f"{creator_path} already exists; re-run with --force to rewrite it"
        )

    template_path = Path(references_dir) / "creator-template.md"
    try:
        template_text = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SetupError(f"{template_path}: cannot read the creator template: {exc}") from exc

    creator_text, todo_sections = render_creator_md(template_text, answers)

    store.ensure_gitignore(project)
    creator_path.write_text(creator_text, encoding="utf-8")

    store.write_json_atomic(config_path, _config_for(config_path, handles, format_accounts))

    if not rules_path.exists():
        rules_path.write_text(RULES_COMMENT + "\n", encoding="utf-8")

    return {
        "project": str(project),
        "creator_md": str(creator_path),
        "config_json": str(config_path),
        "rules_md": str(rules_path),
        "gitignore": str(contentos_dir / ".gitignore"),
        "competitors": handles,
        "format_accounts": format_accounts,
        "todo_sections": todo_sections,
    }
