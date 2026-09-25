"""Tests for `lib/discover.py` and `contentos.py discover`: finding accounts for a creator."""
from __future__ import annotations

import collections
import json
import re
import statistics
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

from tests.helpers import REPO_ROOT, NoNetworkTestCase, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import apify, codes, discover, env, instagram, research, store  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
CFG = dict(store.DEFAULT_CONFIG)


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _main(argv: Sequence[str]) -> Tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = contentos.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _reel(code: str, day: str, plays: int, paid: bool = False, caption: str = "",
          tags: Tuple[str, ...] = ()) -> Dict[str, Any]:
    return {"shortCode": code, "url": f"https://example.invalid/{code}", "ownerUsername": "x",
            "timestamp": f"{day}T12:00:00+00:00", "caption": caption, "hashtags": list(tags),
            "plays": plays, "likes": 100, "comments": 10, "paid_partnership": paid,
            "videoUrl": f"v/{code}", "displayUrl": f"c/{code}", "duration_s": 20.0}


PIA = [
    _reel("A", "2026-09-14", 150000, caption="#habits", tags=("habits",)),
    _reel("B", "2026-09-10", 40000),
    _reel("C", "2026-09-05", 300000, paid=True),
    _reel("D", "2026-08-30", 45000),
    _reel("E", "2026-08-25", 110000),
    _reel("F", "2026-08-10", 50000),
    _reel("G", "2026-07-20", 48000),
    _reel("H", "2026-07-15", 250000),
    _reel("I", "2026-07-01", 52000),
]


class InputBuilderTests(NoNetworkTestCase):
    def test_hashtag_reels_input(self) -> None:
        self.assertEqual(
            apify.build_hashtag_reels_input(["habits", "productivity"], 30, 14),
            {
                "directUrls": [
                    "https://www.instagram.com/explore/tags/habits/",
                    "https://www.instagram.com/explore/tags/productivity/",
                ],
                "resultsType": "reels",
                "resultsLimit": 30,
                "onlyPostsNewerThan": "14 days",
            },
        )


class NormalizeTermsTests(NoNetworkTestCase):
    def test_hashtags_are_cleaned_and_deduped(self) -> None:
        self.assertEqual(
            discover.normalize_hashtags(["#Habits", " habits ", "deep work", "", "bad/tag"]),
            ["habits", "deepwork"],
        )

    def test_hashtag_from_input_url(self) -> None:
        self.assertEqual(
            discover.hashtag_from_input_url("https://www.instagram.com/explore/tags/Habits/"),
            "habits",
        )
        self.assertIsNone(discover.hashtag_from_input_url("https://www.instagram.com/someone/"))
        self.assertIsNone(discover.hashtag_from_input_url(None))


WEB_FILE = FIXTURES_DIR / "discovery-web.sample.json"

EXPECTED_DROPPED = {
    "madeupmaya": "not_found",
    "focusfern": "under 10,000 followers",
    "photophoebe": "no reel in 30 days",
    "tinyhabitshop": "under 10,000 followers",
    "quietquill": "private",
    "goneghost": "not_found",
    "stalestella": "no reel in 30 days",
    "webwillow": "1 in 4 reels under 5,000 views",
    "slowsam": "posts less than every 2 weeks",
}


def _seed_project(project: Path, **config: Any) -> None:
    config_dir = store.contentos_dir(project)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps(dict({"competitors": ["habitlab"]}, **config)), encoding="utf-8"
    )


def _mock_args(project: Path, *extra: str) -> List[str]:
    return [
        "discover", "--project", str(project), "--keywords", "habit coach",
        "--hashtags", "habits,#Productivity", "--handles-file", str(WEB_FILE), *extra,
    ]


def _mock_transport(transport_class: Any = apify.FixtureTransport, reels: Optional[List[Any]] = None,
                    profiles: Optional[List[Any]] = None) -> Any:
    """The mock world's transport, or a scripted subclass of it."""
    return transport_class(
        _fixture("apify_discover_reels_sample.json") if reels is None else reels,
        _fixture("apify_discover_profiles_sample.json") if profiles is None else profiles,
        hashtag_items=_fixture("apify_hashtag_reels_sample.json"),
        keyword_items=_fixture("apify_discover_keyword_reels_sample.json"),
    )


def _run_mock(project: Path, transport: Any, cfg: Optional[Dict[str, Any]] = None,
              **kwargs: Any) -> Tuple[Dict[str, Any], List[str]]:
    """One mock discovery through `run_discover`; returns the document and its progress lines."""
    lines: List[str] = []
    options: Dict[str, Any] = dict(hashtags=["habits", "productivity"], keywords=["habit coach"],
                                   handles_file=WEB_FILE)
    options.update(kwargs)
    doc = discover.run_discover(
        project, store.load_discovery_config(project) if cfg is None else cfg, None,
        mock=True, yes=True, transport=transport, log=lines.append, **options,
    )
    return doc, lines


class _FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class MockDiscoverTests(NoNetworkTestCase):
    def test_mock_run_finds_the_successful_creators(self) -> None:
        with temp_project() as project:
            _seed_project(project)
            code, out, err = _main(_mock_args(project, "--mock", "--yes"))
            self.assertEqual(code, codes.EXIT_OK)
            doc = store.read_json(discover.discovery_path(project))

        self.assertEqual((doc["version"], doc["mode"], doc["partial"]), (2, "mock", False))
        self.assertEqual(doc["seeds"], ["habitlab"])
        self.assertEqual(doc["niche"], {"keywords": ["habit coach"], "hashtags": ["habits", "productivity"]})
        self.assertEqual(doc["settings"]["established_at"], 50000)
        self.assertEqual(doc["cost_estimate_usd"], 1.1502)
        self.assertIn("Step 1 of 3: searching Instagram and checking accounts.", err.splitlines())
        self.assertIn("Step 3 of 3: measuring the reels of 5 creators.", err.splitlines())
        self.assertNotRegex(err, r"(?m)^run \S+: ")
        self.assertEqual(
            [(row["handle"], row["tier"]) for row in doc["candidates"]],
            [("planwithpia", "established"), ("coachcora", "rising"), ("habitharbor", "rising")],
        )
        pia = doc["candidates"][0]
        self.assertEqual(
            {key: pia[key] for key in ("reels_measured", "top_quarter_plays", "median_plays",
                                       "paid_reels", "last_post_days", "posts_per_week", "category")},
            {"reels_measured": 9, "top_quarter_plays": 150000, "median_plays": 52000,
             "paid_reels": 1, "last_post_days": 1, "posts_per_week": 0.7, "category": "Digital creator"},
        )
        self.assertEqual(pia["sources"], ["hashtag:habits", "keyword:habit coach"])
        self.assertEqual(doc["candidates"][2]["sources"], ["related:habitlab", "related:webwillow"])
        self.assertEqual({item["handle"]: item["reason"] for item in doc["dropped"]}, EXPECTED_DROPPED)
        self.assertEqual(
            [(b["shortCode"], b["ratio"]) for b in doc["breakouts"]],
            [("PP01", 2.88), ("HH01", 2.5), ("CC01", 2.46), ("PP05", 2.12)],
        )
        self.assertTrue(any("tiktok.com" in warning for warning in doc["warnings"]))

        self.assertIn(
            "Held to: 10,000+ followers, a reel at least every 2 weeks, 1 in 4 reels at 5,000+ views.", out
        )
        self.assertIn("Established (50,000+ followers):", out)
        self.assertIn("Rising (10,000 to 50,000 followers):", out)
        self.assertIn(" 1. @planwithpia  610,000 followers  1 in 4 reels: 150,000 views", out)
        self.assertNotIn("—", out)
        result = json.loads([line for line in out.splitlines() if line.startswith("RESULT ")][-1][7:])
        self.assertEqual(
            {key: result[key] for key in ("candidates", "established", "rising", "dropped", "breakouts", "partial")},
            {"candidates": 3, "established": 1, "rising": 2, "dropped": 9, "breakouts": 4, "partial": False},
        )

    def test_seeds_are_checked_but_never_measured(self) -> None:
        transport = discover._default_transport(True)
        with temp_project() as project, mock.patch.object(discover, "_default_transport", return_value=transport):
            _seed_project(project)
            code, _out, _err = _main(_mock_args(project, "--mock", "--yes"))
        self.assertEqual(code, codes.EXIT_OK)
        posts = [call for call in transport.calls if call["method"] == "POST"]
        self.assertEqual(posts[2]["json_body"]["directUrls"][0], "https://www.instagram.com/habitlab/")
        self.assertEqual(posts[-1]["json_body"]["resultsType"], "reels")
        # Niche hits first, and among them the web find (webwillow) before
        # the search finds and the similar account (habitharbor).
        self.assertEqual(
            posts[-1]["json_body"]["directUrls"],
            [f"https://www.instagram.com/{handle}/"
             for handle in ("webwillow", "planwithpia", "coachcora", "habitharbor", "slowsam")],
        )

    def test_format_accounts_are_never_checked_or_recommended(self) -> None:
        transport = discover._default_transport(True)
        with temp_project() as project, mock.patch.object(discover, "_default_transport", return_value=transport):
            _seed_project(project, format_accounts=["coachcora"])
            code, _out, _err = _main(_mock_args(project, "--mock", "--yes"))
            self.assertEqual(code, codes.EXIT_OK)
            doc = store.read_json(discover.discovery_path(project))
        self.assertNotIn("coachcora", [row["handle"] for row in doc["candidates"]])
        self.assertNotIn("coachcora", [item["handle"] for item in doc["dropped"]])
        for call in transport.calls:
            if call["method"] == "POST":
                self.assertNotIn(
                    "https://www.instagram.com/coachcora/", call["json_body"].get("directUrls") or []
                )

    def test_runs_are_phased_and_share_the_cap(self) -> None:
        transport = discover._default_transport(True)
        with temp_project() as project, mock.patch.object(discover, "_default_transport", return_value=transport):
            _seed_project(project)
            _main(_mock_args(project, "--mock", "--yes"))
        kinds = [
            "post" if call["method"] == "POST" else "poll"
            for call in transport.calls
            if call["method"] == "POST" or "/actor-runs/" in call["url"]
        ]
        self.assertEqual(kinds[:4], ["post", "post", "post", "poll"])
        self.assertEqual(transport.calls[0]["url"], apify.API_BASE + apify.KEYWORD_ACTOR_RUNS_PATH)
        caps = [call["params"]["maxTotalChargeUsd"] for call in transport.calls if call["method"] == "POST"]
        self.assertEqual(caps, [3.0, 2.946, 2.784, 2.7678, 2.7489])

    def test_a_small_shortlist_leaves_the_rest_out(self) -> None:
        with temp_project() as project:
            _seed_project(project, discover_shortlist=3)
            _main(_mock_args(project, "--mock", "--yes"))
            doc = store.read_json(discover.discovery_path(project))
        self.assertEqual([row["handle"] for row in doc["candidates"]], ["planwithpia", "coachcora"])
        dropped = {item["handle"]: item["reason"] for item in doc["dropped"]}
        self.assertEqual((dropped["habitharbor"], dropped["slowsam"]), ("shortlist full", "shortlist full"))

    def test_a_timed_out_reels_run_marks_creators_not_measured(self) -> None:
        reels = [item for item in _fixture("apify_discover_reels_sample.json")
                 if item["ownerUsername"] == "planwithpia"]

        class TimedOutReels(apify.FixtureTransport):
            def request_json(self, method, url, headers=None, json_body=None, params=None):  # type: ignore[override]
                result = super().request_json(method, url, headers, json_body, params)
                if method == "GET" and url.endswith("/actor-runs/mock-reels"):
                    result["data"]["status"] = "TIMED-OUT"
                return result

        transport = TimedOutReels(
            reels, _fixture("apify_discover_profiles_sample.json"),
            hashtag_items=_fixture("apify_hashtag_reels_sample.json"),
            keyword_items=_fixture("apify_discover_keyword_reels_sample.json"),
        )
        with temp_project() as project:
            _seed_project(project)
            doc = discover.run_discover(
                project, store.load_discovery_config(project), None,
                hashtags=["habits", "productivity"], keywords=["habit coach"], handles_file=WEB_FILE,
                mock=True, yes=True, transport=transport, log=lambda _line: None,
            )
        self.assertTrue(doc["partial"])
        self.assertEqual([row["handle"] for row in doc["candidates"]], ["planwithpia"])
        dropped = {item["handle"]: item["reason"] for item in doc["dropped"]}
        for handle in ("coachcora", "webwillow", "habitharbor", "slowsam"):
            self.assertEqual(dropped[handle], "not measured in time")
        self.assertIn("The reels check ran out of time, so its results are incomplete.", doc["warnings"])
        self.assertFalse(any("mock-reels" in warning for warning in doc["warnings"]))

    def test_works_before_setup(self) -> None:
        with temp_project() as project:
            code, _out, _err = _main(
                ["discover", "--project", str(project), "--keywords", "habit coach", "--mock", "--yes"]
            )
            self.assertEqual(code, codes.EXIT_OK)
            self.assertEqual(store.read_json(discover.discovery_path(project))["seeds"], [])

    def test_seed_warnings_are_plain(self) -> None:
        profiles = _fixture("apify_discover_profiles_sample.json") + [
            {"inputUrl": "https://www.instagram.com/oddone/", "error": "Rate limited, try later"},
        ]
        with temp_project() as project:
            _seed_project(project)
            doc, _lines = _run_mock(project, _mock_transport(profiles=profiles),
                                    seeds=["goneghost", "quietquill", "oddone"])
        for warning in ("watch list account @goneghost was not found",
                        "watch list account @quietquill is private",
                        "watch list account @oddone could not be checked"):
            self.assertIn(warning, doc["warnings"])
        self.assertFalse(any("(" in warning for warning in doc["warnings"] if "watch list" in warning))


class OutOfTimeTests(NoNetworkTestCase):
    """Design spec, "0.6.0 changes": a run that runs out of time says so, and never calls anyone missing."""

    def test_no_time_left_skips_the_account_check_and_the_reels_check(self) -> None:
        clock = _FakeClock()

        class SlowStepA(apify.FixtureTransport):
            def request_json(self, method, url, headers=None, json_body=None, params=None):  # type: ignore[override]
                result = super().request_json(method, url, headers, json_body, params)
                if method == "GET" and url.endswith("/actor-runs/mock-details"):
                    clock.now = discover.BUDGET_S - 10
                return result

        transport = _mock_transport(SlowStepA)
        with temp_project() as project:
            _seed_project(project)
            doc, lines = _run_mock(project, transport, clock=clock)
        posts = [call for call in transport.calls if call["method"] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertEqual([call["params"]["timeout"] for call in posts], [540, 540, 540])
        dropped = {item["handle"]: item["reason"] for item in doc["dropped"]}
        self.assertEqual(dropped, {
            "madeupmaya": "not_found", "focusfern": "under 10,000 followers",
            "photophoebe": "no reel in 30 days",
            "planwithpia": "not checked in time", "coachcora": "not checked in time",
            "goneghost": "not checked in time", "quietquill": "not checked in time",
            "tinyhabitshop": "not checked in time", "habitharbor": "not checked in time",
            "stalestella": "not checked in time",
            "webwillow": "not measured in time", "slowsam": "not measured in time",
        })
        self.assertEqual(doc["candidates"], [])
        self.assertTrue(doc["partial"])
        self.assertIn("There was no time left for the account check, so it was skipped.", doc["warnings"])
        self.assertIn("There was no time left for the reels check, so it was skipped.", doc["warnings"])
        self.assertIn("Account check skipped: out of time.", lines)
        self.assertIn("Reels check skipped: out of time.", lines)

    def test_a_timed_out_account_check_marks_the_accounts_it_missed(self) -> None:
        clock = _FakeClock()
        reached = [item for item in _fixture("apify_discover_profiles_sample.json")
                   if item.get("username") in ("planwithpia", "coachcora")
                   or str(item.get("inputUrl", "")).endswith("/goneghost/")]

        class TimedOutStepB(apify.FixtureTransport):
            details_posts = 0

            def request_json(self, method, url, headers=None, json_body=None, params=None):  # type: ignore[override]
                result = super().request_json(method, url, headers, json_body, params)
                if method == "POST" and result["data"]["id"] == "mock-details":
                    self.details_posts += 1
                    if self.details_posts == 2:
                        result["data"].update(id="mock-details-b", defaultDatasetId="ds-details-b")
                elif method == "GET" and url.endswith("/actor-runs/mock-details"):
                    clock.now = 440.0
                elif method == "GET" and url.endswith("/actor-runs/mock-details-b"):
                    result["data"].update(status="TIMED-OUT", defaultDatasetId="ds-details-b")
                elif method == "GET" and url.endswith("/datasets/ds-details-b/items"):
                    offset = int(params["offset"])
                    return reached[offset:offset + int(params["limit"])]
                return result

        transport = _mock_transport(TimedOutStepB)
        with temp_project() as project:
            _seed_project(project)
            doc, lines = _run_mock(project, transport, clock=clock)
        posts = [call for call in transport.calls if call["method"] == "POST"]
        self.assertEqual([call["params"]["timeout"] for call in posts], [540, 540, 540, 100, 100])
        dropped = {item["handle"]: item["reason"] for item in doc["dropped"]}
        self.assertEqual(dropped["goneghost"], "not_found")
        for handle in ("quietquill", "tinyhabitshop", "habitharbor", "stalestella"):
            self.assertEqual(dropped[handle], "not checked in time")
        self.assertEqual((dropped["webwillow"], dropped["slowsam"]),
                         ("1 in 4 reels under 5,000 views", "posts less than every 2 weeks"))
        self.assertEqual([row["handle"] for row in doc["candidates"]], ["planwithpia", "coachcora"])
        self.assertTrue(doc["partial"])
        self.assertIn("The account check ran out of time, so its results are incomplete.", doc["warnings"])
        self.assertIn("Account check ran out of time.", lines)

    def test_a_seed_the_account_check_never_reached_gets_a_warning(self) -> None:
        reached = [item for item in _fixture("apify_discover_profiles_sample.json")
                   if item.get("username") == "webwillow"
                   or str(item.get("inputUrl", "")).endswith("/madeupmaya/")]

        class TimedOutStepA(apify.FixtureTransport):
            details_polls = 0

            def request_json(self, method, url, headers=None, json_body=None, params=None):  # type: ignore[override]
                result = super().request_json(method, url, headers, json_body, params)
                if method == "GET" and url.endswith("/actor-runs/mock-details"):
                    self.details_polls += 1
                    if self.details_polls == 1:
                        result["data"].update(status="TIMED-OUT", defaultDatasetId="ds-details-a")
                elif method == "GET" and url.endswith("/datasets/ds-details-a/items"):
                    offset = int(params["offset"])
                    return reached[offset:offset + int(params["limit"])]
                return result

        with temp_project() as project:
            _seed_project(project)
            doc, _lines = _run_mock(project, _mock_transport(TimedOutStepA))
        dropped = {item["handle"]: item["reason"] for item in doc["dropped"]}
        self.assertEqual(dropped["madeupmaya"], "not_found")
        for handle in ("focusfern", "slowsam", "photophoebe"):
            self.assertEqual(dropped[handle], "not checked in time")
        self.assertNotIn("habitlab", dropped)
        self.assertIn("watch list account @habitlab was not checked in time", doc["warnings"])
        self.assertTrue(doc["partial"])

    def test_a_cut_short_reels_check_does_not_judge_short_lists(self) -> None:
        reels = _fixture("apify_discover_reels_sample.json")
        # webwillow gets a full 15 reels back, so it is judged; slowsam's 4 may be cut short.
        willow = [item for item in reels if item["ownerUsername"] == "webwillow"]
        for index in range(discover.REELS_PER_ACCOUNT - len(willow)):
            extra = dict(willow[0], shortCode=f"WWX{index:02d}", id=f"id-WWX{index:02d}",
                         url=f"https://www.instagram.com/reel/WWX{index:02d}/",
                         timestamp=f"2026-08-{10 + index:02d}T12:00:00.000Z")
            reels.append(extra)

        class TimedOutReels(apify.FixtureTransport):
            def request_json(self, method, url, headers=None, json_body=None, params=None):  # type: ignore[override]
                result = super().request_json(method, url, headers, json_body, params)
                if method == "GET" and url.endswith("/actor-runs/mock-reels"):
                    result["data"]["status"] = "TIMED-OUT"
                return result

        with temp_project() as project:
            _seed_project(project)
            doc, _lines = _run_mock(project, _mock_transport(TimedOutReels, reels=reels))
        self.assertTrue(doc["partial"])
        self.assertEqual([row["handle"] for row in doc["candidates"]], ["planwithpia", "coachcora", "habitharbor"])
        dropped = {item["handle"]: item["reason"] for item in doc["dropped"]}
        self.assertEqual(dropped["slowsam"], "not measured in time")
        self.assertEqual(dropped["webwillow"], "1 in 4 reels under 5,000 views")
        self.assertIn("The reels check ran out of time, so its results are incomplete.", doc["warnings"])

    def test_a_failed_account_check_is_an_upstream_failure(self) -> None:
        class FailedStepB(apify.FixtureTransport):
            details_polls = 0

            def request_json(self, method, url, headers=None, json_body=None, params=None):  # type: ignore[override]
                result = super().request_json(method, url, headers, json_body, params)
                if method == "GET" and url.endswith("/actor-runs/mock-details"):
                    self.details_polls += 1
                    if self.details_polls == 2:
                        result["data"]["status"] = "FAILED"
                return result

        with temp_project() as project:
            _seed_project(project)
            with self.assertRaises(research.UpstreamFailure) as ctx:
                _run_mock(project, _mock_transport(FailedStepB))
            self.assertFalse(discover.discovery_path(project).exists())
        self.assertEqual(ctx.exception.exit_code, codes.EXIT_UPSTREAM)


class ProgressTests(NoNetworkTestCase):
    """`run_discover` reports progress as plain step lines, never raw run ids."""

    def test_the_mock_run_reports_each_step(self) -> None:
        with temp_project() as project:
            _seed_project(project)
            _doc, lines = _run_mock(project, _mock_transport())
        self.assertEqual(lines, [
            "Step 1 of 3: searching Instagram and checking accounts.",
            "Keyword search started.",
            "Hashtag search started.",
            "Account check started.",
            "Keyword search done.",
            "Hashtag search done.",
            "Account check done.",
            "Step 2 of 3: checking 7 more accounts.",
            "Account check started.",
            "Account check done.",
            "Step 3 of 3: measuring the reels of 5 creators.",
            "Reels check started.",
            "Reels check done.",
        ])
        self.assertFalse(any(re.match(r"^run \S+: ", line) for line in lines))

    def test_one_account_and_one_creator(self) -> None:
        with temp_project() as project:
            cfg = dict(store.load_discovery_config(project), discover_shortlist=1)
            _doc, lines = _run_mock(project, _mock_transport(), cfg=cfg, hashtags=[], keywords=[],
                                    handles_file=None, web_entries=[{"handle": "webwillow", "source_url": "u"}])
        self.assertIn("Step 2 of 3: checking 1 more account.", lines)
        self.assertIn("Step 3 of 3: measuring the reels of 1 creator.", lines)

    def test_nothing_more_to_check_and_nobody_to_measure(self) -> None:
        with temp_project() as project:
            _doc, lines = _run_mock(project, _mock_transport(), hashtags=[], keywords=[], handles_file=None,
                                    web_entries=[{"handle": "focusfern", "source_url": "u"}])
        self.assertIn("Step 2 of 3: no more accounts to check.", lines)
        self.assertIn("Step 3 of 3: nobody to measure.", lines)

    def test_a_long_run_says_it_is_still_running_at_most_once_a_minute(self) -> None:
        clock = _FakeClock()

        class SlowKeywordSearch(apify.FixtureTransport):
            def request_json(self, method, url, headers=None, json_body=None, params=None):  # type: ignore[override]
                result = super().request_json(method, url, headers, json_body, params)
                if method == "GET" and url.endswith("/actor-runs/mock-keyword"):
                    clock.now += 25.0
                    if clock.now < 250.0:
                        result["data"]["status"] = "RUNNING"
                return result

        with temp_project() as project:
            _seed_project(project)
            cfg = dict(store.load_discovery_config(project), poll_interval_s=0)
            doc, lines = _run_mock(project, _mock_transport(SlowKeywordSearch), cfg=cfg, clock=clock)
        self.assertEqual([line for line in lines if "still running" in line], [
            "Keyword search still running, 1 min so far.",
            "Keyword search still running, 2 min so far.",
            "Keyword search still running, 3 min so far.",
        ])
        self.assertLess(lines.index("Keyword search still running, 3 min so far."),
                        lines.index("Keyword search done."))
        self.assertFalse(doc["partial"])


class DiscoverGateTests(NoNetworkTestCase):
    @staticmethod
    def _args(project: Path, *extra: str) -> List[str]:
        return ["discover", "--project", str(project), "--keywords", "habit coach",
                "--hashtags", "habits,#Productivity", *extra]

    def test_estimate_only_exits_3_with_the_estimate(self) -> None:
        with temp_project() as project:
            code, out, _err = _main(self._args(project, "--mock", "--estimate-only"))
            self.assertEqual(code, codes.EXIT_CONFIRM)
            estimate = json.loads(out)
            self.assertEqual((estimate["keyword_reels_usd"], estimate["total_usd"]), (0.054, 1.134))
            self.assertFalse(discover.discovery_path(project).exists())

    def test_missing_yes_exits_3(self) -> None:
        with temp_project() as project:
            self.assertEqual(_main(self._args(project, "--mock"))[0], codes.EXIT_CONFIRM)

    def test_over_the_cap_exits_6(self) -> None:
        with temp_project() as project:
            config_dir = store.contentos_dir(project)
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(json.dumps({"apify_max_charge_usd": 0.05}), encoding="utf-8")
            self.assertEqual(_main(self._args(project, "--mock", "--yes"))[0], codes.EXIT_COST)

    def test_no_key_exits_4(self) -> None:
        no_keys = env.Keys(apify=None, source=None, warnings=[])
        with temp_project() as project, mock.patch.object(env, "resolve_keys", return_value=no_keys):
            self.assertEqual(_main(self._args(project, "--yes"))[0], codes.EXIT_KEYS)

    def test_nothing_to_search_exits_2(self) -> None:
        with temp_project() as project:
            code, _out, err = _main(["discover", "--project", str(project), "--hashtags", "///", "--mock", "--yes"])
        self.assertEqual(code, codes.EXIT_USAGE)
        self.assertIn("needs something to search", err)

    def test_bad_handles_file_exits_2(self) -> None:
        with temp_project() as project:
            bad = Path(project) / "web.json"
            bad.write_text("{not json", encoding="utf-8")
            code, _out, _err = _main(self._args(project, "--handles-file", str(bad), "--mock", "--yes"))
        self.assertEqual(code, codes.EXIT_USAGE)

    def test_a_handles_file_that_is_not_a_list_names_the_file(self) -> None:
        with temp_project() as project:
            bad = Path(project) / "web.json"
            bad.write_text('{"handle": "webwillow"}', encoding="utf-8")
            code, _out, err = _main(self._args(project, "--handles-file", str(bad), "--mock", "--yes"))
            self.assertFalse(discover.discovery_path(project).exists())
        self.assertEqual(code, codes.EXIT_USAGE)
        self.assertIn(str(bad), err)
        self.assertIn("must be a JSON list of finds ({handle, ...}) or bare handles", err)


class KeywordInputUrlTests(NoNetworkTestCase):
    def test_phrase_from_a_keyword_url(self) -> None:
        base = "https://www.instagram.com/explore/search/keyword/?q="
        self.assertEqual(discover.keyword_from_input_url(base + "habit%20coach"), "habit coach")
        self.assertEqual(discover.keyword_from_input_url(base + "Habit+Coach"), "habit coach")
        self.assertIsNone(discover.keyword_from_input_url("https://www.instagram.com/explore/tags/habits/"))
        self.assertIsNone(discover.keyword_from_input_url(None))


class SearchAuthorsTests(NoNetworkTestCase):
    def test_keyword_and_hashtag_reels_group_by_author(self) -> None:
        items = (_fixture("apify_discover_keyword_reels_sample.json")
                 + _fixture("apify_hashtag_reels_sample.json"))
        authors = discover.search_authors(items)
        self.assertEqual(
            sorted(authors),
            ["coachcora", "focusfern", "goneghost", "planwithpia", "quietquill", "tinyhabitshop"],
        )
        self.assertEqual(
            authors["planwithpia"],
            {"reels_seen": 3, "best_plays": 2100000, "sources": ["hashtag:habits", "keyword:habit coach"]},
        )
        self.assertEqual(authors["focusfern"]["sources"], ["hashtag:habits", "hashtag:productivity"])
        self.assertNotIn("brandbox", authors)


class SeedAndWebTests(NoNetworkTestCase):
    def test_a_bad_seed_is_warned_not_fatal(self) -> None:
        seeds, warnings = discover.normalize_seeds(["@HabitLab", "habitlab", "https://www.tiktok.com/@x", ""])
        self.assertEqual(seeds, ["habitlab"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("tiktok.com", warnings[0])

    def test_web_entries_accept_bare_handles_and_drop_bad_ones(self) -> None:
        entries, warnings = discover.normalize_web_entries(
            [{"handle": "@WebWillow", "source_url": "u1"}, "slowsam", {"handle": "https://x.com/y"}]
        )
        self.assertEqual(entries, [
            {"handle": "webwillow", "source_url": "u1", "source_title": None, "reason": None, "followers_seen": None},
            {"handle": "slowsam", "source_url": "", "source_title": None, "reason": None, "followers_seen": None},
        ])
        self.assertEqual(len(warnings), 1)
        with self.assertRaises(discover.DiscoverError) as ctx:
            discover.normalize_web_entries({"handle": "x"})
        self.assertIn("a JSON list of finds ({handle, ...}) or bare handles", str(ctx.exception))

    def test_web_finds_keep_their_reason_title_and_followers_seen(self) -> None:
        entries, warnings = discover.normalize_web_entries([
            {"handle": "@WebWillow", "source_url": "https://a.example/list",
             "source_title": "  12 habit\n  creators ", "reason": "x" * 200, "followers_seen": 250000},
            {"handle": "slowsam", "followers_seen": "250K"},
            {"handle": "focusfern", "followers_seen": -3, "reason": "   "},
            {"handle": "photophoebe", "followers_seen": True, "source_title": 7},
        ])
        self.assertEqual(warnings, [])
        self.assertEqual(entries[0], {
            "handle": "webwillow", "source_url": "https://a.example/list",
            "source_title": "12 habit creators", "reason": "x" * 140, "followers_seen": 250000,
        })
        for entry in entries[1:]:
            with self.subTest(handle=entry["handle"]):
                self.assertEqual((entry["source_title"], entry["reason"], entry["followers_seen"]),
                                 (None, None, None))

    def test_the_sample_web_file_carries_the_new_fields(self) -> None:
        entries, _warnings = discover.load_web_handles(WEB_FILE)
        first = entries[0]
        self.assertEqual(first["handle"], "webwillow")
        self.assertEqual(first["source_title"], "The best habit creators to follow")
        self.assertEqual(first["reason"], "Listed for short habit-building tutorials")
        self.assertEqual(first["followers_seen"], 48000)
        self.assertIsNone(entries[1]["followers_seen"])

    def test_web_handles_skip_seeds_dedupe_and_stop_at_40(self) -> None:
        entries = [{"handle": f"h{i}", "source_url": "u"} for i in range(45)]
        entries += [{"handle": "h1", "source_url": "u"}, {"handle": "habitlab", "source_url": "u"}]
        handles = discover.web_handles(entries, ["habitlab"])
        self.assertEqual(len(handles), 40)
        self.assertEqual(handles[:2], ["h0", "h1"])
        self.assertNotIn("habitlab", handles)


class TopAuthorsTests(NoNetworkTestCase):
    def test_best_plays_first_and_checked_handles_skipped(self) -> None:
        authors = {"a": {"best_plays": 10}, "b": {"best_plays": 30}, "c": {"best_plays": 30},
                   "d": {"best_plays": 99}}
        self.assertEqual(discover.top_authors(authors, 2, {"d"}), ["b", "c"])


class ProfileIndexTests(NoNetworkTestCase):
    def test_index_and_status(self) -> None:
        rows, errors = discover.index_profiles(_fixture("apify_discover_profiles_sample.json"))
        self.assertEqual(discover.profile_status("planwithpia", rows, errors), "ok")
        self.assertEqual(discover.profile_status("quietquill", rows, errors), "private")
        self.assertEqual(discover.profile_status("goneghost", rows, errors), "not_found")
        self.assertEqual(discover.profile_status("nobodyasked", rows, errors), "not_found")
        self.assertEqual(rows["webwillow"]["related"], ["habitharbor"])

    def test_a_cut_short_run_never_calls_a_missing_account_not_found(self) -> None:
        rows, errors = discover.index_profiles(_fixture("apify_discover_profiles_sample.json"))
        self.assertEqual(discover.REASON_NOT_CHECKED, "not checked in time")
        self.assertEqual(discover.profile_status("nobodyasked", rows, errors, partial=True), "not checked in time")
        self.assertEqual(discover.profile_status("goneghost", rows, errors, partial=True), "not_found")
        self.assertEqual(discover.profile_status("planwithpia", rows, errors, partial=True), "ok")
        self.assertIsNone(rows["webwillow"]["category"])
        self.assertEqual(rows["planwithpia"]["followers"], 610000)
        self.assertEqual(rows["planwithpia"]["category"], "Digital creator")


class NicheTests(NoNetworkTestCase):
    def test_keyword_phrase_and_niche_hashtag(self) -> None:
        is_niche = discover.niche_matcher(["habit coach"], ["habits"])
        self.assertTrue(is_niche("Your HABIT   coach says hi"))
        self.assertTrue(is_niche("nothing here", ["Habits"]))
        self.assertTrue(is_niche("a caption with #habits inline"))
        self.assertFalse(is_niche("habitcoach and habitual", ["habitual"]))
        self.assertFalse(is_niche(None, None))
        self.assertFalse(discover.niche_matcher([], [])("habit coach", ["habits"]))

    def test_latest_niche_hit_reads_the_bio_and_latest_posts(self) -> None:
        is_niche = discover.niche_matcher(["habit coach"], ["productivity"])
        self.assertTrue(discover.latest_niche_hit({"bio": "Habit coach for parents", "latest_posts": []}, is_niche))
        self.assertTrue(discover.latest_niche_hit(
            {"bio": "", "latest_posts": [{"caption": "desk", "hashtags": ["productivity"]}]}, is_niche))
        self.assertFalse(discover.latest_niche_hit(
            {"bio": "Slow living", "latest_posts": [{"caption": "Sunday reset", "hashtags": []}]}, is_niche))

    # Design spec, "0.6.0 changes" (Who counts as successful): a keyword
    # phrase matches when all its words are there, in any order or form.
    def test_every_word_of_a_phrase_must_be_there(self) -> None:
        is_niche = discover.niche_matcher(["ai agents for business"], [])
        self.assertTrue(is_niche("AI Agents and Automation for your business"))
        self.assertTrue(is_niche("I help businesses automate with AI agents"))
        self.assertFalse(is_niche("Helping You Master AI Agents"))

    def test_longer_words_match_their_forms_and_short_ones_stand_alone(self) -> None:
        is_niche = discover.niche_matcher(["ai automation"], [])
        self.assertTrue(is_niche("Learn AI, Automations in 60 seconds"))
        self.assertTrue(is_niche("Automate your ads, business and life with AI"))
        self.assertFalse(is_niche("AIautomation tips"))
        self.assertFalse(is_niche("We're so back #CanvaWorldTour"))

    def test_tags_count_as_words(self) -> None:
        is_niche = discover.niche_matcher(["n8n automation"], [])
        self.assertTrue(is_niche("Build this workflow", ["n8n", "automation"]))
        self.assertFalse(is_niche("n8n tips"))

    def test_a_short_word_takes_only_a_plural_s(self) -> None:
        is_niche = discover.niche_matcher(["meal prep for beginners"], [])
        self.assertTrue(is_niche("Meal prep ideas for a beginner"))
        self.assertFalse(is_niche("meals prepped"))

    def test_word_order_does_not_matter(self) -> None:
        is_niche = discover.niche_matcher(["habit coach"], [])
        self.assertTrue(is_niche("coaching habits daily"))
        self.assertTrue(is_niche("Habit coach tip"))

    def test_filler_only_phrases_and_no_terms_never_match(self) -> None:
        self.assertFalse(discover.niche_matcher(["how to"], [])("how to do anything, how to"))
        self.assertFalse(discover.niche_matcher([], [])("how to do anything", ["anything"]))

    def test_letters_beyond_ascii_are_words(self) -> None:
        self.assertTrue(discover.niche_matcher(["café tips"], [])("Best café tips in town"))

    def test_both_passes_match_by_words(self) -> None:
        is_niche = discover.niche_matcher(["n8n automation"], [])
        self.assertTrue(discover.latest_niche_hit(
            {"bio": "", "latest_posts": [{"caption": "Build this workflow", "hashtags": ["n8n", "automation"]}]},
            is_niche))
        reels = [_reel("N1", "2026-09-10", 1000, caption="Automating invoices with n8n"),
                 _reel("N2", "2026-09-09", 1000, caption="n8n tips")]
        self.assertEqual(discover.measure(reels, NOW, is_niche)["niche_hits"], 1)


def _post(day: str, reel: bool = True, pinned: bool = False) -> Dict[str, Any]:
    return {"timestamp": f"{day}T12:00:00+00:00", "is_reel": reel, "is_pinned": pinned,
            "caption": "", "hashtags": []}


class Pass1Tests(NoNetworkTestCase):
    def _row(self, followers: Any = 20000, latest: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        return {"username": "x", "followers": followers, "latest_posts": latest or []}

    def test_every_source_is_held_to_the_floor(self) -> None:
        self.assertEqual(discover.pass1_reason(self._row(9999), CFG, NOW), "under 10,000 followers")
        self.assertEqual(discover.pass1_reason(self._row(None), CFG, NOW), "under 10,000 followers")
        self.assertIsNone(discover.pass1_reason(self._row(10000), CFG, NOW))

    def test_floor_zero_keeps_small_accounts(self) -> None:
        self.assertIsNone(discover.pass1_reason(self._row(12), dict(CFG, discover_min_followers=0), NOW))

    def test_the_newest_unpinned_reel_decides_activity(self) -> None:
        old_pin_new_reel = [_post("2026-01-01", pinned=True), _post("2026-09-10")]
        self.assertIsNone(discover.pass1_reason(self._row(latest=old_pin_new_reel), CFG, NOW))
        new_pin_old_reel = [_post("2026-09-15", pinned=True), _post("2026-07-01")]
        self.assertEqual(discover.pass1_reason(self._row(latest=new_pin_old_reel), CFG, NOW), "no reel in 30 days")
        photos_only = [_post("2026-09-14", reel=False), _post("2026-08-07", reel=False)]
        self.assertEqual(discover.pass1_reason(self._row(latest=photos_only), CFG, NOW), "no reel in 30 days")

    def test_missing_latest_posts_skips_the_activity_check(self) -> None:
        self.assertIsNone(discover.pass1_reason(self._row(latest=[]), CFG, NOW))

    def test_a_daily_carousel_poster_is_left_to_pass_2(self) -> None:
        # 12 photos, 0 to 11 days old: the latest posts never reach back 30 days.
        carousels = [_post(f"2026-09-{day:02d}", reel=False) for day in range(15, 3, -1)]
        self.assertEqual(len(carousels), 12)
        self.assertIsNone(discover.pass1_reason(self._row(latest=carousels), CFG, NOW))

    def test_posts_reaching_back_40_days_with_no_reel_are_inactive(self) -> None:
        days = ("2026-09-15", "2026-09-12", "2026-09-09", "2026-09-06", "2026-09-03", "2026-08-31",
                "2026-08-28", "2026-08-25", "2026-08-21", "2026-08-17", "2026-08-12", "2026-08-06")
        photos = [_post(day, reel=False) for day in days]
        self.assertEqual(discover._days_ago(photos[-1]["timestamp"], NOW), 40)
        self.assertEqual(discover.pass1_reason(self._row(latest=photos), CFG, NOW), "no reel in 30 days")

    def test_pinned_posts_do_not_count_toward_30_days(self) -> None:
        old_pinned_photo = [_post("2026-01-01", reel=False, pinned=True), _post("2026-09-14", reel=False)]
        self.assertIsNone(discover.pass1_reason(self._row(latest=old_pinned_photo), CFG, NOW))
        undated_post = [dict(_post("2026-09-14", reel=False)), dict(_post("2026-01-01", reel=False), timestamp=None)]
        self.assertIsNone(discover.pass1_reason(self._row(latest=undated_post), CFG, NOW))


class ExpansionTests(NoNetworkTestCase):
    def test_ranked_by_distinct_pointers_then_handle(self) -> None:
        rows = [{"username": "HabitLab", "related": ["b", "a", "c", "habitlab"]},
                {"username": "webwillow", "related": ["a", "c"]}]
        pointed = discover.expansion_pointers(rows, skip={"c"})
        self.assertEqual(pointed, {"a": {"habitlab", "webwillow"}, "b": {"habitlab"}})
        self.assertEqual(discover.rank_expansion(pointed, 15), ["a", "b"])
        self.assertEqual(discover.rank_expansion(pointed, 1), ["a"])


class ShortlistTests(NoNetworkTestCase):
    def test_niche_first_then_tiers_alternate(self) -> None:
        rows = [
            {"handle": "big1", "followers": 610000, "niche_hit": True, "source_rank": 1},
            {"handle": "big2", "followers": 52000, "niche_hit": True, "source_rank": 1},
            {"handle": "small1", "followers": 40000, "niche_hit": True, "source_rank": 1},
            {"handle": "small2", "followers": 18000, "niche_hit": True, "source_rank": 1},
            {"handle": "offbig", "followers": 900000, "niche_hit": False, "source_rank": 1},
            {"handle": "offsmall", "followers": 25000, "niche_hit": False, "source_rank": 1},
        ]
        self.assertEqual(discover.shortlist(rows, 10, 50000),
                         ["big1", "small1", "big2", "small2", "offbig", "offsmall"])
        self.assertEqual(discover.shortlist(rows, 3, 50000), ["big1", "small1", "big2"])

    # Design spec, "0.6.0 changes" (Shortlist): after the niche hits, web
    # finds come first, then search finds, then similar accounts.
    def test_the_best_source_comes_first_after_the_niche_hits(self) -> None:
        rows = [
            {"handle": "offrelated", "followers": 2600000, "niche_hit": False, "source_rank": 2},
            {"handle": "offweb", "followers": 200000, "niche_hit": False, "source_rank": 0},
            {"handle": "offkeyword", "followers": 300000, "niche_hit": False, "source_rank": 1},
            {"handle": "hitrelated", "followers": 20000, "niche_hit": True, "source_rank": 2},
        ]
        self.assertEqual(discover.shortlist(rows, 10, 50000),
                         ["hitrelated", "offweb", "offkeyword", "offrelated"])
        self.assertEqual(discover.shortlist(rows, 2, 50000), ["hitrelated", "offweb"])

    def test_source_rank(self) -> None:
        web, keyword = "web:https://example.invalid/top-10", "keyword:habit coach"
        self.assertEqual(discover.source_rank(["related:habitlab", keyword, web]), 0)
        self.assertEqual(discover.source_rank(["related:habitlab", keyword]), 1)
        self.assertEqual(discover.source_rank(["hashtag:habits"]), discover.source_rank([keyword]))
        self.assertEqual(discover.source_rank(["related:habitlab"]), 2)
        self.assertEqual(discover.source_rank([]), 2)


class FixtureShapeTests(NoNetworkTestCase):
    def test_profiles_look_like_a_real_details_run(self) -> None:
        items = _fixture("apify_discover_profiles_sample.json")
        profiles = {item["username"]: item for item in items if "error" not in item}
        for username, item in profiles.items():
            with self.subTest(username=username):
                self.assertLessEqual(
                    {"username", "followersCount", "biography", "latestPosts",
                     "businessCategoryName", "isBusinessAccount"},
                    set(item),
                )
                for post in item["latestPosts"]:
                    self.assertNotIn("videoPlayCount", post)
        self.assertEqual(profiles["webwillow"]["relatedProfiles"][0]["username"], "habitharbor")
        self.assertNotIn("relatedProfiles", profiles["coachcora"])
        missing = sorted(item["inputUrl"].rstrip("/").rsplit("/", 1)[-1] for item in items if "error" in item)
        self.assertEqual(missing, ["goneghost", "madeupmaya"])

    def test_the_0_5_accounts_keep_their_numbers(self) -> None:
        followers = {
            item["username"]: item["followersCount"]
            for item in _fixture("apify_discover_profiles_sample.json") if "error" not in item
        }
        self.assertEqual(
            {name: followers[name] for name in
             ("focusfern", "planwithpia", "quietquill", "habitharbor", "tinyhabitshop", "webwillow")},
            {"focusfern": 9400, "planwithpia": 610000, "quietquill": 22000,
             "habitharbor": 18000, "tinyhabitshop": 140, "webwillow": 52000},
        )

    def test_reel_fixtures_have_plays_and_are_dated_before_mock_now(self) -> None:
        for name in ("apify_discover_keyword_reels_sample.json", "apify_discover_reels_sample.json"):
            for item in _fixture(name):
                with self.subTest(fixture=name, code=item["shortCode"]):
                    self.assertEqual(item["productType"], "clips")
                    self.assertGreater(item["videoPlayCount"], 0)
                    self.assertLessEqual(instagram.parse_ts(item["timestamp"]), research.MOCK_NOW)


class TopQuarterTests(NoNetworkTestCase):
    def test_nearest_rank(self) -> None:
        self.assertEqual(discover.top_quarter([]), 0)
        self.assertEqual(discover.top_quarter([7]), 7)
        self.assertEqual(discover.top_quarter([1, 2, 3, 4]), 3)
        self.assertEqual(discover.top_quarter(list(range(1, 16))), 12)
        self.assertEqual(discover.top_quarter([9, 1, 5, 3, 7, 2, 8, 4, 6]), 7)


class MeasureTests(NoNetworkTestCase):
    def test_metrics_from_known_reels(self) -> None:
        metrics = discover.measure(PIA, NOW, discover.niche_matcher([], ["habits"]))
        self.assertEqual(metrics["reels_measured"], 9)
        self.assertEqual(metrics["top_quarter_plays"], 150000)
        self.assertEqual(metrics["median_plays"], 52000)
        self.assertEqual(metrics["posts_per_week"], 0.7)
        self.assertEqual(metrics["last_post_days"], 1)
        self.assertEqual(metrics["paid_reels"], 1)
        self.assertEqual(metrics["niche_hits"], 1)
        self.assertEqual(metrics["engagement"], round(statistics.median(110 / r["plays"] for r in PIA), 4))
        self.assertEqual([r["url"] for r in metrics["top_reels"]],
                         ["https://example.invalid/H", "https://example.invalid/A"])

    def test_a_full_scrape_measures_cadence_over_its_own_span(self) -> None:
        reels = [_reel(f"R{i}", f"2026-09-{15 - i:02d}", 1000) for i in range(15)]
        self.assertEqual(discover.measure(reels, NOW, lambda *_: False)["posts_per_week"], 7.2)

    def test_no_reels(self) -> None:
        metrics = discover.measure([], NOW, lambda *_: False)
        self.assertEqual(
            (metrics["reels_measured"], metrics["top_quarter_plays"], metrics["median_plays"],
             metrics["last_post_days"], metrics["engagement"], metrics["top_reels"]),
            (0, 0, 0, None, None, []),
        )


class Pass2Tests(NoNetworkTestCase):
    @staticmethod
    def _m(**overrides: Any) -> Dict[str, Any]:
        return dict({"reels_measured": 9, "last_post_days": 1, "top_quarter_plays": 150000}, **overrides)

    def test_reasons_in_order(self) -> None:
        self.assertIsNone(discover.pass2_reason(self._m(), CFG))
        self.assertEqual(
            discover.pass2_reason(self._m(reels_measured=5, last_post_days=40, top_quarter_plays=10), CFG),
            "posts less than every 2 weeks",
        )
        self.assertEqual(discover.pass2_reason(self._m(last_post_days=31, top_quarter_plays=10), CFG),
                         "no reel in 30 days")
        self.assertEqual(discover.pass2_reason(self._m(last_post_days=None), CFG), "no reel in 30 days")
        self.assertEqual(discover.pass2_reason(self._m(top_quarter_plays=4999), CFG),
                         "1 in 4 reels under 5,000 views")
        self.assertIsNone(discover.pass2_reason(self._m(top_quarter_plays=5000, last_post_days=30), CFG))

    def test_the_cadence_dial_sets_the_floor_and_the_words(self) -> None:
        weekly = dict(CFG, discover_post_every_days=7)
        self.assertEqual(discover.pass2_reason(self._m(reels_measured=11), weekly), "posts less than every week")
        self.assertIsNone(discover.pass2_reason(self._m(reels_measured=12), weekly))
        self.assertEqual(
            [discover.cadence_words(days) for days in (1, 7, 10, 14, 28)],
            ["every day", "every week", "every 10 days", "every 2 weeks", "every 4 weeks"],
        )


class TierTests(NoNetworkTestCase):
    def test_boundary_and_order(self) -> None:
        self.assertEqual(discover.tier_of(49999, CFG), "rising")
        self.assertEqual(discover.tier_of(50000, CFG), "established")
        self.assertEqual(discover.tier_of(None, CFG), "rising")
        rows = [{"handle": "b", "tier": "rising", "top_quarter_plays": 9},
                {"handle": "a", "tier": "established", "top_quarter_plays": 1},
                {"handle": "c", "tier": "rising", "top_quarter_plays": 9}]
        self.assertEqual([row["handle"] for row in discover.order_candidates(rows)], ["a", "b", "c"])


class BreakoutTests(NoNetworkTestCase):
    def test_window_ratio_paid_and_fields(self) -> None:
        rows = [{"handle": "x", "tier": "established", "followers": 610000,
                 "median_plays": 52000, "reels_measured": 9}]
        found = discover.find_breakouts(rows, {"x": PIA}, CFG, NOW)
        self.assertEqual([(b["shortCode"], b["ratio"]) for b in found], [("A", 2.88), ("E", 2.12)])
        self.assertEqual(set(found[0]), {"shortCode", "url", "owner", "tier", "timestamp", "plays", "ratio",
                                         "caption", "hashtags", "videoUrl", "displayUrl", "duration_s"})
        self.assertEqual((found[0]["owner"], found[0]["tier"]), ("x", "established"))

    def test_three_per_creator_twenty_overall(self) -> None:
        rows = [{"handle": f"c{n}", "tier": "rising", "followers": 20000, "median_plays": 1000,
                 "reels_measured": 9} for n in range(8)]
        reels = {f"c{n}": [_reel(f"c{n}r{i}", "2026-09-10", 3000 + i) for i in range(5)] for n in range(8)}
        found = discover.find_breakouts(rows, reels, CFG, NOW)
        self.assertEqual(len(found), 20)
        self.assertTrue(all(count <= 3 for count in collections.Counter(b["owner"] for b in found).values()))


class GroupReelsTests(NoNetworkTestCase):
    def test_only_asked_handles_and_no_pinned(self) -> None:
        grouped = discover.group_reels(_fixture("apify_discover_reels_sample.json"), ["planwithpia", "slowsam"])
        self.assertEqual(sorted(grouped), ["planwithpia", "slowsam"])
        self.assertEqual(len(grouped["planwithpia"]), 9)
        self.assertEqual(len(grouped["slowsam"]), 4)


class EstimateAndTableTests(NoNetworkTestCase):
    def test_estimate_counts_every_run(self) -> None:
        cost = discover.estimate(dict(CFG), n_keywords=3, n_hashtags=0, n_seeds=5, n_web=30)
        self.assertEqual((cost["details_usd"], cost["total_usd"]), (0.2025, 1.1745))

    def _doc(self) -> Dict[str, Any]:
        return {
            "settings": {"min_followers": 10000, "min_views": 5000, "post_every_days": 14,
                         "shortlist": 20, "candidates": 25, "established_at": 50000},
            "candidates": [
                {"handle": "planwithpia", "tier": "established", "followers": 610000,
                 "top_quarter_plays": 150000, "posts_per_week": 0.7,
                 "sources": ["hashtag:habits", "keyword:habit coach"]},
                {"handle": "coachcora", "tier": "rising", "followers": 40000,
                 "top_quarter_plays": 41000, "posts_per_week": 0.5, "sources": ["keyword:habit coach"]},
            ],
            "dropped": [{"handle": "a", "reason": "not_found"}, {"handle": "b", "reason": "not_found"},
                        {"handle": "c", "reason": "under 10,000 followers"}],
            "breakouts": [{}, {}, {}],
            "partial": False,
            "cost_estimate_usd": 1.15,
            "warnings": [],
        }

    def test_render_table(self) -> None:
        self.assertEqual(discover.render_table(self._doc()), "\n".join([
            "Held to: 10,000+ followers, a reel at least every 2 weeks, 1 in 4 reels at 5,000+ views.",
            "Established (50,000+ followers):",
            " 1. @planwithpia  610,000 followers  1 in 4 reels: 150,000 views  0.7 reels a week"
            "  found via hashtag:habits, keyword:habit coach",
            "Rising (10,000 to 50,000 followers):",
            " 2. @coachcora  40,000 followers  1 in 4 reels: 41,000 views  0.5 reels a week"
            "  found via keyword:habit coach",
            "Left out 3: not found (2), under 10,000 followers (1).",
            "Beating their own average in the last 30 days: 3 reels.",
        ]))

    def test_render_table_when_nobody_passes_and_the_run_was_partial(self) -> None:
        doc = dict(self._doc(), candidates=[], dropped=[], breakouts=[], partial=True)
        lines = discover.render_table(doc).splitlines()
        self.assertIn("Nobody cleared the bar in the time there was. Run it again to finish.", lines)
        self.assertIn("Some accounts were not checked or measured in time. Run it again to finish them.", lines)
        self.assertNotIn("lower settings", "\n".join(lines))
        self.assertNotIn("—", "\n".join(lines))

    def test_render_table_when_nobody_passes_a_complete_run(self) -> None:
        doc = dict(self._doc(), candidates=[], dropped=[], breakouts=[])
        lines = discover.render_table(doc).splitlines()
        self.assertIn("Nobody cleared the bar. Try other keyword phrases, more web finds, or lower settings.",
                      lines)
        self.assertFalse(any("in time" in line for line in lines))

    def test_render_table_when_a_partial_run_has_candidates(self) -> None:
        lines = discover.render_table(dict(self._doc(), partial=True)).splitlines()
        self.assertEqual(lines[-1],
                         "Some accounts were not checked or measured in time. Run it again to finish them.")
        self.assertFalse(any(line.startswith("Nobody") for line in lines))

    def test_render_table_with_an_unknown_follower_count(self) -> None:
        doc = self._doc()
        doc["candidates"][1]["followers"] = None
        lines = discover.render_table(doc).splitlines()
        self.assertIn(" 2. @coachcora  followers unknown  1 in 4 reels: 41,000 views  0.5 reels a week"
                      "  found via keyword:habit coach", lines)

    def test_result_line(self) -> None:
        self.assertEqual(discover.result_line(self._doc(), Path("/p")), {
            "discovery_path": str(Path("/p") / ".contentos" / "discovery.json"),
            "candidates": 2, "established": 1, "rising": 1, "dropped": 3, "breakouts": 3,
            "cost_estimate_usd": 1.15, "partial": False, "warnings": [], "search_instagram": True,
        })


class CheckOnlyTests(NoNetworkTestCase):
    WEB_ORDER = ("webwillow", "madeupmaya", "focusfern", "slowsam", "photophoebe")

    def test_check_only_checks_the_web_finds_and_nothing_else(self) -> None:
        with temp_project() as project:
            _seed_project(project)
            transport = _mock_transport()
            doc, lines = _run_mock(project, transport, search_instagram=False)
        posts = [call for call in transport.calls if call["method"] == "POST"]
        self.assertFalse(any(apify.KEYWORD_ACTOR_RUNS_PATH in call["url"] for call in posts))
        bodies = [call["json_body"] or {} for call in posts]
        self.assertFalse(any("explore/tags" in json.dumps(body) for body in bodies))
        details = [body for body in bodies if body.get("resultsType") == "details"]
        self.assertEqual(len(details), 1)
        # The watch list (habitlab) is not looked up: it only feeds the similar-accounts step.
        self.assertEqual(details[0]["directUrls"],
                         [f"https://www.instagram.com/{handle}/" for handle in self.WEB_ORDER])
        self.assertIs(doc["search_instagram"], False)
        self.assertEqual(doc["candidates"], [])
        self.assertEqual({item["handle"]: item["reason"] for item in doc["dropped"]},
                         {handle: EXPECTED_DROPPED[handle] for handle in self.WEB_ORDER})
        self.assertIn("Step 1 of 3: checking Claude's finds.", lines)
        self.assertIn("Step 2 of 3: skipped, checking Claude's finds only.", lines)

    def test_a_web_candidate_keeps_its_reason_and_source_title(self) -> None:
        find = {"handle": "planwithpia", "source_url": "https://example.invalid/list",
                "source_title": "Planners to follow", "reason": "Weekly planning reels"}
        with temp_project() as project:
            _seed_project(project)
            doc, _lines = _run_mock(project, _mock_transport(), search_instagram=False,
                                    handles_file=None, web_entries=[find])
        row = doc["candidates"][0]
        self.assertEqual((row["handle"], row["reason"], row["source_title"]),
                         ("planwithpia", "Weekly planning reels", "Planners to follow"))

    def test_search_candidates_have_no_reason(self) -> None:
        with temp_project() as project:
            _seed_project(project)
            doc, _lines = _run_mock(project, _mock_transport())
        self.assertIs(doc["search_instagram"], True)
        for row in doc["candidates"]:
            with self.subTest(handle=row["handle"]):
                self.assertIsNone(row["reason"])
                self.assertIsNone(row["source_title"])

    def test_check_only_needs_a_web_handle(self) -> None:
        with temp_project() as project:
            _seed_project(project)
            with self.assertRaises(discover.DiscoverError) as ctx:
                _run_mock(project, _mock_transport(), search_instagram=False, handles_file=None, web_entries=[])
        self.assertEqual(ctx.exception.exit_code, codes.EXIT_USAGE)
        self.assertEqual(str(ctx.exception), discover.CHECK_ONLY_NEEDS_HANDLES)

    def test_check_only_estimate_counts_the_finds_and_their_reels(self) -> None:
        many = discover.estimate(dict(CFG), 3, 2, 5, 30, search_instagram=False)
        self.assertAlmostEqual(many["total_usd"], (30 + 20 * 15) * apify.PRICE_PER_RESULT, places=4)
        few = discover.estimate(dict(CFG), 0, 0, 0, 4, search_instagram=False)
        self.assertAlmostEqual(few["total_usd"], (4 + 4 * 15) * apify.PRICE_PER_RESULT, places=4)
        self.assertEqual(discover.estimate(dict(CFG), 3, 2, 5, 30),
                         discover.estimate(dict(CFG), 3, 2, 5, 30, search_instagram=True))

    def test_the_check_only_flag_on_the_command(self) -> None:
        with temp_project() as project:
            _seed_project(project)
            code, out, _err = _main(_mock_args(project, "--mock", "--yes", "--check-only"))
        self.assertEqual(code, codes.EXIT_OK)
        self.assertIn("Checked Claude's finds only.", out)
        result = json.loads(out.strip().splitlines()[-1][len("RESULT "):])
        self.assertIs(result["search_instagram"], False)

    def test_an_old_discovery_file_reads_as_a_full_scan(self) -> None:
        doc = {"candidates": [], "dropped": [], "breakouts": [], "cost_estimate_usd": 1.0,
               "partial": False, "warnings": []}
        self.assertIs(discover.result_line(doc, Path("/tmp/p"))["search_instagram"], True)


if __name__ == "__main__":
    unittest.main()
