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
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from lib import apify, codes, instagram, research, setup, store
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
        print(
            f"{index:>2}. @{row['handle']}  {followers} followers  "
            f"best reel {row['best_plays']:,} plays  found via {found}"
        )
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
