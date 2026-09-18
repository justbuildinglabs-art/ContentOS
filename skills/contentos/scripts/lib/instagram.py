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
from urllib.parse import urlsplit

# account_status values normalize_dataset assigns each handle, in the
# order they are checked.
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"
STATUS_PRIVATE = "private"
STATUS_EMPTY = "empty"
STATUS_OK = "ok"

# source_kind values normalize_dataset stamps on every reel and profile:
# "format" when the owner/username (case-insensitively) is one of the
# `format_handles` passed in, else "niche".
SOURCE_KIND_NICHE = "niche"
SOURCE_KIND_FORMAT = "format"


def _to_number(value: Any) -> Optional[float]:
    """Coerce a raw Apify numeric field to a number, tolerating strings.

    Real dataset items are already numeric, but a defensively-written
    normalizer should not crash the whole item over one malformed
    field. Accepts int/float as-is (excluding `bool`, which is
    technically an `int` subclass but never a legitimate count) and
    numeric strings (surrounding whitespace stripped; tried as `int`
    first so a whole count like `"500"` stays an int, then as `float`
    for e.g. `"20.5"`). Anything else -- `None`, a non-numeric string, a
    list, ... -- becomes `None`, so that one field degrades to
    "missing" rather than raising.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            return None
    return None


def pick_plays(item: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """Pick a Reel's play count, preferring `videoPlayCount` over `videoViewCount`.

    Design spec risk table: `videoPlayCount` is frequently stuck at 0 on
    real items, so a positive `videoViewCount` is used instead when that
    happens. Returns `(None, None)` when neither field is a positive
    number, so the reel is excluded from medians and ranking (Task 9)
    rather than scored as zero.
    """
    play_count = _to_number(item.get("videoPlayCount"))
    if play_count is not None and play_count > 0:
        return play_count, "videoPlayCount"

    view_count = _to_number(item.get("videoViewCount"))
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
    A `latestComments` value that is not a list becomes `[]`, and any
    entry within it that is not itself a dict is skipped, rather than
    raising `AttributeError` on `.get`.
    """
    raw = item.get("latestComments")
    if not isinstance(raw, list):
        return []
    return [
        {
            "ownerUsername": comment.get("ownerUsername"),
            "text": comment.get("text"),
            "likesCount": comment.get("likesCount"),
        }
        for comment in raw
        if isinstance(comment, dict)
    ]


def normalize_reel(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one raw Apify "reels" dataset item to the canonical Reel dict.

    Returns `None` for an item that carries an `error` key, has no
    `shortCode`, has a `productType` other than `"clips"`, has
    `isPinned` true -- pinned posts bias medians and the actor input
    already asks Apify to skip them (`skipPinnedPosts`), so any that
    slip through are dropped here too -- or has a missing or
    unparseable `timestamp`.

    A reel with no usable posting time cannot be placed in the lookback
    window or in an account's baseline, so it is dropped exactly like a
    reel with no `shortCode`. It is never an exception: one malformed
    item out of a whole scrape must not abort the research run after
    Apify has already been paid for it.
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

    try:
        timestamp = parse_ts(item["timestamp"]).isoformat()
    except (KeyError, AttributeError, ValueError, TypeError, OverflowError):
        return None

    plays, plays_source = pick_plays(item)

    likes_raw = _to_number(item.get("likesCount"))
    likes = likes_raw if likes_raw is not None and likes_raw >= 0 else None

    comments_raw = _to_number(item.get("commentsCount"))
    comments = comments_raw if comments_raw is not None else 0

    duration_raw = _to_number(item.get("videoDuration"))
    duration_s = float(duration_raw) if duration_raw is not None else None

    return {
        "shortCode": short_code,
        "url": item.get("url") or f"https://www.instagram.com/reel/{short_code}/",
        "ownerUsername": item.get("ownerUsername"),
        "timestamp": timestamp,
        "caption": item.get("caption") or "",
        "hashtags": item.get("hashtags") or [],
        "mentions": item.get("mentions") or [],
        "plays": plays,
        "plays_source": plays_source,
        "likes": likes,
        "comments": comments,
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
        "followers": _to_number(item.get("followersCount")),
        "posts": _to_number(item.get("postsCount")),
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


def _handle_from_input_url(input_url: str) -> str:
    """Extract the handle an error item's `inputUrl` names.

    `inputUrl` is the URL Apify was given as input, e.g.
    `https://www.instagram.com/sproutapp/` -- the handle is its last
    non-empty path segment (trailing slashes ignored), with any leading
    `@` stripped (some inputs use the `@handle` form). Returns `""` when
    the URL has no path segments at all.
    """
    segments = [segment for segment in urlsplit(input_url).path.split("/") if segment]
    if not segments:
        return ""
    return segments[-1].lstrip("@")


def _find_error_item(
    error_items: List[Dict[str, Any]], handle: str
) -> Optional[Dict[str, Any]]:
    """Return the first error item whose `inputUrl` names exactly `handle`, if any.

    Matches on the whole last path segment of `inputUrl`
    (case-insensitively), not a substring -- otherwise an error item for
    `sproutapp2` would also misattribute to the unrelated, healthy
    handle `sproutapp`.
    """
    handle_lower = handle.lower()
    for item in error_items:
        input_url = str(item.get("inputUrl") or "")
        if _handle_from_input_url(input_url).lower() == handle_lower:
            return item
    return None


def normalize_dataset(
    reel_items: List[Dict[str, Any]],
    profile_items: List[Dict[str, Any]],
    handles: List[str],
    format_handles: Optional[List[str]] = None,
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

    Every reel and every profile also carries `source_kind`: `"format"`
    when its owner/`username` (case-insensitively) is one of
    `format_handles`, else `"niche"`. `format_handles` defaults to none,
    so every reel/profile is `"niche"` when it is omitted.
    """
    format_set = {handle.lower() for handle in (format_handles or [])}

    def _source_kind(owner: Any) -> str:
        return SOURCE_KIND_FORMAT if str(owner or "").lower() in format_set else SOURCE_KIND_NICHE

    reels = dedupe_by_shortcode(
        [reel for reel in (normalize_reel(item) for item in reel_items) if reel is not None]
    )
    reels = [dict(reel, source_kind=_source_kind(reel.get("ownerUsername"))) for reel in reels]

    profiles: Dict[str, Dict[str, Any]] = {}
    for item in profile_items:
        profile = normalize_profile(item)
        if profile is not None:
            profile = dict(profile, source_kind=_source_kind(profile["username"]))
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
