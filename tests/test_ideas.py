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
    """A run folder `history.list_runs` counts: it has a `run.json`."""
    run_dir = store.run_dir(project, run_id)
    run_dir.mkdir(parents=True)
    store.write_json_atomic(run_dir / "run.json", {"run_id": run_id})
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
            r1, r2, r3, r4 = "20260901-090000", "20260908-090000", "20260915-090000", "20260922-090000"
            for run_id in (r1, r2, r3, r4):
                _run(project, run_id)
            ledger = {"version": 1, "ideas": {}}
            ideas.record_briefs(
                ledger, r1,
                [_brief("B01", "SCR"), _brief("B02", "SKP"), _brief("B03", "OLD")],
                {sc: _reel(sc) for sc in ("SCR", "SKP", "OLD")},
            )
            # OLD is first shown in r1 and never again (r2 and r3 cut it
            # from a full list); OPN is first shown in r2.
            ideas.record_briefs(ledger, r2, [_brief("B01", "OPN")], {"OPN": _reel("OPN")})
            script = store.run_dir(project, r1) / "04-scripts" / "B01.r0.md"
            script.parent.mkdir(parents=True)
            script.write_text("# script\n", encoding="utf-8")
            (store.contentos_dir(project) / "log.json").write_text(
                json.dumps({"briefs": {f"{r1}/B02": {"state": "skipped"}}}), encoding="utf-8"
            )

            ideas.close_entries(project, ledger, r4, carry_weeks=2)

            # SCR and SKP are old enough to expire too; a script or a
            # mark takes precedence.
            closed = {sc: entry["closed"] for sc, entry in ledger["ideas"].items()}
            self.assertEqual(closed, {"SCR": "scripted", "SKP": "skipped", "OLD": "expired", "OPN": None})

    def test_an_idea_cut_from_the_list_still_expires(self) -> None:
        with temp_project() as project:
            store.contentos_dir(project).mkdir(parents=True)
            r1, r2, r3 = "20260901-090000", "20260908-090000", "20260915-090000"
            for run_id in (r1, r2, r3):
                _run(project, run_id)
            ledger = {"version": 1, "ideas": {}}
            ideas.record_briefs(ledger, r1, [_brief("B01", "AAA")], {"AAA": _reel("AAA")})
            # r2's list was full of better ideas: AAA was not shown.
            ideas.record_briefs(ledger, r2, [_brief("B01", "BBB")], {"BBB": _reel("BBB")})

            ideas.close_entries(project, ledger, r3, carry_weeks=1)

            self.assertEqual(ledger["ideas"]["AAA"]["shown"], [{"run_id": r1, "brief_id": "B01"}])
            self.assertEqual(ledger["ideas"]["AAA"]["closed"], "expired")
            self.assertIsNone(ledger["ideas"]["BBB"]["closed"])

    def test_expiry_counts_only_runs_between_first_run_and_the_ranked_run(self) -> None:
        with temp_project() as project:
            store.contentos_dir(project).mkdir(parents=True)
            r1, r2, r3, r4 = "20260901-090000", "20260908-090000", "20260915-090000", "20260922-090000"
            for run_id in (r1, r3, r4):
                _run(project, run_id)
            # A folder with no run.json is not a run.
            store.run_dir(project, r2).mkdir(parents=True)
            ledger = {"version": 1, "ideas": {}}
            ideas.record_briefs(ledger, r1, [_brief("B01", "AAA")], {"AAA": _reel("AAA")})

            # Ranking r4: only r3 sits between r1 and r4, so AAA stays open.
            ideas.close_entries(project, ledger, r4, carry_weeks=2)
            self.assertIsNone(ledger["ideas"]["AAA"]["closed"])

            # Re-ranking an older run never counts the runs after it.
            ideas.close_entries(project, ledger, r3, carry_weeks=1)
            self.assertIsNone(ledger["ideas"]["AAA"]["closed"])

    def test_carry_weeks_zero_expires_every_open_idea(self) -> None:
        with temp_project() as project:
            store.contentos_dir(project).mkdir(parents=True)
            r1, r2 = "20260901-090000", "20260908-090000"
            for run_id in (r1, r2):
                _run(project, run_id)
            ledger = {"version": 1, "ideas": {}}
            ideas.record_briefs(ledger, r1, [_brief("B01", "AAA")], {"AAA": _reel("AAA")})

            ideas.close_entries(project, ledger, r2, carry_weeks=0)

            self.assertEqual(ledger["ideas"]["AAA"]["closed"], "expired")

    def test_load_drops_malformed_entries_and_shown_pairs(self) -> None:
        # Final review M2: a hand-edited ledger must never crash rank.
        with temp_project() as project:
            good = {"run_id": "R1", "brief_id": "B02"}
            doc = {"version": 1, "ideas": {
                "AAA": "not an entry",
                "BBB": {"first_run": "R1", "closed": None, "shown": [
                    "junk", {"run_id": 5, "brief_id": "B01"}, {"run_id": "R1"}, good,
                ]},
                "CCC": {"first_run": "R1", "closed": None, "shown": "not a list"},
            }}
            ideas.ledger_path(project).parent.mkdir(parents=True)
            ideas.ledger_path(project).write_text(json.dumps(doc), encoding="utf-8")

            ledger = ideas.load_ledger(project)

            self.assertEqual(set(ledger["ideas"]), {"BBB", "CCC"})
            self.assertEqual(ledger["ideas"]["BBB"]["shown"], [good])
            self.assertEqual(ledger["ideas"]["CCC"]["shown"], [])
            ideas.close_entries(project, ledger, "R2", carry_weeks=2)
            ideas.forget_run(ledger, "R9")
            self.assertEqual(set(ledger["ideas"]), {"BBB"})

    def test_close_entries_tolerates_non_dict_log_entries(self) -> None:
        with temp_project() as project:
            store.contentos_dir(project).mkdir(parents=True)
            ledger = {"version": 1, "ideas": {}}
            ideas.record_briefs(ledger, "R1", [_brief("B01", "AAA"), _brief("B02", "BBB")],
                                {"AAA": _reel("AAA"), "BBB": _reel("BBB")})
            (store.contentos_dir(project) / "log.json").write_text(
                json.dumps({"briefs": {"R1/B01": "skipped", "R1/B02": None}}), encoding="utf-8"
            )

            ideas.close_entries(project, ledger, "R2", carry_weeks=2)

            self.assertEqual({sc: e["closed"] for sc, e in ledger["ideas"].items()}, {"AAA": None, "BBB": None})

    def test_save_then_load_round_trips(self) -> None:
        with temp_project() as project:
            ledger = {"version": 1, "ideas": {}}
            ideas.record_briefs(ledger, "R1", [_brief("B01", "AAA")], {"AAA": _reel("AAA")})
            ideas.save_ledger(project, ledger)
            self.assertEqual(ideas.load_ledger(project), ledger)
