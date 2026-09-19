# Weekly Ideas (0.4.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a ContentOS run into a weekly, ranked list of up to 20 ideas that remembers earlier weeks.

**Architecture:** Selection gets a 14-day window, a 2x floor, and a soft per-account cap (`lib/outliers.py`). A new `lib/ideas.py` owns the `.contentos/ideas.json` ledger, and only `rank` writes it. `director.rank_briefs` merges new analyses, carried ideas (scored down by 1.0 per week carried), and format fill from `03-fill.json`, which the existing synthesis dispatch now writes. `briefs.md` opens with a topic-first digest.

**Tech Stack:** Python 3.9-compatible, standard library only, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-16-contentos-design.md`, section "0.4.0 changes (2026-09-19): weekly ideas". It is the binding authority. Read it before starting any task.

## Global Constraints

- Python 3.9-compatible syntax only; every module starts with `from __future__ import annotations`.
- Standard library only. No pip dependencies.
- Tests: `python3 -m unittest discover -s tests -v`. Every test module subclasses `tests.helpers.NoNetworkTestCase`; tests never touch the network and never need real keys.
- Write the failing test first, then the code.
- `lib/__init__.py` stays empty. Creator state lives under `<project>/.contentos/`, never in this repo.
- Creator-facing text: plain language, short sentences, no em dashes.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Run every command from the worktree root `/Users/lesliezhang/git/ContentOS/.claude/worktrees/contentos-0.4.0`.

## File map

| File | Change |
|---|---|
| `skills/contentos/scripts/lib/store.py` | New defaults and keys, validation |
| `skills/contentos/scripts/lib/outliers.py` | `below_min_ratio`, soft per-account cap |
| `skills/contentos/scripts/lib/ideas.py` | New: the ledger |
| `skills/contentos/scripts/schemas/analysis.schema.json` | Optional `idea_title` |
| `skills/contentos/scripts/schemas/fill.schema.json` | New: `03-fill.json` shape |
| `skills/contentos/scripts/lib/director.py` | `idea_title` coercion, synth prompt fill section, `verify_fill`, `rank_briefs` carried and fill, digest in `render_briefs_md` |
| `skills/contentos/scripts/lib/direct.py` | `verify_synth` checks fill, mock seeds fill, `run_rank` wires the ledger |
| `fixtures/fill.sample.json` | New: mock fill ideas |
| `agents/content-director.md`, `agents/script-writer.md` | `idea_title`, `03-fill.json`, fill briefs |
| `skills/contentos/SKILL.md` | No-new-outlier week, picking, `auto_scripts` |
| `tests/helpers.py` | `PRE_WEEKLY_CONFIG` |
| `README.md`, `CHANGELOG.md`, `.claude-plugin/*.json`, `tests/test_manifests.py` | 0.4.0 |

---

### Task 1: Config keys and the pre-weekly test config

**Files:**
- Modify: `skills/contentos/scripts/lib/store.py` (`DEFAULT_CONFIG`, `_NUMERIC_CONFIG_KEYS`, `_INT_CONFIG_KEYS`, the `max_format_briefs` check in `_validate_config`)
- Modify: `tests/helpers.py`, `tests/test_research.py`, `tests/test_frames.py`, `tests/test_transcribe.py`, `tests/test_direct_commands.py`, `tests/test_e2e_mock.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: config keys `lookback_days` (14), `briefs` (20), `min_outlier_ratio` (2.0), `carry_weeks` (2), `fill_ideas` (8), `auto_scripts` (3). `tests.helpers.PRE_WEEKLY_CONFIG = {"lookback_days": 90, "min_outlier_ratio": 1.0, "briefs": 5}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_store.py` (new class at the end, same imports as the module):

```python
class WeeklyConfigTests(NoNetworkTestCase):
    def test_weekly_defaults(self) -> None:
        self.assertEqual(store.DEFAULT_CONFIG["lookback_days"], 14)
        self.assertEqual(store.DEFAULT_CONFIG["briefs"], 20)
        self.assertEqual(store.DEFAULT_CONFIG["min_outlier_ratio"], 2.0)
        self.assertEqual(store.DEFAULT_CONFIG["carry_weeks"], 2)
        self.assertEqual(store.DEFAULT_CONFIG["fill_ideas"], 8)
        self.assertEqual(store.DEFAULT_CONFIG["auto_scripts"], 3)

    def test_zero_turns_off_carry_and_fill(self) -> None:
        with temp_project() as project:
            _write(project, {"competitors": ["a"], "carry_weeks": 0, "fill_ideas": 0})
            config = store.load_config(project)
        self.assertEqual((config["carry_weeks"], config["fill_ideas"]), (0, 0))

    def test_rejects_bad_weekly_values(self) -> None:
        bad = [
            {"min_outlier_ratio": 0},
            {"min_outlier_ratio": "2"},
            {"carry_weeks": -1},
            {"carry_weeks": 1.5},
            {"fill_ideas": True},
            {"auto_scripts": 0},
            {"auto_scripts": 2.5},
        ]
        for override in bad:
            with self.subTest(override=override), temp_project() as project:
                _write(project, dict({"competitors": ["a"]}, **override))
                with self.assertRaises(store.ConfigError):
                    store.load_config(project)
```

If `tests/test_store.py` has no `_write(project, dict)` helper, add one next to its imports:

```python
def _write(project: Path, config: Dict[str, Any]) -> None:
    config_dir = store.contentos_dir(project)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m unittest tests.test_store.WeeklyConfigTests -v`
Expected: FAIL (`KeyError: 'min_outlier_ratio'`, and 90 != 14).

- [ ] **Step 3: Implement** in `store.py`:
  - `DEFAULT_CONFIG`: change `"lookback_days": 90` to `14` and `"briefs": 5` to `20`. After `"min_specifics": 3,` add:

```python
    # 0.4.0: the weekly ideas list (design spec, "0.4.0 changes").
    "min_outlier_ratio": 2.0,
    "carry_weeks": 2,
    "fill_ideas": 8,
    "auto_scripts": 3,
```

  - Add `_COUNT_KEYS_ALLOWING_ZERO = ("max_format_briefs", "carry_weeks", "fill_ideas")` above `_NUMERIC_CONFIG_KEYS`. In the `_NUMERIC_CONFIG_KEYS` comprehension, replace `key not in ("max_format_briefs", "apify_transcript_usd_per_min")` with `key not in _COUNT_KEYS_ALLOWING_ZERO + ("apify_transcript_usd_per_min",)`. Update the comment above it to name all three zero-allowed counts.
  - Add `"auto_scripts"` to `_INT_CONFIG_KEYS`.
  - Replace the single `max_format_briefs` check in `_validate_config` with a loop over `_COUNT_KEYS_ALLOWING_ZERO` that raises `ConfigError(f"{key} must be a whole number greater than or equal to 0")`. Keep the existing message text for `max_format_briefs`, since it is the same string.

- [ ] **Step 4: Run the store tests, then the whole suite**

Run: `python3 -m unittest tests.test_store -v` → PASS.
Run: `python3 -m unittest discover -s tests` → exactly these 10 fail (pinned by a trial run on 2026-09-19): `test_direct_commands` DirectPromptTests ×2 and RankTests ×3, `test_e2e_mock` full pipeline, `test_frames` research wiring, `test_research` mock research, `test_transcribe` CLI backfill and research wiring.

- [ ] **Step 5: Pin the 0.3.0 fixture story in those tests.** In `tests/helpers.py` add:

```python
# The 0.3.0 selection and brief count. Fixture reels span April to
# September and their small accounts give blended ratios near 1.3, so
# tests that check the original five-brief fixture story pin these.
PRE_WEEKLY_CONFIG = {"lookback_days": 90, "min_outlier_ratio": 1.0, "briefs": 5}
```

  - `tests/test_research.py`, `tests/test_frames.py`, `tests/test_transcribe.py`: import `PRE_WEEKLY_CONFIG` and change each `_write_config` body to write `json.dumps(dict(PRE_WEEKLY_CONFIG, **overrides))`. Update the docstring to "`PRE_WEEKLY_CONFIG` overlaid with `overrides`".
  - `tests/test_direct_commands.py` `_write_project`: write `dict(PRE_WEEKLY_CONFIG, competitors=FIXTURE_COMPETITORS, format_accounts=FIXTURE_FORMAT_ACCOUNTS)`.
  - `tests/test_e2e_mock.py` `_run_pipeline`: right after the `setup --answers-file` step, overlay the config:

```python
        config_path = project / ".contentos" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.update(PRE_WEEKLY_CONFIG)
        config_path.write_text(json.dumps(config), encoding="utf-8")
```

- [ ] **Step 6: Run the whole suite** → `OK`.
- [ ] **Step 7: Commit** `git add -A skills/contentos/scripts/lib/store.py tests && git commit` with message "Add the 0.4.0 weekly config keys and pin the 0.3.0 fixture story in tests".

---

### Task 2: Ratio floor and soft per-account cap

**Files:**
- Modify: `skills/contentos/scripts/lib/outliers.py` (`REASON_*`, `_exclusion_reason`, `select_outliers` and its docstring)
- Test: `tests/test_outliers.py`

**Interfaces:**
- Consumes: `cfg["min_outlier_ratio"]` (Task 1).
- Produces: `outliers.REASON_BELOW_MIN_RATIO = "below_min_ratio"`. `select_outliers` keeps its signature.

- [ ] **Step 1: Keep old tests meaningful.** In `tests/test_outliers.py`, change `_cfg` so it starts from the 0.3.0 selection, and document it:

```python
    cfg = dict(DEFAULT_CONFIG)
    # 0.3.0 selection (90-day window, no ratio floor) so pre-0.4.0 tests
    # keep their meaning; 0.4.0 tests override these explicitly.
    cfg.update({"lookback_days": 90, "min_outlier_ratio": 0.0})
    cfg.update(overrides)
    return cfg
```

  In `test_fixture_outliers_selected`, replace both uses of `DEFAULT_CONFIG` with `fixture_cfg = _cfg(min_outlier_ratio=1.0)`. Replace the comment "outside the 90-day lookback" with "outside the lookback".

- [ ] **Step 2: Write the failing tests** (new class):

```python
class WeeklySelectionTests(NoNetworkTestCase):
    def test_below_min_ratio_is_excluded_after_min_plays(self) -> None:
        low = _scored_reel(shortCode="LOW", outlier_ratio=1.9)
        few = _scored_reel(shortCode="FEW", outlier_ratio=1.0, plays=10)
        ok = _scored_reel(shortCode="OK", outlier_ratio=2.0)
        cfg = _cfg(min_outlier_ratio=2.0, min_plays=100)

        selection = outliers.select_outliers([low, few, ok], cfg, NOW)

        reasons = {item["shortCode"]: item["reason"] for item in selection.excluded}
        self.assertEqual(reasons, {"LOW": "below_min_ratio", "FEW": "below_min_plays"})
        self.assertEqual([r["shortCode"] for r in selection.selected], ["OK"])

    def test_below_min_ratio_wins_over_already_briefed(self) -> None:
        low = _scored_reel(shortCode="LOW", outlier_ratio=1.5)
        selection = outliers.select_outliers(
            [low], _cfg(min_outlier_ratio=2.0), NOW, already_briefed={"LOW"}
        )
        self.assertEqual(selection.excluded, [{"shortCode": "LOW", "reason": "below_min_ratio"}])

    def test_overflow_fills_selected_before_any_slot_goes_empty(self) -> None:
        busy = [
            _scored_reel(shortCode=f"B{i}", ownerUsername="busy", outlier_ratio=10.0 - i)
            for i in range(4)
        ]
        quiet = [_scored_reel(shortCode="Q0", ownerUsername="quiet", outlier_ratio=2.5)]
        cfg = _cfg(max_per_account=2, top_k_videos=4, backfill_pool=0)

        selection = outliers.select_outliers(busy + quiet, cfg, NOW)

        # Capped list first (B0, B1, Q0), then busy's overflow (B2).
        self.assertEqual([r["shortCode"] for r in selection.selected], ["B0", "B1", "Q0", "B2"])
        self.assertEqual(selection.excluded, [{"shortCode": "B3", "reason": "per_account_cap"}])

    def test_overflow_goes_to_backfill_after_selected(self) -> None:
        busy = [
            _scored_reel(shortCode=f"B{i}", ownerUsername="busy", outlier_ratio=10.0 - i)
            for i in range(3)
        ]
        cfg = _cfg(max_per_account=1, top_k_videos=1, backfill_pool=1)

        selection = outliers.select_outliers(busy, cfg, NOW)

        self.assertEqual([r["shortCode"] for r in selection.selected], ["B0"])
        self.assertEqual([r["shortCode"] for r in selection.backfill], ["B1"])
        self.assertEqual(selection.excluded, [{"shortCode": "B2", "reason": "per_account_cap"}])
```

- [ ] **Step 3: Run and watch them fail.** `python3 -m unittest tests.test_outliers.WeeklySelectionTests -v` → FAIL. Also run the whole module; any older test that asserted `per_account_cap` for a reel that now lands in `selected` or `backfill` must be updated to the soft-cap result, with a comment citing "0.4.0 soft cap".

- [ ] **Step 4: Implement.** Add `REASON_BELOW_MIN_RATIO = "below_min_ratio"` after `REASON_BELOW_MIN_PLAYS`. In `_exclusion_reason`, after the `min_plays` check:

```python
    if (reel.get("outlier_ratio") or 0) < cfg.get("min_outlier_ratio", 0):
        return REASON_BELOW_MIN_RATIO
```

  Replace the capping loop and the slicing in `select_outliers`:

```python
    max_per_account = cfg["max_per_account"]
    per_account_counts: Dict[str, int] = {}
    capped: List[Dict[str, Any]] = []
    overflow: List[Dict[str, Any]] = []
    for reel in survivors:
        account = _account(reel)
        count = per_account_counts.get(account, 0)
        if count < max_per_account:
            capped.append(reel)
            per_account_counts[account] = count + 1
        else:
            overflow.append(reel)

    # 0.4.0 soft cap: a busy account's extra outliers rank after every
    # capped reel but still beat an empty slot (design spec, "0.4.0 changes").
    ranked = capped + overflow
    top_k = cfg["top_k_videos"]
    backfill_pool = cfg["backfill_pool"]
    selected = ranked[:top_k]
    backfill = ranked[top_k : top_k + backfill_pool]
    listed = {reel["shortCode"] for reel in selected + backfill}
    for reel in overflow:
        if reel["shortCode"] not in listed:
            excluded.append({"shortCode": reel["shortCode"], "reason": REASON_PER_ACCOUNT_CAP})
```

  Update the `select_outliers` docstring: add `below_min_ratio` to the reason order and describe the soft cap in one or two sentences.

- [ ] **Step 5: Run** `python3 -m unittest tests.test_outliers -v` → PASS, then the whole suite → OK.
- [ ] **Step 6: Commit** "Add the 2x ratio floor and make the per-account cap soft".

---

### Task 3: The ideas ledger

**Files:**
- Create: `skills/contentos/scripts/lib/ideas.py`
- Test: `tests/test_ideas.py`

**Interfaces:**
- Consumes: `store.contentos_dir`, `store.read_json`, `store.write_json_atomic`, `store.run_dir`, `history.load_log`, `agents.brief_state`.
- Produces:
  - `ledger_path(project: Path) -> Path`
  - `load_ledger(project: Path) -> Dict[str, Any]`, always `{"version": 1, "ideas": {...}}`
  - `save_ledger(project: Path, ledger: Dict[str, Any]) -> None`
  - `forget_run(ledger, run_id: str) -> None`
  - `close_entries(project: Path, ledger, carry_weeks: int) -> None`
  - `carry_candidates(ledger, run_id: str) -> List[Dict[str, Any]]`, open entries with `first_run < run_id`, each a shallow copy with `weeks_carried = len(shown)` added
  - `record_briefs(ledger, run_id: str, briefs: List[Dict[str, Any]], reels: Dict[str, Dict[str, Any]]) -> None`
  - `REEL_SNAPSHOT_KEYS = ("shortCode", "url", "ownerUsername", "source_kind", "timestamp", "plays", "outlier_ratio", "viral_proof")`

- [ ] **Step 1: Write the failing tests** in a new `tests/test_ideas.py`:

```python
"""Tests for `lib/ideas.py`: the weekly ideas ledger (design spec, "0.4.0 changes")."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from tests.helpers import NoNetworkTestCase, temp_project

from lib import ideas, store  # noqa: E402


def _brief(brief_id: str, shortcode: str, kind: str = "new") -> Dict[str, Any]:
    return {
        "brief_id": brief_id,
        "shortCode": shortcode,
        "kind": kind,
        "idea_title": f"Idea {shortcode}",
        "brief_title": f"Format {shortcode}",
        "analysis_path": f"/abs/{shortcode}.json",
        "frames_dir": f"/abs/frames/{shortcode}",
    }


def _reel(shortcode: str) -> Dict[str, Any]:
    return {
        "shortCode": shortcode, "url": f"https://x/{shortcode}", "ownerUsername": "acct",
        "source_kind": "niche", "timestamp": "2026-09-10T00:00:00+00:00", "plays": 9000,
        "outlier_ratio": 3.0, "viral_proof": 4.0, "caption": "not kept",
    }


def _run(project: Path, run_id: str) -> Path:
    run_dir = store.run_dir(project, run_id)
    run_dir.mkdir(parents=True)
    return run_dir


class LedgerTests(NoNetworkTestCase):
    def test_missing_or_broken_ledger_is_empty(self) -> None:
        with temp_project() as project:
            self.assertEqual(ideas.load_ledger(project), {"version": 1, "ideas": {}})
            ideas.ledger_path(project).parent.mkdir(parents=True)
            ideas.ledger_path(project).write_text("{not json", encoding="utf-8")
            self.assertEqual(ideas.load_ledger(project), {"version": 1, "ideas": {}})

    def test_record_new_brief_creates_entry_with_snapshot(self) -> None:
        ledger = {"version": 1, "ideas": {}}
        ideas.record_briefs(ledger, "20260912-090000", [_brief("B01", "AAA")], {"AAA": _reel("AAA")})
        entry = ledger["ideas"]["AAA"]
        self.assertEqual(entry["first_run"], "20260912-090000")
        self.assertEqual(entry["shown"], [{"run_id": "20260912-090000", "brief_id": "B01"}])
        self.assertIsNone(entry["closed"])
        self.assertNotIn("caption", entry["reel"])
        self.assertEqual(entry["reel"]["outlier_ratio"], 3.0)

    def test_record_carried_brief_appends_shown_and_keeps_snapshot(self) -> None:
        ledger = {"version": 1, "ideas": {}}
        ideas.record_briefs(ledger, "R1", [_brief("B01", "AAA")], {"AAA": _reel("AAA")})
        ideas.record_briefs(ledger, "R2", [_brief("B04", "AAA", kind="carried")], {})
        self.assertEqual(
            ledger["ideas"]["AAA"]["shown"],
            [{"run_id": "R1", "brief_id": "B01"}, {"run_id": "R2", "brief_id": "B04"}],
        )
        self.assertEqual(ledger["ideas"]["AAA"]["first_run"], "R1")

    def test_fill_briefs_are_not_recorded(self) -> None:
        ledger = {"version": 1, "ideas": {}}
        ideas.record_briefs(ledger, "R1", [_brief("B09", "AAA", kind="fill")], {"AAA": _reel("AAA")})
        self.assertEqual(ledger["ideas"], {})

    def test_forget_run_undoes_a_rank(self) -> None:
        ledger = {"version": 1, "ideas": {}}
        ideas.record_briefs(ledger, "R1", [_brief("B01", "AAA")], {"AAA": _reel("AAA")})
        ideas.record_briefs(
            ledger, "R2", [_brief("B01", "BBB"), _brief("B02", "AAA", kind="carried")],
            {"BBB": _reel("BBB")},
        )
        ideas.forget_run(ledger, "R2")
        self.assertEqual(set(ledger["ideas"]), {"AAA"})
        self.assertEqual(ledger["ideas"]["AAA"]["shown"], [{"run_id": "R1", "brief_id": "B01"}])

    def test_carry_candidates_are_open_and_older(self) -> None:
        ledger = {"version": 1, "ideas": {}}
        ideas.record_briefs(ledger, "R1", [_brief("B01", "AAA"), _brief("B02", "CCC")],
                            {"AAA": _reel("AAA"), "CCC": _reel("CCC")})
        ideas.record_briefs(ledger, "R3", [_brief("B01", "BBB")], {"BBB": _reel("BBB")})
        ledger["ideas"]["CCC"]["closed"] = "skipped"
        found = ideas.carry_candidates(ledger, "R2")
        self.assertEqual([entry["reel"]["shortCode"] for entry in found], ["AAA"])
        self.assertEqual(found[0]["weeks_carried"], 1)

    def test_close_entries(self) -> None:
        with temp_project() as project:
            store.contentos_dir(project).mkdir(parents=True)
            r1, r2, r3 = "20260901-090000", "20260908-090000", "20260915-090000"
            for run_id in (r1, r2, r3):
                _run(project, run_id)
            ledger = {"version": 1, "ideas": {}}
            ideas.record_briefs(
                ledger, r1,
                [_brief("B01", "SCR"), _brief("B02", "SKP"), _brief("B03", "OLD"), _brief("B04", "OPN")],
                {sc: _reel(sc) for sc in ("SCR", "SKP", "OLD", "OPN")},
            )
            ideas.record_briefs(ledger, r2, [_brief("B01", "OLD", kind="carried")], {})
            ideas.record_briefs(ledger, r3, [_brief("B01", "OLD", kind="carried")], {})
            script = store.run_dir(project, r1) / "04-scripts" / "B01.r0.md"
            script.parent.mkdir(parents=True)
            script.write_text("# script\n", encoding="utf-8")
            (store.contentos_dir(project) / "log.json").write_text(
                json.dumps({"briefs": {f"{r1}/B02": {"state": "skipped"}}}), encoding="utf-8"
            )

            ideas.close_entries(project, ledger, carry_weeks=2)

            closed = {sc: entry["closed"] for sc, entry in ledger["ideas"].items()}
            self.assertEqual(closed, {"SCR": "scripted", "SKP": "skipped", "OLD": "expired", "OPN": None})

    def test_save_then_load_round_trips(self) -> None:
        with temp_project() as project:
            ledger = {"version": 1, "ideas": {}}
            ideas.record_briefs(ledger, "R1", [_brief("B01", "AAA")], {"AAA": _reel("AAA")})
            ideas.save_ledger(project, ledger)
            self.assertEqual(ideas.load_ledger(project), ledger)
```

- [ ] **Step 2: Run and watch it fail.** `python3 -m unittest tests.test_ideas -v` → `ImportError: cannot import name 'ideas'`.

- [ ] **Step 3: Implement** `skills/contentos/scripts/lib/ideas.py`:

```python
"""The weekly ideas ledger, `.contentos/ideas.json` (design spec, "0.4.0 changes").

One entry per outlier idea ever shown, keyed by its source shortCode.
Only `rank` writes it: it forgets the run it is re-ranking, closes
entries from what other runs recorded, offers the open ones as carried
ideas, and records the briefs it just ranked. Format fill ideas are
never recorded; each week's synthesis makes fresh ones.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from lib import agents, history, store

LEDGER_VERSION = 1
REEL_SNAPSHOT_KEYS = (
    "shortCode", "url", "ownerUsername", "source_kind", "timestamp", "plays",
    "outlier_ratio", "viral_proof",
)
_LOG_CLOSES = ("filmed", "posted", "skipped")


def _empty() -> Dict[str, Any]:
    return {"version": LEDGER_VERSION, "ideas": {}}


def ledger_path(project: Path) -> Path:
    """Return `<project>/.contentos/ideas.json`."""
    return store.contentos_dir(project) / "ideas.json"


def load_ledger(project: Path) -> Dict[str, Any]:
    """The ledger; an empty one when the file is missing or unreadable, like `log.json`."""
    path = ledger_path(project)
    if not path.exists():
        return _empty()
    try:
        doc = store.read_json(path)
    except (ValueError, OSError):
        return _empty()
    if not isinstance(doc, dict) or not isinstance(doc.get("ideas"), dict):
        return _empty()
    return doc


def save_ledger(project: Path, ledger: Dict[str, Any]) -> None:
    store.write_json_atomic(ledger_path(project), ledger)


def forget_run(ledger: Dict[str, Any], run_id: str) -> None:
    """Drop `run_id`'s shown pairs, and any entry left with none, so a re-rank never double-counts."""
    for key in list(ledger["ideas"]):
        entry = ledger["ideas"][key]
        entry["shown"] = [pair for pair in entry.get("shown", []) if pair.get("run_id") != run_id]
        if not entry["shown"]:
            del ledger["ideas"][key]


def _closed_reason(project: Path, entry: Dict[str, Any], log: Dict[str, Any], carry_weeks: int) -> Any:
    for pair in entry.get("shown", []):
        run_dir = store.run_dir(project, pair["run_id"])
        if run_dir.is_dir() and agents.brief_state(run_dir, pair["brief_id"])["revision"] is not None:
            return "scripted"
    for pair in entry.get("shown", []):
        state = log["briefs"].get(f"{pair['run_id']}/{pair['brief_id']}", {}).get("state")
        if state in _LOG_CLOSES:
            return state
    if len(entry.get("shown", [])) >= carry_weeks + 1:
        return "expired"
    return None


def close_entries(project: Path, ledger: Dict[str, Any], carry_weeks: int) -> None:
    """Close each open entry from scripts, `log.json` marks, and how many runs showed it."""
    log = history.load_log(project)
    for entry in ledger["ideas"].values():
        if entry.get("closed") is None:
            entry["closed"] = _closed_reason(project, entry, log, carry_weeks)


def carry_candidates(ledger: Dict[str, Any], run_id: str) -> List[Dict[str, Any]]:
    """Open entries first found before `run_id`, each with `weeks_carried` added, in key order."""
    found = []
    for key in sorted(ledger["ideas"]):
        entry = ledger["ideas"][key]
        if entry.get("closed") is None and entry.get("first_run", "") < run_id:
            found.append(dict(entry, weeks_carried=len(entry.get("shown", []))))
    return found


def record_briefs(
    ledger: Dict[str, Any],
    run_id: str,
    briefs: List[Dict[str, Any]],
    reels: Dict[str, Dict[str, Any]],
) -> None:
    """Add this run's shown pairs; new ideas also get an entry with a reel snapshot."""
    for brief in briefs:
        kind = brief.get("kind")
        shortcode = brief.get("shortCode")
        if kind not in ("new", "carried") or not shortcode:
            continue
        pair = {"run_id": run_id, "brief_id": brief["brief_id"]}
        entry = ledger["ideas"].get(shortcode)
        if entry is None:
            reel = reels.get(shortcode, {})
            entry = {
                "idea_title": brief.get("idea_title"),
                "brief_title": brief.get("brief_title"),
                "first_run": run_id,
                "shown": [],
                "analysis_path": brief.get("analysis_path"),
                "frames_dir": brief.get("frames_dir"),
                "reel": {key: reel.get(key) for key in REEL_SNAPSHOT_KEYS},
                "closed": None,
            }
            ledger["ideas"][shortcode] = entry
        entry["shown"].append(pair)
```

  Check for an import cycle: `lib/history.py` imports `agents` and `store`; `ideas` imports `agents`, `history`, and `store`. Nothing imports `ideas` yet.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_ideas -v` → PASS, then the whole suite → OK.
- [ ] **Step 5: Commit** "Add the ideas ledger".

---

### Task 4: Topic-first `idea_title` in the analysis

**Files:**
- Modify: `skills/contentos/scripts/schemas/analysis.schema.json` (optional property `idea_title`, type string)
- Modify: `skills/contentos/scripts/lib/director.py` (`coerce_analysis` near line 368, `build_director_prompt` field guidance if it lists fields)
- Modify: `agents/content-director.md` (after the `brief_title` bullet near line 70)
- Test: `tests/test_director.py`

**Interfaces:**
- Produces: every coerced analysis has `idea_title: str`, never empty.

- [ ] **Step 1: Failing tests** (append to `tests/test_director.py`, which already has `_valid_analysis_raw()`). Also add `"idea_title"` to `_OPTIONAL_ANALYSIS_PROPERTIES` (line 288), so `SchemaShapeTests` accepts it as an optional root property:

```python
class IdeaTitleTests(NoNetworkTestCase):
    def test_idea_title_kept_when_given(self) -> None:
        raw = dict(_valid_analysis_raw(), idea_title="  Claude can now design your slides  ")
        self.assertEqual(director.coerce_analysis(raw)["idea_title"], "Claude can now design your slides")

    def test_idea_title_falls_back_to_brief_title(self) -> None:
        raw = dict(_valid_analysis_raw(), brief_title="Tool claim, 3 steps")
        raw.pop("idea_title", None)
        coerced = director.coerce_analysis(raw)
        self.assertEqual(coerced["idea_title"], "Tool claim, 3 steps")
        self.assertEqual(director.validate_analysis(coerced), [])
        blank = director.coerce_analysis(dict(raw, idea_title="   "))
        self.assertEqual(blank["idea_title"], "Tool claim, 3 steps")
```

- [ ] **Step 2: Run and watch them fail** (`KeyError: 'idea_title'`).
- [ ] **Step 3: Implement.** Schema: add `"idea_title": {"type": "string"}` to `properties` (not to `required`). `coerce_analysis`: after `brief_title` is built, set `result["idea_title"] = (obj.get("idea_title") or "").strip() or result["brief_title"]` if `obj.get("idea_title")` is a string; otherwise use `result["brief_title"]`. Update its docstring count of properties and add one bullet about the fallback. `agents/content-director.md`: add a bullet under `brief_title`:

```markdown
- `idea_title`: the creator's version as a topic line of 12 words or fewer.
  Name the subject of their reel, not the source format. Example: "Claude can
  now turn a doc into slides", not "Tool claim with three on-screen steps".
```

  If `build_director_prompt` prints a field list or schema excerpt, it already carries the schema file, so no prompt change is needed. Confirm by grepping the prompt output of `direct-prompt` in `tests/test_director.py` for `idea_title` only if the schema is inlined.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_director tests.test_agent_files -v`, then the suite → OK.
- [ ] **Step 5: Commit** "Add a topic-first idea_title to the analysis".

---

### Task 5: Format fill from the synthesis

**Files:**
- Create: `skills/contentos/scripts/schemas/fill.schema.json`, `fixtures/fill.sample.json`
- Modify: `skills/contentos/scripts/lib/director.py` (`build_synth_prompt` gets `fill_ideas: int = 8`, new `verify_fill`)
- Modify: `skills/contentos/scripts/lib/direct.py` (`run_synth_prompt` passes `cfg["fill_ideas"]`, `verify_synth` adds fill problems, `_seed_mock_analyses` seeds fill)
- Modify: `agents/content-director.md` (synthesis section: also write `03-fill.json`)
- Test: `tests/test_director.py`, `tests/test_direct_commands.py`

**Interfaces:**
- Produces: `director.FILL_FIXTURE_NAME`-style constant in `direct.py`: `FILL_FIXTURE_NAME = "fill.sample.json"`. `director.verify_fill(run_dir: Path) -> List[str]` ([] when the file is missing or valid). `director.load_fill(run_dir: Path) -> List[Dict[str, Any]]` (the ideas when the file is present and `verify_fill` passes, else []).

- [ ] **Step 1: Schema** `fill.schema.json`. It must pass `SchemaShapeTests`: draft-07 `$schema`, every nested property required, depth 2 or less, no `$ref` or `additionalProperties`. So `specifics` is required and may be `[]`, and its items stay `{"type": "object"}` with no `properties` (that keeps the depth at 2). Add `(director.load_schema("fill"), set())` to the loop in `SchemaShapeTests.test_schemas_are_flat_no_refs_and_list_every_enum`.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["ideas"],
  "properties": {
    "ideas": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["idea_title", "pillar", "format_from", "angle", "why", "specifics"],
        "properties": {
          "idea_title": {"type": "string", "minLength": 1},
          "pillar": {"type": "string", "minLength": 1},
          "format_from": {"type": "array", "items": {"type": "string", "minLength": 1}},
          "angle": {"type": "string", "minLength": 1},
          "why": {"type": "string", "minLength": 1},
          "specifics": {"type": "array", "items": {"type": "object"}}
        }
      }
    }
  }
}
```

  Check `director._validate_node` supports every keyword used here (`minLength`, nested `items`). If `minItems` is unsupported, check a non-empty `format_from` in `verify_fill` instead.

- [ ] **Step 2: Fixture** `fixtures/fill.sample.json`, three ideas on the sample creator's pillars (from `fixtures/setup-answers.sample.json`), borrowing `HAB005` and `DWN006`, the two fixture reels that clear 2x in 14 days:

```json
{
  "ideas": [
    {
      "idea_title": "The two-minute Sunday reset that saves your week",
      "pillar": "Weekly planning resets",
      "format_from": ["DWN006"],
      "angle": "Open on a messy Monday calendar, then show the three reset moves that fixed it.",
      "why": "The before and after hook worked on DWN006 and a weekly reset has a visible payoff.",
      "specifics": []
    },
    {
      "idea_title": "Notion or paper: one week, same plan, which one held",
      "pillar": "Notion and paper systems compared",
      "format_from": ["HAB005"],
      "angle": "Run the same week in both systems and show the day each one broke.",
      "why": "HAB005's challenge format gives a clear verdict the viewer waits for.",
      "specifics": []
    },
    {
      "idea_title": "One focus trick for the day everything goes wrong",
      "pillar": "Focus tricks that survive a bad week",
      "format_from": ["HAB005", "DWN006"],
      "angle": "Show a bad day on screen, then the single trick that saved the afternoon.",
      "why": "Both proof reels land a payoff inside the first beat.",
      "specifics": []
    }
  ]
}
```

- [ ] **Step 3: Failing tests.** In `tests/test_director.py`:

```python
class FillTests(NoNetworkTestCase):
    def _run_dir(self, root: Path, analyses=("HAB005",)) -> Path:
        run_dir = root / "run"
        (run_dir / "03-analyses").mkdir(parents=True)
        for sc in analyses:
            (run_dir / "03-analyses" / f"{sc}.json").write_text("{}", encoding="utf-8")
        return run_dir

    def test_missing_fill_is_fine(self) -> None:
        with temp_project() as root:
            run_dir = self._run_dir(root)
            self.assertEqual(director.verify_fill(run_dir), [])
            self.assertEqual(director.load_fill(run_dir), [])

    def test_fill_must_match_schema_and_borrow_analyzed_reels(self) -> None:
        with temp_project() as root:
            run_dir = self._run_dir(root)
            idea = {"idea_title": "T", "pillar": "P", "format_from": ["NOPE"], "angle": "A", "why": "W", "specifics": []}
            (run_dir / "03-fill.json").write_text(json.dumps({"ideas": [idea]}), encoding="utf-8")
            problems = director.verify_fill(run_dir)
            self.assertTrue(any("NOPE" in p for p in problems), problems)
            self.assertEqual(director.load_fill(run_dir), [])
            (run_dir / "03-fill.json").write_text(json.dumps({"ideas": [{"idea_title": "T"}]}), encoding="utf-8")
            self.assertTrue(director.verify_fill(run_dir))

    def test_valid_fill_loads(self) -> None:
        with temp_project() as root:
            run_dir = self._run_dir(root)
            idea = {"idea_title": "T", "pillar": "P", "format_from": ["HAB005"], "angle": "A", "why": "W", "specifics": []}
            (run_dir / "03-fill.json").write_text(json.dumps({"ideas": [idea]}), encoding="utf-8")
            self.assertEqual(director.verify_fill(run_dir), [])
            self.assertEqual(director.load_fill(run_dir), [idea])

    def test_synth_prompt_asks_for_fill(self) -> None:
        with temp_project() as root:
            run_dir = self._run_dir(root)
            creator = root / "creator.md"
            creator.write_text("# Creator\n", encoding="utf-8")
            prompt = director.build_synth_prompt(run_dir, REFERENCES_DIR, creator, fill_ideas=5)
            self.assertIn(str((run_dir / "03-fill.json").resolve()), prompt)
            self.assertIn("at most 5", prompt)
            none = director.build_synth_prompt(run_dir, REFERENCES_DIR, creator, fill_ideas=0)
            self.assertNotIn("03-fill.json", none)
```

  (`REFERENCES_DIR` is already defined at the top of `tests/test_director.py`.)

  In `tests/test_direct_commands.py`, add a test that `rank --mock` on a `PRE_WEEKLY_CONFIG` project seeds `03-fill.json` from the fixture, and that `verify --stage synth` exits 7 after the file is overwritten with an idea whose `format_from` is `["NOPE"]`.

- [ ] **Step 4: Run and watch them fail.**
- [ ] **Step 5: Implement.**
  - `director.verify_fill(run_dir)`: when `03-fill.json` is absent, return `[]`. Otherwise parse it (a parse error is one problem), run `validate_against(load_schema("fill"), doc)`, and for each idea whose `format_from` is empty or names a shortCode without `03-analyses/<sc>.json`, add `f"fill idea {i + 1}: format_from {sc} has no analysis in this run"` (or "is empty").
  - `director.load_fill(run_dir)`: `[]` unless the file exists and `verify_fill` returns `[]`; then `doc["ideas"]`.
  - `build_synth_prompt(..., fill_ideas: int = 8)`: when `fill_ideas > 0`, add a `## Fill ideas` section before `## Rules` that names the path `03-fill.json` (absolute), says "at most {fill_ideas}", lists the six required keys (`specifics` may be `[]`) with one line each (copy the wording from the spec's "Format fill" bullet), says `pillar` is copied as written from `## Pillars` in the creator profile, and says a fill idea must not repeat a topic the analyses already cover. In `## Output`, add "Then write the fill ideas to exactly this path: <path>" when `fill_ideas > 0`.
  - `direct.run_synth_prompt` passes `fill_ideas=cfg["fill_ideas"]`. Check its current signature; if it has no `cfg`, load it with `store.load_config(project)` as the other prompt builders do.
  - `direct.verify_synth`: `problems = director.verify_patterns(patterns_path) + director.verify_fill(run_dir)`.
  - `direct._seed_mock_analyses`: after seeding patterns, copy `fixtures/fill.sample.json` to `03-fill.json` when it is missing and the run has at least one analysis for a shortCode in the fixture's `format_from` lists. Skip it otherwise, so a mock week with no new outliers has no fill (spec).
  - `agents/content-director.md`: in the synthesis section, one paragraph saying that when the prompt has `## Fill ideas`, it also writes `03-fill.json` exactly as described there.
- [ ] **Step 6: Run** the two modules, then the suite → OK.
- [ ] **Step 7: Commit** "Write format fill ideas from the synthesis and verify them".

---

### Task 6: `rank_briefs` merges new, carried, and fill

**Files:**
- Modify: `skills/contentos/scripts/lib/director.py` (`_candidate_sort_key`, `rank_briefs`)
- Test: `tests/test_director.py`

**Interfaces:**
- Consumes: carried entries from `ideas.carry_candidates` (Task 3) with the analysis loaded by the caller; fill ideas from `director.load_fill` (Task 5); `analysis["idea_title"]` (Task 4).
- Produces:

```python
def rank_briefs(
    analyses: Dict[str, Dict[str, Any]],
    reels: List[Dict[str, Any]],
    n: int,
    run_dir: Path,
    max_format_briefs: int = 2,
    carried: Optional[List[Dict[str, Any]]] = None,
    fill: Optional[List[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]
```

  `carried` items: `{"reel": <snapshot>, "analysis": <coerced>, "weeks_carried": int, "first_run": str, "analysis_path": str, "frames_dir": str}`. Every brief gains `kind`, `weeks_carried`, `idea_title`, `days_old`, `outlier_ratio`, and `first_run` (carried only). Fill briefs have `brief_score`, `viral_proof`, `outlier_ratio`, `score_scalable`, `score_convertible`, and `score_fit` set to `None`.

- [ ] **Step 1: Failing tests** (append to `tests/test_director.py`; it already has `_scored_reel(shortcode, **extra)` and `_valid_analysis_raw()`, whose scores make `brief_score = 5.15 + 0.35 * viral_proof`). Add `from datetime import datetime, timezone` to the imports.

```python
WEEKLY_NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _pair(sc: str, viral: float, ts: str = "2026-09-12T00:00:00+00:00"):
    reel = _scored_reel(sc, viral_proof=viral, timestamp=ts)
    analysis = director.coerce_analysis(
        dict(_valid_analysis_raw(), brief_title=f"F {sc}", idea_title=f"I {sc}")
    )
    return reel, analysis


def _carried(sc: str, viral: float, weeks: int) -> Dict[str, Any]:
    reel, analysis = _pair(sc, viral, ts="2026-09-02T00:00:00+00:00")
    return {
        "reel": reel, "analysis": analysis, "weeks_carried": weeks, "first_run": "R0",
        "analysis_path": f"/old/03-analyses/{sc}.json", "frames_dir": f"/old/frames/{sc}",
    }


class WeeklyRankTests(NoNetworkTestCase):
    def test_strong_carried_idea_beats_weak_new_one(self) -> None:
        reel, analysis = _pair("NEW1", 3.0)  # 6.2
        with temp_project() as root:
            briefs = director.rank_briefs(
                {"NEW1": analysis}, [reel], 20, root,
                carried=[_carried("OLD1", 10.0, 1)], now=WEEKLY_NOW,  # 8.65 - 1 = 7.65
            )
        self.assertEqual([b["shortCode"] for b in briefs], ["OLD1", "NEW1"])
        old = briefs[0]
        self.assertEqual((old["kind"], old["weeks_carried"], old["first_run"]), ("carried", 1, "R0"))
        self.assertEqual(old["brief_score"], 7.65)
        self.assertEqual(old["analysis_path"], "/old/03-analyses/OLD1.json")
        self.assertEqual(old["frames_dir"], "/old/frames/OLD1")
        self.assertEqual(old["days_old"], 14)

    def test_weeks_carried_can_sink_an_idea(self) -> None:
        reel, analysis = _pair("NEW1", 3.0)  # 6.2
        with temp_project() as root:
            briefs = director.rank_briefs(
                {"NEW1": analysis}, [reel], 20, root,
                carried=[_carried("OLD1", 4.0, 2)], now=WEEKLY_NOW,  # 6.55 - 2 = 4.55
            )
        self.assertEqual([b["shortCode"] for b in briefs], ["NEW1", "OLD1"])
        self.assertEqual(briefs[1]["brief_score"], 4.55)

    def test_fill_only_takes_leftover_slots_below_real_ideas(self) -> None:
        reel, analysis = _pair("NEW1", 3.0)
        fill = [
            {"idea_title": "Fill A", "pillar": "P", "format_from": ["NEW1"], "angle": "Angle A",
             "why": "W", "specifics": []},
            {"idea_title": "Fill B", "pillar": "P", "format_from": ["MISSING"], "angle": "B",
             "why": "W", "specifics": []},
            {"idea_title": "Fill C", "pillar": "P", "format_from": ["NEW1"], "angle": "Angle C",
             "why": "W", "specifics": []},
        ]
        with temp_project() as root:
            briefs = director.rank_briefs({"NEW1": analysis}, [reel], 2, root, fill=fill, now=WEEKLY_NOW)
        self.assertEqual([b["kind"] for b in briefs], ["new", "fill"])
        filled = briefs[1]
        self.assertEqual((filled["idea_title"], filled["adaptation"]), ("Fill A", "Angle A"))
        for key in ("brief_score", "viral_proof", "score_scalable", "score_convertible",
                    "score_fit", "days_old", "outlier_ratio"):
            self.assertIsNone(filled[key], key)
        self.assertEqual((filled["specifics"], filled["steps"]), ([], []))
        self.assertEqual(filled["format"], analysis["format"])
        self.assertEqual(filled["brief_title"], "F NEW1")
        self.assertEqual([b["brief_id"] for b in briefs], ["B01", "B02"])

    def test_new_brief_fields(self) -> None:
        reel, analysis = _pair("NEW1", 3.0)
        with temp_project() as root:
            brief = director.rank_briefs({"NEW1": analysis}, [reel], 5, root, now=WEEKLY_NOW)[0]
        self.assertEqual(
            (brief["kind"], brief["weeks_carried"], brief["idea_title"], brief["days_old"],
             brief["outlier_ratio"]),
            ("new", 0, "I NEW1", 4, 4.0),
        )
        self.assertNotIn("first_run", brief)
```

- [ ] **Step 2: Run and watch them fail** (`TypeError: unexpected keyword argument 'carried'`).
- [ ] **Step 3: Implement.**
  - Candidates become `(reel, analysis, meta)` triples. `meta` for new: `{"kind": "new", "weeks_carried": 0, "analysis_path": str((run_dir / "03-analyses" / f"{sc}.json").resolve()), "frames_dir": str((run_dir / "frames" / sc).resolve())}`. For carried: `{"kind": "carried", "weeks_carried": item["weeks_carried"], "first_run": item["first_run"], "analysis_path": item["analysis_path"], "frames_dir": item["frames_dir"]}`.
  - `_effective_score(reel, analysis, meta) = round(max(0.0, brief_score(analysis, reel) - 1.0 * meta["weeks_carried"]), 2)`. `_candidate_sort_key` takes the triple and uses the effective score, then `outlier_ratio`, then `shortCode`. Update its docstring.
  - The `max_format_briefs` walk and the backfill of skipped format briefs run unchanged over the merged triples.
  - After cutting to `n`, if `len(taken) < n`, walk `fill` in order: skip an idea whose `format_from[0]` is not in `analyses` or has no reel in `reels`; build a fill brief from that proof reel and analysis; stop at `n`.
  - One `_brief_dict(index, reel, analysis, meta, now)` builds every brief so the three kinds share the 0.3.0 keys. `brief_score` is the effective score (None for fill). `idea_title` is `analysis["idea_title"]` for new and carried and the fill title for fill. `days_old` is `(now - instagram.parse_ts(reel["timestamp"])).days` when `now` and a timestamp exist, else `None` (always `None` for fill). For fill, also override `adaptation`, set `specifics` to the fill `specifics` (copied) or `[]`, set `steps` to `[]`, and set `viral_proof`, `outlier_ratio`, and the three director scores to `None`. New and carried briefs carry `"outlier_ratio": reel.get("outlier_ratio")`.
  - Rewrite the `rank_briefs` docstring: add the carried and fill rules and the new keys, and point to the spec's "0.4.0 changes".
- [ ] **Step 4: Run** `python3 -m unittest tests.test_director -v`, then the suite → OK. Older rank tests must still pass unchanged, since `carried` and `fill` default to none and every 0.3.0 key is still present.
- [ ] **Step 5: Commit** "Rank carried ideas and format fill alongside new ideas".

---

### Task 7: Wire the ledger into `rank`

**Files:**
- Modify: `skills/contentos/scripts/lib/direct.py` (`run_rank`)
- Test: `tests/test_direct_commands.py`

**Interfaces:**
- Consumes: Tasks 3, 5, and 6. `research.MOCK_NOW` for mock runs.
- Produces: `rank` writes `.contentos/ideas.json`. The summary gains `"new"`, `"carried"`, and `"fill"` counts.

- [ ] **Step 1: Failing tests** in `tests/test_direct_commands.py` (use the module's existing mock project helpers):
  1. `test_rank_records_ideas_in_the_ledger`: on a `PRE_WEEKLY_CONFIG` mock project, `research --mock --yes` then `rank --mock`. `ideas.json` holds one entry per `new` brief, each `shown` once, and `rank` run twice leaves every entry with exactly one `shown` pair.
  2. `test_second_run_carries_unscripted_ideas`: after week 1, write `04-scripts/B01.r0.md` in week 1's run, then `research --mock --yes` again (`already_briefed` empties the selection) and `rank --run latest`. Expect exit 0, every brief `kind == "carried"` with `weeks_carried == 1`, week 1's B01 source absent, and the carried briefs' `analysis_path` pointing into week 1's run folder.
  3. `test_rank_fails_only_when_nothing_to_rank`: a fresh project with an empty selection and no ledger → exit 2 with the existing message.
  4. `test_mark_skipped_closes_an_idea`: week 1, then `mark --run <week1> --brief B02 --state skipped`, then week 2. B02's source is not carried and the ledger shows `closed: "skipped"`.

- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Implement** in `run_rank`, after `_collect_analyses`:

```python
    ledger = ideas.load_ledger(project)
    ideas.forget_run(ledger, run_dir.name)
    ideas.close_entries(project, ledger, cfg["carry_weeks"])
    carried = [] if cfg["carry_weeks"] == 0 else _load_carried(ledger, run_dir.name, log)
    fill = director.load_fill(run_dir) if analyses and cfg["fill_ideas"] > 0 else []
    if not analyses and not carried and not fill:
        raise DirectError(...)  # the existing message and EXIT_USAGE
    briefs = director.rank_briefs(
        analyses, selected, cfg["briefs"], run_dir,
        max_format_briefs=cfg["max_format_briefs"], carried=carried, fill=fill,
        now=_rank_now(run_dir),
    )
    ...  # write 03-briefs.json and briefs.md as before
    ideas.record_briefs(ledger, run_dir.name, briefs, {r["shortCode"]: r for r in selected})
    ideas.save_ledger(project, ledger)
```

  - `_load_carried(ledger, run_id, log)`: for each `ideas.carry_candidates(ledger, run_id)` entry, skip (with one `log` line) when its shortCode is already a new analysis in this run, or when `_read_analysis(Path(entry["analysis_path"]))` fails or the coerced analysis fails `validate_analysis`. Return the `carried` items shape from Task 6.
  - `_rank_now(run_dir)`: `research.MOCK_NOW` when `run.json` `mode` is `"mock"`, else `datetime.fromisoformat(run.json["created_at"])`, else `datetime.now(timezone.utc)`. Add `from lib import ideas, research` (check for no import cycle: `research` does not import `direct`).
  - Move the existing "no valid analysis" raise below the carry and fill computation, as shown, and reword the docstring: it fails only when there is nothing at all to rank.
  - `03-briefs.json` gains `"counts": {"new": ..., "carried": ..., "fill": ...}`. The printed summary adds the same three keys.
- [ ] **Step 4: Run** the module, then the suite → OK.
- [ ] **Step 5: Commit** "Carry unpicked ideas across runs through the ledger".

---

### Task 8: The topic-first digest in `briefs.md`

**Files:**
- Modify: `skills/contentos/scripts/lib/director.py` (`render_briefs_md`)
- Test: `tests/test_director.py` (and update any existing `briefs.md` assertions that expected `# Briefs` as line 1 or `## B01: <brief_title>`)

- [ ] **Step 1: Failing test:**

```python
class DigestTests(NoNetworkTestCase):
    def test_digest_lines_per_kind(self) -> None:
        base = {
            "source_url": "https://x", "source_kind": "niche", "format": "screen_demo",
            "hook_type": "bold_claim", "emotion_lead": "curiosity", "brief_score": 7.5,
            "viral_proof": 6.0, "score_convertible": 7, "score_scalable": 6, "score_fit": 8,
            "risk_flags": ["none"], "confidence": "high", "transferable_mechanism": "M",
            "why_it_worked": "W.", "adaptation": "A", "avoid": "V", "specifics": [], "steps": [],
            "frames_dir": "/f", "brief_title": "Tool claim, 3 steps", "outlier_ratio": 52.97,
        }
        briefs = [
            dict(base, brief_id="B01", kind="new", weeks_carried=0, idea_title="New idea",
                 ownerUsername="mavgpt", days_old=4),
            dict(base, brief_id="B02", kind="carried", weeks_carried=1, idea_title="Old idea",
                 ownerUsername="raycfu", days_old=11, first_run="20260912-090000"),
            dict(base, brief_id="B03", kind="fill", weeks_carried=0, idea_title="Fill idea",
                 ownerUsername="mavgpt", days_old=None, brief_score=None, viral_proof=None,
                 score_convertible=None, score_scalable=None, score_fit=None),
        ]
        text = director.render_briefs_md(briefs)
        lines = text.splitlines()
        self.assertEqual(lines[0], "# This week's ideas")
        self.assertIn("1. B01 · New · New idea. @mavgpt, 52.97x their usual, 4 days old.", lines)
        self.assertIn("2. B02 · Carried over, week 2 · Old idea. @raycfu, 52.97x their usual, 11 days old.", lines)
        self.assertIn("3. B03 · Format fill, less proven · Fill idea. Borrows the bold_claim hook from @mavgpt.", lines)
        self.assertIn("# Briefs", lines)
        self.assertIn("## B02: Old idea", lines)
        self.assertIn("- Kind: Carried over, week 2 (first shown in 20260912-090000).", lines)
        self.assertIn("- Source format: Tool claim, 3 steps", lines)
        self.assertNotIn("—", text)
```

- [ ] **Step 2: Run and watch it fail.**
- [ ] **Step 3: Implement.** A `_kind_label(brief)` helper returns `New`, `Carried over, week {weeks_carried + 1}`, or `Format fill, less proven`. The digest line is `f"{i}. {id} · {label} · {idea_title}. {proof}"`, where proof is `@{owner}, {ratio:.2f}x their usual, {days} days old.` (omit the days clause when `days_old` is None) or, for fill, `Borrows the {hook_type} hook from @{owner}.`. After the numbered list, add a blank line and `# Briefs`, then the existing sections with the heading `## {id}: {idea_title}` (falling back to `brief_title`), then `- Kind: <label>` (plus ` (first shown in <first_run>)` for carried), and `- Source format: <brief_title>` before the existing lines. Print `Scores:` only when `brief_score` is not None, and fix its missing `- ` bullet while there. Briefs without a `kind` (0.3.0 files) render as `New`. Update the docstring.
- [ ] **Step 4: Run** the module, then the suite; update older `render_briefs_md` assertions that pinned line 1 or the old heading → OK.
- [ ] **Step 5: Commit** "Open briefs.md with a ranked, topic-first ideas list".

---

### Task 9: Skill, writer, and picking

**Files:**
- Modify: `skills/contentos/SKILL.md` (run flow steps 4 to 7, "Choosing briefs", the `--auto` line near line 166, Loop 2)
- Modify: `agents/script-writer.md` (fill briefs)
- Test: `tests/test_skill_md.py`, `tests/test_agent_files.py`

- [ ] **Step 1: Failing tests** in `tests/test_skill_md.py` (it defines `SKILL_MD`):

```python
    def test_weekly_picking_and_auto_scripts(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        for needle in ("`Top 3`", "`Top 5`", "`All <n>`", "auto_scripts", "03-fill.json",
                       "no new outliers"):
            self.assertIn(needle, text)
        self.assertNotIn("take the top `briefs`", text)
```

  In `tests/test_agent_files.py`, assert `WRITER_AGENT.read_text(encoding="utf-8")` contains the literal `` `kind` is `fill` ``.

- [ ] **Step 2: Run and watch them fail.**
- [ ] **Step 3: Edit the text** (plain language, short sentences, no em dashes):
  - Run flow: before step 4, "If research selected no reels (no new outliers this week), skip the director loop and the synthesis and go straight to `rank`. It lists last weeks' unpicked ideas."
  - Loop 2: the synthesis also writes `03-fill.json` (format ideas on the creator's pillars), checked by the same `verify --stage synth`.
  - "Choosing briefs": show the numbered list from the top of `briefs.md` (kind, idea title, proof). Explain the three kinds in one sentence each. Ask with AskUserQuestion: `Top 3`, `Top 5`, `All <n>`, with ids such as `B02 B07` typed through Other. With `--auto`, take the top `auto_scripts` from `.contentos/config.json` (default 3). Replace the paragraph about raising `briefs` with the new default of 20. Keep the format-account paragraph.
  - Line 166: "`--auto` skips the brief question and takes the top `auto_scripts` briefs (default 3)."
  - `agents/script-writer.md`, after "Execute the brief": "When the brief's `kind` is `fill`, the topic is the brief's `idea_title` and `adaptation`. The analysis supplies the format and the hook only. Do not borrow its subject, specifics, or steps."
- [ ] **Step 4: Run** both modules, then the suite → OK.
- [ ] **Step 5: Commit** "Teach the skill and the writer the weekly list".

---

### Task 10: Weekly end-to-end mock

**Files:**
- Test: `tests/test_e2e_mock.py`

- [ ] **Step 1: Write the test** (new class, uses the new defaults, no `PRE_WEEKLY_CONFIG`):

```python
class WeeklyMockTests(NoNetworkTestCase):
    def test_two_weeks_carry_the_unscripted_idea(self) -> None:
        with temp_project() as project, mock.patch.dict(os.environ, _NO_GLOBAL_ENV):
            _require_ok(*_main(["setup", "--project", str(project), "--answers-file",
                                str(SETUP_ANSWERS_FIXTURE)]), "setup")
            _require_ok(*_main(["research", "--project", str(project), "--mock", "--yes"]), "week 1 research")
            code, out, err = _main(["rank", "--project", str(project), "--run", "latest", "--mock"])
            _require_ok(code, out, err, "week 1 rank")
            week1 = json.loads(out)
            self.assertEqual((week1["new"], week1["carried"]), (2, 0))
            self.assertEqual(week1["fill"], 3)
            _require_ok(*_main(["verify", "--project", str(project), "--run", "latest", "--stage", "synth"]),
                        "week 1 verify synth")
            week1_dir = store.resolve_run(project, "latest")
            script = week1_dir / "04-scripts" / "B01.r0.md"
            script.parent.mkdir(parents=True)
            shutil.copyfile(SCRIPT_FIXTURE, script)
            text = (week1_dir / "briefs.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# This week's ideas"))
            self.assertIn("Format fill, less proven", text)

            _require_ok(*_main(["research", "--project", str(project), "--mock", "--yes"]), "week 2 research")
            code, out, err = _main(["rank", "--project", str(project), "--run", "latest", "--mock"])
            _require_ok(code, out, err, "week 2 rank")
            week2 = json.loads(out)
            self.assertEqual((week2["new"], week2["carried"], week2["fill"]), (0, 1, 0))
            week2_dir = store.resolve_run(project, "latest")
            self.assertNotEqual(week2_dir, week1_dir)
            self.assertIn("Carried over, week 2", (week2_dir / "briefs.md").read_text(encoding="utf-8"))
```

  If `_require_ok` takes `(code, out, err, label)`, the `*_main(...)` splat plus the label works as written. Otherwise unpack first. Import `store` from `lib` if the module does not already.

- [ ] **Step 2: Run it.** It should pass if Tasks 1 to 9 are correct. If it fails, fix the code, not the test, unless the test contradicts the spec.
- [ ] **Step 3: Run the whole suite, then commit** "Add a two-week weekly mock test".

---

### Task 11: Docs, version, and the real-run check

**Files:**
- Modify: `README.md` (use case, cost, and "What a run produces"), `CHANGELOG.md` (`## [0.4.0] - 2026-09-19`), `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `tests/test_manifests.py` (`VERSION = "0.4.0"`), `skills/contentos/references/stages.md` only if it restates defaults

- [ ] **Step 1:** Bump the version in `tests/test_manifests.py` first, run it, and watch it fail. Then bump both manifests and run it again → PASS.
- [ ] **Step 2: CHANGELOG** `[0.4.0]` Added: the weekly ideas list (up to 20, ranked, tagged), the ledger and carry-over, format fill, `idea_title`, and `auto_scripts`. Changed: the 14-day window, the 2x floor, the soft cap, and the `briefs.md` layout. Upgrade note: "Projects set up before 0.4.0 pin `lookback_days: 90` and `briefs: 5` in `.contentos/config.json`. Change them to 14 and 20 for the weekly list."
- [ ] **Step 3: README.** Describe the weekly use (run once a week on the same accounts, pick from up to 20 ideas, unpicked ones return for two more weeks). Keep the existing cost math and add that weekly cost equals one run.
- [ ] **Step 4: Real-run check (no Apify spend).** Copy the live run into a scratch project and re-rank it there. Never re-rank inside `/Users/lesliezhang/git/ContentOS/.contentos`.

```bash
S="$(mktemp -d)"; mkdir -p "$S/.contentos/runs"
cp /Users/lesliezhang/git/ContentOS/.contentos/{creator.md,config.json} "$S/.contentos/"
cp -R /Users/lesliezhang/git/ContentOS/.contentos/runs/20260918-145826 "$S/.contentos/runs/"
python3 - "$S/.contentos/config.json" <<'EOF'
import json, sys
p = sys.argv[1]; c = json.load(open(p)); c.update(lookback_days=14, briefs=20); json.dump(c, open(p, "w"), indent=2)
EOF
python3 skills/contentos/scripts/contentos.py rank --project "$S" --run 20260918-145826
head -30 "$S/.contentos/runs/20260918-145826/briefs.md"
```

  Expect 20 briefs (this run's 20 analyses) where there were 5. Titles fall back to `brief_title`, because these analyses predate `idea_title`. Record the counts in the final report.
- [ ] **Step 5: Full suite, under `/usr/bin/python3` too if it is 3.9.**

```bash
python3 -m unittest discover -s tests
/usr/bin/python3 --version && /usr/bin/python3 -m unittest discover -s tests
```

- [ ] **Step 6: Commit** "Release 0.4.0: weekly ideas".
