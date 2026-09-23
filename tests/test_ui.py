"""Tests for `lib/ui.py`: the discovery control panel's API, server loop, and page."""
from __future__ import annotations

import json
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

from tests.helpers import REPO_ROOT, NoNetworkTestCase, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
import contentos  # noqa: E402
from lib import codes, discover, env, research, store, ui  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
WEB_FILE = FIXTURES_DIR / "discovery-web.sample.json"
TOKEN = "t" * 43
PORT = 5055
HOST = {"host": f"127.0.0.1:{PORT}", "x-contentos-token": TOKEN}
JSON_HEADERS = dict(HOST, **{"content-type": "application/json"})
NO_KEY = env.Keys(apify=None, source=None, warnings=[])


def _write_config(project: Path, config: Dict[str, Any]) -> None:
    config_dir = store.contentos_dir(project)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _app(project: Path, **overrides: Any) -> ui.App:
    web, _warnings = discover.load_web_handles(WEB_FILE)
    options: Dict[str, Any] = dict(
        keywords=["habit coach"], hashtags=["habits", "productivity"], web_entries=web, seeds=[],
        mock=True, runner=lambda job: job(), resolve_keys=lambda _project: NO_KEY,
    )
    options.update(overrides)
    return ui.App(project, store.load_discovery_config(project), TOKEN, PORT, **options)


def _call(app: ui.App, method: str, path: str, payload: Any = None,
          headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any]:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    if headers is None:
        headers = JSON_HEADERS if method == "POST" else HOST
    response = app.handle(method, path, headers, body)
    if response.content_type.startswith("application/json"):
        return response.status, json.loads(response.body)
    return response.status, response.body


class GuardTests(NoNetworkTestCase):
    def test_the_page_needs_a_local_host_but_no_token(self) -> None:
        with temp_project() as project:
            app = _app(project)
            page = app.handle("GET", "/", {"host": f"localhost:{PORT}"}, b"")
            self.assertEqual(page.status, 200)
            self.assertTrue(page.content_type.startswith("text/html"))
            self.assertEqual(app.handle("GET", "/", {"host": "evil.example:80"}, b"").status, 403)
            self.assertEqual(app.handle("GET", "/", {"host": f"127.0.0.1:{PORT + 1}"}, b"").status, 403)

    def test_the_api_needs_the_token(self) -> None:
        with temp_project() as project:
            app = _app(project)
            self.assertEqual(_call(app, "GET", "/api/state", headers={"host": f"127.0.0.1:{PORT}"})[0], 403)
            wrong = dict(HOST, **{"x-contentos-token": "wrong"})
            self.assertEqual(_call(app, "GET", "/api/state", headers=wrong)[0], 403)
            self.assertEqual(_call(app, "GET", "/api/state")[0], 200)

    def test_posts_must_be_json_objects(self) -> None:
        with temp_project() as project:
            app = _app(project)
            text = dict(HOST, **{"content-type": "text/plain"})
            self.assertEqual(app.handle("POST", "/api/estimate", text, b"{}").status, 415)
            self.assertEqual(app.handle("POST", "/api/estimate", JSON_HEADERS, b"{nope").status, 400)
            self.assertEqual(app.handle("POST", "/api/estimate", JSON_HEADERS, b"[1]").status, 400)

    def test_unknown_route_and_wrong_method(self) -> None:
        with temp_project() as project:
            app = _app(project)
            self.assertEqual(_call(app, "GET", "/api/nope")[0], 404)
            self.assertEqual(_call(app, "GET", "/api/run")[0], 405)


class StateAndEstimateTests(NoNetworkTestCase):
    def test_state_before_setup(self) -> None:
        with temp_project() as project:
            status, data = _call(_app(project), "GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertFalse(data["set_up"])
        self.assertTrue(data["has_key"])
        self.assertEqual(data["settings"], {"discover_min_followers": 10000, "discover_min_views": 5000,
                                            "discover_post_every_days": 14, "discover_shortlist": 20})
        self.assertEqual(data["watch_list"], [])
        self.assertEqual([entry["handle"] for entry in data["web"]],
                         ["webwillow", "madeupmaya", "focusfern", "slowsam", "photophoebe"])
        self.assertEqual((data["cap_usd"], data["established_at"], data["state"]), (3.0, 50000, "idle"))

    def test_no_key_outside_mock(self) -> None:
        with temp_project() as project:
            self.assertFalse(_call(_app(project, mock=False), "GET", "/api/state")[1]["has_key"])

    def test_estimate_matches_discover_estimate(self) -> None:
        with temp_project() as project:
            status, data = _call(_app(project), "POST", "/api/estimate", {"settings": {"discover_shortlist": 10}})
        expected = discover.estimate(dict(store.DEFAULT_CONFIG, discover_shortlist=10), 1, 2, 0, 5)
        self.assertEqual(status, 200)
        self.assertEqual(data["total_usd"], expected["total_usd"])
        self.assertTrue(data["within_cap"])

    def test_edited_inputs_change_the_estimate(self) -> None:
        payload = {"keywords": [], "hashtags": [], "web": [{"handle": "webwillow", "source_url": "u"}]}
        with temp_project() as project:
            data = _call(_app(project), "POST", "/api/estimate", payload)[1]
        self.assertEqual(data["total_usd"], discover.estimate(dict(store.DEFAULT_CONFIG), 0, 0, 0, 1)["total_usd"])

    def test_a_bad_or_unknown_setting_is_named(self) -> None:
        with temp_project() as project:
            app = _app(project)
            for settings, name in (({"discover_shortlist": 0}, "discover_shortlist"),
                                   ({"lookback_days": 3}, "lookback_days")):
                with self.subTest(settings=settings):
                    status, data = _call(app, "POST", "/api/estimate", {"settings": settings})
                    self.assertEqual(status, 400)
                    self.assertIn(name, data["error"])


class RunTests(NoNetworkTestCase):
    def test_a_mock_run_finishes_and_serves_the_results(self) -> None:
        with temp_project() as project:
            app = _app(project)
            self.assertEqual(_call(app, "GET", "/api/discovery")[0], 409)
            self.assertEqual(_call(app, "POST", "/api/run", {}), (202, {"state": "running"}))
            status = _call(app, "GET", "/api/status")[1]
            doc = _call(app, "GET", "/api/discovery")[1]
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["summary"]["candidates"], 3)
        self.assertTrue(status["log"])
        self.assertEqual(doc["version"], 2)
        self.assertEqual([row["handle"] for row in doc["candidates"]], ["planwithpia", "coachcora", "habitharbor"])

    def test_one_run_at_a_time(self) -> None:
        with temp_project() as project:
            app = _app(project, runner=lambda job: None)
            self.assertEqual(_call(app, "POST", "/api/run", {})[0], 202)
            self.assertEqual(_call(app, "POST", "/api/run", {})[0], 409)

    def test_over_the_cap_is_refused_with_code_6(self) -> None:
        with temp_project() as project:
            _write_config(project, {"apify_max_charge_usd": 0.05})
            status, data = _call(_app(project), "POST", "/api/run", {})
        self.assertEqual((status, data["code"]), (400, codes.EXIT_COST))
        self.assertIn("cap", data["error"])

    def test_no_key_is_refused_with_code_4(self) -> None:
        with temp_project() as project:
            status, data = _call(_app(project, mock=False), "POST", "/api/run", {})
        self.assertEqual((status, data["code"]), (400, codes.EXIT_KEYS))

    def test_nothing_to_search_is_refused(self) -> None:
        with temp_project() as project:
            status, data = _call(_app(project), "POST", "/api/run", {"keywords": [], "hashtags": [], "web": []})
        self.assertEqual((status, data["code"]), (400, codes.EXIT_USAGE))

    def test_remember_writes_the_dials_once_set_up(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["habitlab"]})
            _call(_app(project), "POST", "/api/run",
                  {"settings": {"discover_min_followers": 25000}, "remember": True})
            on_disk = store.read_json(store.contentos_dir(project) / "config.json")
        self.assertEqual(on_disk["competitors"], ["habitlab"])
        self.assertEqual(
            {key: on_disk[key] for key in ui.DIAL_KEYS},
            {"discover_min_followers": 25000, "discover_min_views": 5000,
             "discover_post_every_days": 14, "discover_shortlist": 20},
        )

    def test_remember_is_ignored_before_setup(self) -> None:
        with temp_project() as project:
            _call(_app(project), "POST", "/api/run", {"remember": True})
            self.assertFalse((store.contentos_dir(project) / "config.json").exists())

    def test_a_failed_run_reports_its_message(self) -> None:
        def boom(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
            raise research.UpstreamFailure("apify said no")

        with temp_project() as project:
            app = _app(project, run_discover=boom)
            _call(app, "POST", "/api/run", {})
            status = _call(app, "GET", "/api/status")[1]
        self.assertEqual((status["state"], status["error"]), ("error", "apify said no"))


class SaveAndCloseTests(NoNetworkTestCase):
    def test_save_adds_picks_after_the_current_watch_list(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["habitlab"], "format_accounts": ["kevbuildsapps"]})
            app = _app(project)
            status, data = _call(app, "POST", "/api/save", {"picks": ["@PlanWithPia", "coachcora", "habitlab"]})
            config = store.read_json(store.contentos_dir(project) / "config.json")
        self.assertEqual(status, 200)
        self.assertEqual(config["competitors"], ["habitlab", "planwithpia", "coachcora"])
        self.assertEqual(config["format_accounts"], ["kevbuildsapps"])
        self.assertEqual(data["picks"], ["planwithpia", "coachcora", "habitlab"])
        self.assertTrue(app.finished["saved"])

    def test_save_before_setup_writes_the_picks_file(self) -> None:
        with temp_project() as project:
            app = _app(project)
            status, _data = _call(app, "POST", "/api/save", {"picks": ["planwithpia"]})
            picks = store.read_json(store.contentos_dir(project) / ui.PICKS_FILE_NAME)
            self.assertFalse((store.contentos_dir(project) / "config.json").exists())
        self.assertEqual(status, 200)
        self.assertEqual(picks["picks"], ["planwithpia"])

    def test_save_needs_real_picks(self) -> None:
        with temp_project() as project:
            app = _app(project)
            for payload in ({"picks": []}, {"picks": "planwithpia"}, {"picks": ["not a handle!"]}):
                with self.subTest(payload=payload):
                    self.assertEqual(_call(app, "POST", "/api/save", payload)[0], 400)
            self.assertIsNone(app.finished)

    def test_close_finishes_without_saving(self) -> None:
        with temp_project() as project:
            app = _app(project)
            self.assertEqual(_call(app, "POST", "/api/close", {}), (200, {"saved": False}))
        self.assertEqual(app.finished["saved"], False)
        self.assertEqual(app.finished["picks"], [])


if __name__ == "__main__":
    unittest.main()
