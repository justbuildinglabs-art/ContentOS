"""Tests for lib/http.py: urllib wrapper with retries and capped downloads.

Every scenario below drives `lib.http` through a fake opener object (see
`FakeOpener`/`ScriptedResponse`) that returns or raises scripted results
in order and records every `urllib.request.Request` it was asked to
open. Nothing here ever touches the network: NoNetworkTestCase also
patches `urllib.request.urlopen`/`OpenerDirector.open` to raise, as a
second line of defense in case a test forgets to pass `opener=`.
"""
from __future__ import annotations

import io
import unittest
import urllib.error
import urllib.request
from typing import Any, List, Optional

from tests.helpers import NoNetworkTestCase, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so this import must come after it.
from lib.http import (  # noqa: E402
    BACKOFF_S,
    BROWSER_UA,
    INSTAGRAM_REFERER,
    DownloadResult,
    HTTPError,
    download_to_file,
    request_json,
)


class ScriptedResponse:
    """A fake response object satisfying the opener's response contract:

    `.status`, `.headers` (a mapping with `.get`), `.read()`/`.read(n)`
    (bytes), and `.close()`. `.read(n)` actually chunks through `body`
    rather than returning it all at once, so download_to_file's 64 KiB
    streaming loop is genuinely exercised.
    """

    def __init__(self, status: int, body: bytes = b"", headers: Optional[dict] = None) -> None:
        self.status = status
        self._body = body
        self._pos = 0
        self.headers = dict(headers or {})
        self.closed = False
        self.read_calls = 0

    def read(self, n: int = -1) -> bytes:
        self.read_calls += 1
        if n is None or n < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    """Returns/raises scripted results from `.open()` in order.

    Records every `Request` (and the `timeout` it was called with) so
    tests can assert method, URL (with params), headers, and body.
    """

    def __init__(self, script: List[Any]) -> None:
        self._script = list(script)
        self.requests: List[urllib.request.Request] = []
        self.timeouts: List[Any] = []

    def open(self, request: urllib.request.Request, timeout: Any = None) -> Any:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self._script:
            raise AssertionError("FakeOpener script exhausted")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _header(request: urllib.request.Request, name: str) -> Optional[str]:
    """Read a header from a urllib Request.

    `Request.add_header` stores keys via `key.capitalize()` (so
    "User-Agent" is stored as "User-agent"), and `get_header` does not
    re-normalize its argument. Passing `name.capitalize()` here matches
    however the header was originally spelled when added.
    """
    return request.get_header(name.capitalize())


def _http_error(
    url: str, code: int, headers: Optional[dict] = None, body: bytes = b""
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", headers or {}, io.BytesIO(body))


def _url_error(reason: str = "connection refused") -> urllib.error.URLError:
    return urllib.error.URLError(reason)


class RequestJsonTests(NoNetworkTestCase):
    def test_request_json_parses_body_and_sets_headers(self) -> None:
        opener = FakeOpener([ScriptedResponse(200, body=b'{"ok": true, "n": 3}')])
        sleeps: List[float] = []

        result = request_json(
            "POST",
            "https://api.example.com/things",
            json_body={"a": 1},
            params={"q": "x"},
            opener=opener,
            sleep=sleeps.append,
        )

        self.assertEqual(result, {"ok": True, "n": 3})
        self.assertEqual(sleeps, [])
        self.assertEqual(len(opener.requests), 1)
        req = opener.requests[0]
        self.assertEqual(req.get_full_url(), "https://api.example.com/things?q=x")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.data, b'{"a": 1}')
        self.assertEqual(_header(req, "Accept"), "application/json")
        self.assertEqual(_header(req, "User-Agent"), "contentos-plugin")
        self.assertEqual(_header(req, "Content-Type"), "application/json")
        self.assertEqual(opener.timeouts, [30])

    def test_request_json_params_appended_with_ampersand_when_query_present(self) -> None:
        opener = FakeOpener([ScriptedResponse(200, body=b"{}")])

        request_json(
            "GET",
            "https://api.example.com/things?existing=1",
            params={"q": "x"},
            opener=opener,
        )

        self.assertEqual(
            opener.requests[0].get_full_url(),
            "https://api.example.com/things?existing=1&q=x",
        )

    def test_request_json_caller_headers_override_defaults(self) -> None:
        opener = FakeOpener([ScriptedResponse(200, body=b"{}")])

        request_json(
            "GET",
            "https://api.example.com/things",
            headers={"Accept": "text/plain", "User-Agent": "custom-ua"},
            opener=opener,
        )

        req = opener.requests[0]
        self.assertEqual(_header(req, "Accept"), "text/plain")
        self.assertEqual(_header(req, "User-Agent"), "custom-ua")

    def test_request_json_empty_body_returns_empty_dict(self) -> None:
        opener = FakeOpener([ScriptedResponse(200, body=b"")])

        result = request_json("GET", "https://api.example.com/x", opener=opener)

        self.assertEqual(result, {})

    def test_request_json_non_json_body_raises_http_error(self) -> None:
        opener = FakeOpener([ScriptedResponse(200, body=b"not json at all")])

        with self.assertRaises(HTTPError) as ctx:
            request_json("GET", "https://api.example.com/x", opener=opener)

        self.assertEqual(ctx.exception.status_code, 200)
        self.assertEqual(ctx.exception.body, "not json at all")

    def test_retries_on_5xx_then_succeeds(self) -> None:
        opener = FakeOpener(
            [
                _http_error("https://api.example.com/x", 503),
                _http_error("https://api.example.com/x", 500),
                ScriptedResponse(200, body=b'{"x": 1}'),
            ]
        )
        sleeps: List[float] = []

        result = request_json(
            "GET", "https://api.example.com/x", opener=opener, sleep=sleeps.append
        )

        self.assertEqual(result, {"x": 1})
        self.assertEqual(sleeps, [BACKOFF_S[0], BACKOFF_S[1]])
        self.assertEqual(len(opener.requests), 3)

    def test_no_retry_on_4xx_except_429(self) -> None:
        for code in (400, 401, 404, 422):
            with self.subTest(code=code):
                opener = FakeOpener([_http_error("https://api.example.com/x", code, body=b"nope")])
                sleeps: List[float] = []

                with self.assertRaises(HTTPError) as ctx:
                    request_json(
                        "GET", "https://api.example.com/x", opener=opener, sleep=sleeps.append
                    )

                self.assertEqual(ctx.exception.status_code, code)
                self.assertEqual(ctx.exception.body, "nope")
                self.assertEqual(len(opener.requests), 1)
                self.assertEqual(sleeps, [])

    def test_429_honors_retry_after(self) -> None:
        opener = FakeOpener(
            [
                _http_error("https://api.example.com/x", 429, headers={"Retry-After": "7"}),
                ScriptedResponse(200, body=b"{}"),
            ]
        )
        sleeps: List[float] = []

        request_json("GET", "https://api.example.com/x", opener=opener, sleep=sleeps.append)

        self.assertEqual(sleeps, [7])

    def test_429_falls_back_to_backoff_when_retry_after_missing_or_invalid(self) -> None:
        cases = [None, "not-a-number", "Wed, 21 Oct 2026 07:28:00 GMT"]
        for retry_after in cases:
            with self.subTest(retry_after=retry_after):
                headers = {"Retry-After": retry_after} if retry_after is not None else {}
                opener = FakeOpener(
                    [
                        _http_error("https://api.example.com/x", 429, headers=headers),
                        ScriptedResponse(200, body=b"{}"),
                    ]
                )
                sleeps: List[float] = []

                request_json(
                    "GET", "https://api.example.com/x", opener=opener, sleep=sleeps.append
                )

                self.assertEqual(sleeps, [BACKOFF_S[0]])

    def test_retries_exhausted_raises_http_error_for_5xx(self) -> None:
        opener = FakeOpener(
            [
                _http_error("https://api.example.com/x", 500, body=b"one"),
                _http_error("https://api.example.com/x", 500, body=b"two"),
                _http_error("https://api.example.com/x", 500, body=b"three"),
                _http_error("https://api.example.com/x", 500, body=b"four"),
            ]
        )
        sleeps: List[float] = []

        with self.assertRaises(HTTPError) as ctx:
            request_json(
                "GET",
                "https://api.example.com/x",
                opener=opener,
                sleep=sleeps.append,
                retries=3,
            )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.body, "four")
        self.assertEqual(len(opener.requests), 4)
        # 3 retries: BACKOFF_S[0], BACKOFF_S[1], BACKOFF_S[2], no 4th backoff
        # value defined so BACKOFF_S[-1] (8) would repeat for any retry
        # beyond this.
        self.assertEqual(sleeps, [BACKOFF_S[0], BACKOFF_S[1], BACKOFF_S[2]])

    def test_backoff_reuses_last_value_beyond_scripted_length(self) -> None:
        opener = FakeOpener(
            [_http_error("https://api.example.com/x", 500) for _ in range(5)]
            + [ScriptedResponse(200, body=b"{}")]
        )
        sleeps: List[float] = []

        request_json(
            "GET", "https://api.example.com/x", opener=opener, sleep=sleeps.append, retries=5
        )

        self.assertEqual(sleeps, [2, 4, 8, 8, 8])

    def test_url_error_retries_then_reraises_after_exhausted(self) -> None:
        opener = FakeOpener([_url_error("dns failure") for _ in range(4)])
        sleeps: List[float] = []

        with self.assertRaises(urllib.error.URLError):
            request_json(
                "GET", "https://api.example.com/x", opener=opener, sleep=sleeps.append, retries=3
            )

        self.assertEqual(len(opener.requests), 4)
        self.assertEqual(sleeps, [BACKOFF_S[0], BACKOFF_S[1], BACKOFF_S[2]])

    def test_url_error_then_succeeds(self) -> None:
        opener = FakeOpener([_url_error("timed out"), ScriptedResponse(200, body=b'{"ok": 1}')])
        sleeps: List[float] = []

        result = request_json(
            "GET", "https://api.example.com/x", opener=opener, sleep=sleeps.append
        )

        self.assertEqual(result, {"ok": 1})
        self.assertEqual(sleeps, [BACKOFF_S[0]])

    def test_request_json_without_opener_hits_network_guard(self) -> None:
        # No opener= given, so request_json falls back to
        # urllib.request.build_opener(), whose .open() NoNetworkTestCase
        # has patched to raise -- proving the real network is never one
        # missing kwarg away in production code that forgets opener=.
        with self.assertRaises(AssertionError):
            request_json("GET", "https://api.example.com/x")


class DownloadToFileTests(NoNetworkTestCase):
    def test_download_ok_streams_to_part_then_renames(self) -> None:
        body = bytes(range(256)) * 300  # 76,800 bytes: forces multiple 64 KiB reads.
        response = ScriptedResponse(200, body=body, headers={"Content-Length": str(len(body))})
        opener = FakeOpener([response])

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "abc123.mp4"

            result = download_to_file(
                "https://cdn.example.com/abc123.mp4", dest, max_bytes=1_000_000, opener=opener
            )

            self.assertEqual(result, DownloadResult("ok", len(body), 200))
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_bytes(), body)
            part_path = dest.with_name(dest.name + ".part")
            self.assertFalse(part_path.exists())

        # 64 KiB chunking actually happened, not one big read().
        self.assertGreater(response.read_calls, 1)

    def test_download_too_large_by_content_length_reads_no_body(self) -> None:
        response = ScriptedResponse(200, body=b"x" * 1000, headers={"Content-Length": "999999"})
        opener = FakeOpener([response])

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "big.mp4"

            result = download_to_file(
                "https://cdn.example.com/big.mp4", dest, max_bytes=1000, opener=opener
            )

            self.assertEqual(result, DownloadResult("too_large", 0, 200))
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_name(dest.name + ".part").exists())

        self.assertEqual(response.read_calls, 0)
        self.assertTrue(response.closed)

    def test_download_too_large_when_stream_exceeds_cap_deletes_partial(self) -> None:
        body = b"y" * 100_000  # No Content-Length header: the cap check only bites mid-stream.
        opener = FakeOpener([ScriptedResponse(200, body=body)])

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "overflow.mp4"

            result = download_to_file(
                "https://cdn.example.com/overflow.mp4", dest, max_bytes=50_000, opener=opener
            )

            self.assertEqual(result.status, "too_large")
            self.assertEqual(result.bytes, 0)
            self.assertEqual(result.http_code, 200)
            self.assertFalse(dest.exists())
            self.assertFalse(dest.with_name(dest.name + ".part").exists())

    def test_download_maps_410_expired_403_blocked_404_failed(self) -> None:
        cases = [(410, "expired"), (403, "blocked"), (404, "failed")]
        for code, expected_status in cases:
            with self.subTest(code=code):
                opener = FakeOpener(
                    [_http_error("https://cdn.example.com/x.mp4", code)]
                )

                with temp_project() as project_dir:
                    dest = project_dir / "videos" / f"{code}.mp4"

                    result = download_to_file(
                        "https://cdn.example.com/x.mp4", dest, max_bytes=1000, opener=opener
                    )

                    self.assertEqual(result, DownloadResult(expected_status, 0, code))
                    self.assertFalse(dest.exists())

                self.assertEqual(len(opener.requests), 1)

    def test_download_sets_user_agent_and_referer(self) -> None:
        body = b"abc"
        opener = FakeOpener(
            [ScriptedResponse(200, body=body, headers={"Content-Length": str(len(body))})]
        )

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "ua.mp4"

            download_to_file("https://cdn.example.com/ua.mp4", dest, max_bytes=1000, opener=opener)

        req = opener.requests[0]
        self.assertEqual(_header(req, "User-Agent"), BROWSER_UA)
        self.assertEqual(_header(req, "Referer"), INSTAGRAM_REFERER)

    def test_download_caller_headers_override_defaults(self) -> None:
        body = b"abc"
        opener = FakeOpener(
            [ScriptedResponse(200, body=body, headers={"Content-Length": str(len(body))})]
        )

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "ua2.mp4"

            download_to_file(
                "https://cdn.example.com/ua2.mp4",
                dest,
                max_bytes=1000,
                headers={"Referer": "https://override.example.com/"},
                opener=opener,
            )

        req = opener.requests[0]
        self.assertEqual(_header(req, "Referer"), "https://override.example.com/")
        self.assertEqual(_header(req, "User-Agent"), BROWSER_UA)

    def test_download_skips_existing_file(self) -> None:
        class ExplodingOpener:
            def open(self, request: Any, timeout: Any = None) -> Any:
                raise AssertionError("download_to_file must not request an existing file")

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "already-there.mp4"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b"already downloaded")

            result = download_to_file(
                "https://cdn.example.com/already-there.mp4",
                dest,
                max_bytes=1000,
                opener=ExplodingOpener(),
            )

        self.assertEqual(result, DownloadResult("ok", len(b"already downloaded"), None))

    def test_download_empty_existing_file_is_not_skipped(self) -> None:
        body = b"freshly downloaded"
        opener = FakeOpener(
            [ScriptedResponse(200, body=body, headers={"Content-Length": str(len(body))})]
        )

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "empty.mp4"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b"")  # zero-byte leftover from a previous crash.

            result = download_to_file(
                "https://cdn.example.com/empty.mp4", dest, max_bytes=1000, opener=opener
            )

        self.assertEqual(result, DownloadResult("ok", len(body), 200))
        self.assertEqual(len(opener.requests), 1)

    def test_download_retries_429_and_5xx_then_failed(self) -> None:
        opener = FakeOpener(
            [
                _http_error("https://cdn.example.com/x.mp4", 429, headers={"Retry-After": "3"}),
                _http_error("https://cdn.example.com/x.mp4", 503),
                _http_error("https://cdn.example.com/x.mp4", 503),
                _http_error("https://cdn.example.com/x.mp4", 503),
            ]
        )
        sleeps: List[float] = []

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "flaky.mp4"

            result = download_to_file(
                "https://cdn.example.com/x.mp4",
                dest,
                max_bytes=1000,
                opener=opener,
                sleep=sleeps.append,
                retries=3,
            )

        self.assertEqual(result, DownloadResult("failed", 0, 503))
        self.assertEqual(len(opener.requests), 4)
        self.assertEqual(sleeps, [3, BACKOFF_S[1], BACKOFF_S[2]])

    def test_download_network_error_exhausts_retries_and_reports_none_http_code(self) -> None:
        opener = FakeOpener([_url_error("connection reset") for _ in range(4)])
        sleeps: List[float] = []

        with temp_project() as project_dir:
            dest = project_dir / "videos" / "network-flaky.mp4"

            result = download_to_file(
                "https://cdn.example.com/x.mp4",
                dest,
                max_bytes=1000,
                opener=opener,
                sleep=sleeps.append,
                retries=3,
            )

        self.assertEqual(result, DownloadResult("failed", 0, None))
        self.assertEqual(len(opener.requests), 4)
        self.assertEqual(sleeps, [BACKOFF_S[0], BACKOFF_S[1], BACKOFF_S[2]])

    def test_download_without_opener_hits_network_guard(self) -> None:
        with temp_project() as project_dir:
            dest = project_dir / "videos" / "no-opener.mp4"

            with self.assertRaises(AssertionError):
                download_to_file("https://cdn.example.com/x.mp4", dest, max_bytes=1000)


if __name__ == "__main__":
    unittest.main()
