"""Instagram Reel/profile normalization: raw Apify items -> canonical shape.

Every downstream stage (Task 9's baselines and scoring in
`lib/outliers.py`, the `research` command, `lib/video.py`/`lib/frames.py`
downloads, and the content-director prompt) works from one canonical Reel
dict, never from raw Apify fields directly. See the design spec's
"External API facts" for the raw item fields read here and "Stage 1 --
research" step 3 for the canonical Reel shape, the `plays` fallback rule,
and the per-handle `account_status` values this produces.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# account_status values normalize_dataset assigns each handle, in the
# order they are checked.
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"
STATUS_PRIVATE = "private"
STATUS_EMPTY = "empty"
STATUS_OK = "ok"


def pick_plays(item: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """Pick a Reel's play count, preferring `videoPlayCount` over `videoViewCount`.

    Design spec risk table: `videoPlayCount` is frequently stuck at 0 on
    real items, so a positive `videoViewCount` is used instead when that
    happens. Returns `(None, None)` when neither field is a positive
    number, so the reel is excluded from medians and ranking (Task 9)
    rather than scored as zero.
    """
    play_count = item.get("videoPlayCount")
    if play_count is not None and play_count > 0:
        return play_count, "videoPlayCount"

    view_count = item.get("videoViewCount")
    if view_count is not None and view_count > 0:
        return view_count, "videoViewCount"

    return None, None


def parse_ts(value: Any) -> datetime:
    """Parse an Apify timestamp into a UTC-aware datetime.

    Accepts an ISO 8601 string with a trailing `Z` (replaced with
    `+00:00` before `datetime.fromisoformat`, since that call on Python
    3.9 does not accept a bare `Z`), an ISO 8601 string with an explicit
    UTC offset (converted to UTC), a naive ISO 8601 string (treated as
    already being UTC), or an integer/float epoch-seconds value.
    """
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"

    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_comments(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map raw `latestComments` entries to the canonical comment shape.

    Missing fields on any one comment become `None` rather than being
    dropped, so `latestComments` is always a list of same-shaped dicts.
    """
    return [
        {
            "ownerUsername": comment.get("ownerUsername"),
            "text": comment.get("text"),
            "likesCount": comment.get("likesCount"),
        }
        for comment in item.get("latestComments") or []
    ]


def normalize_reel(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one raw Apify "reels" dataset item to the canonical Reel dict.

    Returns `None` for an item that carries an `error` key, has no
    `shortCode`, has a `productType` other than `"clips"`, or has
    `isPinned` true -- pinned posts bias medians and the actor input
    already asks Apify to skip them (`skipPinnedPosts`), so any that
    slip through are dropped here too.
    """
    if "error" in item:
        return None

    short_code = item.get("shortCode")
    if not short_code:
        return None

    if item.get("productType") != "clips":
        return None

    if item.get("isPinned"):
        return None

    plays, plays_source = pick_plays(item)

    likes_raw = item.get("likesCount")
    likes = likes_raw if likes_raw is not None and likes_raw >= 0 else None

    duration_raw = item.get("videoDuration")
    duration_s = float(duration_raw) if duration_raw is not None else None

    return {
        "shortCode": short_code,
        "url": item.get("url") or f"https://www.instagram.com/reel/{short_code}/",
        "ownerUsername": item.get("ownerUsername"),
        "timestamp": parse_ts(item["timestamp"]).isoformat(),
        "caption": item.get("caption") or "",
        "hashtags": item.get("hashtags") or [],
        "mentions": item.get("mentions") or [],
        "plays": plays,
        "plays_source": plays_source,
        "likes": likes,
        "comments": item.get("commentsCount") or 0,
        "duration_s": duration_s,
        "videoUrl": item.get("videoUrl"),
        "displayUrl": item.get("displayUrl"),
        "isPinned": bool(item.get("isPinned", False)),
        "latestComments": _latest_comments(item),
        "musicInfo": item.get("musicInfo"),
    }


def normalize_profile(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one raw Apify "details" dataset item to the canonical profile dict.

    Returns `None` for an item that carries an `error` key or has no
    `username`.
    """
    if "error" in item:
        return None

    username = item.get("username")
    if not username:
        return None

    return {
        "username": username,
        "followers": item.get("followersCount"),
        "posts": item.get("postsCount"),
        "verified": bool(item.get("verified")),
        "private": bool(item.get("private")),
        "url": item.get("url") or f"https://www.instagram.com/{username}/",
    }


def dedupe_by_shortcode(reels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop later duplicates of the same `shortCode`, keeping the first seen."""
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for reel in reels:
        short_code = reel["shortCode"]
        if short_code in seen:
            continue
        seen.add(short_code)
        deduped.append(reel)
    return deduped


def _is_not_found_error(error_text: Any) -> bool:
    """True when an error item's text describes a not-found/404 profile.

    The design spec's rule is "contains 'not found' or '404'"; Apify's
    own wording for this varies in whether it uses a space or an
    underscore (e.g. "not found" vs. "not_found"), so underscores are
    normalized to spaces first.
    """
    text = str(error_text or "").lower().replace("_", " ")
    return "not found" in text or "404" in text


def _find_error_item(
    error_items: List[Dict[str, Any]], handle: str
) -> Optional[Dict[str, Any]]:
    """Return the first error item whose `inputUrl` names `handle`, if any."""
    handle_lower = handle.lower()
    for item in error_items:
        input_url = str(item.get("inputUrl") or "").lower()
        if handle_lower in input_url:
            return item
    return None


def normalize_dataset(
    reel_items: List[Dict[str, Any]],
    profile_items: List[Dict[str, Any]],
    handles: List[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Normalize one Apify research run into reels, profiles, and account status.

    `reels` is every successfully normalized reel item (any owner),
    deduplicated by `shortCode`. `profiles` maps lowercase username to
    its normalized profile dict. `account_status` has exactly one entry
    per handle in `handles`, keyed as given -- matching against reels,
    profiles, and error items is case-insensitive, per the design spec:
    a handle whose `inputUrl` appears on an `error` item resolves to
    `not_found` or `error` depending on the error text; otherwise a
    handle with a private profile resolves to `private`; otherwise a
    handle is `ok` when at least one reel's `ownerUsername` matches it,
    else `empty`.
    """
    reels = dedupe_by_shortcode(
        [reel for reel in (normalize_reel(item) for item in reel_items) if reel is not None]
    )

    profiles: Dict[str, Dict[str, Any]] = {}
    for item in profile_items:
        profile = normalize_profile(item)
        if profile is not None:
            profiles[profile["username"].lower()] = profile

    error_items = [item for item in list(reel_items) + list(profile_items) if "error" in item]

    account_status: Dict[str, str] = {}
    for handle in handles:
        handle_lower = handle.lower()

        error_item = _find_error_item(error_items, handle)
        if error_item is not None:
            account_status[handle] = (
                STATUS_NOT_FOUND if _is_not_found_error(error_item.get("error")) else STATUS_ERROR
            )
            continue

        profile = profiles.get(handle_lower)
        if profile is not None and profile["private"]:
            account_status[handle] = STATUS_PRIVATE
            continue

        has_reel = any(
            (reel.get("ownerUsername") or "").lower() == handle_lower for reel in reels
        )
        account_status[handle] = STATUS_OK if has_reel else STATUS_EMPTY

    return reels, profiles, account_status
