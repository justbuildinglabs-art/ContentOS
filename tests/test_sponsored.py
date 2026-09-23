"""Tests for `lib/sponsored.py`: spotting a paid partnership from text the pipeline has."""
from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional

from tests.helpers import NoNetworkTestCase

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import sponsored  # noqa: E402


def _reel(caption: str = "", hashtags: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"shortCode": "ABC123", "caption": caption, "hashtags": hashtags or []}


class LabelSignalTests(NoNetworkTestCase):
    def test_the_scraper_flag_is_the_first_signal(self) -> None:
        reel = dict(_reel("New gear #ad", ["ad"]), paidPartnership=True)
        self.assertEqual(
            sponsored.detect(reel)["signals"], ["label:paid_partnership", "hashtag:ad"]
        )

    def test_only_a_true_boolean_counts(self) -> None:
        for value in (False, None, "true", 1):
            with self.subTest(value=value):
                self.assertFalse(sponsored.detect(dict(_reel(), paidPartnership=value))["detected"])


class HashtagSignalTests(NoNetworkTestCase):
    def test_ad_hashtag_is_detected(self) -> None:
        result = sponsored.detect(_reel("My morning routine #ad", ["ad"]))
        self.assertTrue(result["detected"])
        self.assertEqual(result["signals"], ["hashtag:ad"])

    def test_hashtag_in_list_only_is_detected(self) -> None:
        result = sponsored.detect(_reel("My morning routine", ["Sponsored"]))
        self.assertEqual(result["signals"], ["hashtag:sponsored"])

    def test_hashtag_in_caption_only_is_detected(self) -> None:
        result = sponsored.detect(_reel("Loving this. #PaidPartnership with them"))
        self.assertEqual(result["signals"], ["hashtag:paidpartnership"])

    def test_leading_hash_in_the_list_is_ignored(self) -> None:
        result = sponsored.detect(_reel("", ["#gifted"]))
        self.assertEqual(result["signals"], ["hashtag:gifted"])

    def test_lookalike_hashtags_do_not_match(self) -> None:
        reel = _reel(
            "New trip #adventure #advice #ads_tips #additive",
            ["adventure", "advice", "ads_tips", "additive"],
        )
        self.assertEqual(sponsored.detect(reel), {"detected": False, "signals": []})


class PhraseSignalTests(NoNetworkTestCase):
    def test_caption_phrases_are_detected(self) -> None:
        for caption, phrase in (
            ("Paid partnership with Acme.", "paid partnership"),
            ("This one is SPONSORED BY Acme", "sponsored by"),
            ("Made in partnership with @acme", "in partnership with"),
            ("I partnered with Acme on this", "partnered with"),
            ("Thanks to Acme for sponsoring this reel", "for sponsoring"),
        ):
            with self.subTest(caption=caption):
                result = sponsored.detect(_reel(caption))
                self.assertTrue(result["detected"])
                self.assertEqual(result["signals"], [f"caption:{phrase}"])

    def test_near_miss_phrases_do_not_match(self) -> None:
        for caption in (
            "Partner workout you can do at home",
            "Unsponsored and honest review",
            "My partnership with my co-founder",
            "I read every ad on the subway",
        ):
            with self.subTest(caption=caption):
                self.assertFalse(sponsored.detect(_reel(caption))["detected"])

    def test_transcript_phrase_is_detected(self) -> None:
        result = sponsored.detect(_reel("Morning routine"), "this video is sponsored by Acme")
        self.assertEqual(result["signals"], ["transcript:sponsored by"])

    def test_signals_are_deduped_and_ordered(self) -> None:
        reel = _reel("#ad Sponsored by Acme. #ad again", ["ad", "AD"])
        result = sponsored.detect(reel, "sponsored by Acme")
        self.assertEqual(
            result["signals"],
            ["hashtag:ad", "caption:sponsored by", "transcript:sponsored by"],
        )


class MalformedInputTests(NoNetworkTestCase):
    def test_missing_and_wrong_typed_fields_are_not_an_error(self) -> None:
        for reel in ({}, {"caption": None, "hashtags": None}, {"caption": 7, "hashtags": [3, None, "ad"]}):
            with self.subTest(reel=reel):
                result = sponsored.detect(reel)
                self.assertIsInstance(result["detected"], bool)
        self.assertEqual(
            sponsored.detect({"caption": 7, "hashtags": [3, None, "ad"]})["signals"], ["hashtag:ad"]
        )


class PartnerTagTests(NoNetworkTestCase):
    def test_brand_partner_tags_are_detected(self) -> None:
        for caption, tags, signal in (
            ("keep it quiet #higgsfieldpartner @higgsfield.ai", ["higgsfieldpartner"], "hashtag:higgsfieldpartner"),
            ("Built it in an hour #LovablePartner", [], "hashtag:lovablepartner"),
            ("Email flows that sell", ["OmnisendPartner"], "hashtag:omnisendpartner"),
            ("Slides in seconds", ["gammapartner"], "hashtag:gammapartner"),
            ("My new app", ["replitpartners"], "hashtag:replitpartners"),
            ("Prompts that work #chatgpt_partner", [], "hashtag:chatgpt_partner"),
            ("New drop", ["nikeambassador"], "hashtag:nikeambassador"),
        ):
            with self.subTest(signal=signal):
                self.assertEqual(sponsored.detect(_reel(caption, tags))["signals"], [signal])

    def test_generic_partner_tags_do_not_match(self) -> None:
        for tag in (
            "gympartner", "workoutpartner", "lifepartner", "businesspartner", "studypartners",
            "crimepartner", "partner", "partners", "partnerworkout", "ambassador",
        ):
            with self.subTest(tag=tag):
                self.assertFalse(sponsored.detect(_reel(f"#{tag}", [tag]))["detected"])


if __name__ == "__main__":
    unittest.main()
