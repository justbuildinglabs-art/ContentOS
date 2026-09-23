"""Spot a paid partnership from the text the pipeline already has.

Instagram draws its "Paid partnership" label over the player, so it is
never in the video file and no keyframe can show it (design spec,
"0.5.0 changes"). The scraper does return that label as data, the raw
item's `paidPartnership` boolean (seen on a hashtag reels scrape,
2026-09-19). This module reads it, then the hashtags, the caption, and
the transcript, because plenty of sponsored reels never set the label.
0.6.0 also reads a brand's own partner tag, such as #higgsfieldpartner.
It is pure: no network, no files.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Whole hashtags only: "ad" must never match "adventure" or "advice".
SPONSORED_HASHTAGS = frozenset(
    {
        "ad",
        "sponsored",
        "sponsoredpost",
        "paidpartnership",
        "paidpartner",
        "brandpartner",
        "gifted",
    }
)

# 0.6.0: a brand's own partner tag, such as #higgsfieldpartner,
# #LovablePartner, #replitpartners, or #nikeambassador (design spec, "0.6.0
# changes", Paid filter). Underscores are dropped first (#chatgpt_partner).
# The prefix must be 3 or more characters and not a generic word, so
# #gympartner and #businesspartner never match.
_PARTNER_TAG_RE = re.compile(r"^([a-z0-9]{3,})(partners?|ambassadors?)$")
GENERIC_PARTNER_PREFIXES = frozenset(
    {
        "life", "business", "gym", "workout", "training", "study",
        "accountability", "dance", "travel", "running", "crime",
    }
)

# Matched on word boundaries, case-insensitively, in this order.
SPONSORED_PHRASES = (
    "paid partnership",
    "sponsored by",
    "in partnership with",
    "partnered with",
    "for sponsoring",
)

_HASHTAG_RE = re.compile(r"#(\w+)")
_PHRASE_RES = tuple(
    (phrase, re.compile(r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b", re.IGNORECASE))
    for phrase in SPONSORED_PHRASES
)


def _is_partner_tag(tag: str) -> bool:
    """True for a lowercased hashtag like `higgsfieldpartner` or `chatgpt_partner`."""
    match = _PARTNER_TAG_RE.match(tag.replace("_", ""))
    return bool(match) and match.group(1) not in GENERIC_PARTNER_PREFIXES


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _hashtags(reel: Dict[str, Any]) -> List[str]:
    """Every hashtag on the reel, lowercased, list field first, then the caption."""
    listed = reel.get("hashtags")
    tags = [tag.lstrip("#") for tag in listed if isinstance(tag, str)] if isinstance(listed, list) else []
    tags.extend(_HASHTAG_RE.findall(_text(reel.get("caption"))))
    return [tag.lower() for tag in tags]


def _phrase_signals(source: str, text: str) -> List[str]:
    return [f"{source}:{phrase}" for phrase, pattern in _PHRASE_RES if pattern.search(text)]


def detect(reel: Dict[str, Any], transcript_text: Optional[str] = None) -> Dict[str, Any]:
    """Return `{"detected": bool, "signals": [...]}` for one reel.

    Signals are `label:paid_partnership` (the raw item's
    `paidPartnership` is exactly `True`), `hashtag:<tag>`,
    `caption:<phrase>`, and `transcript:<phrase>`, deduped, in that
    order. `reel` may be a raw scraper item or a canonical Reel. Missing or
    wrong-typed fields count as empty, never as an error: one odd item
    must not abort a scrape that is already paid for.
    """
    signals: List[str] = []
    if reel.get("paidPartnership") is True:
        signals.append("label:paid_partnership")
    for tag in _hashtags(reel):
        signal = f"hashtag:{tag}"
        if (tag in SPONSORED_HASHTAGS or _is_partner_tag(tag)) and signal not in signals:
            signals.append(signal)
    signals.extend(_phrase_signals("caption", _text(reel.get("caption"))))
    signals.extend(_phrase_signals("transcript", _text(transcript_text)))
    return {"detected": bool(signals), "signals": signals}
