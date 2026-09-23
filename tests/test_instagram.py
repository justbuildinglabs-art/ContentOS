"""Tests for lib/instagram.py: Apify item -> canonical Reel/profile shape.

Every downstream stage (Task 9 baselines/scoring, the `research` command,
downloads, the director prompt) trusts the canonical shape this module
produces, so these tests pin the exact keys and defaults, not just "it
returns something." `test_fixture_contains_required_edge_cases` and
`test_normalize_dataset_matches_fixture_handles` also load the two
`fixtures/apify_*_sample.json` files that `--mock` runs (Task 10) will
serve through `apify.FixtureTransport`, so a change to either file that
drops a required edge case fails loudly here first.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from tests.helpers import NoNetworkTestCase, REPO_ROOT

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so this import must come after it.
from lib import instagram  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"

# Matches the controller's ruling: tests needing "now" for lookback math use
# 2026-09-16 UTC explicitly rather than the real current time.
NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
LOOKBACK_CUTOFF_90D = NOW - timedelta(days=90)


def _clip_item(**overrides: Any) -> Dict[str, Any]:
    """A minimal, valid raw Apify "reels" item; override fields per test."""
    item: Dict[str, Any] = {
        "shortCode": "SC000",
        "url": "https://www.instagram.com/reel/SC000/",
        "caption": "caption text #tag",
        "hashtags": ["tag"],
        "mentions": [],
        "ownerUsername": "someaccount",
        "timestamp": "2026-08-15T12:00:00.000Z",
        "likesCount": 10,
        "commentsCount": 1,
        "videoPlayCount": 100,
        "videoViewCount": 120,
        "videoDuration": 20.0,
        "videoUrl": "https://example.invalid/videos/SC000.mp4",
        "displayUrl": "https://example.invalid/covers/SC000.jpg",
        "productType": "clips",
        "isPinned": False,
        "latestComments": [],
        "musicInfo": None,
    }
    item.update(overrides)
    return item


def _load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class NormalizeReelTests(NoNetworkTestCase):
    def test_maps_core_fields(self) -> None:
        item = {
            "shortCode": "ABC123",
            "url": "https://www.instagram.com/reel/ABC123/",
            "caption": "Testing this out #test #demo",
            "hashtags": ["test", "demo"],
            "mentions": ["@friend"],
            "ownerUsername": "SproutApp",
            "timestamp": "2026-09-01T10:00:00.000Z",
            "likesCount": 250,
            "commentsCount": 12,
            "videoPlayCount": 5000,
            "videoViewCount": 5600,
            "videoDuration": 24.5,
            "videoUrl": "https://example.invalid/videos/ABC123.mp4",
            "displayUrl": "https://example.invalid/covers/ABC123.jpg",
            "productType": "clips",
            "isPinned": False,
            "latestComments": [
                {"ownerUsername": "commenter1", "text": "love this", "likesCount": 4},
                {"ownerUsername": "commenter2", "text": "need this", "likesCount": None},
            ],
            "musicInfo": {
                "musicName": "Original audio",
                "artistName": "SproutApp",
                "musicId": "1",
            },
        }

        result = instagram.normalize_reel(item)

        self.assertEqual(
            result,
            {
                "shortCode": "ABC123",
                "url": "https://www.instagram.com/reel/ABC123/",
                "ownerUsername": "SproutApp",
                "timestamp": "2026-09-01T10:00:00+00:00",
                "caption": "Testing this out #test #demo",
                "hashtags": ["test", "demo"],
                "mentions": ["@friend"],
                "plays": 5000,
                "plays_source": "videoPlayCount",
                "likes": 250,
                "comments": 12,
                "duration_s": 24.5,
                "videoUrl": "https://example.invalid/videos/ABC123.mp4",
                "displayUrl": "https://example.invalid/covers/ABC123.jpg",
                "isPinned": False,
                "latestComments": [
                    {"ownerUsername": "commenter1", "text": "love this", "likesCount": 4},
                    {"ownerUsername": "commenter2", "text": "need this", "likesCount": None},
                ],
                "musicInfo": {
                    "musicName": "Original audio",
                    "artistName": "SproutApp",
                    "musicId": "1",
                },
                "paid_partnership": False,
                "paid_signals": [],
            },
        )
        self.assertEqual(
            set(result),
            {
                "shortCode", "url", "ownerUsername", "timestamp", "caption", "hashtags",
                "mentions", "plays", "plays_source", "likes", "comments", "duration_s",
                "videoUrl", "displayUrl", "isPinned", "latestComments", "musicInfo",
                "paid_partnership", "paid_signals",
            },
        )

    def test_flags_a_paid_partnership_from_the_scraper_label(self) -> None:
        result = instagram.normalize_reel(_clip_item(paidPartnership=True))
        self.assertIs(result["paid_partnership"], True)
        self.assertEqual(result["paid_signals"], ["label:paid_partnership"])

    def test_flags_a_paid_partnership_from_caption_and_hashtags(self) -> None:
        result = instagram.normalize_reel(
            _clip_item(caption="New gear. Sponsored by Acme #ad", hashtags=["ad"])
        )
        self.assertIs(result["paid_partnership"], True)
        self.assertEqual(result["paid_signals"], ["hashtag:ad", "caption:sponsored by"])

    def test_defaults_and_url_fallback_on_a_sparse_item(self) -> None:
        sparse = {
            "shortCode": "SPARSE1",
            "ownerUsername": "sparseacct",
            "timestamp": "2026-08-01T00:00:00Z",
            "productType": "clips",
        }

        result = instagram.normalize_reel(sparse)

        self.assertEqual(result["url"], "https://www.instagram.com/reel/SPARSE1/")
        self.assertEqual(result["caption"], "")
        self.assertEqual(result["hashtags"], [])
        self.assertEqual(result["mentions"], [])
        self.assertEqual(result["comments"], 0)
        self.assertIsNone(result["plays"])
        self.assertIsNone(result["plays_source"])
        self.assertIsNone(result["likes"])
        self.assertIsNone(result["duration_s"])
        self.assertIsNone(result["videoUrl"])
        self.assertIsNone(result["displayUrl"])
        self.assertIsNone(result["musicInfo"])
        self.assertEqual(result["latestComments"], [])
        self.assertFalse(result["isPinned"])

    def test_latest_comments_missing_fields_become_none(self) -> None:
        item = _clip_item(latestComments=[{"text": "no owner or likes here"}])

        result = instagram.normalize_reel(item)

        self.assertEqual(
            result["latestComments"],
            [{"ownerUsername": None, "text": "no owner or likes here", "likesCount": None}],
        )

    def test_hidden_likes_minus_one_becomes_none(self) -> None:
        hidden = _clip_item(shortCode="LK1", likesCount=-1)
        self.assertIsNone(instagram.normalize_reel(hidden)["likes"])

        missing = _clip_item(shortCode="LK2")
        missing.pop("likesCount")
        self.assertIsNone(instagram.normalize_reel(missing)["likes"])

        negative_other = _clip_item(shortCode="LK3", likesCount=-42)
        self.assertIsNone(instagram.normalize_reel(negative_other)["likes"])

        present = _clip_item(shortCode="LK4", likesCount=42)
        self.assertEqual(instagram.normalize_reel(present)["likes"], 42)

        zero = _clip_item(shortCode="LK5", likesCount=0)
        self.assertEqual(instagram.normalize_reel(zero)["likes"], 0)

    def test_skips_non_clips_and_pinned(self) -> None:
        non_clip = _clip_item(shortCode="NC1", productType="image")
        pinned = _clip_item(shortCode="PIN1", isPinned=True)
        error_item = {"error": "boom", "inputUrl": "https://www.instagram.com/x/"}
        no_shortcode = _clip_item()
        no_shortcode.pop("shortCode")

        self.assertIsNone(instagram.normalize_reel(non_clip))
        self.assertIsNone(instagram.normalize_reel(pinned))
        self.assertIsNone(instagram.normalize_reel(error_item))
        self.assertIsNone(instagram.normalize_reel(no_shortcode))

        # Sanity check: an otherwise-identical item without those problems
        # does normalize, so the None results above are the field, not a
        # broken fixture.
        valid = _clip_item(shortCode="OK1")
        self.assertIsNotNone(instagram.normalize_reel(valid))

    def test_latest_comments_skips_non_dict_entries(self) -> None:
        item = _clip_item(
            latestComments=[
                {"ownerUsername": "keepme", "text": "hi", "likesCount": 2},
                "a bare string comment",
                None,
                42,
                ["nested", "list"],
            ]
        )

        result = instagram.normalize_reel(item)

        self.assertEqual(
            result["latestComments"],
            [{"ownerUsername": "keepme", "text": "hi", "likesCount": 2}],
        )

        non_list = _clip_item(latestComments="not a list at all")
        self.assertEqual(instagram.normalize_reel(non_list)["latestComments"], [])

        numberish = _clip_item(latestComments=12345)
        self.assertEqual(instagram.normalize_reel(numberish)["latestComments"], [])

        all_garbage = _clip_item(latestComments=["oops", 1, None, [1, 2]])
        self.assertEqual(instagram.normalize_reel(all_garbage)["latestComments"], [])

    def test_drops_a_reel_whose_timestamp_is_missing_or_unparseable(self) -> None:
        # A reel with no usable posting time cannot be placed in the
        # lookback window or the baseline, so it is dropped the same way
        # a reel without a shortCode is. It must never abort the run.
        null_ts = _clip_item(shortCode="NULLTS", timestamp=None)
        self.assertIsNone(instagram.normalize_reel(null_ts))

        no_ts = _clip_item(shortCode="NOTS")
        del no_ts["timestamp"]
        self.assertIsNone(instagram.normalize_reel(no_ts))

        junk_ts = _clip_item(shortCode="JUNKTS", timestamp="not a date")
        self.assertIsNone(instagram.normalize_reel(junk_ts))

        listy_ts = _clip_item(shortCode="LISTTS", timestamp=["2026-08-15T12:00:00Z"])
        self.assertIsNone(instagram.normalize_reel(listy_ts))

        huge_ts = _clip_item(shortCode="HUGETS", timestamp=10 ** 20)
        self.assertIsNone(instagram.normalize_reel(huge_ts))

    def test_dataset_still_normalizes_around_a_reel_with_a_bad_timestamp(self) -> None:
        reels, _profiles, status = instagram.normalize_dataset(
            [
                _clip_item(shortCode="GOOD1", ownerUsername="someaccount"),
                _clip_item(shortCode="BADTS", ownerUsername="someaccount", timestamp=None),
            ],
            [],
            ["someaccount"],
        )

        self.assertEqual([reel["shortCode"] for reel in reels], ["GOOD1"])
        self.assertEqual(status["someaccount"], "ok")


class PickPlaysTests(NoNetworkTestCase):
    def test_plays_prefers_playcount_then_viewcount_then_none(self) -> None:
        self.assertEqual(
            instagram.pick_plays({"videoPlayCount": 500, "videoViewCount": 900}),
            (500, "videoPlayCount"),
        )
        self.assertEqual(
            instagram.pick_plays({"videoPlayCount": 0, "videoViewCount": 900}),
            (900, "videoViewCount"),
        )
        self.assertEqual(
            instagram.pick_plays({"videoPlayCount": 0, "videoViewCount": 0}),
            (None, None),
        )
        self.assertEqual(instagram.pick_plays({}), (None, None))
        self.assertEqual(
            instagram.pick_plays({"videoPlayCount": -5, "videoViewCount": -10}),
            (None, None),
        )


class NumericCoercionTests(NoNetworkTestCase):
    def test_numeric_strings_are_coerced_and_garbage_becomes_missing(self) -> None:
        # pick_plays: numeric strings behave like the equivalent numbers.
        self.assertEqual(
            instagram.pick_plays({"videoPlayCount": "500", "videoViewCount": "900"}),
            (500, "videoPlayCount"),
        )
        self.assertEqual(
            instagram.pick_plays({"videoPlayCount": "not a number", "videoViewCount": "900"}),
            (900, "videoViewCount"),
        )
        self.assertEqual(
            instagram.pick_plays({"videoPlayCount": "not a number", "videoViewCount": "nope"}),
            (None, None),
        )
        self.assertEqual(
            instagram.pick_plays({"videoPlayCount": "  250  "}), (250, "videoPlayCount")
        )

        # normalize_reel: likes/comments/duration all route through the same
        # coercion, so a malformed field degrades to "missing" rather than
        # raising and aborting the whole item.
        item = _clip_item(
            videoPlayCount="250",
            videoViewCount="10",
            likesCount="100",
            commentsCount=None,
            videoDuration="twenty",
        )
        result = instagram.normalize_reel(item)
        self.assertEqual(result["plays"], 250)
        self.assertEqual(result["plays_source"], "videoPlayCount")
        self.assertEqual(result["likes"], 100)
        self.assertEqual(result["comments"], 0)
        self.assertIsNone(result["duration_s"])

        garbage_likes = _clip_item(likesCount="not a number")
        self.assertIsNone(instagram.normalize_reel(garbage_likes)["likes"])

        garbage_comments = _clip_item(commentsCount="not a number")
        self.assertEqual(instagram.normalize_reel(garbage_comments)["comments"], 0)

        # normalize_profile: followers/posts route through the same helper.
        profile = instagram.normalize_profile(
            {
                "username": "acct",
                "followersCount": "12000",
                "postsCount": "not a number",
                "private": False,
            }
        )
        self.assertEqual(profile["followers"], 12000)
        self.assertIsNone(profile["posts"])

        # A bool is never mistaken for a legitimate numeric count.
        self.assertEqual(instagram.pick_plays({"videoPlayCount": True}), (None, None))


class ParseTsTests(NoNetworkTestCase):
    def test_parse_ts_zulu_offset_and_epoch(self) -> None:
        self.assertEqual(
            instagram.parse_ts("2026-09-01T10:00:00Z"),
            datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            instagram.parse_ts("2026-09-01T10:00:00.500Z"),
            datetime(2026, 9, 1, 10, 0, 0, 500000, tzinfo=timezone.utc),
        )
        self.assertEqual(
            instagram.parse_ts("2026-09-01T05:00:00-05:00"),
            datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            instagram.parse_ts("2026-09-01T10:00:00"),
            datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(instagram.parse_ts(0), datetime(1970, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(instagram.parse_ts(0.0), datetime(1970, 1, 1, tzinfo=timezone.utc))

        zulu = instagram.parse_ts("2026-09-01T10:00:00Z")
        self.assertEqual(zulu.isoformat(), "2026-09-01T10:00:00+00:00")
        for value in (
            instagram.parse_ts("2026-09-01T10:00:00Z"),
            instagram.parse_ts("2026-09-01T05:00:00-05:00"),
            instagram.parse_ts("2026-09-01T10:00:00"),
            instagram.parse_ts(0),
        ):
            self.assertEqual(value.utcoffset(), timedelta(0))


class NormalizeProfileTests(NoNetworkTestCase):
    def test_profile_followers_and_private(self) -> None:
        profile = instagram.normalize_profile(
            {
                "username": "habitlab",
                "followersCount": 180000,
                "postsCount": 640,
                "verified": True,
                "private": False,
            }
        )
        self.assertEqual(
            profile,
            {
                "username": "habitlab",
                "followers": 180000,
                "posts": 640,
                "verified": True,
                "private": False,
                "url": "https://www.instagram.com/habitlab/",
            },
        )

        private_profile = instagram.normalize_profile(
            {
                "username": "privatepeople",
                "followersCount": 3000,
                "postsCount": 45,
                "verified": False,
                "private": True,
            }
        )
        self.assertTrue(private_profile["private"])
        self.assertEqual(private_profile["followers"], 3000)

        sparse = instagram.normalize_profile({"username": "noreach", "private": False})
        self.assertIsNone(sparse["followers"])
        self.assertIsNone(sparse["posts"])
        self.assertFalse(sparse["verified"])
        self.assertEqual(sparse["url"], "https://www.instagram.com/noreach/")

        self.assertIsNone(instagram.normalize_profile({"error": "not found"}))
        self.assertIsNone(instagram.normalize_profile({"followersCount": 10}))


class DedupeTests(NoNetworkTestCase):
    def test_dedupes_by_shortcode(self) -> None:
        first = instagram.normalize_reel(_clip_item(shortCode="DUP1", likesCount=10))
        duplicate = instagram.normalize_reel(_clip_item(shortCode="DUP1", likesCount=999))
        other = instagram.normalize_reel(_clip_item(shortCode="DUP2", likesCount=5))

        deduped = instagram.dedupe_by_shortcode([first, duplicate, other])

        self.assertEqual([r["shortCode"] for r in deduped], ["DUP1", "DUP2"])
        self.assertEqual(deduped[0]["likes"], 10)  # first occurrence wins

    def test_dedupe_is_applied_inside_normalize_dataset(self) -> None:
        reel_items = [
            _clip_item(shortCode="DUP1", ownerUsername="someaccount"),
            _clip_item(shortCode="DUP1", ownerUsername="someaccount"),
            _clip_item(shortCode="DUP2", ownerUsername="someaccount"),
        ]

        reels, _, _ = instagram.normalize_dataset(reel_items, [], ["someaccount"])

        self.assertEqual(sorted(r["shortCode"] for r in reels), ["DUP1", "DUP2"])


class NormalizeDatasetTests(NoNetworkTestCase):
    def test_skips_error_items_and_records_account_status(self) -> None:
        reel_items = [
            _clip_item(shortCode="OK1", ownerUsername="acct_ok"),
            _clip_item(shortCode="OK2", ownerUsername="acct_ok"),
            _clip_item(shortCode="OK3", ownerUsername="CamelCaseAcct"),
            _clip_item(shortCode="OK4", ownerUsername="mixedhandle"),
            {"error": "Profile not found", "inputUrl": "https://www.instagram.com/acct_missing/"},
            {
                "error": "Rate limited, try again later",
                "inputUrl": "https://www.instagram.com/acct_broken/",
            },
        ]
        profile_items = [
            {
                "username": "acct_ok",
                "followersCount": 1000,
                "postsCount": 20,
                "verified": False,
                "private": False,
            },
            {
                "username": "acct_empty",
                "followersCount": 500,
                "postsCount": 5,
                "verified": False,
                "private": False,
            },
            {
                "username": "acct_private",
                "followersCount": 800,
                "postsCount": 10,
                "verified": False,
                "private": True,
            },
        ]
        handles = [
            "acct_ok",
            "acct_empty",
            "acct_missing",
            "acct_broken",
            "acct_private",
            "acct_silent",
            "camelcaseacct",
            # Mixed-case handle matched against a lowercase reel owner: the
            # match is case-insensitive, but the output key must preserve
            # exactly the case `handles` gave, not the reel's casing.
            "MixedHandle",
        ]

        reels, profiles, account_status = instagram.normalize_dataset(
            reel_items, profile_items, handles
        )

        self.assertEqual(len(reels), 4)
        self.assertEqual(
            account_status,
            {
                "acct_ok": "ok",
                "acct_empty": "empty",
                "acct_missing": "not_found",
                "acct_broken": "error",
                "acct_private": "private",
                "acct_silent": "empty",
                "camelcaseacct": "ok",
                "MixedHandle": "ok",
            },
        )
        self.assertIn("acct_ok", profiles)
        self.assertIn("acct_private", profiles)
        self.assertTrue(profiles["acct_private"]["private"])

    def test_error_item_matches_exact_handle_not_prefix(self) -> None:
        # A valid reel for "sproutapp" plus an error item whose inputUrl
        # names the unrelated handle "sproutapp2" must not bleed the error
        # onto "sproutapp" just because one handle is a string-prefix of
        # the other's inputUrl.
        reel_items = [
            _clip_item(shortCode="SP1", ownerUsername="sproutapp"),
            {
                "error": "Profile not found",
                "inputUrl": "https://www.instagram.com/sproutapp2/",
            },
        ]

        reels, _profiles, account_status = instagram.normalize_dataset(
            reel_items, [], ["sproutapp", "sproutapp2"]
        )

        self.assertEqual(account_status["sproutapp"], "ok")
        self.assertEqual(account_status["sproutapp2"], "not_found")
        self.assertEqual([r["shortCode"] for r in reels], ["SP1"])

        # Same story the other way around: a prefix handle's error must
        # not attribute to a longer handle that merely starts with it.
        reel_items_reverse = [
            _clip_item(shortCode="HL1", ownerUsername="habitlabpro"),
            {
                "error": "Profile not found",
                "inputUrl": "https://www.instagram.com/habitlab/",
            },
        ]

        _reels2, _profiles2, account_status2 = instagram.normalize_dataset(
            reel_items_reverse, [], ["habitlab", "habitlabpro"]
        )

        self.assertEqual(account_status2["habitlab"], "not_found")
        self.assertEqual(account_status2["habitlabpro"], "ok")


class SourceKindTests(NoNetworkTestCase):
    def test_reels_and_profiles_carry_source_kind(self) -> None:
        reel_items = [
            _clip_item(shortCode="N1", ownerUsername="nicheacct"),
            _clip_item(shortCode="F1", ownerUsername="formatacct"),
            # Case-insensitive match against format_handles, and the
            # output's ownerUsername casing is preserved regardless.
            _clip_item(shortCode="F2", ownerUsername="FormatAcct"),
        ]
        profile_items = [
            {
                "username": "nicheacct",
                "followersCount": 1000,
                "postsCount": 5,
                "verified": False,
                "private": False,
            },
            {
                "username": "FormatAcct",
                "followersCount": 2000,
                "postsCount": 8,
                "verified": False,
                "private": False,
            },
        ]

        reels, profiles, _account_status = instagram.normalize_dataset(
            reel_items,
            profile_items,
            ["nicheacct", "formatacct"],
            format_handles=["formatacct"],
        )

        by_shortcode = {reel["shortCode"]: reel for reel in reels}
        self.assertEqual(by_shortcode["N1"]["source_kind"], "niche")
        self.assertEqual(by_shortcode["F1"]["source_kind"], "format")
        self.assertEqual(by_shortcode["F2"]["source_kind"], "format")

        self.assertEqual(profiles["nicheacct"]["source_kind"], "niche")
        self.assertEqual(profiles["formatacct"]["source_kind"], "format")

    def test_source_kind_defaults_to_niche_without_format_handles(self) -> None:
        reels, profiles, _account_status = instagram.normalize_dataset(
            [_clip_item(shortCode="N1", ownerUsername="someacct")],
            [
                {
                    "username": "someacct",
                    "followersCount": 1,
                    "postsCount": 1,
                    "verified": False,
                    "private": False,
                }
            ],
            ["someacct"],
        )

        self.assertEqual(reels[0]["source_kind"], "niche")
        self.assertEqual(profiles["someacct"]["source_kind"], "niche")


class FixtureTests(NoNetworkTestCase):
    def test_fixture_contains_required_edge_cases(self) -> None:
        reel_items = _load_fixture("apify_reels_sample.json")
        profile_items = _load_fixture("apify_profiles_sample.json")

        non_error_items = [item for item in reel_items if "error" not in item]
        error_items = [item for item in reel_items if "error" in item]

        self.assertTrue(error_items, "expected at least one error item")
        self.assertTrue(all("inputUrl" in item for item in error_items))

        self.assertTrue(any(item.get("isPinned") is True for item in non_error_items))
        self.assertTrue(
            any(
                item.get("videoPlayCount") == 0 and item.get("videoViewCount") == 0
                for item in non_error_items
            )
        )
        self.assertTrue(
            any(
                item.get("videoPlayCount") == 0 and (item.get("videoViewCount") or 0) > 0
                for item in non_error_items
            )
        )
        self.assertTrue(any(item.get("likesCount") == -1 for item in non_error_items))
        self.assertTrue(any(item.get("videoDuration") == 400 for item in non_error_items))
        self.assertTrue(
            any(
                instagram.parse_ts(item["timestamp"]) < LOOKBACK_CUTOFF_90D
                for item in non_error_items
            )
        )
        self.assertTrue(any(profile.get("private") is True for profile in profile_items))

        for item in non_error_items:
            for key in ("shortCode", "ownerUsername", "videoUrl", "displayUrl", "productType"):
                self.assertTrue(item.get(key), f"{key} missing/empty on item {item!r}")

    def test_fixture_has_three_handles_each_with_a_clear_outlier(self) -> None:
        reel_items = _load_fixture("apify_reels_sample.json")
        profile_items = _load_fixture("apify_profiles_sample.json")
        handles = ("sproutapp", "habitlab", "dailywins")

        # Reels that would actually survive normalize_reel (clips, not
        # pinned) -- the fixture's pinned reel belongs to one of these
        # accounts too, but must not count toward its 5-or-6 reel tally.
        normalizable_by_handle: Dict[str, List[dict]] = {handle: [] for handle in handles}
        for item in reel_items:
            if "error" in item or item.get("productType") != "clips" or item.get("isPinned"):
                continue
            owner = item.get("ownerUsername")
            if owner in normalizable_by_handle:
                normalizable_by_handle[owner].append(item)

        total = sum(len(items) for items in normalizable_by_handle.values())
        self.assertGreaterEqual(total, 15)

        for handle, items in normalizable_by_handle.items():
            self.assertIn(len(items), (5, 6), f"{handle} should have 5 or 6 reels, got {len(items)}")
            plays_values = [
                instagram.pick_plays(item)[0]
                for item in items
                if instagram.pick_plays(item)[0] is not None
            ]
            median = statistics.median(plays_values)
            self.assertTrue(
                any(plays >= 4 * median for plays in plays_values),
                f"{handle} has no reel at >= 4x its median plays ({median}): {plays_values}",
            )

        followers_by_username = {p["username"]: p.get("followersCount") for p in profile_items}
        self.assertEqual(followers_by_username.get("sproutapp"), 12000)
        self.assertEqual(followers_by_username.get("habitlab"), 180000)
        self.assertEqual(followers_by_username.get("dailywins"), 950000)

    def test_normalize_dataset_matches_fixture_handles(self) -> None:
        reel_items = _load_fixture("apify_reels_sample.json")
        profile_items = _load_fixture("apify_profiles_sample.json")
        handles = ["sproutapp", "habitlab", "dailywins", "ghostaccount", "privatepeople"]

        reels, profiles, account_status = instagram.normalize_dataset(
            reel_items, profile_items, handles
        )

        self.assertEqual(
            account_status,
            {
                "sproutapp": "ok",
                "habitlab": "ok",
                "dailywins": "ok",
                "ghostaccount": "not_found",
                "privatepeople": "private",
            },
        )
        # The pinned reel and the error item never make it into `reels`.
        self.assertTrue(all(reel["isPinned"] is False for reel in reels))
        self.assertTrue(all("error" not in reel for reel in reels))
        self.assertIn("privatepeople", profiles)
        self.assertTrue(profiles["privatepeople"]["private"])
        self.assertEqual(profiles["privatepeople"]["followers"], 3000)


class PartnerTagReelTests(NoNetworkTestCase):
    def test_brand_partner_tag_flags_the_reel(self) -> None:
        item = {
            "shortCode": "P1", "productType": "clips", "timestamp": "2026-09-13T12:00:00.000Z",
            "caption": "keep it quiet #higgsfieldpartner", "hashtags": ["higgsfieldpartner"],
            "videoPlayCount": 203003,
        }
        reel = instagram.normalize_reel(item)
        self.assertTrue(reel["paid_partnership"])
        self.assertEqual(reel["paid_signals"], ["hashtag:higgsfieldpartner"])


if __name__ == "__main__":
    import unittest

    unittest.main()
