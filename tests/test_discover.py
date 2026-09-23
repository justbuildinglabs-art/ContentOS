"""Tests for `lib/discover.py` and `contentos.py discover`: finding accounts for a creator."""
from __future__ import annotations

import json
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


def _discover_args(project: Path, *extra: str) -> List[str]:
    return [
        "discover", "--project", str(project), "--hashtags", "habits,#Productivity",
        "--keywords", "habit coach", *extra,
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

    def test_profile_search_input(self) -> None:
        self.assertEqual(
            apify.build_profile_search_input("habit coach", 10),
            {"search": "habit coach", "searchType": "profile", "searchLimit": 10,
             "resultsType": "details"},
        )

    def test_discover_cost(self) -> None:
        estimate = apify.estimate_discover_cost(
            n_hashtags=2, reels_per_hashtag=30, n_keywords=1, search_limit=10,
            n_candidates=25, n_web_handles=3,
        )
        self.assertEqual(estimate["hashtag_reels_usd"], 0.162)
        self.assertEqual(estimate["profile_search_usd"], 0.027)
        self.assertEqual(estimate["details_usd"], 0.1026)
        self.assertEqual(estimate["total_usd"], 0.2916)


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


class AggregateAuthorsTests(NoNetworkTestCase):
    def test_groups_reels_by_author_and_skips_paid_photos_and_errors(self) -> None:
        authors = discover.aggregate_authors(_fixture("apify_hashtag_reels_sample.json"))

        self.assertEqual(
            sorted(authors), ["focusfern", "goneghost", "planwithpia", "quietquill"]
        )
        fern = authors["focusfern"]
        self.assertEqual(fern["reels_seen"], 2)
        self.assertEqual(fern["hashtags_hit"], ["habits", "productivity"])
        self.assertEqual(fern["best_plays"], 420000)
        self.assertEqual(fern["median_plays"], 365000)
        self.assertEqual(len(fern["sample_captions"]), 2)
        # brandbox's only reel is a paid partnership.
        self.assertNotIn("brandbox", authors)


class MockDiscoverTests(NoNetworkTestCase):
    def test_mock_run_ranks_verified_candidates(self) -> None:
        with temp_project() as project:
            code, out, _err = _main(
                _discover_args(
                    project, "--handles-file", str(FIXTURES_DIR / "discovery-web.sample.json"),
                    "--mock", "--yes",
                )
            )
            self.assertEqual(code, codes.EXIT_OK)
            doc = store.read_json(store.contentos_dir(project) / "discovery.json")

        self.assertEqual(
            [row["handle"] for row in doc["candidates"]],
            ["focusfern", "planwithpia", "webwillow", "habitharbor"],
        )
        fern = doc["candidates"][0]
        self.assertTrue(fern["small_account"])
        self.assertEqual(fern["followers"], 9400)
        self.assertEqual(
            fern["sources"],
            ["hashtag:habits", "hashtag:productivity", "web:https://example.invalid/top-10"],
        )
        self.assertEqual(doc["candidates"][3]["sources"], ["keyword:habit coach"])
        self.assertEqual(doc["candidates"][2]["score"], 0)

        dropped = {item["handle"]: item["reason"] for item in doc["dropped"]}
        self.assertEqual(
            dropped,
            {
                "quietquill": "private",
                "goneghost": "not_found",
                "madeupmaya": "not_found",
                "tinyhabitshop": "under 10000 followers",
            },
        )
        self.assertTrue(any("tiktok.com" in warning for warning in doc["warnings"]))
        self.assertEqual(doc["hashtags"], ["habits", "productivity"])
        self.assertEqual(doc["mode"], "mock")

        result_line = [line for line in out.splitlines() if line.startswith("RESULT ")][-1]
        result = json.loads(result_line[len("RESULT "):])
        self.assertEqual(result["candidates"], 4)
        self.assertIn("@focusfern", out)
        self.assertNotIn("best reel 0 plays", out)
        self.assertIn("@webwillow  52,000 followers  no reel seen under your hashtags", out)
        self.assertNotIn("—", out)

    def test_works_before_setup_and_with_no_competitors_yet(self) -> None:
        with temp_project() as project:
            self.assertFalse((store.contentos_dir(project) / "config.json").exists())
            code, _out, _err = _main(_discover_args(project, "--mock", "--yes"))
            self.assertEqual(code, codes.EXIT_OK)

    def test_hashtag_authors_are_never_dropped_for_size(self) -> None:
        with temp_project() as project:
            config_dir = store.contentos_dir(project)
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps({"discover_min_followers": 100000}), encoding="utf-8"
            )
            _main(_discover_args(project, "--mock", "--yes"))
            doc = store.read_json(config_dir / "discovery.json")
        self.assertEqual([row["handle"] for row in doc["candidates"]], ["focusfern", "planwithpia"])


class DiscoverGateTests(NoNetworkTestCase):
    def test_estimate_only_exits_3_with_the_estimate(self) -> None:
        with temp_project() as project:
            code, out, _err = _main(_discover_args(project, "--mock", "--estimate-only"))
            self.assertEqual(code, 3)
            self.assertEqual(json.loads(out)["total_usd"], 0.2835)
            self.assertFalse((store.contentos_dir(project) / "discovery.json").exists())

    def test_missing_yes_exits_3(self) -> None:
        with temp_project() as project:
            code, _out, _err = _main(_discover_args(project, "--mock"))
            self.assertEqual(code, 3)

    def test_over_the_cap_exits_6(self) -> None:
        with temp_project() as project:
            config_dir = store.contentos_dir(project)
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps({"apify_max_charge_usd": 0.05}), encoding="utf-8"
            )
            code, _out, _err = _main(_discover_args(project, "--mock", "--yes"))
            self.assertEqual(code, 6)

    def test_no_key_exits_4(self) -> None:
        no_keys = env.Keys(apify=None, source=None, warnings=[])
        with temp_project() as project:
            with mock.patch.object(env, "resolve_keys", return_value=no_keys):
                code, _out, _err = _main(_discover_args(project, "--yes"))
            self.assertEqual(code, 4)

    def test_no_usable_hashtag_exits_2(self) -> None:
        with temp_project() as project:
            code, _out, err = _main(
                ["discover", "--project", str(project), "--hashtags", "///", "--mock", "--yes"]
            )
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertIn("hashtag", err)

    def test_bad_handles_file_exits_2(self) -> None:
        with temp_project() as project:
            bad = Path(project) / "web.json"
            bad.write_text("{not json", encoding="utf-8")
            code, _out, _err = _main(
                _discover_args(project, "--handles-file", str(bad), "--mock", "--yes")
            )
            self.assertEqual(code, codes.EXIT_USAGE)


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
        self.assertEqual(entries, [{"handle": "webwillow", "source_url": "u1"},
                                   {"handle": "slowsam", "source_url": ""}])
        self.assertEqual(len(warnings), 1)
        with self.assertRaises(discover.DiscoverError):
            discover.normalize_web_entries({"handle": "x"})

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
        photos_only = [_post("2026-09-14", reel=False)]
        self.assertEqual(discover.pass1_reason(self._row(latest=photos_only), CFG, NOW), "no reel in 30 days")

    def test_missing_latest_posts_skips_the_activity_check(self) -> None:
        self.assertIsNone(discover.pass1_reason(self._row(latest=[]), CFG, NOW))


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
            {"handle": "big1", "followers": 610000, "niche_hit": True},
            {"handle": "big2", "followers": 52000, "niche_hit": True},
            {"handle": "small1", "followers": 40000, "niche_hit": True},
            {"handle": "small2", "followers": 18000, "niche_hit": True},
            {"handle": "offbig", "followers": 900000, "niche_hit": False},
            {"handle": "offsmall", "followers": 25000, "niche_hit": False},
        ]
        self.assertEqual(discover.shortlist(rows, 10, 50000),
                         ["big1", "small1", "big2", "small2", "offbig", "offsmall"])
        self.assertEqual(discover.shortlist(rows, 3, 50000), ["big1", "small1", "big2"])


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


if __name__ == "__main__":
    unittest.main()
