"""Tests for `lib/report_html.py`: the self-contained `report.html`."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.helpers import NoNetworkTestCase, temp_project
from tests.test_report import _CONTEXT_SCRIPT, _build_run_with_every_status, _write_qa

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import agents, report_html, store  # noqa: E402


def _enrich_briefs(run_dir: Path) -> None:
    """Give the fixture briefs the ranking fields a real run carries."""
    doc = store.read_json(run_dir / "03-briefs.json")
    for index, brief in enumerate(doc["briefs"]):
        brief.update(
            {
                "shortCode": f"SC{index}",
                "source_url": f"https://www.instagram.com/p/SC{index}/",
                "ownerUsername": "acme",
                "source_kind": "niche",
                "format": "tutorial",
                "hook_type": "bold_claim",
                "emotion_lead": "awe",
                "brief_score": 8.5 - index * 0.1,
                "viral_proof": 9.0,
                "score_convertible": 9,
                "score_scalable": 8,
                "score_fit": 8,
                "risk_flags": ["brand_ip"],
                "confidence": "high",
                "adaptation": "Swap the subject for the lead follow-up workflow.",
                "avoid": "The same video editing walkthrough.",
                "transferable_mechanism": "State one new job a common tool can do, then prove it on screen.",
                "why_it_worked": "The claim lands in frame one. The screen proves it by second three. Then the keyword.",
                "specifics": [
                    {"kind": "repo", "name": "remotion-dev/remotion", "detail": "React video library",
                     "evidence": "frame 3", "public": True}
                ],
            }
        )
    doc["briefs"][0]["brief_title"] = "Pass brief <script>alert(1)</script>"
    store.write_json_atomic(run_dir / "03-briefs.json", doc)


class ReportHtmlTests(NoNetworkTestCase):
    def _render(self) -> str:
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)
            _enrich_briefs(run_dir)
            agents.script_path(run_dir, "B01", 0).write_text(_CONTEXT_SCRIPT, encoding="utf-8")
            _write_qa(
                run_dir, "B01", 0, "pass",
                scores={"hook_scroll_stop": 9, "body_specificity": 6},
                checks={"not_generic": "fail"},
                issues=[{"check_or_score": "body_specificity", "severity": "major",
                         "detail": "Beat two is category language.", "fix": "Name the tool."}],
                strongest_line="It drafts. You decide.",
            )
            return report_html.render_report_html(run_dir)

    def test_page_has_every_part_the_creator_needs(self) -> None:
        html = self._render()
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("<title>", html)
        for text in (
            "What to do next",
            "Fill-in list",
            "B01", "B05",
            "Beats 3-8s, on screen",
            "remotion-dev/remotion",
            "https://www.instagram.com/p/SC0/",
            "hook_scroll_stop",
            "Name the tool.",
            "It drafts. You decide.",
            "State one new job a common tool can do",
        ):
            with self.subTest(text=text):
                self.assertIn(text, html)

    def test_scraped_and_written_text_is_escaped(self) -> None:
        html = self._render()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_page_is_self_contained(self) -> None:
        html = self._render()
        self.assertNotIn("<script", html.lower())
        self.assertIsNone(re.search(r"<link[^>]+stylesheet", html))
        self.assertIsNone(re.search(r"""src=["']https?://""", html))
        self.assertIn("prefers-color-scheme: dark", html)

    def test_no_em_dashes(self) -> None:
        self.assertNotIn("—", self._render())

    def test_a_fill_brief_is_titled_by_its_idea_title(self) -> None:
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)
            _enrich_briefs(run_dir)
            doc = store.read_json(run_dir / "03-briefs.json")
            doc["briefs"][4].update({"kind": "fill", "idea_title": "My own Sunday week card"})
            store.write_json_atomic(run_dir / "03-briefs.json", doc)
            html = report_html.render_report_html(run_dir)
        self.assertIn("<h3>My own Sunday week card</h3>", html)
        self.assertIn("<td>My own Sunday week card</td>", html)
        self.assertNotIn("<h3>Pending brief</h3>", html)
        self.assertNotIn("<td>Pending brief</td>", html)

    def test_funnel_names_the_ratio_floor_in_plain_words(self) -> None:
        # Final review M1: `below_min_ratio` gets a label from the run's
        # own `min_outlier_ratio`, never the raw reason key.
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)
            store.update_run(run_dir, config=dict(store.read_json(run_dir / "run.json")["config"],
                                                  min_outlier_ratio=2.0))
            doc = store.read_json(run_dir / "02-outliers.json")
            doc["excluded"] = [{"shortCode": "X1", "reason": "below_min_ratio"},
                               {"shortCode": "X2", "reason": "below_min_ratio"}]
            store.write_json_atomic(run_dir / "02-outliers.json", doc)
            html = report_html.render_report_html(run_dir)
        self.assertIn("minus <b>2</b> below 2x their usual", html)
        self.assertNotIn("below_min_ratio", html)

    def test_funnel_names_paid_partnerships_in_plain_words(self) -> None:
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)
            doc = store.read_json(run_dir / "02-outliers.json")
            doc["excluded"] = [{"shortCode": "X1", "reason": "paid_partnership"}]
            store.write_json_atomic(run_dir / "02-outliers.json", doc)
            html = report_html.render_report_html(run_dir)
        self.assertIn("minus <b>1</b> paid partnerships", html)
        self.assertNotIn("paid_partnership", html)

    def test_funnel_counts_reels_the_director_left_out_as_paid(self) -> None:
        with temp_project() as project:
            run_dir = _build_run_with_every_status(project)
            doc = store.read_json(run_dir / "03-briefs.json")
            doc["skipped_paid"] = [
                {"shortCode": "PAID1", "ownerUsername": "acme", "evidence": "<b>use code</b>"}
            ]
            store.write_json_atomic(run_dir / "03-briefs.json", doc)
            html = report_html.render_report_html(run_dir)
        self.assertIn("minus <b>1</b> left out after analysis as paid partnerships (@acme)", html)
        # Numbers and handles only: the director's evidence quotes scraped text.
        self.assertNotIn("use code", html)

    def test_a_run_with_no_briefs_still_renders(self) -> None:
        with temp_project() as project:
            config_dir = store.contentos_dir(project)
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(json.dumps({"competitors": ["a"]}), encoding="utf-8")
            run_dir = store.init_run(project, store.load_config(project), mode="mock")
            html = report_html.render_report_html(run_dir)
        self.assertIn("No briefs yet", html)


if __name__ == "__main__":
    unittest.main()
