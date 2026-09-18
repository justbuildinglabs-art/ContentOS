"""Apify REST client: actor runs, dataset paging, and cost estimates.

Every call goes through a `Transport` (never `lib.http` directly), so
callers can swap `HttpTransport` (delegates to `lib.http.request_json`)
for `FixtureTransport` under `--mock`, touching no network at all. See
the design spec's "External API facts" (Apify) for the REST shapes
wrapped here and "Stage 1 -- research" steps 1-2 for how the research
stage (Task 10) uses `estimate_cost`, `start_run`, `wait_for_run`, and
`iter_dataset_items` to run the reels and details actor runs and page
through their datasets. `contentos.py`'s `diagnose --live` handler is
the only other caller here, via `check_token`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Protocol

from lib import http

API_BASE = "https://api.apify.com/v2"
ACTOR_ID = "apify~instagram-scraper"
# Single constant per the design spec ("the docs also show /v2/actors/");
# confirmed against the live API in the live smoke before being trusted.
ACTOR_RUNS_PATH = "/acts/apify~instagram-scraper/runs"
PRICE_PER_RESULT = 0.0027
RUN_TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}


def build_reels_input(
    handles: List[str], results_limit: int, baseline_lookback_days: int
) -> dict:
    """Build the actor input for a "reels" run (design spec, Apify facts).

    `resultsType` "reels" and "details" cannot be mixed in one run, so
    this and `build_details_input` are always started as two separate
    `start_run` calls.
    """
    return {
        "directUrls": [f"https://www.instagram.com/{handle}/" for handle in handles],
        "resultsType": "reels",
        "resultsLimit": results_limit,
        "onlyPostsNewerThan": f"{baseline_lookback_days} days",
        "skipPinnedPosts": True,
    }


def build_details_input(handles: List[str]) -> dict:
    """Build the actor input for a "details" (profile) run."""
    return {
        "directUrls": [f"https://www.instagram.com/{handle}/" for handle in handles],
        "resultsType": "details",
    }


@dataclass
class CostEstimate:
    """Apify cost estimate for one research run (spec Stage 1, step 1)."""

    reels_usd: float
    details_usd: float
    total_usd: float
    max_items: int


def estimate_cost(
    n_accounts: int, reels_per_account: int, price: float = PRICE_PER_RESULT
) -> CostEstimate:
    """Estimate the Apify cost for scraping `n_accounts` competitor accounts.

    Design spec Stage 1 step 1: `reels_usd` is one result per reel
    across every account (the reels run), `details_usd` is one result
    per account (the details run returns exactly one profile each).
    `max_items` is the `maxItems` cap `start_run` passes for the reels
    run. USD values are rounded to 4 decimals.
    """
    raw_reels = n_accounts * reels_per_account * price
    raw_details = n_accounts * price
    return CostEstimate(
        reels_usd=round(raw_reels, 4),
        details_usd=round(raw_details, 4),
        total_usd=round(raw_reels + raw_details, 4),
        max_items=n_accounts * reels_per_account,
    )


class Transport(Protocol):
    """Structural interface every Apify transport implements.

    Any object with a matching `request_json` satisfies this --
    `HttpTransport` for real calls, `FixtureTransport` for `--mock`, or
    a small scripted fake a test builds for one polling sequence.
    """

    def request_json(
        self,
        method: str,
        url: str,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        ...


class HttpTransport:
    """Transport that delegates to `lib.http.request_json` with its defaults.

    Only `method`/`url`/`headers`/`json_body`/`params` are threaded
    through; `request_json`'s own `timeout`/`retries`/`opener`/`sleep`
    defaults apply unchanged.
    """

    def request_json(
        self,
        method: str,
        url: str,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        return http.request_json(
            method, url, headers=headers, json_body=json_body, params=params
        )


class FixtureTransport:
    """Deterministic transport for `--mock`: no network touched.

    Answers exactly the four Apify routes the research stage calls --
    starting a run (branching on the input's `resultsType`), polling a
    run to `SUCCEEDED`, paging a dataset's items, and checking a token
    -- and raises `http.HTTPError(404, ...)` for anything else. Every
    call is recorded in `.calls` regardless of route.

    Polling sequences (RUNNING -> SUCCEEDED, or a FAILED/TIMED-OUT run)
    need more than this fixture answers in one canned shot -- a run's
    status here is always SUCCEEDED on the very first poll. Tests that
    need those sequences build their own small scripted transport.
    """

    def __init__(self, reels_items: List[dict], details_items: List[dict]) -> None:
        self.reels_items = reels_items
        self.details_items = details_items
        self.calls: List[Dict[str, Any]] = []
        self._datasets: Dict[str, List[dict]] = {
            "ds-reels": reels_items,
            "ds-details": details_items,
        }
        self._dataset_of_run: Dict[str, str] = {
            "mock-reels": "ds-reels",
            "mock-details": "ds-details",
        }

    def request_json(
        self,
        method: str,
        url: str,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "params": params,
            }
        )

        runs_url = f"{API_BASE}{ACTOR_RUNS_PATH}"
        if method == "POST" and url == runs_url:
            results_type = (json_body or {}).get("resultsType")
            run_id = "mock-reels" if results_type == "reels" else "mock-details"
            return {
                "data": {
                    "id": run_id,
                    "status": "READY",
                    "defaultDatasetId": self._dataset_of_run[run_id],
                }
            }

        run_prefix = f"{API_BASE}/actor-runs/"
        if method == "GET" and url.startswith(run_prefix):
            run_id = url[len(run_prefix):]
            dataset_id = self._dataset_of_run.get(run_id, "ds-reels")
            return {
                "data": {"id": run_id, "status": "SUCCEEDED", "defaultDatasetId": dataset_id}
            }

        dataset_prefix = f"{API_BASE}/datasets/"
        dataset_suffix = "/items"
        if method == "GET" and url.startswith(dataset_prefix) and url.endswith(dataset_suffix):
            dataset_id = url[len(dataset_prefix):-len(dataset_suffix)]
            items = self._datasets.get(dataset_id, [])
            offset = int((params or {}).get("offset", 0))
            limit = int((params or {}).get("limit", len(items)))
            return items[offset:offset + limit]

        if method == "GET" and url == f"{API_BASE}/users/me":
            return {"data": {"username": "mock"}}

        raise http.HTTPError(404, f"unknown fixture route: {method} {url}")


@dataclass
class RunRef:
    """A reference to one Apify actor run, as returned by `start_run`/`wait_for_run`."""

    id: str
    status: str
    dataset_id: str
    partial: bool = False


class ApifyRunFailed(Exception):
    """Raised by `wait_for_run` when the run terminates FAILED or ABORTED."""


def start_run(
    token: str,
    actor_input: dict,
    max_total_charge_usd: float,
    max_items: int,
    timeout_s: float,
    transport: Transport,
) -> RunRef:
    """Start one Apify actor run and return its initial `RunRef`.

    Design spec Apify facts: `maxTotalChargeUsd`/`maxItems`/`timeout`
    cap the run's cost and runtime; `waitForFinish=0` returns
    immediately instead of blocking on the sync endpoint (which 408s at
    300 s and must never be used).
    """
    response = transport.request_json(
        "POST",
        f"{API_BASE}{ACTOR_RUNS_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        json_body=actor_input,
        params={
            "maxTotalChargeUsd": max_total_charge_usd,
            "maxItems": max_items,
            "timeout": int(timeout_s),
            "waitForFinish": 0,
        },
    )
    data = response["data"]
    return RunRef(id=data["id"], status=data["status"], dataset_id=data["defaultDatasetId"])


def wait_for_run(
    token: str,
    run: RunRef,
    poll_s: float,
    timeout_s: float,
    transport: Transport,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Optional[Callable[[str], None]] = None,
) -> RunRef:
    """Poll `GET /actor-runs/<id>` until `run` reaches a terminal status.

    `SUCCEEDED` returns the updated `RunRef`. `FAILED`/`ABORTED` raise
    `ApifyRunFailed(status)`. `TIMED-OUT` (Apify's own status) returns
    with `partial=True`, the same outcome as this side's `timeout_s`
    elapsing first while the run is still going -- the two differ only
    in who gave up first. `sleep(poll_s)` runs between polls, never
    after a terminal one. `log`, when given, is called with one line
    per poll.
    """
    start = clock()
    current = run
    while True:
        response = transport.request_json(
            "GET",
            f"{API_BASE}/actor-runs/{current.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = response["data"]
        current = RunRef(
            id=data["id"], status=data["status"], dataset_id=data["defaultDatasetId"]
        )
        if log is not None:
            log(f"run {current.id}: {current.status}")

        if current.status == "SUCCEEDED":
            return current
        if current.status in ("FAILED", "ABORTED"):
            raise ApifyRunFailed(current.status)
        if current.status == "TIMED-OUT":
            return RunRef(current.id, current.status, current.dataset_id, partial=True)

        if clock() - start >= timeout_s:
            return RunRef(current.id, current.status, current.dataset_id, partial=True)
        sleep(poll_s)


def iter_dataset_items(
    token: str, dataset_id: str, transport: Transport, page: int = 1000
) -> Iterator[dict]:
    """Yield every item in an Apify dataset, paging by offset.

    Stops after a page shorter than `page` items, including an empty
    one -- the caller never has to know the dataset's total size ahead
    of time.
    """
    offset = 0
    while True:
        items = transport.request_json(
            "GET",
            f"{API_BASE}/datasets/{dataset_id}/items",
            headers={"Authorization": f"Bearer {token}"},
            params={"clean": "true", "format": "json", "limit": page, "offset": offset},
        )
        for item in items:
            yield item
        if len(items) < page:
            return
        offset += page


def check_token(token: str, transport: Transport) -> bool:
    """Validate `token` at zero cost via `GET /users/me`.

    Never raises: any `http.HTTPError` (e.g. an expired/invalid token)
    or `OSError` is treated as an invalid or unreachable token and
    reported as `False`. `OSError` is the catch-all on purpose --
    `urllib.error.URLError` and `socket.timeout`/`TimeoutError` are
    both `OSError` subclasses, so a DNS failure, a refused connection,
    and a real socket timeout all collapse into the same `False`
    without needing to name each one. A bare `Exception` that isn't one
    of these is a genuine programming error and is left to propagate.
    """
    try:
        transport.request_json(
            "GET",
            f"{API_BASE}/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    except (http.HTTPError, OSError):
        return False
    return True
