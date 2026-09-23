"""Tests for lib/apify.py: Apify REST client, cost estimate, and the
contentos.py `diagnose --live` wiring.

`apify.FixtureTransport` (what `--mock` uses) and a small
`ScriptedApifyTransport` built here (for polling sequences
FixtureTransport is deliberately too simple to answer -- it always
reports a run SUCCEEDED on the very first poll) stand in for the real
Apify API in every scenario. Nothing here ever touches the network;
NoNetworkTestCase is also a second line of defense.
"""
from __future__ import annotations

import json
import os
import socket
import unittest
import urllib.error
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

from tests.helpers import NoNetworkTestCase, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import apify, codes  # noqa: E402
from lib.http import HTTPError  # noqa: E402


class ScriptedApifyTransport:
    """Returns/raises each scripted response from `.request_json` in order.

    Used for polling sequences and failure injection that
    `apify.FixtureTransport` is deliberately too simple to answer (it
    always reports a run SUCCEEDED on the very first poll -- see its
    own docstring). Records every call the same shape FixtureTransport
    does, so assertions read the same way against either.
    """

    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def request_json(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
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
        if not self._responses:
            raise AssertionError("ScriptedApifyTransport script exhausted")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _run_diagnose_cli(
    project_dir: Path, extra_args: List[str], environ: Dict[str, str]
) -> Any:
    """Call contentos.main(["diagnose", ...]) in-process; return (code, payload).

    `os.environ` is replaced (not merged) with `environ` for the
    duration of the call, so the test is deterministic regardless of
    whatever the developer's own shell happens to export -- a real
    APIFY_API_TOKEN sitting in the ambient environment must never leak
    into a "no key resolves" assertion.
    """
    with mock.patch.dict(os.environ, environ, clear=True):
        buffer = StringIO()
        with redirect_stdout(buffer):
            exit_code = contentos.main(
                ["diagnose", "--project", str(project_dir)] + extra_args
            )
    return exit_code, json.loads(buffer.getvalue())


class BuildInputTests(NoNetworkTestCase):
    def test_reels_input_shape(self) -> None:
        result = apify.build_reels_input(["acct1", "acct2"], 30, 365)

        self.assertEqual(
            result,
            {
                "directUrls": [
                    "https://www.instagram.com/acct1/",
                    "https://www.instagram.com/acct2/",
                ],
                "resultsType": "reels",
                "resultsLimit": 30,
                "onlyPostsNewerThan": "365 days",
                "skipPinnedPosts": True,
            },
        )

    def test_details_input_shape(self) -> None:
        result = apify.build_details_input(["acct1", "acct2"])

        self.assertEqual(
            result,
            {
                "directUrls": [
                    "https://www.instagram.com/acct1/",
                    "https://www.instagram.com/acct2/",
                ],
                "resultsType": "details",
            },
        )


class EstimateCostTests(NoNetworkTestCase):
    def test_estimate_math_five_accounts_thirty_reels(self) -> None:
        result = apify.estimate_cost(5, 30)

        self.assertEqual(result, apify.CostEstimate(0.405, 0.0135, 0.4185, 150))


class StartRunTests(NoNetworkTestCase):
    def test_start_run_sets_bearer_and_query_params(self) -> None:
        transport = apify.FixtureTransport(reels_items=[], details_items=[])
        actor_input = apify.build_reels_input(["acct1"], 30, 365)

        run = apify.start_run(
            "tok123",
            actor_input,
            max_total_charge_usd=3.0,
            max_items=150,
            timeout_s=900,
            transport=transport,
        )

        self.assertEqual(run, apify.RunRef("mock-reels", "READY", "ds-reels"))
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], f"{apify.API_BASE}{apify.ACTOR_RUNS_PATH}")
        self.assertEqual(call["headers"], {"Authorization": "Bearer tok123"})
        self.assertEqual(call["json_body"], actor_input)
        self.assertEqual(
            call["params"],
            {"maxTotalChargeUsd": 3.0, "maxItems": 150, "timeout": 900, "waitForFinish": 0},
        )


class WaitForRunTests(NoNetworkTestCase):
    def test_wait_polls_until_succeeded(self) -> None:
        transport = ScriptedApifyTransport(
            [
                {"data": {"id": "run-1", "status": "RUNNING", "defaultDatasetId": "ds-1"}},
                {"data": {"id": "run-1", "status": "RUNNING", "defaultDatasetId": "ds-1"}},
                {"data": {"id": "run-1", "status": "SUCCEEDED", "defaultDatasetId": "ds-1"}},
            ]
        )
        run = apify.RunRef(id="run-1", status="READY", dataset_id="ds-1")
        sleeps: List[float] = []
        logs: List[str] = []

        result = apify.wait_for_run(
            "tok",
            run,
            poll_s=5,
            timeout_s=900,
            transport=transport,
            sleep=sleeps.append,
            clock=lambda: 0.0,
            log=logs.append,
        )

        self.assertEqual(result, apify.RunRef("run-1", "SUCCEEDED", "ds-1"))
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [5, 5])
        self.assertEqual(len(logs), 3)
        for call in transport.calls:
            self.assertEqual(call["method"], "GET")
            self.assertEqual(call["url"], f"{apify.API_BASE}/actor-runs/run-1")
            self.assertEqual(call["headers"], {"Authorization": "Bearer tok"})

    def test_wait_raises_on_failed(self) -> None:
        for status in ("FAILED", "ABORTED"):
            with self.subTest(status=status):
                transport = ScriptedApifyTransport(
                    [{"data": {"id": "run-1", "status": status, "defaultDatasetId": "ds-1"}}]
                )
                run = apify.RunRef(id="run-1", status="READY", dataset_id="ds-1")

                with self.assertRaises(apify.ApifyRunFailed) as ctx:
                    apify.wait_for_run(
                        "tok",
                        run,
                        poll_s=5,
                        timeout_s=900,
                        transport=transport,
                        sleep=lambda _s: None,
                        clock=lambda: 0.0,
                    )

                self.assertEqual(str(ctx.exception), status)

    def test_wait_marks_partial_on_timed_out_and_on_timeout(self) -> None:
        # Apify itself reports the run TIMED-OUT.
        transport = ScriptedApifyTransport(
            [{"data": {"id": "run-1", "status": "TIMED-OUT", "defaultDatasetId": "ds-1"}}]
        )
        run = apify.RunRef(id="run-1", status="READY", dataset_id="ds-1")

        result = apify.wait_for_run(
            "tok",
            run,
            poll_s=5,
            timeout_s=900,
            transport=transport,
            sleep=lambda _s: None,
            clock=lambda: 0.0,
        )

        self.assertEqual(result, apify.RunRef("run-1", "TIMED-OUT", "ds-1", partial=True))

        # Still RUNNING when this side's own timeout_s elapses first.
        transport2 = ScriptedApifyTransport(
            [{"data": {"id": "run-2", "status": "RUNNING", "defaultDatasetId": "ds-2"}}]
        )
        run2 = apify.RunRef(id="run-2", status="READY", dataset_id="ds-2")
        clock_values = iter([0.0, 901.0])
        sleeps: List[float] = []

        result2 = apify.wait_for_run(
            "tok",
            run2,
            poll_s=5,
            timeout_s=900,
            transport=transport2,
            sleep=sleeps.append,
            clock=lambda: next(clock_values),
        )

        self.assertEqual(result2, apify.RunRef("run-2", "RUNNING", "ds-2", partial=True))
        self.assertEqual(len(transport2.calls), 1)
        self.assertEqual(sleeps, [])


class IterDatasetItemsTests(NoNetworkTestCase):
    def test_iter_dataset_items_paginates_by_offset(self) -> None:
        items = [{"i": i} for i in range(5)]
        transport = apify.FixtureTransport(reels_items=items, details_items=[])

        collected = list(apify.iter_dataset_items("tok", "ds-reels", transport, page=2))

        self.assertEqual(collected, items)
        dataset_calls = [c for c in transport.calls if c["method"] == "GET"]
        self.assertEqual(len(dataset_calls), 3)
        self.assertEqual([c["params"]["offset"] for c in dataset_calls], [0, 2, 4])
        for call in dataset_calls:
            self.assertEqual(call["url"], f"{apify.API_BASE}/datasets/ds-reels/items")
            self.assertEqual(call["params"]["limit"], 2)
            self.assertEqual(call["params"]["clean"], "true")
            self.assertEqual(call["params"]["format"], "json")
            self.assertEqual(call["headers"], {"Authorization": "Bearer tok"})


class FixtureTransportTests(NoNetworkTestCase):
    def test_fixture_transport_roundtrip(self) -> None:
        reels_items = [{"shortCode": "abc"}, {"shortCode": "def"}]
        details_items = [{"username": "acct1"}]
        transport = apify.FixtureTransport(reels_items=reels_items, details_items=details_items)

        reels_run = apify.start_run(
            "tok", apify.build_reels_input(["acct1"], 30, 365), 3.0, 30, 900, transport
        )
        self.assertEqual(reels_run, apify.RunRef("mock-reels", "READY", "ds-reels"))

        details_run = apify.start_run(
            "tok", apify.build_details_input(["acct1"]), 3.0, 1, 900, transport
        )
        self.assertEqual(details_run, apify.RunRef("mock-details", "READY", "ds-details"))

        reels_run = apify.wait_for_run(
            "tok",
            reels_run,
            poll_s=1,
            timeout_s=10,
            transport=transport,
            sleep=lambda _s: None,
            clock=lambda: 0.0,
        )
        self.assertEqual(reels_run, apify.RunRef("mock-reels", "SUCCEEDED", "ds-reels"))

        self.assertEqual(
            list(apify.iter_dataset_items("tok", reels_run.dataset_id, transport)),
            reels_items,
        )
        self.assertEqual(
            list(apify.iter_dataset_items("tok", details_run.dataset_id, transport)),
            details_items,
        )
        self.assertTrue(apify.check_token("tok", transport))

        self.assertGreaterEqual(len(transport.calls), 6)
        for call in transport.calls:
            self.assertEqual(set(call), {"method", "url", "headers", "json_body", "params"})

    def test_fixture_transport_unknown_route_raises_http_error(self) -> None:
        transport = apify.FixtureTransport(reels_items=[], details_items=[])

        with self.assertRaises(HTTPError) as ctx:
            transport.request_json("DELETE", f"{apify.API_BASE}/actor-runs/mock-reels")

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(len(transport.calls), 1)


class CheckTokenTests(NoNetworkTestCase):
    def test_check_token(self) -> None:
        transport = apify.FixtureTransport(reels_items=[], details_items=[])

        self.assertTrue(apify.check_token("tok", transport))
        self.assertEqual(transport.calls[-1]["headers"], {"Authorization": "Bearer tok"})
        self.assertEqual(transport.calls[-1]["url"], f"{apify.API_BASE}/users/me")

    def test_check_token_false_on_401(self) -> None:
        transport = ScriptedApifyTransport([HTTPError(401, "unauthorized")])

        self.assertFalse(apify.check_token("bad-tok", transport))

    def test_check_token_false_on_url_error(self) -> None:
        transport = ScriptedApifyTransport([urllib.error.URLError("unreachable")])

        self.assertFalse(apify.check_token("tok", transport))

    def test_check_token_returns_false_on_socket_timeout(self) -> None:
        transport = ScriptedApifyTransport([socket.timeout()])

        self.assertFalse(apify.check_token("tok", transport))


class DiagnoseLiveTests(NoNetworkTestCase):
    """contentos.py `diagnose --live` wiring, exercised in-process via
    contentos.main() so nothing here needs a subprocess or the real
    network: lib.http.request_json is patched instead, wherever a
    resolved key would otherwise reach HttpTransport.
    """

    def test_diagnose_live_is_null_without_flag(self) -> None:
        with temp_project() as project_dir:
            exit_code, payload = _run_diagnose_cli(
                project_dir, [], {"CONTENTOS_CONFIG_DIR": ""}
            )

        self.assertEqual(exit_code, codes.EXIT_OK)
        self.assertIsNone(payload["apify_live"])

    def test_diagnose_live_false_when_no_key_resolves(self) -> None:
        # No lib.http.request_json patch here: NoNetworkTestCase's own
        # network guard would fail this test loudly if the "no key"
        # branch ever tried to make a real HttpTransport call.
        with temp_project() as project_dir:
            exit_code, payload = _run_diagnose_cli(
                project_dir, ["--live"], {"CONTENTOS_CONFIG_DIR": ""}
            )

        self.assertEqual(exit_code, codes.EXIT_OK)
        self.assertIs(payload["apify_live"], False)

    def test_diagnose_live_true_when_check_token_succeeds(self) -> None:
        with temp_project() as project_dir:
            with mock.patch(
                "lib.http.request_json", return_value={"data": {"username": "mock"}}
            ):
                exit_code, payload = _run_diagnose_cli(
                    project_dir,
                    ["--live"],
                    {"CONTENTOS_CONFIG_DIR": "", "APIFY_API_TOKEN": "fake-token"},
                )

        self.assertEqual(exit_code, codes.EXIT_OK)
        self.assertIs(payload["apify_live"], True)

    def test_diagnose_live_false_when_check_token_fails(self) -> None:
        with temp_project() as project_dir:
            with mock.patch(
                "lib.http.request_json", side_effect=HTTPError(401, "unauthorized")
            ):
                exit_code, payload = _run_diagnose_cli(
                    project_dir,
                    ["--live"],
                    {"CONTENTOS_CONFIG_DIR": "", "APIFY_API_TOKEN": "fake-token"},
                )

        self.assertEqual(exit_code, codes.EXIT_OK)
        self.assertIs(payload["apify_live"], False)


class DiscoveryApifyTests(NoNetworkTestCase):
    def test_keyword_reels_input(self) -> None:
        self.assertEqual(
            apify.build_keyword_reels_input(["habit coach", "morning routine"], 20),
            {"hashtags": ["habit coach", "morning routine"], "keywordSearch": True,
             "resultsType": "reels", "resultsLimit": 20},
        )
        self.assertEqual(apify.KEYWORD_ACTOR_RUNS_PATH, "/acts/apify~instagram-hashtag-scraper/runs")

    def test_estimate_discovery(self) -> None:
        self.assertEqual(
            apify.estimate_discovery(keyword_reels=60, hashtag_reels=0, details=75, profile_reels=300),
            {"keyword_reels_usd": 0.162, "hashtag_reels_usd": 0.0, "details_usd": 0.2025,
             "reels_usd": 0.81, "total_usd": 1.1745},
        )

    def test_fixture_transport_gives_each_discovery_run_its_dataset(self) -> None:
        transport = apify.FixtureTransport(
            [{"shortCode": "R"}], [{"username": "d"}],
            hashtag_items=[{"shortCode": "H"}], keyword_items=[{"shortCode": "K"}],
        )
        runs = {
            "keyword": apify.start_run(
                "tok", apify.build_keyword_reels_input(["x"], 5), 1.0, 5, 60, transport,
                runs_path=apify.KEYWORD_ACTOR_RUNS_PATH,
            ),
            "hashtag": apify.start_run(
                "tok", apify.build_hashtag_reels_input(["x"], 5, 14), 1.0, 5, 60, transport
            ),
            "reels": apify.start_run("tok", apify.build_reels_input(["d"], 15, 90), 1.0, 15, 60, transport),
            "details": apify.start_run("tok", apify.build_details_input(["d"]), 1.0, 1, 60, transport),
        }
        items = {
            name: list(apify.iter_dataset_items("tok", run.dataset_id, transport))
            for name, run in runs.items()
        }
        self.assertEqual(items, {
            "keyword": [{"shortCode": "K"}], "hashtag": [{"shortCode": "H"}],
            "reels": [{"shortCode": "R"}], "details": [{"username": "d"}],
        })


if __name__ == "__main__":
    unittest.main()
