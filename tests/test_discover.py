"""Tests for `lib/discover.py` and `contentos.py discover`: finding accounts for a creator."""
from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, List, Sequence, Tuple
from unittest import mock

from tests.helpers import REPO_ROOT, NoNetworkTestCase, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import apify, codes, discover, env, store  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"


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
                "tinyhabitshop": "under 1000 followers",
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


if __name__ == "__main__":
    unittest.main()
