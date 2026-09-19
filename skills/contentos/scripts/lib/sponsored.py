"""Spot a paid partnership from the text the pipeline already has.

Instagram draws its "Paid partnership" label over the player, so it is
never in the video file and no keyframe can show it (design spec,
"0.5.0 changes"). This module reads the caption, the hashtags, and the
transcript instead. It is pure: no network, no files.
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

    Signals are `hashtag:<tag>`, `caption:<phrase>`, and
    `transcript:<phrase>`, deduped, in that order. Missing or
    wrong-typed fields count as empty, never as an error: one odd item
    must not abort a scrape that is already paid for.
    """
    signals: List[str] = []
    for tag in _hashtags(reel):
        signal = f"hashtag:{tag}"
        if tag in SPONSORED_HASHTAGS and signal not in signals:
            signals.append(signal)
    signals.extend(_phrase_signals("caption", _text(reel.get("caption"))))
    signals.extend(_phrase_signals("transcript", _text(transcript_text)))
    return {"detected": bool(signals), "signals": signals}
