"""The discovery control panel: one local page to set the dials, run, and pick.

Design spec, "0.6.0 changes", Control panel. `App.handle` is pure: the
tests call it directly and never open a socket, and `serve` puts it behind
the standard library's ThreadingHTTPServer on 127.0.0.1. The page can spend
the creator's Apify credit, so every API call needs the session token and a
local Host header, and every run needs a click after the estimate shows.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib import codes, discover, env, research, setup, store

PAGE_PATH = Path(__file__).resolve().parent.parent / "ui" / "discover.html"
PICKS_FILE_NAME = "discovery-picks.json"
TOKEN_HEADER = "x-contentos-token"
DIAL_KEYS = (
    "discover_min_followers",
    "discover_min_views",
    "discover_post_every_days",
    "discover_shortlist",
)
ROUTES = (
    "/api/state",
    "/api/estimate",
    "/api/run",
    "/api/status",
    "/api/discovery",
    "/api/save",
    "/api/close",
)
LOG_LINES_KEPT = 200


@dataclass
class Response:
    """One answer from `App.handle`: what the server writes back."""

    status: int
    content_type: str
    body: bytes


def json_response(status: int, payload: Any) -> Response:
    return Response(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))


def thread_runner(job: Callable[[], None]) -> None:
    """Run `job` on a daemon thread, so a long discovery never blocks a request."""
    threading.Thread(target=job, daemon=True).start()


def _strings(value: Any) -> List[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


class App:
    """The panel's API for one `ui` session."""

    def __init__(
        self,
        project: Path,
        cfg: Dict[str, Any],
        token: str,
        port: int,
        keywords: List[str],
        hashtags: List[str],
        web_entries: List[Dict[str, str]],
        seeds: List[str],
        mock: bool = False,
        runner: Callable[[Callable[[], None]], None] = thread_runner,
        resolve_keys: Callable[[Path], env.Keys] = env.resolve_keys,
        clock: Callable[[], float] = time.monotonic,
        run_discover: Callable[..., Dict[str, Any]] = discover.run_discover,
    ) -> None:
        self.project = Path(project)
        self.cfg = cfg
        self.token = token
        self.port = port
        self.keywords = keywords
        self.hashtags = hashtags
        self.web_entries = web_entries
        self.seeds = seeds
        self.mock = mock
        self.runner = runner
        self.resolve_keys = resolve_keys
        self.clock = clock
        self.run_discover = run_discover
        self.state = "idle"
        self.log: List[str] = []
        self.error: Optional[str] = None
        self.summary: Optional[Dict[str, Any]] = None
        self.last_settings: Dict[str, Any] = {key: cfg[key] for key in DIAL_KEYS}
        self.finished: Optional[Dict[str, Any]] = None
        self.last_seen = clock()
        self._lock = threading.Lock()

    def handle(self, method: str, path: str, headers: Dict[str, str], body: bytes) -> Response:
        """Answer one request. Guards first: Host, then route, then token, then JSON."""
        self.last_seen = self.clock()
        lowered = {str(key).lower(): str(value) for key, value in headers.items()}
        if lowered.get("host") not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return json_response(403, {"error": "This panel only answers on this computer."})
        route = path.split("?", 1)[0]
        if method == "GET" and route == "/":
            return Response(200, "text/html; charset=utf-8", PAGE_PATH.read_bytes())
        if route not in ROUTES:
            return json_response(404, {"error": "Not found."})
        sent = lowered.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(sent.encode("utf-8"), self.token.encode("utf-8")):
            return json_response(403, {"error": "Open the panel from the link Claude gave you."})
        payload: Dict[str, Any] = {}
        if method == "POST":
            if not lowered.get("content-type", "").startswith("application/json"):
                return json_response(415, {"error": "Send JSON."})
            try:
                parsed = json.loads(body.decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                return json_response(400, {"error": "That request was not valid JSON."})
            if not isinstance(parsed, dict):
                return json_response(400, {"error": "Send a JSON object."})
            payload = parsed
        handlers = {
            ("GET", "/api/state"): self._state,
            ("POST", "/api/estimate"): self._estimate,
            ("POST", "/api/run"): self._run,
            ("GET", "/api/status"): self._status,
            ("GET", "/api/discovery"): self._discovery,
            ("POST", "/api/save"): self._save,
            ("POST", "/api/close"): self._close,
        }
        handler = handlers.get((method, route))
        if handler is None:
            return json_response(405, {"error": "Not allowed."})
        return handler(payload)

    # -- helpers -----------------------------------------------------------

    def _set_up(self) -> bool:
        return (store.contentos_dir(self.project) / "config.json").exists()

    def _config_with(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        settings = payload.get("settings", {})
        if not isinstance(settings, dict):
            raise store.ConfigError("settings must be an object")
        unknown = sorted(set(settings) - set(DIAL_KEYS))
        if unknown:
            raise store.ConfigError(f"unknown settings: {', '.join(unknown)}")
        cfg = dict(self.cfg, **settings)
        store.check_discovery_config(cfg)
        return cfg

    def _inputs(self, payload: Dict[str, Any]) -> Tuple[List[str], List[str], List[Dict[str, str]]]:
        keywords = discover.normalize_keywords(_strings(payload.get("keywords", self.keywords)))
        hashtags = discover.normalize_hashtags(_strings(payload.get("hashtags", self.hashtags)))
        try:
            web, _warnings = discover.normalize_web_entries(payload.get("web", self.web_entries))
        except discover.DiscoverError:
            web = []
        return keywords, hashtags, web

    def _cost(
        self, cfg: Dict[str, Any], keywords: List[str], hashtags: List[str], web: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        seeds, format_accounts, _warnings = discover.never_recommended(cfg, self.seeds)
        checked_web = discover.web_handles(web, seeds + format_accounts)
        cost = discover.estimate(cfg, len(keywords), len(hashtags), len(seeds), len(checked_web))
        cap = cfg["apify_max_charge_usd"]
        return dict(cost, cap_usd=cap, within_cap=cost["total_usd"] <= cap)

    def _log(self, message: str) -> None:
        with self._lock:
            self.log.append(message)
            del self.log[:-LOG_LINES_KEPT]

    def _fail(self, message: str) -> None:
        with self._lock:
            self.state, self.error = "error", message

    def _is_running(self) -> bool:
        with self._lock:
            return self.state == "running"

    # -- routes --------------------------------------------------------------

    def _state(self, _payload: Dict[str, Any]) -> Response:
        keys = self.resolve_keys(self.project)
        return json_response(200, {
            "set_up": self._set_up(),
            "mock": self.mock,
            "has_key": self.mock or bool(keys.apify),
            "cap_usd": self.cfg["apify_max_charge_usd"],
            "established_at": self.cfg["small_account_followers"],
            "settings": dict(self.last_settings),
            "keywords": self.keywords,
            "hashtags": self.hashtags,
            "web": self.web_entries,
            "watch_list": list(self.cfg.get("competitors") or []),
            "state": self.state,
        })

    def _estimate(self, payload: Dict[str, Any]) -> Response:
        try:
            cfg = self._config_with(payload)
        except store.ConfigError as exc:
            return json_response(400, {"error": str(exc), "code": codes.EXIT_USAGE})
        return json_response(200, self._cost(cfg, *self._inputs(payload)))

    def _run(self, payload: Dict[str, Any]) -> Response:
        with self._lock:
            if self.state == "running":
                return json_response(409, {"error": "A search is already running."})
            try:
                cfg = self._config_with(payload)
            except store.ConfigError as exc:
                return json_response(400, {"error": str(exc), "code": codes.EXIT_USAGE})
            keywords, hashtags, web = self._inputs(payload)
            seeds, format_accounts, _warnings = discover.never_recommended(cfg, self.seeds)
            checked_web = discover.web_handles(web, seeds + format_accounts)
            if not (keywords or hashtags or checked_web or seeds):
                return json_response(400, {
                    "error": "Add a keyword phrase, a hashtag, or a handle first.", "code": codes.EXIT_USAGE,
                })
            cost = self._cost(cfg, keywords, hashtags, web)
            if not cost["within_cap"]:
                return json_response(400, {
                    "error": f"This would cost about ${cost['total_usd']:.2f}, over your "
                             f"${cost['cap_usd']:.2f} cap. Check fewer creators or drop the hashtags.",
                    "code": codes.EXIT_COST,
                })
            keys = self.resolve_keys(self.project)
            if not self.mock and not keys.apify:
                return json_response(400, {
                    "error": "No Apify key found. Ask Claude to walk you through adding it.",
                    "code": codes.EXIT_KEYS,
                })
            self.last_settings = {key: cfg[key] for key in DIAL_KEYS}
            if payload.get("remember") is True and self._set_up():
                try:
                    store.update_config_keys(self.project, self.last_settings)
                except store.ConfigError as exc:
                    return json_response(400, {"error": str(exc), "code": codes.EXIT_USAGE})
            self.state, self.log, self.error, self.summary = "running", [], None, None
        self.runner(lambda: self._work(cfg, keys, keywords, hashtags, web))
        return json_response(202, {"state": "running"})

    def _work(
        self, cfg: Dict[str, Any], keys: env.Keys, keywords: List[str], hashtags: List[str],
        web: List[Dict[str, str]],
    ) -> None:
        try:
            doc = self.run_discover(
                self.project, cfg, keys, hashtags=hashtags, keywords=keywords, seeds=self.seeds,
                web_entries=web, mock=self.mock, yes=True, log=self._log,
            )
        except (research.ResearchError, discover.DiscoverError) as exc:
            self._fail(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the page must never wait forever on a crash
            self._fail(f"Something went wrong: {exc}")
            return
        with self._lock:
            self.state = "done"
            self.summary = discover.result_line(doc, self.project)

    def _status(self, _payload: Dict[str, Any]) -> Response:
        with self._lock:
            return json_response(200, {
                "state": self.state, "log": self.log[-50:], "error": self.error, "summary": self.summary,
            })

    def _discovery(self, _payload: Dict[str, Any]) -> Response:
        path = discover.discovery_path(self.project)
        if self.state != "done" or not path.exists():
            return json_response(409, {"error": "No results yet. Press Run first."})
        return json_response(200, store.read_json(path))

    def _save(self, payload: Dict[str, Any]) -> Response:
        if self._is_running():
            return json_response(409, {
                "error": "A search is running. Wait for it to finish, then save or close.",
            })
        raw = payload.get("picks")
        if not isinstance(raw, list):
            return json_response(400, {"error": "Send picks as a list of handles."})
        try:
            picks = setup.normalize_handles(raw)
        except setup.SetupError as exc:
            return json_response(400, {"error": str(exc)})
        if not picks:
            return json_response(400, {"error": "Tick at least one creator, or press Close."})
        if self._set_up():
            current = list(self.cfg.get("competitors") or [])
            try:
                saved = setup.run_accounts(self.project, current + [handle for handle in picks if handle not in current])
            except setup.SetupError as exc:
                return json_response(400, {"error": str(exc)})
            answer: Dict[str, Any] = {"saved": True, "picks": picks, "competitors": saved["competitors"]}
        else:
            path = store.contentos_dir(self.project) / PICKS_FILE_NAME
            store.write_json_atomic(path, {"picks": picks, "created_at": datetime.now(timezone.utc).isoformat()})
            answer = {"saved": True, "picks": picks, "picks_path": str(path)}
        self.finished = dict(answer, settings=self.last_settings)
        return json_response(200, answer)

    def _close(self, _payload: Dict[str, Any]) -> Response:
        if self._is_running():
            return json_response(409, {
                "error": "A search is running. Wait for it to finish, then save or close.",
            })
        self.finished = {"saved": False, "picks": [], "settings": self.last_settings}
        return json_response(200, {"saved": False})
