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
