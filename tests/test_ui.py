"""Tests for `lib/ui.py`: the discovery control panel's API, server loop, and page."""
from __future__ import annotations

import functools
import inspect
import json
import os
import re
import signal
import stat
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
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


def _set_up_project(project: Path, config: Dict[str, Any]) -> None:
    """A project `setup` has run on: both `config.json` and `creator.md` exist."""
    _write_config(project, config)
    handles = "\n".join(f"- {handle}" for handle in config.get("competitors", []))
    (store.contentos_dir(project) / "creator.md").write_text(
        f"# Creator\n\n## Competitors\n{handles}\n", encoding="utf-8"
    )


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

    def test_only_accepted_requests_keep_the_panel_awake(self) -> None:
        now = {"t": 0.0}
        with temp_project() as project:
            app = _app(project, clock=lambda: now["t"])
            now["t"] = 10.0
            app.handle("GET", "/", {"host": "evil.example:80"}, b"")
            _call(app, "GET", "/api/state", headers={"host": "evil.example:80", "x-contentos-token": TOKEN})
            _call(app, "GET", "/api/state", headers={"host": f"127.0.0.1:{PORT}"})
            _call(app, "GET", "/api/state", headers=dict(HOST, **{"x-contentos-token": "wrong"}))
            self.assertEqual(app.last_seen, 0.0)
            now["t"] = 20.0
            self.assertEqual(_call(app, "GET", "/api/state")[0], 200)
            self.assertEqual(app.last_seen, 20.0)
            now["t"] = 30.0
            self.assertEqual(app.handle("GET", "/", {"host": f"localhost:{PORT}"}, b"").status, 200)
            self.assertEqual(app.last_seen, 30.0)

    def test_the_key_never_reaches_the_page(self) -> None:
        secret = "apify_api_TESTSECRET123"
        keys = env.Keys(apify=secret, source="env", warnings=[])

        def mock_discover(project: Path, cfg: Dict[str, Any], _keys: Any, **kwargs: Any) -> Dict[str, Any]:
            # The real discovery in the mock world, so no request leaves the machine.
            return discover.run_discover(project, cfg, None, **dict(kwargs, mock=True))

        with temp_project() as project:
            _set_up_project(project, {"competitors": ["habitlab"]})
            app = _app(project, mock=False, resolve_keys=lambda _project: keys, run_discover=mock_discover)
            answers = [
                app.handle("GET", "/", {"host": f"127.0.0.1:{PORT}"}, b""),
                app.handle("GET", "/api/state", HOST, b""),
                app.handle("POST", "/api/estimate", JSON_HEADERS, b"{}"),
                app.handle("POST", "/api/run", JSON_HEADERS, b'{"remember": true}'),
                app.handle("GET", "/api/status", HOST, b""),
                app.handle("GET", "/api/discovery", HOST, b""),
                app.handle("POST", "/api/save", JSON_HEADERS, b'{"picks": ["planwithpia"]}'),
                app.handle("POST", "/api/close", JSON_HEADERS, b"{}"),
                app.handle("POST", "/api/run", JSON_HEADERS, b"{}"),
                app.handle("GET", "/api/state", {"host": f"127.0.0.1:{PORT}"}, b""),
            ]
            config_text = (store.contentos_dir(project) / "config.json").read_text(encoding="utf-8")
        self.assertEqual([answer.status for answer in answers[:8]], [200, 200, 200, 202, 200, 200, 200, 409])
        for answer in answers:
            with self.subTest(status=answer.status):
                self.assertNotIn(b"TESTSECRET", answer.body)
        self.assertNotIn("TESTSECRET", config_text)


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
        self.assertEqual([card["handle"] for card in data["cards"]],
                         ["webwillow", "madeupmaya", "focusfern", "slowsam", "photophoebe"])
        self.assertEqual(data["cards"][0], {
            "handle": "webwillow", "source_url": "https://example.invalid/best-habit-creators",
            "source_title": "The best habit creators to follow", "reason": "Listed for short habit-building tutorials",
            "followers_seen": 48000, "origin": "claude", "kept": False, "removed": False, "new": False,
        })
        self.assertEqual((data["search_instagram"], data["notes"], data["finished"]), (True, [], False))
        self.assertNotIn("web", data)
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

    def test_the_estimate_matches_discovery_when_a_web_find_is_a_format_account(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["habitlab"], "format_accounts": ["webwillow"]})
            app = _app(project)
            status, data = _call(app, "POST", "/api/estimate", {})
            web, _warnings = discover.load_web_handles(WEB_FILE)
            with self.assertRaises(research.ConfirmationRequired) as ctx:
                discover.run_discover(
                    project, store.load_discovery_config(project), None,
                    hashtags=["habits", "productivity"], keywords=["habit coach"],
                    web_entries=web, mock=True, estimate_only=True,
                )
        self.assertEqual(status, 200)
        self.assertEqual(data["total_usd"], ctx.exception.payload["total_usd"])

    def test_check_only_estimate(self) -> None:
        with temp_project() as project:
            data = _call(_app(project), "POST", "/api/estimate", {"search_instagram": False})[1]
        expected = discover.estimate(dict(store.DEFAULT_CONFIG), 1, 2, 0, 5, search_instagram=False)
        self.assertEqual(data["total_usd"], expected["total_usd"])
        self.assertIsNone(data["note"])

    def test_a_check_only_estimate_with_no_creators_says_what_to_do(self) -> None:
        with temp_project() as project:
            app = _app(project)
            check_only = _call(app, "POST", "/api/estimate", {"search_instagram": False, "web": []})[1]
            full = _call(app, "POST", "/api/estimate", {"search_instagram": True, "web": []})[1]
        self.assertEqual(check_only["note"], ui.CHECK_ONLY_NEEDS_CARDS)
        self.assertIsNone(full["note"])


class CardTests(NoNetworkTestCase):
    def test_normalize_cards_keeps_known_fields_and_drops_the_rest(self) -> None:
        cards = ui.normalize_cards([
            {"handle": "@PlanWithPia", "reason": "r", "origin": "you", "kept": True, "removed": "yes",
             "new": True, "check": {"passed": True}, "extra": 1},
            {"handle": "planwithpia", "origin": "claude"},
            {"handle": "https://www.tiktok.com/@nope"},
            {"handle": "coachcora", "origin": "martian"},
            "not a card",
        ])
        self.assertEqual(cards, [
            {"handle": "planwithpia", "source_url": "", "source_title": None, "reason": "r",
             "followers_seen": None, "origin": "you", "kept": True, "removed": False, "new": True},
            {"handle": "coachcora", "source_url": "", "source_title": None, "reason": None,
             "followers_seen": None, "origin": "claude", "kept": False, "removed": False, "new": False},
        ])
        self.assertEqual(ui.normalize_cards({"handle": "x"}), [])

    def test_normalize_cards_holds_at_most_120(self) -> None:
        cards = ui.normalize_cards([{"handle": f"h{i}"} for i in range(130)])
        self.assertEqual(len(cards), ui.MAX_CARDS)

    def test_the_estimate_records_the_page(self) -> None:
        with temp_project() as project:
            app = _app(project)
            _call(app, "POST", "/api/estimate", {
                "keywords": ["habit stacking"], "hashtags": ["#Tiny"], "search_instagram": False,
                "cards": [{"handle": "slowsam", "kept": True}],
            })
            data = _call(app, "GET", "/api/state")[1]
        self.assertEqual((data["keywords"], data["hashtags"], data["search_instagram"]),
                         (["habit stacking"], ["tiny"], False))
        self.assertEqual([(card["handle"], card["kept"]) for card in data["cards"]], [("slowsam", True)])


    def test_a_finished_panel_no_longer_records_the_page(self) -> None:
        with temp_project() as project:
            app = _app(project)
            _call(app, "POST", "/api/close", {})
            status, _data = _call(app, "POST", "/api/estimate", {"keywords": ["late"], "cards": [],
                                                                "settings": {"discover_shortlist": 5}})
        self.assertEqual(status, 200)
        self.assertEqual(app.keywords, ["habit coach"])
        self.assertEqual(len(app.cards), 5)
        self.assertEqual(app.page_settings["discover_shortlist"], 20)

    def test_a_page_from_an_older_panel_cannot_overwrite_the_cards(self) -> None:
        with temp_project() as project:
            app = _app(project)
            generation = _call(app, "GET", "/api/state")[1]["generation"]
            self.assertIsInstance(generation, str)
            self.assertNotEqual(generation, _call(_app(project), "GET", "/api/state")[1]["generation"])
            stale = {"generation": "older", "cards": [{"handle": "slowsam"}], "keywords": ["old"]}
            for route in ("/api/estimate", "/api/search-again"):
                with self.subTest(route=route):
                    status, data = _call(app, "POST", route, stale)
                    self.assertEqual(status, 409)
                    self.assertIs(data["stale"], True)
            self.assertEqual((len(app.cards), app.keywords, app.finished), (5, ["habit coach"], None))
            self.assertFalse(ui.handoff_path(project).exists())
            current = {"generation": generation, "cards": [{"handle": "slowsam"}]}
            self.assertEqual(_call(app, "POST", "/api/estimate", current)[0], 200)
        self.assertEqual([card["handle"] for card in app.cards], ["slowsam"])


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

    def test_a_check_only_run(self) -> None:
        with temp_project() as project:
            app = _app(project)
            self.assertEqual(_call(app, "POST", "/api/run", {"search_instagram": False})[0], 202)
            doc = _call(app, "GET", "/api/discovery")[1]
        self.assertIs(doc["search_instagram"], False)

    def test_check_only_needs_a_handle(self) -> None:
        with temp_project() as project:
            status, data = _call(_app(project), "POST", "/api/run", {"search_instagram": False, "web": []})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], ui.CHECK_ONLY_NEEDS_CARDS)
        self.assertEqual(ui.CHECK_ONLY_NEEDS_CARDS, "To check only Claude's finds, add at least one creator "
                                                    "first, or tick Also search Instagram for more creators.")

    def test_remember_writes_the_dials_once_set_up(self) -> None:
        with temp_project() as project:
            _set_up_project(project, {"competitors": ["habitlab"]})
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

    def test_save_and_close_wait_for_a_running_search(self) -> None:
        running = "A search is running. Wait for it to finish first."
        with temp_project() as project:
            app = _app(project, runner=lambda job: None)
            self.assertEqual(_call(app, "POST", "/api/run", {})[0], 202)
            self.assertEqual(_call(app, "POST", "/api/save", {"picks": ["planwithpia"]}), (409, {"error": running}))
            self.assertEqual(_call(app, "POST", "/api/close", {}), (409, {"error": running}))
        self.assertIsNone(app.finished)

    def test_a_finished_panel_refuses_run_save_and_close(self) -> None:
        finished = (409, {"error": "This panel is finished. Go back to Claude."})
        for first, payload in (("/api/close", {}), ("/api/save", {"picks": ["planwithpia"]})):
            with self.subTest(first=first), temp_project() as project:
                app = _app(project)
                self.assertEqual(_call(app, "POST", first, payload)[0], 200)
                ended = dict(app.finished)
                self.assertEqual(_call(app, "POST", "/api/run", {}), finished)
                self.assertEqual(_call(app, "POST", "/api/save", {"picks": ["coachcora"]}), finished)
                self.assertEqual(_call(app, "POST", "/api/close", {}), finished)
                self.assertEqual(app.finished, ended)
                self.assertEqual(app.state, "idle")

    def test_run_save_and_close_check_and_change_state_under_the_lock(self) -> None:
        for path, payload, status in (("/api/run", {}, 202), ("/api/save", {"picks": ["planwithpia"]}, 200),
                                      ("/api/close", {}, 200)):
            with self.subTest(path=path), temp_project() as project:
                app = _app(project, runner=lambda job: None)
                answers: List[Tuple[int, Any]] = []
                worker = threading.Thread(target=lambda: answers.append(_call(app, "POST", path, payload)))
                with app._lock:
                    worker.start()
                    worker.join(0.1)
                    # The request waits for the lock before it reads or changes anything.
                    self.assertTrue(worker.is_alive())
                    self.assertEqual((app.state, app.finished), ("idle", None))
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(answers[0][0], status)


class SaveAndCloseTests(NoNetworkTestCase):
    def test_save_adds_picks_after_the_current_watch_list(self) -> None:
        with temp_project() as project:
            _set_up_project(project, {"competitors": ["habitlab"], "format_accounts": ["kevbuildsapps"]})
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

    def test_a_config_without_creator_md_is_not_set_up(self) -> None:
        # The chat flow can write config.json before setup has run.
        with temp_project() as project:
            _write_config(project, {"discover_min_followers": 20000})
            config_path = store.contentos_dir(project) / "config.json"
            before = config_path.read_text(encoding="utf-8")
            app = _app(project)
            self.assertFalse(_call(app, "GET", "/api/state")[1]["set_up"])
            self.assertEqual(_call(app, "POST", "/api/run", {"remember": True})[0], 202)
            self.assertEqual(config_path.read_text(encoding="utf-8"), before)
            status, data = _call(app, "POST", "/api/save", {"picks": ["planwithpia"]})
            picks = store.read_json(store.contentos_dir(project) / ui.PICKS_FILE_NAME)
            after = config_path.read_text(encoding="utf-8")
        self.assertEqual(status, 200)
        self.assertEqual(data["picks_path"], str(store.contentos_dir(project) / ui.PICKS_FILE_NAME))
        self.assertEqual(picks["picks"], ["planwithpia"])
        self.assertEqual(after, before)

    def test_save_reads_the_watch_list_on_disk_at_save_time(self) -> None:
        with temp_project() as project:
            _set_up_project(project, {"competitors": ["habitlab"]})
            app = _app(project)
            # The watch list changed after the panel opened (the chat, or another save).
            _set_up_project(project, {"competitors": ["habitlab", "chase.h.ai"]})
            status, data = _call(app, "POST", "/api/save", {"picks": ["planwithpia"]})
            config = store.read_json(store.contentos_dir(project) / "config.json")
        self.assertEqual(status, 200)
        self.assertEqual(config["competitors"], ["habitlab", "chase.h.ai", "planwithpia"])
        self.assertEqual(data["competitors"], ["habitlab", "chase.h.ai", "planwithpia"])

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


class SearchAgainTests(NoNetworkTestCase):
    PAGE = {
        "keywords": ["habit stacking"], "hashtags": ["tiny"], "search_instagram": False,
        "settings": {"discover_shortlist": 12},
        "cards": [{"handle": "webwillow", "kept": True},
                  {"handle": "focusfern", "removed": True}],
    }

    def test_search_again_writes_the_handoff_and_finishes(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["habitlab"], "format_accounts": ["formatfred"]})
            app = _app(project, seeds=["typedtia"])
            status, data = _call(app, "POST", "/api/search-again", self.PAGE)
            path = ui.handoff_path(project)
            doc = store.read_json(path)
            mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual((status, data), (200, {"next": "claude_search"}))
        self.assertEqual(mode, 0o600)
        self.assertEqual((doc["version"], doc["token"], doc["port"]), (1, TOKEN, PORT))
        self.assertEqual((doc["keywords"], doc["hashtags"], doc["seeds"]), (["habit stacking"], ["tiny"], ["typedtia"]))
        self.assertEqual([(c["handle"], c["kept"], c["removed"]) for c in doc["cards"]],
                         [("webwillow", True, False), ("focusfern", False, True)])
        self.assertEqual(doc["settings"]["discover_shortlist"], 12)
        self.assertEqual((doc["search_instagram"], doc["has_results"]), (False, False))
        finished = app.finished
        self.assertEqual(finished["next"], "claude_search")
        self.assertEqual(finished["handoff_path"], str(path))
        # Removed cards, the watch list, typed seeds, and format accounts are all known.
        self.assertEqual(finished["known"], ["webwillow", "focusfern", "habitlab", "typedtia", "formatfred"])
        self.assertEqual((finished["saved"], finished["picks"]), (False, []))

    def test_has_results_after_a_finished_scan(self) -> None:
        with temp_project() as project:
            app = _app(project)
            _call(app, "POST", "/api/run", {})
            _call(app, "POST", "/api/search-again", {})
            self.assertIs(store.read_json(ui.handoff_path(project))["has_results"], True)

    def test_has_results_after_a_scan_then_a_failed_scan(self) -> None:
        calls: List[int] = []

        def succeed_then_fail(project: Path, cfg: Dict[str, Any], keys: Any, **kwargs: Any) -> Dict[str, Any]:
            calls.append(1)
            if len(calls) > 1:
                raise discover.DiscoverError("Apify timed out")
            return discover.run_discover(project, cfg, keys, **kwargs)

        with temp_project() as project:
            app = _app(project, run_discover=succeed_then_fail)
            _call(app, "POST", "/api/run", {})
            _call(app, "POST", "/api/run", {})
            self.assertEqual(app.state, "error")
            _call(app, "POST", "/api/search-again", {})
            # discovery.json still holds the first scan, so the resumed panel shows it.
            self.assertIs(store.read_json(ui.handoff_path(project))["has_results"], True)

    def test_a_resumed_panel_with_results_hands_them_on(self) -> None:
        with temp_project() as project:
            _call(_app(project), "POST", "/api/run", {})
            app = _app(project, done=True, run_discover=mock.Mock(side_effect=discover.DiscoverError("boom")))
            _call(app, "POST", "/api/run", {})
            _call(app, "POST", "/api/search-again", {})
            self.assertIs(store.read_json(ui.handoff_path(project))["has_results"], True)

    def test_search_again_waits_for_a_scan_and_refuses_a_finished_panel(self) -> None:
        with temp_project() as project:
            app = _app(project, runner=lambda job: None)
            _call(app, "POST", "/api/run", {})
            self.assertEqual(_call(app, "POST", "/api/search-again", {})[0], 409)
            self.assertFalse(ui.handoff_path(project).exists())
        with temp_project() as project:
            app = _app(project)
            _call(app, "POST", "/api/close", {})
            self.assertEqual(_call(app, "POST", "/api/search-again", {})[1]["error"], ui.FINISHED_ERROR)

    def test_a_bad_setting_is_refused_and_nothing_is_written(self) -> None:
        with temp_project() as project:
            app = _app(project)
            status, _data = _call(app, "POST", "/api/search-again", {"settings": {"discover_shortlist": 0}})
            self.assertEqual(status, 400)
            self.assertFalse(ui.handoff_path(project).exists())
            self.assertIsNone(app.finished)

    def test_an_idle_timeout_writes_the_handoff_from_the_last_estimate(self) -> None:
        now = {"t": 0.0}
        with temp_project() as project:
            app = _app(project, clock=lambda: now["t"])
            _call(app, "POST", "/api/estimate", {"keywords": ["morning routine"],
                                                 "cards": [{"handle": "slowsam", "kept": True}]})
            now["t"] = 4000.0
            app.stop_if_idle(3600)
            doc = store.read_json(ui.handoff_path(project))
        self.assertEqual((app.finished["reason"], app.finished["handoff_path"]),
                         ("idle", str(ui.handoff_path(project))))
        self.assertEqual(doc["keywords"], ["morning routine"])
        self.assertEqual([(c["handle"], c["kept"]) for c in doc["cards"]], [("slowsam", True)])

    def test_an_idle_handoff_keeps_the_dials_the_creator_last_set(self) -> None:
        now = {"t": 0.0}
        dials = {"discover_min_followers": 25000, "discover_shortlist": 10}
        with temp_project() as project:
            app = _app(project, clock=lambda: now["t"])
            _call(app, "POST", "/api/estimate", {"settings": dials})
            now["t"] = 4000.0
            app.stop_if_idle(3600)
            doc = store.read_json(ui.handoff_path(project))
        self.assertEqual((doc["settings"]["discover_min_followers"], doc["settings"]["discover_shortlist"]),
                         (25000, 10))
        # The RESULT's settings stay the last run's (none here, so the config's), as in 0.6.0.
        self.assertEqual((app.finished["settings"]["discover_min_followers"],
                          app.finished["settings"]["discover_shortlist"]), (10000, 20))

    def test_a_failed_handoff_write_still_ends_an_idle_panel(self) -> None:
        now = {"t": 0.0}
        full = OSError(28, "No space left on device")
        with temp_project() as project, mock.patch.object(ui.App, "_write_handoff", side_effect=full):
            app = _app(project, clock=lambda: now["t"])
            now["t"] = 4000.0
            app.stop_if_idle(3600)
        self.assertEqual((app.finished["saved"], app.finished["reason"]), (False, "idle"))
        self.assertNotIn("handoff_path", app.finished)
        self.assertIn("could not be saved to reopen", app.finished["warning"])
        self.assertIn("No space left on device", app.finished["warning"])

    def test_a_failed_handoff_write_leaves_search_again_open(self) -> None:
        full = OSError(28, "No space left on device")
        with temp_project() as project, mock.patch.object(ui.App, "_write_handoff", side_effect=full):
            app = _app(project)
            status, data = _call(app, "POST", "/api/search-again", {})
        self.assertEqual((status, data), (500, {"error": "Could not save the panel for Claude. Try again."}))
        self.assertIsNone(app.finished)

    def test_save_and_close_remove_a_stale_handoff(self) -> None:
        for route, payload in (("/api/save", {"picks": ["webwillow"]}), ("/api/close", {})):
            with self.subTest(route=route), temp_project() as project:
                ui.handoff_path(project).parent.mkdir(parents=True, exist_ok=True)
                ui.handoff_path(project).write_text("{}", encoding="utf-8")
                self.assertEqual(_call(_app(project), "POST", route, payload)[0], 200)
                self.assertFalse(ui.handoff_path(project).exists())

    def test_serve_ends_with_the_search_again_result(self) -> None:
        def search_again(srv: _FakeServer) -> None:
            session = store.read_json(ui.session_path(project))
            token = session["url"].split("#t=", 1)[1]
            srv.app.handle("POST", "/api/search-again", {"host": f"127.0.0.1:{PORT}", "x-contentos-token": token,
                                                         "content-type": "application/json"}, b"{}")

        def factory(address: Tuple[str, int], handler: Any) -> _FakeServer:
            server = _FakeServer(address, handler)
            server.steps = [search_again]
            return server

        with temp_project() as project, redirect_stdout(StringIO()):
            result = ui.serve(project, store.load_discovery_config(project), ["habit coach"], [], [], [],
                              mock=True, server_factory=factory)
            self.assertTrue(ui.handoff_path(project).exists())
            self.assertFalse(ui.session_path(project).exists())
        self.assertEqual((result["next"], result["keywords"]), ("claude_search", ["habit coach"]))
        self.assertTrue(result["discovery_path"].endswith("discovery.json"))


class _FakeServer:
    """Stands in for ThreadingHTTPServer: no socket, scripted requests."""

    def __init__(self, address: Tuple[str, int], handler: Any) -> None:
        self.address = address
        self.handler = handler
        self.server_address = ("127.0.0.1", PORT)
        self.timeout: Optional[float] = None
        self.closed = False
        self.steps: List[Any] = []
        self.app: Optional[ui.App] = None

    def handle_request(self) -> None:
        if self.steps:
            self.steps.pop(0)(self)

    def server_close(self) -> None:
        self.closed = True


def _closing_factory(project: Path, made: List[_FakeServer], seen: List[Dict[str, Any]]) -> Any:
    def factory(address: Tuple[str, int], handler: Any) -> _FakeServer:
        server = _FakeServer(address, handler)

        def close(srv: _FakeServer) -> None:
            session = store.read_json(ui.session_path(project))
            seen.append(session)
            token = session["url"].split("#t=", 1)[1]
            srv.app.handle("POST", "/api/close", {"host": f"127.0.0.1:{PORT}", "x-contentos-token": token,
                                                  "content-type": "application/json"}, b"{}")

        server.steps = [close]
        made.append(server)
        return server

    return factory


class ServeTests(NoNetworkTestCase):
    def test_serve_writes_the_session_file_and_removes_it_on_close(self) -> None:
        made: List[_FakeServer] = []
        seen: List[Dict[str, Any]] = []
        with temp_project() as project:
            out = StringIO()
            with redirect_stdout(out):
                result = ui.serve(project, store.load_discovery_config(project), [], [], [], [], mock=True,
                                  server_factory=_closing_factory(project, made, seen))
            self.assertFalse(ui.session_path(project).exists())
            gitignore = (store.contentos_dir(project) / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("ui-session.json", gitignore.splitlines())
        self.assertIn("ui-handoff.json", gitignore.splitlines())
        self.assertEqual(made[0].address, ("127.0.0.1", 0))
        self.assertTrue(made[0].closed)
        self.assertTrue(seen[0]["url"].startswith(f"http://127.0.0.1:{PORT}/#t="))
        self.assertEqual(seen[0]["pid"], os.getpid())
        self.assertEqual((result["saved"], result["picks"]), (False, []))
        self.assertTrue(result["discovery_path"].endswith("discovery.json"))
        self.assertTrue(out.getvalue().startswith(f"UI http://127.0.0.1:{PORT}/#t="))

    def test_serve_stops_when_idle(self) -> None:
        ticks = iter([0.0] + [3601.0] * 10)
        with temp_project() as project, redirect_stdout(StringIO()):
            result = ui.serve(project, store.load_discovery_config(project), [], [], [], [], mock=True,
                              idle_minutes=60, server_factory=_FakeServer, clock=lambda: next(ticks))
        self.assertEqual((result["saved"], result["reason"]), (False, "idle"))

    def test_open_flag_opens_the_link(self) -> None:
        opened: List[str] = []
        with temp_project() as project, redirect_stdout(StringIO()):
            ui.serve(project, store.load_discovery_config(project), [], [], [], [], mock=True,
                     open_browser=True, opener=opened.append,
                     server_factory=_closing_factory(project, [], []))
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].startswith(f"http://127.0.0.1:{PORT}/#t="))

    def test_the_session_file_is_readable_only_by_the_creator(self) -> None:
        modes: List[int] = []

        def read_mode(_srv: _FakeServer) -> None:
            # While serve runs, the link with the token is on disk.
            modes.append(stat.S_IMODE(os.stat(ui.session_path(project)).st_mode))

        def factory(address: Tuple[str, int], handler: Any) -> _FakeServer:
            server = _closing_factory(project, [], [])(address, handler)
            server.steps.insert(0, read_mode)
            return server

        with temp_project() as project, redirect_stdout(StringIO()):
            ui.serve(project, store.load_discovery_config(project), [], [], [], [], mock=True,
                     server_factory=factory)
        self.assertEqual(modes, [0o600])

    def test_the_default_server_waits_for_its_answers(self) -> None:
        default = inspect.signature(ui.serve).parameters["server_factory"].default
        self.assertIs(default, ui.PanelServer)
        self.assertTrue(issubclass(ui.PanelServer, ThreadingHTTPServer))
        # server_close() joins the request threads, so a Save or Close answer is always sent.
        self.assertIs(ui.PanelServer.daemon_threads, False)
        self.assertEqual(ui._Handler.timeout, 10)

    def test_the_idle_timeout_waits_for_a_running_search(self) -> None:
        now = {"t": 0.0}
        seen: List[Any] = []

        def factory(address: Tuple[str, int], handler: Any) -> _FakeServer:
            server = _FakeServer(address, handler)

            def start_search(srv: _FakeServer) -> None:
                srv.app.state = "running"
                now["t"] = 3601.0

            def still_running(srv: _FakeServer) -> None:
                seen.append(srv.app.finished)

            def search_done(srv: _FakeServer) -> None:
                srv.app.state = "done"

            def too_far(_srv: _FakeServer) -> None:
                raise AssertionError("serve kept going after the idle timeout")

            server.steps = [start_search, still_running, search_done, too_far]
            return server

        with temp_project() as project, redirect_stdout(StringIO()):
            result = ui.serve(project, store.load_discovery_config(project), [], [], [], [], mock=True,
                              idle_minutes=60, server_factory=factory, clock=lambda: now["t"])
        self.assertEqual(seen, [None])
        self.assertEqual((result["saved"], result["reason"]), (False, "idle"))

    def test_sigterm_and_sighup_end_serve_and_clean_up(self) -> None:
        names = [name for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)]
        self.assertIn("SIGTERM", names)
        for name in names:
            signum = getattr(signal, name)
            before = signal.getsignal(signum)
            installed: List[Any] = []

            def factory(address: Tuple[str, int], handler: Any) -> _FakeServer:
                server = _FakeServer(address, handler)

                def signalled(_srv: _FakeServer) -> None:
                    # What the OS would run on the main thread; no real signal is sent.
                    stop = signal.getsignal(signum)
                    installed.append(stop)
                    stop(signum, None)

                server.steps = [signalled]
                return server

            with self.subTest(signal=name), temp_project() as project, redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    ui.serve(project, store.load_discovery_config(project), [], [], [], [], mock=True,
                             server_factory=factory)
                self.assertEqual(ctx.exception.code, 128 + signum)
                self.assertFalse(ui.session_path(project).exists())
                self.assertIsNot(installed[0], before)
                self.assertEqual(signal.getsignal(signum), before)

    def test_signals_are_left_alone_off_the_main_thread(self) -> None:
        before = signal.getsignal(signal.SIGTERM)
        during: List[Any] = []
        errors: List[BaseException] = []

        def factory(address: Tuple[str, int], handler: Any) -> _FakeServer:
            server = _closing_factory(project, [], [])(address, handler)
            server.steps.insert(0, lambda _srv: during.append(signal.getsignal(signal.SIGTERM)))
            return server

        def serve_elsewhere() -> None:
            try:
                ui.serve(project, store.load_discovery_config(project), [], [], [], [], mock=True,
                         server_factory=factory)
            except BaseException as exc:  # noqa: BLE001 - reported to the test thread
                errors.append(exc)

        with temp_project() as project, redirect_stdout(StringIO()):
            worker = threading.Thread(target=serve_elsewhere)
            worker.start()
            worker.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(during, [before])


def _handoff(project: Path, **overrides: Any) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "version": 1, "token": TOKEN, "port": PORT, "created_at": "2026-09-24T00:00:00+00:00",
        "keywords": ["habit coach"], "hashtags": ["habits"], "seeds": ["typedtia"],
        "cards": [{"handle": "webwillow", "kept": True, "new": True},
                  {"handle": "focusfern", "removed": True}],
        "settings": {"discover_min_followers": 25000, "discover_min_views": 5000,
                     "discover_post_every_days": 14, "discover_shortlist": 12},
        "search_instagram": False, "has_results": False,
    }
    doc.update(overrides)
    ui.handoff_path(project).parent.mkdir(parents=True, exist_ok=True)
    ui.handoff_path(project).write_text(json.dumps(doc), encoding="utf-8")
    return doc


class ResumeTests(NoNetworkTestCase):
    def test_load_handoff_refuses_what_it_cannot_reopen(self) -> None:
        with temp_project() as project:
            with self.assertRaises(ui.HandoffError):
                ui.load_handoff(project)
            ui.handoff_path(project).parent.mkdir(parents=True, exist_ok=True)
            for text in ("{nope", "[]", json.dumps({"version": 2, "token": TOKEN}),
                         json.dumps({"version": 1, "token": ""})):
                with self.subTest(text=text):
                    ui.handoff_path(project).write_text(text, encoding="utf-8")
                    with self.assertRaises(ui.HandoffError):
                        ui.load_handoff(project)
            _handoff(project)
            self.assertEqual(ui.load_handoff(project)["token"], TOKEN)

    def test_merge_finds_marks_only_new_handles_new(self) -> None:
        cards = ui.normalize_cards([{"handle": "webwillow", "new": True}, {"handle": "focusfern", "removed": True}])
        finds, _warnings = discover.normalize_web_entries(
            ["focusfern", "habitlab", "coachcora", "coachcora", "webwillow", "planwithpia"])
        merged, warnings = ui.merge_finds(cards, finds, ["habitlab"])
        self.assertEqual([(c["handle"], c["new"], c["removed"]) for c in merged], [
            ("webwillow", False, False), ("focusfern", False, True),
            ("coachcora", True, False), ("planwithpia", True, False),
        ])
        self.assertEqual(warnings, [])

    def test_merge_finds_holds_at_most_120_cards(self) -> None:
        cards = ui.normalize_cards([{"handle": f"h{i}"} for i in range(118)])
        finds, _warnings = discover.normalize_web_entries([f"n{i}" for i in range(5)])
        merged, warnings = ui.merge_finds(cards, finds, [])
        self.assertEqual(len(merged), 120)
        self.assertEqual(warnings, ["The panel holds 120 creators, so 3 new finds were left out."])

    def test_resume_session_restores_the_panel(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["habitlab"]})
            handoff = _handoff(project)
            finds, _warnings = discover.normalize_web_entries(["habitlab", "coachcora"])
            session = ui.resume_session(project, store.load_discovery_config(project), handoff, finds)
        self.assertEqual((session["token"], session["port"]), (TOKEN, PORT))
        self.assertEqual((session["keywords"], session["hashtags"], session["seeds"]),
                         (["habit coach"], ["habits"], ["typedtia"]))
        self.assertEqual([c["handle"] for c in session["cards"]], ["webwillow", "focusfern", "coachcora"])
        self.assertEqual(session["settings"]["discover_min_followers"], 25000)
        self.assertEqual((session["search_instagram"], session["done"], session["notes"]), (False, False, []))

    def test_bad_settings_or_port_fall_back_to_defaults(self) -> None:
        with temp_project() as project:
            handoff = _handoff(project, settings={"discover_shortlist": 0}, port="x")
            session = ui.resume_session(project, store.load_discovery_config(project), handoff, [])
        self.assertEqual(session["settings"]["discover_shortlist"], 20)
        self.assertEqual(session["port"], 0)

    def test_done_only_when_the_results_file_exists(self) -> None:
        with temp_project() as project:
            handoff = _handoff(project, has_results=True)
            cfg = store.load_discovery_config(project)
            self.assertFalse(ui.resume_session(project, cfg, handoff, [])["done"])
            app = _app(project)
            _call(app, "POST", "/api/run", {})
            self.assertTrue(ui.resume_session(project, cfg, handoff, [])["done"])

    def test_a_resumed_panel_shows_the_last_results_with_no_new_spend(self) -> None:
        with temp_project() as project:
            _call(_app(project), "POST", "/api/run", {})
            app = _app(project, done=True)
            status = _call(app, "GET", "/api/status")[1]
            doc_status, doc = _call(app, "GET", "/api/discovery")
        self.assertEqual((status["state"], status["summary"]["candidates"]), ("done", 3))
        self.assertEqual((doc_status, doc["version"]), (200, 2))

    def test_serve_reuses_the_token_and_port_and_removes_the_handoff(self) -> None:
        made: List[_FakeServer] = []
        seen: List[Dict[str, Any]] = []
        with temp_project() as project, redirect_stdout(StringIO()) as out:
            handoff = _handoff(project)
            cfg = store.load_discovery_config(project)
            session = ui.resume_session(project, cfg, handoff, [])
            ui.serve(project, cfg, session["keywords"], session["hashtags"], [], session["seeds"], mock=True,
                     port=session["port"], resume=session, server_factory=_closing_factory(project, made, seen))
            self.assertFalse(ui.handoff_path(project).exists())
        self.assertEqual(made[0].address, ("127.0.0.1", PORT))
        self.assertTrue(seen[0]["url"].endswith(f"#t={TOKEN}"))
        self.assertIn(f"#t={TOKEN}", out.getvalue())

    def test_a_taken_port_falls_back_to_a_new_one(self) -> None:
        tried: List[int] = []
        inner = _closing_factory

        with temp_project() as project, redirect_stdout(StringIO()):
            closing = inner(project, [], [])

            def factory(address: Tuple[str, int], handler: Any) -> Any:
                tried.append(address[1])
                if address[1] == PORT:
                    raise OSError(48, "Address already in use")
                return closing(address, handler)

            handoff = _handoff(project)
            cfg = store.load_discovery_config(project)
            session = ui.resume_session(project, cfg, handoff, [])
            ui.serve(project, cfg, [], [], [], [], mock=True, port=PORT, resume=session, server_factory=factory)
        self.assertEqual(tried, [PORT, 0])


def _main(argv: Sequence[str]) -> Tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = contentos.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class UiCommandTests(NoNetworkTestCase):
    def test_the_command_serves_and_prints_the_result(self) -> None:
        captured: Dict[str, Any] = {}

        def fake_serve(project: Path, cfg: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
            captured.update(kwargs)
            return {"saved": True, "picks": ["planwithpia"], "settings": {}, "discovery_path": "x"}

        with temp_project() as project, mock.patch.object(ui, "serve", side_effect=fake_serve):
            code, out, _err = _main([
                "ui", "--project", str(project), "--mock", "--keywords", "habit coach",
                "--hashtags", "habits,#Productivity", "--seeds", "HabitLab",
                "--handles-file", str(WEB_FILE), "--open", "--port", "5055",
            ])
        self.assertEqual(code, codes.EXIT_OK)
        self.assertEqual(captured["keywords"], ["habit coach"])
        self.assertEqual(captured["hashtags"], ["habits", "productivity"])
        self.assertEqual(captured["seeds"], ["HabitLab"])
        self.assertEqual([entry["handle"] for entry in captured["web_entries"]],
                         ["webwillow", "madeupmaya", "focusfern", "slowsam", "photophoebe"])
        self.assertEqual((captured["open_browser"], captured["port"], captured["mock"]), (True, 5055, True))
        self.assertEqual(json.loads(out.strip().splitlines()[-1][len("RESULT "):])["picks"], ["planwithpia"])

    def test_a_bad_handles_file_exits_2(self) -> None:
        refuse = AssertionError("ui.serve must not run")
        with temp_project() as project, mock.patch.object(ui, "serve", side_effect=refuse) as serve:
            bad = Path(project) / "web.json"
            bad.write_text("{not json", encoding="utf-8")
            code, _out, _err = _main(["ui", "--project", str(project), "--handles-file", str(bad)])
        self.assertEqual(code, codes.EXIT_USAGE)
        serve.assert_not_called()

    def test_idle_minutes_must_be_more_than_0(self) -> None:
        refuse = AssertionError("ui.serve must not run")
        for minutes in ("0", "-5", "nan"):
            with self.subTest(minutes=minutes), temp_project() as project, \
                    mock.patch.object(ui, "serve", side_effect=refuse) as serve:
                code, _out, err = _main(["ui", "--project", str(project), "--mock", "--idle-minutes", minutes])
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertIn("--idle-minutes must be more than 0", err)
            serve.assert_not_called()

    def test_a_port_that_will_not_open_exits_2(self) -> None:
        real_serve = ui.serve
        for port, failure in (("5055", OSError(48, "Address already in use")),
                              ("70000", OverflowError("bind(): port must be 0-65535."))):
            def refuse(_address: Tuple[str, int], _handler: Any, failure: BaseException = failure) -> Any:
                raise failure

            with self.subTest(port=port), temp_project() as project, \
                    mock.patch.object(ui, "serve", functools.partial(real_serve, server_factory=refuse)):
                code, out, err = _main(["ui", "--project", str(project), "--mock", "--port", port])
                self.assertFalse(ui.session_path(project).exists())
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertEqual(err.strip(), f"Could not open the panel on port {port}: {failure}")
            self.assertNotIn("RESULT", out)

    def test_resume_with_nothing_to_resume_exits_2(self) -> None:
        refuse = AssertionError("ui.serve must not run")
        with temp_project() as project, mock.patch.object(ui, "serve", side_effect=refuse):
            code, out, err = _main(["ui", "--project", str(project), "--resume"])
        self.assertEqual(code, codes.EXIT_USAGE)
        self.assertIn("Nothing to resume", err)
        self.assertNotIn("RESULT", out)

    def test_resume_refuses_new_search_terms(self) -> None:
        refuse = AssertionError("ui.serve must not run")
        for flag in ("--keywords", "--hashtags", "--seeds"):
            with self.subTest(flag=flag), temp_project() as project, \
                    mock.patch.object(ui, "serve", side_effect=refuse):
                _handoff(project)
                code, _out, err = _main(["ui", "--project", str(project), "--resume", flag, "x"])
            self.assertEqual(code, codes.EXIT_USAGE)
            self.assertIn("--resume", err)

    def test_resume_adds_the_new_finds_and_serves(self) -> None:
        captured: Dict[str, Any] = {}

        def fake_serve(project: Path, cfg: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
            captured.update(kwargs)
            return {"saved": False, "picks": [], "settings": {}, "discovery_path": "x"}

        with temp_project() as project, mock.patch.object(ui, "serve", side_effect=fake_serve):
            _handoff(project)
            code, _out, _err = _main(["ui", "--project", str(project), "--mock", "--resume",
                                      "--handles-file", str(WEB_FILE)])
        self.assertEqual(code, codes.EXIT_OK)
        cards = captured["resume"]["cards"]
        self.assertEqual([(c["handle"], c["new"]) for c in cards][:3],
                         [("webwillow", False), ("focusfern", False), ("madeupmaya", True)])
        self.assertEqual((captured["port"], captured["keywords"]), (PORT, ["habit coach"]))


class PageTests(NoNetworkTestCase):
    def _page(self) -> str:
        return ui.PAGE_PATH.read_text(encoding="utf-8")

    def test_the_page_loads_nothing_from_outside_and_never_writes_raw_html(self) -> None:
        text = self._page()
        # The one address the page names is Instagram's, for profile links.
        self.assertEqual(text.count('var INSTAGRAM_URL = "https://www.instagram.com/";'), 1)
        rest = text.replace('var INSTAGRAM_URL = "https://www.instagram.com/";', "")
        for banned in ("http://", "https://", "innerHTML", "outerHTML", "insertAdjacentHTML",
                       "document.write", "<script src", "@import", "—"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, rest)

    def test_the_page_calls_every_route_and_sends_the_token(self) -> None:
        text = self._page()
        self.assertEqual(set(re.findall(r'"(/api/[a-z-]+)"', text)), set(ui.ROUTES))
        self.assertIn("X-ContentOS-Token", text)
        self.assertIn("prefers-color-scheme: dark", text)
        for element_id in ("keywords", "hashtags", "cards", "web-add", "web-add-btn", "claude-btn", "claude-error",
                           "watch-list", "dial-followers", "dial-views", "dial-every", "dial-shortlist", "remember",
                           "search-instagram", "cost", "run-btn", "run-error", "log", "results", "summary",
                           "partial", "warnings", "picked", "unchecked-note", "close-btn", "save-btn",
                           "save-error", "done-note", "waiting"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', text)

    def test_the_buttons_and_labels_say_what_they_do(self) -> None:
        text = self._page()
        for phrase in ("Run the Apify scan", "Search again with Claude", "Also search Instagram for more creators",
                       "Found by Claude", "Added by you", "Found on Instagram", "Similar to @", "New",
                       "not checked", "Keep", "Passed", "Missed: "):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_a_check_only_estimate_with_nothing_to_check_shows_its_note(self) -> None:
        estimate = self._page().split("function estimate()", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("data.note", estimate)

    def test_save_works_before_any_scan_and_names_unchecked_picks(self) -> None:
        text = self._page()
        self.assertIn(" of your picks were not checked with Apify.", text)
        self.assertIn(" of your picks was not checked with Apify.", text)
        start = text.split("function start(state)", 1)[1]
        self.assertNotIn('$("save-btn").disabled = true', start)

    def test_search_again_waits_for_the_panel_to_come_back(self) -> None:
        text = self._page()
        self.assertIn("Claude is searching in your chat. This page comes back by itself.", text)
        self.assertIn("Ask Claude for a new link.", text)
        wait = text.split("function waitForPanel(since)", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("3000", wait)
        self.assertIn("state.finished", wait)
        self.assertIn("window.location.reload()", wait)
        self.assertIn("WAIT_MS = 15 * 60 * 1000", text)

    def test_a_page_from_an_older_panel_reloads(self) -> None:
        text = self._page()
        self.assertIn("generation: generation", text.split("function inputs()", 1)[1].split("\n  }\n", 1)[0])
        self.assertIn("generation = state.generation", text.split("function start(state)", 1)[1])
        for name in ("function estimate()", "function searchAgain()"):
            with self.subTest(name=name):
                body = text.split(name, 1)[1].split("\n  }\n", 1)[0]
                self.assertIn("reloadIfStale(error)", body)
        self.assertIn("window.location.reload()", text.split("function reloadIfStale(error)", 1)[1])

    def test_the_token_comes_only_from_the_fragment(self) -> None:
        text = self._page()
        self.assertIn("window.location.hash.match(/(?:^#|&)t=([^&]+)/)", text)
        self.assertNotIn("match(/t=([^&]+)/)", text)

    def test_a_reload_picks_up_the_run_or_its_results(self) -> None:
        start = self._page().split("function start(state)", 1)[1]
        self.assertIn('state.state === "running"', start)
        self.assertIn('state.state !== "idle"', start)
        self.assertIn("poll();", start)

    def test_every_button_waits_while_a_scan_runs(self) -> None:
        text = self._page()
        busy = text.split("function setRunning(running)", 1)[1].split("}", 1)[0]
        for button in ("run-btn", "save-btn", "close-btn", "claude-btn"):
            with self.subTest(button=button):
                self.assertIn(f'$("{button}").disabled = running;', busy)
        self.assertIn("setRunning(true)", text.split("function run()", 1)[1])
        self.assertIn("setRunning(false)", text.split("function poll()", 1)[1])

    def test_a_partial_run_and_its_warnings_are_shown_as_plain_text(self) -> None:
        text = self._page()
        self.assertIn("Some accounts were not checked or measured in time. Run it again to finish them.", text)
        self.assertIn("doc.partial", text)
        self.assertIn("doc.warnings", text)

    def test_counts_are_worded_plainly(self) -> None:
        text = self._page()
        for phrase in ('"last reel today"', '" day ago"', '" days ago"', '"followers unknown"'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertNotIn('row.last_post_days + " days ago"', text)

    def test_the_save_note_covers_saving_before_setup(self) -> None:
        self.assertIn("for your setup", self._page())

    def test_source_links_are_only_http_or_https(self) -> None:
        text = self._page()
        self.assertIn("/^https?:\\/\\//i.test(", text)


if __name__ == "__main__":
    unittest.main()
