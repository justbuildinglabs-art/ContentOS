"""Tests for `lib/history.py`: the weekly log, run history, and briefed sources."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from tests.helpers import NoNetworkTestCase, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import agents, history, store  # noqa: E402


def _run(project: Path, run_id: str, shortcodes: List[str], cost: float = 0.42) -> Path:
    """A run directory with `run.json` and a `03-briefs.json` ranking `shortcodes`."""
    config_dir = store.contentos_dir(project)
    config_dir.mkdir(parents=True, exist_ok=True)
    if not (config_dir / "config.json").exists():
        (config_dir / "config.json").write_text(json.dumps({"competitors": ["a"]}), encoding="utf-8")
    run_dir = store.run_dir(project, run_id)
    run_dir.mkdir(parents=True)
    store.write_json_atomic(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "created_at": f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}T10:00:00+00:00",
            "mode": "live",
            "stages": {"research": {"status": "ok", "reels_total": 145, "selected": 20}},
            "costs": {"apify": {"estimate_usd": cost}},
        },
    )
    briefs = [
        {"brief_id": f"B{index:02d}", "shortCode": sc, "brief_title": f"Brief {sc}"}
        for index, sc in enumerate(shortcodes, start=1)
    ]
    store.write_json_atomic(run_dir / "03-briefs.json", {"briefs": briefs})
    return run_dir


class MarkTests(NoNetworkTestCase):
    def test_mark_records_state_url_and_time(self) -> None:
        with temp_project() as project:
            _run(project, "20260918-145826", ["AAA"])
            now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
            entry = history.mark(
                project, "20260918-145826", "B01", "posted",
                url="https://www.instagram.com/p/XYZ/", now=now,
            )
            log = history.load_log(project)
        self.assertEqual(entry["state"], "posted")
        key = "20260918-145826/B01"
        self.assertEqual(log["briefs"][key]["url"], "https://www.instagram.com/p/XYZ/")
        self.assertEqual(log["briefs"][key]["updated_at"], "2026-09-20T12:00:00+00:00")

    def test_mark_rejects_an_unknown_state_or_brief(self) -> None:
        with temp_project() as project:
            _run(project, "20260918-145826", ["AAA"])
            with self.assertRaises(ValueError):
                history.mark(project, "20260918-145826", "B01", "published")
            with self.assertRaises(ValueError):
                history.mark(project, "20260918-145826", "B09", "filmed")
            with self.assertRaises(store.RunNotFound):
                history.mark(project, "20990101-000000", "B01", "filmed")

    def test_a_later_mark_replaces_the_earlier_one(self) -> None:
        with temp_project() as project:
            _run(project, "20260918-145826", ["AAA"])
            history.mark(project, "20260918-145826", "B01", "filmed")
            history.mark(project, "20260918-145826", "B01", "posted")
            log = history.load_log(project)
        self.assertEqual(log["briefs"]["20260918-145826/B01"]["state"], "posted")

    def test_load_log_is_empty_without_a_file(self) -> None:
        with temp_project() as project:
            self.assertEqual(history.load_log(project), {"briefs": {}})


class HistoryTests(NoNetworkTestCase):
    def test_history_has_one_row_per_run_newest_first(self) -> None:
        with temp_project() as project:
            _run(project, "20260911-090000", ["OLD1"], cost=0.40)
            new_run = _run(project, "20260918-145826", ["AAA", "BBB"], cost=0.42)
            path = agents.script_path(new_run, "B01", 0)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\nbrief_id: B01\n---\n\n## Hook\n\nHi.\n", encoding="utf-8")
            qa = agents.qa_path(new_run, "B01", 0)
            qa.parent.mkdir(parents=True, exist_ok=True)
            qa.write_text(json.dumps({"verdict": "pass"}), encoding="utf-8")
            history.mark(project, "20260918-145826", "B01", "posted", url="https://x.test/p/1")
            text = history.render_history(project)
        rows = [line for line in text.splitlines() if line.startswith("| 2026")]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("| 20260918-145826 "))
        self.assertIn("$0.42", rows[0])
        cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
        header = [cell.strip() for cell in text.splitlines()[text.splitlines().index(
            next(l for l in text.splitlines() if l.startswith("| run ")))].strip("|").split("|")]
        row = dict(zip(header, cells))
        self.assertEqual(row["briefs"], "2")
        self.assertEqual(row["passed"], "1")
        self.assertEqual(row["posted"], "1")
        self.assertNotIn("—", text)

    def test_history_with_no_runs(self) -> None:
        with temp_project() as project:
            store.contentos_dir(project).mkdir(parents=True)
            self.assertIn("No runs yet.", history.render_history(project))


class BriefedShortcodesTests(NoNetworkTestCase):
    def test_collects_every_briefed_source_except_the_named_run(self) -> None:
        with temp_project() as project:
            _run(project, "20260911-090000", ["OLD1", "OLD2"])
            _run(project, "20260918-145826", ["NEW1"])
            everything = history.briefed_shortcodes(project)
            earlier = history.briefed_shortcodes(project, exclude_run_id="20260918-145826")
        self.assertEqual(everything, {"OLD1", "OLD2", "NEW1"})
        self.assertEqual(earlier, {"OLD1", "OLD2"})

    def test_a_run_without_briefs_is_skipped(self) -> None:
        with temp_project() as project:
            run_dir = _run(project, "20260911-090000", ["OLD1"])
            (run_dir / "03-briefs.json").unlink()
            self.assertEqual(history.briefed_shortcodes(project), set())


if __name__ == "__main__":
    unittest.main()
