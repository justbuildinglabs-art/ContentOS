"""Thin urllib wrapper: retrying JSON requests and capped file downloads.

This is the only module in the plugin that talks HTTP directly (see
CLAUDE.md and the design spec's "Global Constraints": stdlib only, no
third-party HTTP clients). `lib/apify.py` (Task 7) calls `request_json`
for the Apify REST API; `lib/video.py` (Task 11) calls
`download_to_file` to fetch Instagram CDN mp4s and cover images, which
expire, 403, or 410 unpredictably (design spec, "Stage 1 -- research",
step 7).

Only `urllib.request`, `urllib.error`, `urllib.parse`, `socket`, `json`,
`os`, and `time` are used here -- never the stdlib `http` package, to
avoid any confusion with this module's own name (`lib.http`).
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
INSTAGRAM_REFERER = "https://www.instagram.com/"

# Sleep before the i-th retry (0-indexed); the last value repeats for any
# retry beyond this. Shared by request_json and download_to_file so both
# back off the same way.
BACKOFF_S = (2, 4, 8)

_CHUNK_SIZE = 65536  # 64 KiB, per the download streaming contract.
_DOWNLOAD_TIMEOUT_S = 30  # download_to_file has no timeout parameter of its own.


class HTTPError(Exception):
    """An HTTP-level failure that request_json gave up on.

    Raised immediately for any 4xx other than 429, and after retries are
    exhausted for 5xx/429. Also raised when a response in the 2xx/3xx
    range has a non-empty body that is not valid JSON.
    """

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


@dataclass
class DownloadResult:
    """Outcome of one `download_to_file` call."""

    status: str  # "ok" | "too_large" | "expired" | "blocked" | "failed"
    bytes: int
    http_code: Optional[int]


def _status_of(response: Any) -> int:
    """Return a response-like object's HTTP status, whatever it calls it."""
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    return status


def _decode_body(body_bytes: bytes) -> str:
    """Decode a response body for HTTPError messages and JSON parsing."""
    if not body_bytes:
        return ""
    return body_bytes.decode("utf-8", errors="replace")


def _int_header(headers: Any, name: str) -> Optional[int]:
    """Return header `name` parsed as an int, or None if absent/invalid."""
    if not headers:
        return None
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _backoff_for(attempt: int) -> int:
    """Backoff seconds for the (0-indexed) attempt about to be retried."""
    return BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]


def _retry_delay(attempt: int, status: int, headers: Any) -> float:
    """Seconds to sleep before retrying `status`; honors 429's Retry-After."""
    if status == 429:
        retry_after = _int_header(headers, "Retry-After")
        if retry_after is not None:
            return retry_after
    return _backoff_for(attempt)


def _with_params(url: str, params: Optional[Mapping[str, Any]]) -> str:
    """Append `params` to `url` as a URL-encoded query string."""
    if not params:
        return url
    query = urllib.parse.urlencode(params)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{query}"


def _open(
    opener: Any, request: urllib.request.Request, timeout: int
) -> Tuple[int, Any, bytes]:
    """Run one attempt through `opener`, normalizing HTTPError to a tuple.

    A real `urllib.request.build_opener()` raises `urllib.error.HTTPError`
    for any non-2xx/3xx response; a fake test opener may instead just
    return a response object whose `.status` is already 4xx/5xx. Both
    shapes collapse here into the same `(status, headers, body)` tuple so
    callers only ever branch on a status code. `URLError`/`socket.timeout`
    (pure network failures, no status) are left to propagate.
    """
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        finally:
            exc.close()
        return exc.code, exc.headers, body

    try:
        status = _status_of(response)
        body = response.read()
        return status, response.headers, body
    finally:
        response.close()


def request_json(
    method: str,
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    json_body: Optional[Any] = None,
    params: Optional[Mapping[str, Any]] = None,
    timeout: int = 30,
    retries: int = 3,
    opener: Optional[Any] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Call a JSON HTTP API with retries, returning the parsed response body.

    `params` are URL-encoded onto `url`'s query string (appended with
    `?`, or `&` when `url` already has a query string). `json_body`, when
    given, is sent as a UTF-8 JSON request body with `Content-Type:
    application/json`. `Accept: application/json` and `User-Agent:
    contentos-plugin` are always sent, unless `headers` overrides them --
    `headers` is applied last, so a caller can override any default
    (including Content-Type).

    Retries on 5xx, on 429 (honoring a `Retry-After` header that parses
    as an int, else the `BACKOFF_S` backoff), and on
    `URLError`/`socket.timeout`; `retries` extra attempts follow the
    first one, sleeping via `sleep` between them (injected so tests never
    really sleep). Any other 4xx raises `HTTPError` immediately, without
    retrying. When retries are exhausted, an HTTP failure raises
    `HTTPError` and a network failure re-raises the last
    `URLError`/`socket.timeout`.

    An empty response body parses as `{}`; a non-empty body that is not
    valid JSON raises `HTTPError`.
    """
    if opener is None:
        opener = urllib.request.build_opener()

    full_url = _with_params(url, params)
    data: Optional[bytes] = None
    final_headers: Dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "contentos-plugin",
    }
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    if headers:
        final_headers.update(headers)

    attempts = retries + 1
    for attempt in range(attempts):
        is_last_attempt = attempt == attempts - 1
        request = urllib.request.Request(
            full_url, data=data, headers=final_headers, method=method
        )
        try:
            status, resp_headers, body_bytes = _open(opener, request, timeout)
        except (urllib.error.URLError, socket.timeout):
            if not is_last_attempt:
                sleep(_backoff_for(attempt))
                continue
            raise

        if status == 429 or status >= 500:
            if not is_last_attempt:
                sleep(_retry_delay(attempt, status, resp_headers))
                continue
            raise HTTPError(status, _decode_body(body_bytes))
        if 400 <= status < 500:
            raise HTTPError(status, _decode_body(body_bytes))

        if not body_bytes:
            return {}
        text = _decode_body(body_bytes)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPError(status, text) from exc

    raise AssertionError("unreachable: the retry loop always returns or raises")


def _classify_download_status(status: int) -> str:
    """Classify a >=400 download status.

    Returns "expired" (410), "blocked" (403), "retry" (429 or 5xx -- the
    caller still has to decide whether an attempt remains), or "failed"
    (any other 4xx, including 404). This is the single mapping shared by
    both of `download_to_file`'s error paths: a raised
    `urllib.error.HTTPError` and a response returned normally but
    carrying an error status.
    """
    if status == 410:
        return "expired"
    if status == 403:
        return "blocked"
    if status == 429 or status >= 500:
        return "retry"
    return "failed"


def _resolve_bad_status(
    status: int,
    headers: Any,
    attempt: int,
    is_last_attempt: bool,
    sleep: Callable[[float], None],
) -> Optional[DownloadResult]:
    """Turn a >=400 download status into a terminal result, or None to retry.

    None means `_classify_download_status` said "retry" (429/5xx): the
    right backoff has already been slept, skipped when this is the last
    attempt since there is nothing left to wait for. The caller should
    then just `continue` its attempt loop -- on the last attempt,
    `continue` ends the loop exactly like `break` would (there is no
    next iteration), falling through to the caller's own final `failed`
    return.
    """
    outcome = _classify_download_status(status)
    if outcome == "retry":
        if not is_last_attempt:
            sleep(_retry_delay(attempt, status, headers))
        return None
    if outcome == "failed":
        return DownloadResult("failed", 0, status)
    return DownloadResult(outcome, 0, status)  # "expired" | "blocked"


def download_to_file(
    url: str,
    dest: Path,
    max_bytes: int,
    headers: Optional[Mapping[str, str]] = None,
    retries: int = 3,
    opener: Optional[Any] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> DownloadResult:
    """Download `url` to `dest`, capped at `max_bytes`, with retries.

    Skips the request entirely when `dest` already exists with a
    non-zero size, returning `ok` with that size -- CDN videos and
    covers are never re-downloaded once saved (design spec, "Stage 1 --
    research", step 7). Otherwise sends a browser `User-Agent` and the
    Instagram `Referer`, merged under any caller `headers` (so a caller
    can override either).

    A >=400 status is handled identically whether the opener raises
    `urllib.error.HTTPError` for it or just returns a response object
    carrying that status (`_classify_download_status` is the one mapping
    both branches use): 410 -> `expired`, 403 -> `blocked`, 429/5xx ->
    retried, any other 4xx -> `failed`. None of these read the body.

    A response under 400 is bailed out as `too_large`, without reading
    the body, when `Content-Length` parses as bigger than `max_bytes`.
    Otherwise it is streamed in 64 KiB chunks to `<dest>.part` (creating
    `dest`'s parent directories first). Three things can happen mid
    stream: the bytes read so far exceed `max_bytes`, in which case
    streaming stops and `.part` is deleted, also as `too_large`; a
    `URLError`/`socket.timeout`/`OSError` (a dropped connection, a local
    disk error), which is treated exactly like a connection failure that
    never got a response at all -- `.part` is deleted and the attempt
    retries with the same backoff; or any other, truly unexpected
    exception, which still deletes `.part` before propagating rather
    than leaving it behind. On a clean completion, `.part` is atomically
    renamed onto `dest`.

    429, 5xx, and `URLError`/`socket.timeout` -- whether raised while
    opening the connection or mid-stream -- retry with the same backoff
    as `request_json` (`retries` extra attempts, honoring a 429's
    `Retry-After`), then give up as `failed`. `http_code` on the result
    is the last HTTP status observed across all attempts (set as soon as
    a response, successful or not, is actually received -- even if
    streaming it then fails), or None when every attempt was a pure
    network failure before any response arrived.
    """
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return DownloadResult("ok", dest.stat().st_size, None)

    if opener is None:
        opener = urllib.request.build_opener()

    final_headers: Dict[str, str] = {
        "User-Agent": BROWSER_UA,
        "Referer": INSTAGRAM_REFERER,
    }
    if headers:
        final_headers.update(headers)

    part_path = dest.with_name(dest.name + ".part")
    attempts = retries + 1
    last_http_code: Optional[int] = None

    for attempt in range(attempts):
        is_last_attempt = attempt == attempts - 1
        request = urllib.request.Request(url, headers=final_headers, method="GET")

        try:
            response = opener.open(request, timeout=_DOWNLOAD_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            status = exc.code
            last_http_code = status
            error_headers = exc.headers
            exc.close()
            result = _resolve_bad_status(status, error_headers, attempt, is_last_attempt, sleep)
            if result is not None:
                return result
            continue
        except (urllib.error.URLError, socket.timeout):
            if not is_last_attempt:
                sleep(_backoff_for(attempt))
            continue

        status = _status_of(response)
        last_http_code = status

        if status >= 400:
            error_headers = response.headers
            response.close()
            result = _resolve_bad_status(status, error_headers, attempt, is_last_attempt, sleep)
            if result is not None:
                return result
            continue

        written = 0
        stream_too_large = False
        stream_failed = False
        try:
            content_length = _int_header(response.headers, "Content-Length")
            if content_length is not None and content_length > max_bytes:
                return DownloadResult("too_large", 0, status)

            part_path.parent.mkdir(parents=True, exist_ok=True)
            with open(part_path, "wb") as fh:
                while True:
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        stream_too_large = True
                        break
                    fh.write(chunk)
        except (urllib.error.URLError, socket.timeout, OSError):
            # A connection drop or local disk error partway through the
            # stream is no different from one that happens before any
            # bytes arrive: clean up and retry it the same way.
            stream_failed = True
        except Exception:
            part_path.unlink(missing_ok=True)
            raise
        finally:
            response.close()

        if stream_too_large:
            part_path.unlink(missing_ok=True)
            return DownloadResult("too_large", 0, status)

        if stream_failed:
            part_path.unlink(missing_ok=True)
            if not is_last_attempt:
                sleep(_backoff_for(attempt))
            continue

        os.replace(part_path, dest)
        return DownloadResult("ok", written, status)

    return DownloadResult("failed", 0, last_http_code)
