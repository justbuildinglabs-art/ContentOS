"""Creator discovery: find accounts worth watching from hashtags, keywords, and web handles.

Design spec, "0.5.0 changes". A creator with no marketing background
cannot name 3 to 8 accounts in their niche, so this finds them. Three
runs of the same scraper the research stage uses:

- Run A: recent reels under each hashtag, grouped by author.
- Run B: one profile search per keyword.
- Run C: a details run over every candidate, which is also what drops a
  stale or invented handle the orchestrator's web search brought back.

Python never searches the web. The orchestrator does, and hands the
handles in through `--handles-file`. Nothing here needs `setup` to have
run (`store.load_discovery_config`). The cost gates, their order, and
their exit codes are the research stage's own (`research.check_gates`).
"""
from __future__ import annotations

import json
import math
import re
import statistics
import sys
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
PROFILE_SEARCH_FIXTURE_NAME = "apify_profile_search_sample.json"
DISCOVER_PROFILES_FIXTURE_NAME = "apify_discover_profiles_sample.json"

DISCOVERY_FILE_NAME = "discovery.json"
DISCOVERY_VERSION = 1

# Hits asked of each profile search (design spec: `searchLimit` 10).
SEARCH_LIMIT = 10
# A small account with a big reel is the best outlier source.
SMALL_ACCOUNT_BONUS = 1.25
SAMPLE_CAPTIONS = 2
_CAPTION_CHARS = 140

_HASHTAG_RE = re.compile(r"^\w+$")

# 0.6.0 (design spec, "0.6.0 changes").
MAX_WEB_HANDLES = 40
ACTIVE_DAYS = 30
REASON_NO_RECENT_REEL = "no reel in 30 days"
_CAPTION_TAG_RE = re.compile(r"#(\w+)")
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
_REASON_LABELS = {"not_found": "not found", "error": "could not be checked", "private": "private"}


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
    handle: str, rows: Dict[str, Dict[str, Any]], error_items: List[Dict[str, Any]]
) -> str:
    """`ok`, `private`, `not_found`, or `error` for one handle a details run was asked for."""
    error_item = instagram._find_error_item(error_items, handle)
    if error_item is not None:
        not_found = instagram._is_not_found_error(error_item.get("error"))
        return instagram.STATUS_NOT_FOUND if not_found else instagram.STATUS_ERROR
    row = rows.get(handle)
    if row is None:
        # No profile and no error item: the scrape returned nothing for it.
        return instagram.STATUS_NOT_FOUND
    return instagram.STATUS_PRIVATE if row["private"] else instagram.STATUS_OK


def niche_matcher(keywords: List[str], hashtags: List[str]) -> Callable[..., bool]:
    """Build `is_niche(text, tags=None)`: a keyword phrase or a niche hashtag is present.

    Phrases match case-insensitively on word boundaries with any run of
    spaces between words. Hashtags match whole, from the tag list or from
    `#tags` inside the text. With no terms at all, nothing matches.
    """
    patterns = [
        re.compile(r"\b" + r"\s+".join(map(re.escape, keyword.split())) + r"\b", re.IGNORECASE)
        for keyword in keywords
        if keyword.split()
    ]
    wanted = {tag.lower() for tag in hashtags}

    def is_niche(text: Any, tags: Any = None) -> bool:
        body = text if isinstance(text, str) else ""
        if any(pattern.search(body) for pattern in patterns):
            return True
        found = {tag.lstrip("#").lower() for tag in (tags if isinstance(tags, list) else []) if isinstance(tag, str)}
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

    The follower floor applies to every source (0 turns it off). When the
    details run returned `latestPosts`, an account needs an unpinned reel
    from the last 30 days; with no `latestPosts`, pass 2 decides activity.
    """
    floor = cfg["discover_min_followers"]
    if (row.get("followers") or 0) < floor:
        return f"under {_count(floor)} followers"
    latest = row.get("latest_posts") or []
    if latest:
        recent = [
            post for post in latest
            if post["is_reel"] and not post["is_pinned"] and post["timestamp"]
            and _days_ago(post["timestamp"], now) <= ACTIVE_DAYS
        ]
        if not recent:
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
            lines.append(
                f"{number:>2}. @{row['handle']}  {_count(row['followers'])} followers  "
                f"1 in 4 reels: {_count(row['top_quarter_plays'])} views  "
                f"{row['posts_per_week']} reels a week  found via {found}"
            )
    if not doc["candidates"]:
        lines.append("Nobody cleared the bar. Try other keyword phrases, more web finds, or lower settings.")
    if doc["dropped"]:
        counts: Dict[str, int] = {}
        for item in doc["dropped"]:
            counts[item["reason"]] = counts.get(item["reason"], 0) + 1
        parts = [f"{_REASON_LABELS.get(reason, reason)} ({count})" for reason, count in counts.items()]
        lines.append(f"Left out {len(doc['dropped'])}: {', '.join(parts)}.")
    lines.append(f"Beating their own average in the last 30 days: {len(doc['breakouts'])} reels.")
    if doc["partial"]:
        lines.append("Some accounts were not measured in time. Run it again to finish them.")
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
# Aggregation and ranking (pure)
# ---------------------------------------------------------------------------


def aggregate_authors(reel_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Group Run A's reels by lowercase author.

    Uses `instagram.normalize_reel`, so photos, pinned posts, and error
    items fall away exactly as they do in research. A reel flagged as a
    paid partnership does not count: an account found only through
    sponsored reels is a brand channel, not a creator to learn from.
    """
    reels = instagram.dedupe_by_shortcode(
        [
            dict(reel, _hashtag=hashtag_from_input_url(item.get("inputUrl")))
            for item in reel_items
            if isinstance(item, dict)
            for reel in [instagram.normalize_reel(item)]
            if reel is not None
        ]
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for reel in reels:
        owner = str(reel.get("ownerUsername") or "").lower()
        if owner and not reel["paid_partnership"]:
            grouped.setdefault(owner, []).append(reel)

    authors: Dict[str, Dict[str, Any]] = {}
    for owner, owned in grouped.items():
        plays = [reel["plays"] for reel in owned if reel.get("plays") is not None]
        by_plays = sorted(owned, key=lambda reel: (-(reel.get("plays") or 0), reel["shortCode"]))
        authors[owner] = {
            "reels_seen": len(owned),
            "hashtags_hit": sorted({reel["_hashtag"] for reel in owned if reel["_hashtag"]}),
            "best_plays": max(plays) if plays else 0,
            "median_plays": statistics.median(plays) if plays else 0,
            "sample_captions": [
                reel["caption"][:_CAPTION_CHARS] for reel in by_plays[:SAMPLE_CAPTIONS]
            ],
        }
    return authors


def author_score(author: Dict[str, Any]) -> float:
    """`log2(max(best_plays, 1)) * max(distinct hashtags hit, 1)`, before the size bonus."""
    return math.log2(max(author["best_plays"], 1)) * max(len(author["hashtags_hit"]), 1)


def _top_authors(authors: Dict[str, Dict[str, Any]], limit: int) -> List[str]:
    return sorted(authors, key=lambda handle: (-author_score(authors[handle]), handle))[:limit]


def _add_source(sources: Dict[str, List[str]], handle: str, source: str) -> None:
    listed = sources.setdefault(handle, [])
    if source not in listed:
        listed.append(source)


def _profile_status(
    handle: str, profiles: Dict[str, Dict[str, Any]], error_items: List[Dict[str, Any]]
) -> str:
    profile = profiles.get(handle)
    if profile is None:
        for item in error_items:
            if instagram._handle_from_input_url(str(item.get("inputUrl") or "")).lower() == handle:
                not_found = instagram._is_not_found_error(item.get("error"))
                return instagram.STATUS_NOT_FOUND if not_found else instagram.STATUS_ERROR
        # No profile and no error item: the scrape returned nothing for it.
        return instagram.STATUS_NOT_FOUND
    return instagram.STATUS_PRIVATE if profile["private"] else instagram.STATUS_OK


def rank_candidates(
    handles: List[str],
    authors: Dict[str, Dict[str, Any]],
    sources: Dict[str, List[str]],
    profile_items: List[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Turn Run C's profiles into `(candidates, dropped)`.

    Dropped: `not_found`, `error`, `private`, and a keyword or web
    candidate under `discover_min_followers` (profile search returns
    many tiny and business accounts). An author found through a hashtag
    is never dropped for size. Scored candidates come first by score,
    then unscored ones by followers; ties break on handle.
    """
    profiles: Dict[str, Dict[str, Any]] = {}
    bios: Dict[str, str] = {}
    for item in profile_items:
        profile = instagram.normalize_profile(item) if isinstance(item, dict) else None
        if profile is not None:
            key = profile["username"].lower()
            profiles[key] = profile
            bios[key] = item.get("biography") if isinstance(item.get("biography"), str) else ""
    error_items = [item for item in profile_items if isinstance(item, dict) and "error" in item]

    min_followers = cfg["discover_min_followers"]
    small_under = cfg["small_account_followers"]
    candidates: List[Dict[str, Any]] = []
    dropped: List[Dict[str, str]] = []
    for handle in handles:
        status = _profile_status(handle, profiles, error_items)
        if status != instagram.STATUS_OK:
            dropped.append({"handle": handle, "reason": status})
            continue
        profile = profiles[handle]
        followers = profile["followers"]
        author = authors.get(handle)
        if author is None and (followers or 0) < min_followers:
            dropped.append({"handle": handle, "reason": f"under {min_followers} followers"})
            continue

        small = followers is not None and followers < small_under
        score = 0.0
        if author is not None:
            score = author_score(author) * (SMALL_ACCOUNT_BONUS if small else 1.0)
        candidates.append(
            {
                "handle": handle,
                "url": profile["url"],
                "followers": followers,
                "verified": profile["verified"],
                "posts": profile["posts"],
                "bio": bios.get(handle, ""),
                "small_account": small,
                "reels_seen": author["reels_seen"] if author else 0,
                "hashtags_hit": author["hashtags_hit"] if author else [],
                "best_plays": author["best_plays"] if author else 0,
                "median_plays": author["median_plays"] if author else 0,
                "sample_captions": author["sample_captions"] if author else [],
                "sources": sources.get(handle, []),
                "score": round(score, 2) if author else 0,
            }
        )

    candidates.sort(key=lambda row: (-row["score"], -(row["followers"] or 0), row["handle"]))
    return candidates, dropped


# ---------------------------------------------------------------------------
# run_discover
# ---------------------------------------------------------------------------


def _default_transport(mock: bool) -> apify.Transport:
    if not mock:
        return apify.HttpTransport()

    def load(name: str) -> List[dict]:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return apify.FixtureTransport(
        [],
        load(DISCOVER_PROFILES_FIXTURE_NAME),
        hashtag_items=load(HASHTAG_REELS_FIXTURE_NAME),
        search_items=load(PROFILE_SEARCH_FIXTURE_NAME),
    )


def _scrape(
    token: str,
    actor_input: dict,
    max_items: int,
    cfg: Dict[str, Any],
    transport: apify.Transport,
    log: Callable[[str], None],
    warnings: List[str],
    label: str,
) -> List[dict]:
    """Start one run, wait for it, and return its items."""
    run = apify.start_run(
        token, actor_input, cfg["apify_max_charge_usd"], max_items, cfg["apify_timeout_s"], transport
    )
    run = apify.wait_for_run(
        token, run, cfg["poll_interval_s"], cfg["apify_timeout_s"], transport, log=log
    )
    if run.partial:
        warnings.append(f"{label} run {run.id} did not finish before timeout; results may be partial")
    return list(apify.iter_dataset_items(token, run.dataset_id, transport))


def _print_table(candidates: List[Dict[str, Any]], dropped: List[Dict[str, str]]) -> None:
    print("Accounts found, best first:")
    for index, row in enumerate(candidates, start=1):
        followers = "?" if row["followers"] is None else f"{row['followers']:,}"
        found = ", ".join(row["sources"]) or "-"
        reel = (
            f"best reel {row['best_plays']:,} plays"
            if row["reels_seen"]
            else "no reel seen under your hashtags"
        )
        print(f"{index:>2}. @{row['handle']}  {followers} followers  {reel}  found via {found}")
    if not candidates:
        print("None. Try broader hashtags.")
    if dropped:
        print(f"Left out: {len(dropped)} (private, not found, or too small).")


def run_discover(
    project: Path,
    cfg: Dict[str, Any],
    keys: Optional[Keys],
    hashtags: List[str],
    keywords: List[str],
    handles_file: Optional[Path] = None,
    mock: bool = False,
    yes: bool = False,
    estimate_only: bool = False,
    transport: Optional[apify.Transport] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Run `contentos.py discover` and write `.contentos/discovery.json`.

    Raises `DiscoverError` (exit 2) for no usable hashtag or a bad
    `--handles-file`, and the research stage's own errors for the cost
    cap (6), missing confirmation (3), missing key (4), and an upstream
    failure (5). Nothing is written before the gates pass.
    """
    if log is None:
        log = _default_log
    tags = normalize_hashtags(hashtags)
    if not tags:
        raise DiscoverError("discover needs at least one hashtag, like --hashtags habits,productivity")
    terms = normalize_keywords(keywords)
    web_entries, warnings = load_web_handles(handles_file)
    web_handles = sorted({entry["handle"] for entry in web_entries})

    estimate = apify.estimate_discover_cost(
        n_hashtags=len(tags),
        reels_per_hashtag=cfg["discover_reels_per_hashtag"],
        n_keywords=len(terms),
        search_limit=SEARCH_LIMIT,
        n_candidates=cfg["discover_candidates"],
        n_web_handles=len(web_handles),
    )
    cap = cfg["apify_max_charge_usd"]
    payload = dict(
        estimate, hashtags=tags, keywords=terms, web_handles=len(web_handles),
        cap_usd=cap, within_cap=estimate["total_usd"] <= cap,
    )
    research.check_gates(payload, mock, yes, estimate_only, keys)

    if transport is None:
        transport = _default_transport(mock)
    token = (keys.apify if keys else None) or "mock-token"

    try:
        reel_items = _scrape(
            token,
            apify.build_hashtag_reels_input(tags, cfg["discover_reels_per_hashtag"], cfg["lookback_days"]),
            len(tags) * cfg["discover_reels_per_hashtag"], cfg, transport, log, warnings, "hashtag reels",
        )
        authors = aggregate_authors(reel_items)
        sources: Dict[str, List[str]] = {}
        handles = _top_authors(authors, cfg["discover_candidates"])
        for handle in handles:
            for tag in authors[handle]["hashtags_hit"]:
                _add_source(sources, handle, f"hashtag:{tag}")

        for term in terms:
            items = _scrape(
                token, apify.build_profile_search_input(term, SEARCH_LIMIT),
                SEARCH_LIMIT, cfg, transport, log, warnings, f"profile search '{term}'",
            )
            for item in items:
                username = item.get("username") if isinstance(item, dict) else None
                if isinstance(username, str) and username:
                    handle = username.lower()
                    if handle not in handles:
                        handles.append(handle)
                    _add_source(sources, handle, f"keyword:{term}")

        for entry in web_entries:
            if entry["handle"] not in handles:
                handles.append(entry["handle"])
            _add_source(sources, entry["handle"], f"web:{entry['source_url']}")

        profile_items: List[dict] = []
        if handles:
            profile_items = _scrape(
                token, apify.build_details_input(handles), len(handles),
                cfg, transport, log, warnings, "details",
            )
    except (apify.ApifyRunFailed, HTTPError, OSError) as exc:
        raise research.UpstreamFailure(str(exc)) from exc

    candidates, dropped = rank_candidates(handles, authors, sources, profile_items, cfg)

    now = research.MOCK_NOW if mock else datetime.now(timezone.utc)
    out_path = store.contentos_dir(project) / DISCOVERY_FILE_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    store.write_json_atomic(
        out_path,
        {
            "version": DISCOVERY_VERSION,
            "created_at": now.isoformat(),
            "mode": "mock" if mock else "live",
            "hashtags": tags,
            "keywords": terms,
            "cost_estimate_usd": estimate["total_usd"],
            "candidates": candidates,
            "dropped": dropped,
            "warnings": warnings,
        },
    )

    _print_table(candidates, dropped)
    result = {
        "discovery_path": str(out_path),
        "candidates": len(candidates),
        "dropped": len(dropped),
        "cost_estimate_usd": estimate["total_usd"],
        "warnings": warnings,
    }
    print("RESULT " + json.dumps(result))
    return result
