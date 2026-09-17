"""`setup`: turn a founder's short answers into their `.contentos/` state.

`setup` is the first command a founder runs. The skill interviews them,
writes the answers to `.contentos/setup-answers.json`, and calls this
module, which writes the four files every later stage reads:

- `product.md`, built from `references/product-template.md`. The
  headings and their order come from the template, so the one place that
  defines the shape of a product file stays the template itself. A
  section the founder answered carries their words; a section they did
  not keeps the template's guidance and a `TODO` line, so the file reads
  as a to-do list rather than as finished work.
- `config.json`, `store.DEFAULT_CONFIG` with the competitor handles
  normalized (see `normalize_handle`).
- `rules.md`, one comment line, and only when it does not exist yet.
  This file is the founder's own; `setup --force` never touches it.
- `.gitignore`, through `store.ensure_gitignore`.

See the design spec's "Reference files" row for `product-template.md`
and its "Skill" section for the `setup` flow. Nothing here touches the
network, and nothing is written until every answer has been validated.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib import codes, store

# The one comment line a fresh `rules.md` carries. `store.read_rules`
# drops comment lines, so an untouched rules.md reads as empty.
RULES_COMMENT = (
    "# One correction per line. ContentOS appends these to the writer and QA prompts."
)

# The line an unanswered section ends with, under the template's own
# guidance. The skill points the founder at these after setup.
TODO_LINE = "TODO: fill this in."

# What one unanswered sub-bullet of Audience profile or Brand voice says.
TODO_BULLET = "TODO"

# The template heading `setup` never copies: `product.md` is the
# founder's own file, not a reference file with a sources footer.
SOURCES_HEADING = "Sources"

# The `## ` heading that holds the scraped accounts. Always answered:
# `run_setup` refuses an empty competitor list.
COMPETITORS_HEADING = "Competitors"

# Sections whose answer is one sentence, rendered as a paragraph.
TEXT_SECTIONS = {
    "Product": "product_name",
    "One-liner": "one_liner",
    "CTA": "cta",
}

# Sections whose answer is a list, rendered one bullet per item. A
# string answer counts as a one-item list (`demo_moment`).
LIST_SECTIONS = {
    "Core features": "core_features",
    "Demo moments": "demo_moment",
    "Allowed claims": "allowed_claims",
    "Forbidden claims": "forbidden_claims",
    "Proof assets": "proof_assets",
    "Hashtag seeds": "hashtag_seeds",
    COMPETITORS_HEADING: "competitors",
}

# Sections the template writes as labeled sub-bullets. The setup
# interview only asks for some of them, so each label either carries the
# founder's answer or reads TODO; the labels and their order come from
# the template, never from here.
BULLET_SECTIONS = {
    "Audience profile": {
        "Who specifically": "target_user",
        "Their number one frustration, in their own words": "frustration",
        "Top 3 objections": "objection",
    },
    "Brand voice": {
        "3 adjectives it is": "voice_on",
        "3 adjectives it is not": "voice_off",
        "10 off-limits words": "off_limits_words",
    },
}

# The header of the generated file. `product.md` is read by the
# director, the writer, and the reviewer, so it says up front what it is
# for.
PRODUCT_HEADER = """# Product facts

ContentOS reads this file at every stage. A claim, a feature, or a word that is
not written here does not exist for the writer or the reviewer. Keep each answer
short and concrete. Plain language, no em dashes. Anything marked TODO is still
yours to fill in."""

_INSTAGRAM_MARKER = "instagram.com/"


class SetupError(Exception):
    """A setup failure, carrying the exit code `contentos.py` returns.

    Always `codes.EXIT_USAGE` today: every way setup can fail is the
    founder (or the skill) handing it something it cannot work from.
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


def normalize_handle(raw: Any) -> str:
    """Reduce one competitor answer to a bare lowercase Instagram handle.

    Accepts `sproutapp`, `@SproutApp`, and any profile URL
    (`https://www.instagram.com/SproutApp/reels/?hl=en`). Everything
    after the host is cut back to the first path segment, a query string
    or fragment is dropped, a leading `@` is stripped, and the result is
    lowercased. Returns `""` for anything that leaves no handle behind,
    which the caller drops.
    """
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if not value:
        return ""

    marker_at = value.lower().find(_INSTAGRAM_MARKER)
    if marker_at != -1:
        value = value[marker_at + len(_INSTAGRAM_MARKER):]
    for separator in ("?", "#"):
        value = value.split(separator, 1)[0]

    segments = [segment for segment in value.split("/") if segment]
    if not segments:
        return ""
    return segments[0].lstrip("@").strip().lower()


def normalize_handles(raw_handles: Any) -> List[str]:
    """Normalize every competitor answer, dropping blanks and duplicates.

    Order is the founder's, because the research summary and the run
    report list accounts in config order and a founder scanning that
    table expects to see the order they typed.
    """
    if not isinstance(raw_handles, list):
        return []
    handles: List[str] = []
    for raw in raw_handles:
        handle = normalize_handle(raw)
        if handle and handle not in handles:
            handles.append(handle)
    return handles


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
    """Split `product-template.md` into `(heading, guidance, bullet labels)`.

    `guidance` is the prose under the heading, line for line as the
    template wrote it. `bullet labels` are the `- Label: hint` lines'
    labels, in order, for the two sections the template writes that way.
    The `Sources` footer is dropped: it belongs to the reference file,
    not to a founder's product.md.
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


def _render_section(
    heading: str, guidance: List[str], labels: List[str], answers: Dict[str, Any]
) -> Tuple[List[str], bool]:
    """Render one product.md section; return its lines and whether it was answered."""
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


def render_product_md(
    template_text: str, answers: Dict[str, Any]
) -> Tuple[str, List[str]]:
    """Build `product.md` from the template and the founder's answers.

    Returns the file's text and the list of headings left as TODO, in
    template order, so the skill can point the founder at them.
    """
    blocks = [PRODUCT_HEADER]
    todo: List[str] = []

    for heading, guidance, labels in parse_template(template_text):
        lines, answered = _render_section(heading, guidance, labels, answers)
        if not answered:
            todo.append(heading)
        blocks.append("\n".join(lines).rstrip())

    return "\n\n".join(blocks).rstrip() + "\n", todo


def run_setup(
    project: Path,
    answers: Dict[str, Any],
    references_dir: Path,
    force: bool = False,
) -> Dict[str, Any]:
    """Write a founder's `.contentos/` state from their answers.

    Refuses, with exit 2, when `answers` is not a JSON object, when no
    usable competitor handle survives normalization, or when
    `product.md` already exists and `force` is false. Nothing is written
    until every one of those has passed, so a refused setup leaves the
    project exactly as it found it.

    `force` rewrites `product.md` and `config.json`. It never rewrites
    `rules.md`: those lines are the founder's own corrections, and
    re-running setup must not throw them away.

    Returns `{project, product_md, config_json, rules_md, gitignore,
    competitors, todo_sections}`.
    """
    project = Path(project)
    if not isinstance(answers, dict):
        raise SetupError("answers must be a JSON object")

    handles = normalize_handles(answers.get("competitors"))
    if not handles:
        raise SetupError(
            "no competitor accounts; setup needs 3 to 8 Instagram handles to research"
        )

    contentos_dir = store.contentos_dir(project)
    product_path = contentos_dir / "product.md"
    config_path = contentos_dir / "config.json"
    rules_path = contentos_dir / "rules.md"

    if product_path.exists() and not force:
        raise SetupError(
            f"{product_path} already exists; re-run with --force to rewrite it"
        )

    template_path = Path(references_dir) / "product-template.md"
    try:
        template_text = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SetupError(f"{template_path}: cannot read the product template: {exc}") from exc

    product_text, todo_sections = render_product_md(template_text, answers)

    store.ensure_gitignore(project)
    product_path.write_text(product_text, encoding="utf-8")

    config = copy.deepcopy(store.DEFAULT_CONFIG)
    config["competitors"] = handles
    store.write_json_atomic(config_path, config)

    if not rules_path.exists():
        rules_path.write_text(RULES_COMMENT + "\n", encoding="utf-8")

    return {
        "project": str(project),
        "product_md": str(product_path),
        "config_json": str(config_path),
        "rules_md": str(rules_path),
        "gitignore": str(contentos_dir / ".gitignore"),
        "competitors": handles,
        "todo_sections": todo_sections,
    }
