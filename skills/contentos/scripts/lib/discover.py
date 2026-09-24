"""Creator discovery: find creators who are already winning in a niche (0.6.0).

Design spec, "0.6.0 changes". Sources are the orchestrator's web finds,
seeds (the watch list plus handles the creator typed), Instagram keyword
reel search, optional hashtag reels, and one hop of Instagram's similar
accounts. Three Apify steps share one time budget and one charge cap: step
A starts the keyword, hashtag, and details runs together; step B checks
search authors and similar accounts; step C scrapes the shortlist's reels.
Pass 1 (details) drops missing, private, small, and inactive accounts, and
pass 2 (reels) holds the rest to the bar. Survivors are Established or
Rising, and their breakout reels feed 0.7.0's trends.

Python never searches the web: the orchestrator does, and hands the
handles in. Nothing here needs `setup` to have run
(`store.load_discovery_config`). The cost gates, their order, and their
exit codes are the research stage's own (`research.check_gates`).
"""
from __future__ import annotations

import json
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlsplit

from lib import apify, codes, instagram, outliers, research, setup, store
from lib.env import Keys
from lib.http import HTTPError

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = SCRIPTS_DIR.parent.parent.parent / "fixtures"
HASHTAG_REELS_FIXTURE_NAME = "apify_hashtag_reels_sample.json"
KEYWORD_REELS_FIXTURE_NAME = "apify_discover_keyword_reels_sample.json"
DISCOVER_PROFILES_FIXTURE_NAME = "apify_discover_profiles_sample.json"
DISCOVER_REELS_FIXTURE_NAME = "apify_discover_reels_sample.json"

DISCOVERY_FILE_NAME = "discovery.json"
DISCOVERY_VERSION = 2

_HASHTAG_RE = re.compile(r"^\w+$")

# 0.6.0 (design spec, "0.6.0 changes").
MAX_WEB_HANDLES = 40
ACTIVE_DAYS = 30
REASON_NO_RECENT_REEL = "no reel in 30 days"
_CAPTION_TAG_RE = re.compile(r"#(\w+)")
# A niche word is a run of letters and digits; underscores split words.
_NICHE_WORD_RE = re.compile(r"[^\W_]+")
# Words a keyword phrase can do without (design spec, "0.6.0 changes",
# Who counts as successful).
NICHE_FILLER_WORDS = frozenset({
    "a", "an", "and", "or", "the", "for", "of", "to", "in", "on", "at", "by", "with", "from",
    "your", "you", "my", "our", "how", "what", "is", "are",
})
KEYWORD_REELS_PER_TERM = 20
EXPAND_LIMIT = 15
REELS_PER_ACCOUNT = 15
WINDOW_DAYS = 90
TREND_DAYS = 30
MAX_BREAKOUTS = 20
BREAKOUTS_PER_CREATOR = 3
TOP_REEL_CHARS = 140
TIER_ESTABLISHED = "established"
TIER_RISING = "rising"
REASON_SHORTLIST_FULL = "shortlist full"
REASON_NOT_MEASURED = "not measured in time"
# A cut-short or skipped account check never reached this handle, so it
# is not called missing (design spec, "0.6.0 changes").
REASON_NOT_CHECKED = "not checked in time"
_REASON_LABELS = {"not_found": "not found", "error": "could not be checked", "private": "private"}
_SEED_WARNINGS = {
    instagram.STATUS_NOT_FOUND: "watch list account @{handle} was not found",
    instagram.STATUS_PRIVATE: "watch list account @{handle} is private",
    instagram.STATUS_ERROR: "watch list account @{handle} could not be checked",
    REASON_NOT_CHECKED: "watch list account @{handle} was not checked in time",
}
# Every wait and every run's own timeout share this budget, so all of
# discover's Apify runs fit inside one 10-minute Bash call.
BUDGET_S = 540.0
# A run that would start with less than this left is skipped, and counts
# as timed out.
MIN_RUN_S = 30
# While a run is going, at most one "still running" line per this long.
PROGRESS_EVERY_S = 60.0
# Step names, for warnings and progress lines.
STEP_KEYWORD = "keyword search"
STEP_HASHTAG = "hashtag search"
STEP_DETAILS = "account check"
STEP_REELS = "reels check"


class DiscoverError(Exception):
    """A discovery usage failure, carrying the exit code `contentos.py` returns."""

    def __init__(self, message: str, exit_code: int = codes.EXIT_USAGE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _default_log(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def normalize_hashtags(raw: List[str]) -> List[str]:
    """Lowercase, strip `#` and spaces, drop blanks, bad characters, and repeats."""
    tags: List[str] = []
    for value in raw:
        tag = "".join(str(value).split()).lstrip("#").lower()
        if tag and _HASHTAG_RE.match(tag) and tag not in tags:
            tags.append(tag)
    return tags


def normalize_keywords(raw: List[str]) -> List[str]:
    """Collapse whitespace, drop blanks and case-insensitive repeats."""
    keywords: List[str] = []
    for value in raw:
        keyword = " ".join(str(value).split())
        if keyword and keyword.lower() not in [kept.lower() for kept in keywords]:
            keywords.append(keyword)
    return keywords


def hashtag_from_input_url(input_url: Any) -> Optional[str]:
    """The tag a reel's `inputUrl` names, or None when it is not a tag page."""
    if not isinstance(input_url, str):
        return None
    segments = [segment for segment in urlsplit(input_url).path.split("/") if segment]
    if len(segments) >= 3 and segments[0] == "explore" and segments[1] == "tags":
        return segments[2].lower()
    return None


def load_web_handles(path: Optional[Path]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Read `--handles-file`: a JSON list of `{handle, source_url}`.

    Returns `(entries, warnings)` with each `handle` already through
    `setup.normalize_handle`. An entry whose handle fails it (another
    platform's URL, odd characters) is dropped with a warning, because
    these come from web pages and one bad line must not stop the run.
    A file that is missing or not a JSON list is a usage error.
    """
    if path is None:
        return [], []
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DiscoverError(f"could not read --handles-file {path}: {exc}") from exc
    if not isinstance(doc, list):
        raise DiscoverError(f"--handles-file {path} must be a JSON list of {{handle, source_url}}")
    return normalize_web_entries(doc)


def normalize_web_entries(doc: Any) -> Tuple[List[Dict[str, str]], List[str]]:
    """Clean the web finds: a list of `{handle, source_url}`, or bare handles.

    Each handle goes through `setup.normalize_handle`. An entry that fails
    it (another platform's URL, odd characters) is dropped with a warning,
    because these come from web pages and one bad line must not stop the
    run. A value that is not a list is a DiscoverError.
    """
    if not isinstance(doc, list):
        raise DiscoverError("web handles must be a JSON list of {handle, source_url}")
    entries: List[Dict[str, str]] = []
    warnings: List[str] = []
    for item in doc:
        raw = item.get("handle") if isinstance(item, dict) else item
        source_url = item.get("source_url") if isinstance(item, dict) else None
        try:
            handle = setup.normalize_handle(raw)
        except setup.SetupError as exc:
            warnings.append(f"web handle {raw!r} dropped: {exc}")
            continue
        if handle:
            entries.append({"handle": handle, "source_url": str(source_url or "")})
    return entries, warnings


def web_handles(entries: List[Dict[str, str]], seeds: List[str]) -> List[str]:
    """The web handles to check: in order, once each, never a seed, at most 40."""
    handles: List[str] = []
    for entry in entries:
        if entry["handle"] not in handles and entry["handle"] not in seeds:
            handles.append(entry["handle"])
    return handles[:MAX_WEB_HANDLES]


def normalize_seeds(raw: List[str]) -> Tuple[List[str], List[str]]:
    """Clean the seeds (the watch list plus handles the creator typed).

    A seed that is not an Instagram handle is dropped with a warning; a
    blank one is skipped. Order is kept and repeats are dropped.
    """
    seeds: List[str] = []
    warnings: List[str] = []
    for value in raw:
        try:
            handle = setup.normalize_handle(value)
        except setup.SetupError as exc:
            warnings.append(f"seed {value!r} dropped: {exc}")
            continue
        if handle and handle not in seeds:
            seeds.append(handle)
    return seeds, warnings


def never_recommended(cfg: Dict[str, Any], typed_seeds: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """The handles discovery checks for expansion only, and the ones it never checks.

    Returns `(seeds, format_accounts, warnings)`: seeds are the project's
    `competitors` plus the handles the creator typed; format accounts come
    from other niches and are never candidates (design spec, "0.6.0 changes",
    Sources). Warnings cover seeds only.
    """
    seeds, warnings = normalize_seeds(list(cfg.get("competitors") or []) + list(typed_seeds))
    format_accounts, _unused = normalize_seeds(list(cfg.get("format_accounts") or []))
    return seeds, format_accounts, warnings


def keyword_from_input_url(input_url: Any) -> Optional[str]:
    """The phrase a keyword-search reel's `inputUrl` names, or None (0.6.0)."""
    if not isinstance(input_url, str):
        return None
    parts = urlsplit(input_url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if segments[:3] != ["explore", "search", "keyword"]:
        return None
    values = parse_qs(parts.query).get("q")
    phrase = " ".join(values[0].split()).lower() if values else ""
    return phrase or None


def search_authors(reel_items: List[Any]) -> Dict[str, Dict[str, Any]]:
    """Group keyword and hashtag reels by lowercase author (0.6.0).

    Each author gets `reels_seen`, `best_plays`, and `sources`
    (`hashtag:<tag>` and `keyword:<phrase>`, sorted). Photos, pinned posts,
    error items, and paid reels fall away as in research, so an account
    found only through sponsored reels never becomes a candidate.
    """
    tagged: List[Dict[str, Any]] = []
    for item in reel_items:
        if not isinstance(item, dict):
            continue
        reel = instagram.normalize_reel(item)
        if reel is None or reel["paid_partnership"]:
            continue
        tag = hashtag_from_input_url(item.get("inputUrl"))
        phrase = keyword_from_input_url(item.get("inputUrl"))
        source = f"hashtag:{tag}" if tag else (f"keyword:{phrase}" if phrase else None)
        tagged.append(dict(reel, _source=source))

    authors: Dict[str, Dict[str, Any]] = {}
    for reel in instagram.dedupe_by_shortcode(tagged):
        owner = str(reel.get("ownerUsername") or "").lower()
        if not owner:
            continue
        entry = authors.setdefault(owner, {"reels_seen": 0, "best_plays": 0, "sources": []})
        entry["reels_seen"] += 1
        entry["best_plays"] = max(entry["best_plays"], reel.get("plays") or 0)
        if reel["_source"] and reel["_source"] not in entry["sources"]:
            entry["sources"].append(reel["_source"])
    for entry in authors.values():
        entry["sources"].sort()
    return authors


def top_authors(authors: Dict[str, Dict[str, Any]], limit: int, skip: Set[str]) -> List[str]:
    """The `limit` search authors with the best reel, minus handles already checked."""
    ranked = sorted(
        (handle for handle in authors if handle not in skip),
        key=lambda handle: (-authors[handle]["best_plays"], handle),
    )
    return ranked[:limit]


def index_profiles(items: List[Any]) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Key a details run's profiles by lowercase username; return the error items too.

    Each row is `instagram.normalize_profile` plus `instagram.profile_extras`.
    The mock transport serves every profile to every details run, so callers
    look up only the handles they asked for.
    """
    rows: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "error" in item:
            errors.append(item)
            continue
        profile = instagram.normalize_profile(item)
        if profile is not None:
            rows[profile["username"].lower()] = dict(profile, **instagram.profile_extras(item))
    return rows, errors


def profile_status(
    handle: str, rows: Dict[str, Dict[str, Any]], error_items: List[Dict[str, Any]], partial: bool = False
) -> str:
    """`ok`, `private`, `not_found`, or `error` for one handle a details run was asked for.

    `partial` says the run was cut short or skipped. Then a handle with no
    profile and no error item is `REASON_NOT_CHECKED`: the run may simply
    never have reached it. An error item that says not found still counts.
    """
    error_item = instagram._find_error_item(error_items, handle)
    if error_item is not None:
        not_found = instagram._is_not_found_error(error_item.get("error"))
        return instagram.STATUS_NOT_FOUND if not_found else instagram.STATUS_ERROR
    row = rows.get(handle)
    if row is None:
        # No profile and no error item: the scrape returned nothing for it.
        return REASON_NOT_CHECKED if partial else instagram.STATUS_NOT_FOUND
    return instagram.STATUS_PRIVATE if row["private"] else instagram.STATUS_OK


def _niche_words(value: Any) -> List[str]:
    """The lowercase runs of letters and digits in `value`, in any script."""
    return _NICHE_WORD_RE.findall(value.lower()) if isinstance(value, str) else []


def _has_word(word: str, searched: Set[str]) -> bool:
    """True when one phrase word, or a form of it, is among the searched words.

    A word of 4 characters or fewer matches itself or itself plus `s`
    (`ai`, `n8n`, `meal` and `meals`). A longer word matches any searched
    word that starts with its first `max(4, len(word) - 3)` characters
    (`automation` matches automate and automations, `agents` matches agent).
    """
    if len(word) <= 4:
        return word in searched or word + "s" in searched
    stem = word[:max(4, len(word) - 3)]
    return any(found.startswith(stem) for found in searched)


def niche_matcher(keywords: List[str], hashtags: List[str]) -> Callable[..., bool]:
    """Build `is_niche(text, tags=None)`: a keyword phrase or a niche hashtag is present.

    A phrase matches by its words, not as an exact phrase. Words are
    lowercase runs of letters and digits (underscores split them). Each of
    the phrase's words, leaving out `NICHE_FILLER_WORDS`, must be among the
    words searched: the text's words plus the words of every tag in
    `tags`, in any order and anywhere. A phrase word of 4 characters or
    fewer matches itself or itself plus `s`; a longer one matches any word
    that starts with its first `max(4, len(word) - 3)` characters
    (`_has_word`). So `ai agents for business` matches "I help businesses
    automate with AI agents". A phrase left with no words never matches.
    A niche hashtag matches whole, from the tag list or from `#tags` inside
    the text. With no terms at all, nothing matches.
    """
    phrases = [
        words
        for words in ([word for word in _niche_words(keyword) if word not in NICHE_FILLER_WORDS]
                      for keyword in keywords)
        if words
    ]
    wanted = {tag.lower() for tag in hashtags}

    def is_niche(text: Any, tags: Any = None) -> bool:
        body = text if isinstance(text, str) else ""
        tag_list = [tag for tag in (tags if isinstance(tags, list) else []) if isinstance(tag, str)]
        searched = set(_niche_words(body))
        for tag in tag_list:
            searched.update(_niche_words(tag))
        if any(all(_has_word(word, searched) for word in words) for words in phrases):
            return True
        found = {tag.lstrip("#").lower() for tag in tag_list}
        found.update(tag.lower() for tag in _CAPTION_TAG_RE.findall(body))
        return bool(wanted & found)

    return is_niche


def latest_niche_hit(row: Dict[str, Any], is_niche: Callable[..., bool]) -> bool:
    """True when the bio or any latest post is about the niche (pass 1's hint)."""
    if is_niche(row.get("bio")):
        return True
    return any(is_niche(post.get("caption"), post.get("hashtags")) for post in row.get("latest_posts") or [])


def _count(value: Any) -> str:
    """A count for people: `10000` -> `10,000`, `2500.5` -> `2,500.5`."""
    number = float(value)
    return f"{int(number):,}" if number.is_integer() else f"{number:,}"


def _days_ago(timestamp: Any, now: datetime) -> int:
    """Whole days from `timestamp` to `now`, rounded down."""
    return int((now - instagram.parse_ts(timestamp)).total_seconds() // 86400)


def pass1_reason(row: Dict[str, Any], cfg: Dict[str, Any], now: datetime) -> Optional[str]:
    """Why a checked profile stops at pass 1, or None when it goes on.

    The follower floor applies to every source (0 turns it off). An account
    is called inactive only when its unpinned `latestPosts` cover the whole
    30 days (one of them is more than 30 days old) and none of its unpinned
    reels is from the last 30 days. `latestPosts` is the last 12 posts of
    any kind, so a daily carousel poster's newest reel can sit just past
    them: when the posts do not reach back 30 days, pass 2 decides activity.
    """
    floor = cfg["discover_min_followers"]
    if (row.get("followers") or 0) < floor:
        return f"under {_count(floor)} followers"
    dated = [post for post in row.get("latest_posts") or [] if not post["is_pinned"] and post["timestamp"]]
    covers_window = any(_days_ago(post["timestamp"], now) > ACTIVE_DAYS for post in dated)
    recent_reel = any(post["is_reel"] and _days_ago(post["timestamp"], now) <= ACTIVE_DAYS for post in dated)
    if covers_window and not recent_reel:
        return REASON_NO_RECENT_REEL
    return None


def expansion_pointers(pointer_rows: List[Dict[str, Any]], skip: Set[str]) -> Dict[str, Set[str]]:
    """Map each similar account to the checked accounts that list it.

    `pointer_rows` are the seeds and the web survivors. An account never
    points at itself, and anything in `skip` (already checked or queued)
    is left out.
    """
    pointed: Dict[str, Set[str]] = {}
    for row in pointer_rows:
        source = str(row["username"]).lower()
        for handle in row.get("related") or []:
            if handle != source and handle not in skip:
                pointed.setdefault(handle, set()).add(source)
    return pointed


def rank_expansion(pointed: Dict[str, Set[str]], limit: int) -> List[str]:
    """Most pointed-at similar accounts first, then by handle; at most `limit`."""
    return sorted(pointed, key=lambda handle: (-len(pointed[handle]), handle))[:limit]


def shortlist(rows: List[Dict[str, Any]], size: int, small_under: float) -> List[str]:
    """Order pass-1 survivors for the reels pass and keep the first `size` handles.

    Niche hits come first. Inside each group, Established and Rising
    alternate, each by followers (most first), then handle, so a list of
    big accounts never squeezes out every rising one.
    """
    def by_followers(group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(group, key=lambda row: (-(row.get("followers") or 0), row["handle"]))

    ordered: List[str] = []
    for niche in (True, False):
        group = [row for row in rows if bool(row.get("niche_hit")) is niche]
        big = by_followers([row for row in group if (row.get("followers") or 0) >= small_under])
        small = by_followers([row for row in group if (row.get("followers") or 0) < small_under])
        for index in range(max(len(big), len(small))):
            for side in (big, small):
                if index < len(side):
                    ordered.append(side[index]["handle"])
    return ordered[:size]


# ---------------------------------------------------------------------------
# Pass 2: measuring and tier assignment (pure)
# ---------------------------------------------------------------------------


def group_reels(items: List[Any], handles: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """The reels run's reels per asked handle (pinned, photos, and repeats dropped)."""
    wanted = [handle.lower() for handle in handles]
    reels = instagram.dedupe_by_shortcode(
        [reel for reel in (instagram.normalize_reel(item) for item in items if isinstance(item, dict))
         if reel is not None]
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {handle: [] for handle in wanted}
    for reel in reels:
        owner = str(reel.get("ownerUsername") or "").lower()
        if owner in grouped:
            grouped[owner].append(reel)
    return grouped


def top_quarter(values: List[float]) -> float:
    """The nearest-rank 75th percentile: at least 1 in 4 values reach it (0 for none)."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[math.ceil(0.75 * len(ordered)) - 1]


def measure(reels: List[Dict[str, Any]], now: datetime, is_niche: Callable[..., bool]) -> Dict[str, Any]:
    """Pass 2's numbers for one creator (design spec, "0.6.0 changes").

    Paid reels count toward the numbers, as in research's baselines, but
    never toward `top_reels`. `posts_per_week` spreads the reels over the
    oldest one's age when all 15 came back, else over the 90-day window.
    """
    plays = [reel["plays"] for reel in reels if reel.get("plays") is not None]
    times = sorted(instagram.parse_ts(reel["timestamp"]) for reel in reels)
    count = len(reels)
    if count >= REELS_PER_ACCOUNT and times:
        span_days = max((now - times[0]).total_seconds() / 86400, 1.0)
    else:
        span_days = float(WINDOW_DAYS)
    rates = [
        ((reel.get("likes") or 0) + (reel.get("comments") or 0)) / reel["plays"]
        for reel in reels if (reel.get("plays") or 0) > 0
    ]
    unpaid = sorted(
        (reel for reel in reels if not reel["paid_partnership"] and reel.get("plays") is not None),
        key=lambda reel: (-reel["plays"], reel["shortCode"]),
    )
    return {
        "reels_measured": count,
        "top_quarter_plays": top_quarter(plays),
        "median_plays": statistics.median(plays) if plays else 0,
        "posts_per_week": round(count / (span_days / 7), 1),
        "last_post_days": _days_ago(times[-1].isoformat(), now) if times else None,
        "paid_reels": sum(1 for reel in reels if reel["paid_partnership"]),
        "niche_hits": sum(1 for reel in reels if is_niche(reel.get("caption"), reel.get("hashtags"))),
        "engagement": round(statistics.median(rates), 4) if rates else None,
        "top_reels": [
            {"url": reel["url"], "plays": reel["plays"], "timestamp": reel["timestamp"],
             "caption": (reel.get("caption") or "")[:TOP_REEL_CHARS]}
            for reel in unpaid[:2]
        ],
    }


def cadence_words(days: int) -> str:
    """`14` -> `every 2 weeks`, `7` -> `every week`, `10` -> `every 10 days`."""
    if days % 7 == 0:
        weeks = days // 7
        return "every week" if weeks == 1 else f"every {weeks} weeks"
    return "every day" if days == 1 else f"every {days} days"


def pass2_reason(metrics: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """Why a measured creator misses the bar, checked in the spec's order, or None."""
    every = cfg["discover_post_every_days"]
    if metrics["reels_measured"] < WINDOW_DAYS // every:
        return f"posts less than {cadence_words(every)}"
    if metrics["last_post_days"] is None or metrics["last_post_days"] > ACTIVE_DAYS:
        return REASON_NO_RECENT_REEL
    floor = cfg["discover_min_views"]
    if metrics["top_quarter_plays"] < floor:
        return f"1 in 4 reels under {_count(floor)} views"
    return None


def tier_of(followers: Any, cfg: Dict[str, Any]) -> str:
    """Established at `small_account_followers` or more, else Rising."""
    return TIER_ESTABLISHED if (followers or 0) >= cfg["small_account_followers"] else TIER_RISING


def order_candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Established first, then by top-quarter views (most first), then handle."""
    return sorted(
        rows, key=lambda row: (row["tier"] != TIER_ESTABLISHED, -row["top_quarter_plays"], row["handle"])
    )


def find_breakouts(
    rows: List[Dict[str, Any]],
    reels_by_owner: Dict[str, List[Dict[str, Any]]],
    cfg: Dict[str, Any],
    now: datetime,
) -> List[Dict[str, Any]]:
    """Unpaid reels from the last 30 days at `min_outlier_ratio` x or more their creator's median.

    At most 3 per creator and 20 overall, highest ratio first. Each keeps
    what 0.7.0's trends step needs to fetch and read the reel.
    """
    found: List[Tuple[float, Dict[str, Any]]] = []
    for row in rows:
        baseline = outliers.Baseline(
            median=float(row["median_plays"]), n=row["reels_measured"],
            confidence=outliers.CONFIDENCE_OK, metric=outliers.METRIC_PLAYS,
        )
        mine: List[Tuple[float, Dict[str, Any]]] = []
        for reel in reels_by_owner.get(row["handle"], []):
            if reel["paid_partnership"] or reel.get("plays") is None:
                continue
            if _days_ago(reel["timestamp"], now) > TREND_DAYS:
                continue
            ratio = outliers.score_reel(reel, baseline, {"followers": row["followers"]}, cfg)["outlier_ratio"]
            if ratio is None or ratio < cfg["min_outlier_ratio"]:
                continue
            mine.append((ratio, {
                "shortCode": reel["shortCode"], "url": reel["url"], "owner": row["handle"],
                "tier": row["tier"], "timestamp": reel["timestamp"], "plays": reel["plays"],
                "ratio": round(ratio, 2), "caption": reel.get("caption") or "",
                "hashtags": reel.get("hashtags") or [], "videoUrl": reel.get("videoUrl"),
                "displayUrl": reel.get("displayUrl"), "duration_s": reel.get("duration_s"),
            }))
        mine.sort(key=lambda pair: (-pair[0], pair[1]["shortCode"]))
        found.extend(mine[:BREAKOUTS_PER_CREATOR])
    found.sort(key=lambda pair: (-pair[0], pair[1]["shortCode"]))
    return [entry for _ratio, entry in found[:MAX_BREAKOUTS]]


def estimate(cfg: Dict[str, Any], n_keywords: int, n_hashtags: int, n_seeds: int, n_web: int) -> Dict[str, float]:
    """The cost of one discovery at these settings; the CLI and the panel both use it."""
    return apify.estimate_discovery(
        keyword_reels=n_keywords * KEYWORD_REELS_PER_TERM,
        hashtag_reels=n_hashtags * cfg["discover_reels_per_hashtag"],
        details=n_seeds + n_web + cfg["discover_candidates"] + EXPAND_LIMIT,
        profile_reels=cfg["discover_shortlist"] * REELS_PER_ACCOUNT,
    )


def discovery_path(project: Path) -> Path:
    """`<project>/.contentos/discovery.json`."""
    return store.contentos_dir(project) / DISCOVERY_FILE_NAME


def render_table(doc: Dict[str, Any]) -> str:
    """The plain-text summary `discover` prints; every number comes from `doc`."""
    settings = doc["settings"]
    lines = [
        f"Held to: {_count(settings['min_followers'])}+ followers, a reel at least "
        f"{cadence_words(settings['post_every_days'])}, 1 in 4 reels at "
        f"{_count(settings['min_views'])}+ views."
    ]
    tiers = (
        (TIER_ESTABLISHED, f"Established ({_count(settings['established_at'])}+ followers):"),
        (TIER_RISING, f"Rising ({_count(settings['min_followers'])} to "
                      f"{_count(settings['established_at'])} followers):"),
    )
    number = 0
    for tier, heading in tiers:
        group = [row for row in doc["candidates"] if row["tier"] == tier]
        if not group:
            continue
        lines.append(heading)
        for row in group:
            number += 1
            found = ", ".join(row["sources"]) or "-"
            followers = row.get("followers")
            followers_text = "followers unknown" if followers is None else f"{_count(followers)} followers"
            lines.append(
                f"{number:>2}. @{row['handle']}  {followers_text}  "
                f"1 in 4 reels: {_count(row['top_quarter_plays'])} views  "
                f"{row['posts_per_week']} reels a week  found via {found}"
            )
    if not doc["candidates"] and doc["partial"]:
        lines.append("Nobody cleared the bar in the time there was. Run it again to finish.")
    elif not doc["candidates"]:
        lines.append("Nobody cleared the bar. Try other keyword phrases, more web finds, or lower settings.")
    if doc["dropped"]:
        counts: Dict[str, int] = {}
        for item in doc["dropped"]:
            counts[item["reason"]] = counts.get(item["reason"], 0) + 1
        parts = [f"{_REASON_LABELS.get(reason, reason)} ({count})" for reason, count in counts.items()]
        lines.append(f"Left out {len(doc['dropped'])}: {', '.join(parts)}.")
    lines.append(f"Beating their own average in the last 30 days: {len(doc['breakouts'])} reels.")
    if doc["partial"]:
        lines.append("Some accounts were not checked or measured in time. Run it again to finish them.")
    return "\n".join(lines)


def result_line(doc: Dict[str, Any], project: Path) -> Dict[str, Any]:
    """The `RESULT {...}` payload for the CLI and the panel's status."""
    tiers = [row["tier"] for row in doc["candidates"]]
    return {
        "discovery_path": str(discovery_path(project)),
        "candidates": len(tiers),
        "established": tiers.count(TIER_ESTABLISHED),
        "rising": tiers.count(TIER_RISING),
        "dropped": len(doc["dropped"]),
        "breakouts": len(doc["breakouts"]),
        "cost_estimate_usd": doc["cost_estimate_usd"],
        "partial": doc["partial"],
        "warnings": doc["warnings"],
    }


# ---------------------------------------------------------------------------
# run_discover
# ---------------------------------------------------------------------------


def _add_source(sources: Dict[str, List[str]], handle: str, source: str) -> None:
    listed = sources.setdefault(handle, [])
    if source not in listed:
        listed.append(source)


def _default_transport(mock: bool) -> apify.Transport:
    if not mock:
        return apify.HttpTransport()

    def load(name: str) -> List[dict]:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return apify.FixtureTransport(
        load(DISCOVER_REELS_FIXTURE_NAME),
        load(DISCOVER_PROFILES_FIXTURE_NAME),
        hashtag_items=load(HASHTAG_REELS_FIXTURE_NAME),
        keyword_items=load(KEYWORD_REELS_FIXTURE_NAME),
    )


@dataclass
class _Started:
    """One of discover's runs: its step's name, the Apify run (None when skipped), and when it began."""

    step: str
    run: Optional[apify.RunRef]
    at: float


class _Apify:
    """Starts discover's Apify runs inside one time budget and one charge cap.

    Design spec, "0.6.0 changes": each run's `maxTotalChargeUsd` is the cap
    minus what earlier runs reserved (their `maxItems` times the price), and
    every wait and every run's own `timeout` get what is left of
    `BUDGET_S`, so all the runs fit one 10-minute Bash call. A run that
    would start with under `MIN_RUN_S` left is skipped and counts as timed
    out. Progress goes to `log` as plain lines named after the step, never
    a run id.
    """

    def __init__(
        self,
        token: str,
        cfg: Dict[str, Any],
        transport: apify.Transport,
        log: Callable[[str], None],
        warnings: List[str],
        clock: Callable[[], float],
    ) -> None:
        self.token = token
        self.cfg = cfg
        self.transport = transport
        self.log = log
        self.warnings = warnings
        self.clock = clock
        self.deadline = clock() + BUDGET_S
        self.reserved = 0.0

    def _left(self) -> float:
        """What is left of the budget for one run or wait, capped by `apify_timeout_s`."""
        return max(min(self.deadline - self.clock(), self.cfg["apify_timeout_s"]), 0.0)

    def start(
        self, actor_input: dict, max_items: int, step: str, runs_path: str = apify.ACTOR_RUNS_PATH
    ) -> _Started:
        if self.deadline - self.clock() < MIN_RUN_S:
            self.log(f"{step.capitalize()} skipped: out of time.")
            self.warnings.append(f"There was no time left for the {step}, so it was skipped.")
            return _Started(step, None, self.clock())
        cap = round(max(self.cfg["apify_max_charge_usd"] - self.reserved, apify.PRICE_PER_RESULT), 4)
        self.reserved += max_items * apify.PRICE_PER_RESULT
        run = apify.start_run(
            self.token, actor_input, cap, max_items, self._left(), self.transport, runs_path=runs_path
        )
        self.log(f"{step.capitalize()} started.")
        return _Started(step, run, self.clock())

    def finish(self, started: _Started) -> Tuple[List[dict], bool]:
        """The run's items, and True when it was cut short or skipped."""
        if started.run is None:
            return [], True
        run = apify.wait_for_run(
            self.token, started.run, self.cfg["poll_interval_s"], self._left(), self.transport,
            sleep=self._still_running(started), clock=self.clock,
        )
        items = list(apify.iter_dataset_items(self.token, run.dataset_id, self.transport))
        if run.partial:
            self.log(f"{started.step.capitalize()} ran out of time.")
            self.warnings.append(f"The {started.step} ran out of time, so its results are incomplete.")
        else:
            self.log(f"{started.step.capitalize()} done.")
        return items, run.partial

    def _still_running(self, started: _Started) -> Callable[[float], None]:
        """`wait_for_run`'s sleep, which only runs between polls of a run still going.

        It says so at most once per `PROGRESS_EVERY_S` of the clock, so a
        run that finishes on its first poll says nothing here.
        """
        next_line = [started.at + PROGRESS_EVERY_S]

        def sleep(seconds: float) -> None:
            now = self.clock()
            if now >= next_line[0]:
                minutes = int((now - started.at) // 60)
                self.log(f"{started.step.capitalize()} still running, {minutes} min so far.")
                next_line[0] = now + PROGRESS_EVERY_S
            time.sleep(seconds)

        return sleep


def _plural(count: int, word: str) -> str:
    """`1 account`, `7 accounts`."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _dropped(handle: str, reason: str, row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {"handle": handle, "reason": reason}
    if row is not None and row.get("followers") is not None:
        item["followers"] = row["followers"]
    return item


def _candidate(
    handle: str,
    row: Dict[str, Any],
    metrics: Dict[str, Any],
    sources: Dict[str, List[str]],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    candidate = {
        "handle": handle, "url": row["url"], "full_name": row["full_name"],
        "followers": row["followers"], "verified": row["verified"], "category": row["category"],
        "bio": row["bio"], "tier": tier_of(row["followers"], cfg), "sources": list(sources.get(handle, [])),
    }
    candidate.update(metrics)
    return candidate


def run_discover(
    project: Path,
    cfg: Dict[str, Any],
    keys: Optional[Keys],
    hashtags: List[str],
    keywords: List[str],
    seeds: Optional[List[str]] = None,
    handles_file: Optional[Path] = None,
    web_entries: Optional[List[Any]] = None,
    mock: bool = False,
    yes: bool = False,
    estimate_only: bool = False,
    transport: Optional[apify.Transport] = None,
    log: Optional[Callable[[str], None]] = None,
    now: Optional[datetime] = None,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """Run one discovery and write `.contentos/discovery.json` (version 2).

    `seeds` are handles the creator typed; the project's `competitors` are
    always added. The web finds come from `web_entries` (the panel) or
    `handles_file` (the CLI). Raises DiscoverError (exit 2) when there is
    nothing to search or the web file is bad, and the research stage's
    errors for the cost cap (6), a missing confirmation (3), a missing key
    (4), and an upstream failure (5). Nothing is written before the gates
    pass. Returns the document it wrote.

    Progress goes to `log` as plain step lines. A step cut short or skipped
    for time makes the document `partial`: accounts it never reached are
    "not checked in time", and creators it could not fully measure are "not
    measured in time", never missing or failing.
    """
    if log is None:
        log = _default_log
    tags = normalize_hashtags(hashtags)
    terms = normalize_keywords(keywords)
    if web_entries is None:
        entries, warnings = load_web_handles(handles_file)
    else:
        entries, warnings = normalize_web_entries(web_entries)
    # Design spec, "0.6.0 changes" (Sources): format_accounts are never candidates.
    seed_handles, format_handles, seed_warnings = never_recommended(cfg, list(seeds or []))
    warnings.extend(seed_warnings)
    web = web_handles(entries, seed_handles + format_handles)
    if len({entry["handle"] for entry in entries} - set(seed_handles)) > MAX_WEB_HANDLES:
        warnings.append(f"only the first {MAX_WEB_HANDLES} web handles were checked")
    if not (terms or tags or web or seed_handles):
        raise DiscoverError(
            "discover needs something to search: --keywords, --hashtags, a --handles-file, or a watch list"
        )

    cost = estimate(cfg, len(terms), len(tags), len(seed_handles), len(web))
    cap = cfg["apify_max_charge_usd"]
    payload = dict(
        cost, keywords=terms, hashtags=tags, seeds=len(seed_handles), web_handles=len(web),
        cap_usd=cap, within_cap=cost["total_usd"] <= cap,
    )
    research.check_gates(payload, mock, yes, estimate_only, keys)

    if transport is None:
        transport = _default_transport(mock)
    token = (keys.apify if keys else None) or "mock-token"
    if now is None:
        now = research.MOCK_NOW if mock else datetime.now(timezone.utc)
    is_niche = niche_matcher(terms, tags)
    runs = _Apify(token, cfg, transport, log, warnings, clock)
    sources: Dict[str, List[str]] = {}
    for entry in entries:
        if entry["handle"] in web:
            _add_source(sources, entry["handle"], f"web:{entry['source_url']}")
    dropped: List[Dict[str, Any]] = []
    survivors: Dict[str, Dict[str, Any]] = {}

    def pass_one(handles: List[str], items: List[Any], pointers: List[Dict[str, Any]], cut: bool) -> None:
        rows, error_items = index_profiles(items)
        for handle in handles:
            status = profile_status(handle, rows, error_items, partial=cut)
            if handle in seed_handles:
                if status == instagram.STATUS_OK:
                    pointers.append(rows[handle])
                else:
                    warnings.append(_SEED_WARNINGS[status].format(handle=handle))
                continue
            if status != instagram.STATUS_OK:
                dropped.append(_dropped(handle, status, rows.get(handle)))
                continue
            reason = pass1_reason(rows[handle], cfg, now)
            if reason is not None:
                dropped.append(_dropped(handle, reason, rows[handle]))
                continue
            survivors[handle] = rows[handle]
            pointers.append(rows[handle])

    step_a = seed_handles + web
    chosen: List[str] = []
    # `partial`: any step cut short or skipped; `reels_cut`: step C was.
    partial = reels_cut = False
    reels_by_owner: Dict[str, List[Dict[str, Any]]] = {}
    try:
        log("Step 1 of 3: searching Instagram and checking accounts.")
        started = []
        if terms:
            started.append(("keyword", runs.start(
                apify.build_keyword_reels_input(terms, KEYWORD_REELS_PER_TERM),
                len(terms) * KEYWORD_REELS_PER_TERM, STEP_KEYWORD,
                runs_path=apify.KEYWORD_ACTOR_RUNS_PATH,
            )))
        if tags:
            started.append(("hashtag", runs.start(
                apify.build_hashtag_reels_input(tags, cfg["discover_reels_per_hashtag"], cfg["lookback_days"]),
                len(tags) * cfg["discover_reels_per_hashtag"], STEP_HASHTAG,
            )))
        if step_a:
            started.append(("details", runs.start(apify.build_details_input(step_a), len(step_a), STEP_DETAILS)))
        fetched = {kind: runs.finish(ref) for kind, ref in started}
        partial = any(cut for _items, cut in fetched.values())
        not_run: Tuple[List[dict], bool] = ([], False)

        pointers: List[Dict[str, Any]] = []
        details_items, details_cut = fetched.get("details", not_run)
        pass_one(step_a, details_items, pointers, details_cut)

        authors = search_authors(fetched.get("keyword", not_run)[0] + fetched.get("hashtag", not_run)[0])
        found = top_authors(authors, cfg["discover_candidates"], set(step_a) | set(format_handles))
        for handle in step_a + found:
            for source in authors.get(handle, {}).get("sources", []):
                _add_source(sources, handle, source)
        pointed = expansion_pointers(pointers, set(step_a) | set(found) | set(format_handles))
        expanded = rank_expansion(pointed, EXPAND_LIMIT)
        for handle in expanded:
            for pointer in sorted(pointed[handle]):
                _add_source(sources, handle, f"related:{pointer}")

        step_b = found + expanded
        if step_b:
            log(f"Step 2 of 3: checking {_plural(len(step_b), 'more account')}.")
            items_b, cut_b = runs.finish(runs.start(apify.build_details_input(step_b), len(step_b), STEP_DETAILS))
            partial = partial or cut_b
            pass_one(step_b, items_b, [], cut_b)
        else:
            log("Step 2 of 3: no more accounts to check.")

        pool = [
            {"handle": handle, "followers": row["followers"], "niche_hit": latest_niche_hit(row, is_niche)}
            for handle, row in survivors.items()
        ]
        chosen = shortlist(pool, cfg["discover_shortlist"], cfg["small_account_followers"])
        for item in pool:
            if item["handle"] not in chosen:
                dropped.append(_dropped(item["handle"], REASON_SHORTLIST_FULL, survivors[item["handle"]]))

        if chosen:
            log(f"Step 3 of 3: measuring the reels of {_plural(len(chosen), 'creator')}.")
            reel_items, reels_cut = runs.finish(runs.start(
                apify.build_reels_input(chosen, REELS_PER_ACCOUNT, WINDOW_DAYS),
                len(chosen) * REELS_PER_ACCOUNT, STEP_REELS,
            ))
            partial = partial or reels_cut
            reels_by_owner = group_reels(reel_items, chosen)
        else:
            log("Step 3 of 3: nobody to measure.")
    except (apify.ApifyRunFailed, HTTPError, OSError) as exc:
        raise research.UpstreamFailure(str(exc)) from exc

    candidates: List[Dict[str, Any]] = []
    for handle in chosen:
        row = survivors[handle]
        owned = reels_by_owner.get(handle, [])
        if reels_cut and not owned:
            dropped.append(_dropped(handle, REASON_NOT_MEASURED, row))
            continue
        metrics = measure(owned, now, is_niche)
        reason = pass2_reason(metrics, cfg)
        if reason is not None:
            # A cut-short reels check may have stopped partway through a
            # creator's list, so a short list is not judged.
            if reels_cut and len(owned) < REELS_PER_ACCOUNT:
                reason = REASON_NOT_MEASURED
            dropped.append(_dropped(handle, reason, row))
            continue
        candidates.append(_candidate(handle, row, metrics, sources, cfg))
    candidates = order_candidates(candidates)

    doc = {
        "version": DISCOVERY_VERSION,
        "created_at": now.isoformat(),
        "mode": "mock" if mock else "live",
        "niche": {"keywords": terms, "hashtags": tags},
        "seeds": seed_handles,
        "settings": {
            "min_followers": cfg["discover_min_followers"],
            "min_views": cfg["discover_min_views"],
            "post_every_days": cfg["discover_post_every_days"],
            "shortlist": cfg["discover_shortlist"],
            "candidates": cfg["discover_candidates"],
            "established_at": cfg["small_account_followers"],
        },
        "cost_estimate_usd": cost["total_usd"],
        "partial": partial,
        "candidates": candidates,
        "breakouts": find_breakouts(candidates, reels_by_owner, cfg, now),
        "dropped": dropped,
        "warnings": warnings,
    }
    store.write_json_atomic(discovery_path(project), doc)
    return doc
