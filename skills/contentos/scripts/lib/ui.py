"""The discovery control panel: one local page to set the dials, run, and pick.

Design spec, "0.6.0 changes", Control panel. `App.handle` is pure: the
tests call it directly and never open a socket, and `serve` puts it behind
the standard library's ThreadingHTTPServer on 127.0.0.1. The page can spend
the creator's Apify credit, so every API call needs the session token and a
local Host header, and every run needs a click after the estimate shows.
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
FINISHED_ERROR = "This panel is finished. Go back to Claude."


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
        """Answer one request. Guards first: Host, then route, then token, then JSON.

        Only a request that passes the Host check (and, for the API, the
        token check) counts as the creator still being here, so nothing
        refused can keep the idle timer alive.
        """
        lowered = {str(key).lower(): str(value) for key, value in headers.items()}
        if lowered.get("host") not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return json_response(403, {"error": "This panel only answers on this computer."})
        route = path.split("?", 1)[0]
        if not route.startswith("/api/"):
            self.last_seen = self.clock()
            if method == "GET" and route == "/":
                return Response(200, "text/html; charset=utf-8", PAGE_PATH.read_bytes())
            return json_response(404, {"error": "Not found."})
        if route not in ROUTES:
            return json_response(404, {"error": "Not found."})
        sent = lowered.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(sent.encode("utf-8"), self.token.encode("utf-8")):
            return json_response(403, {"error": "Open the panel from the link Claude gave you."})
        self.last_seen = self.clock()
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
        """True once `setup` has run: both `config.json` and `creator.md` exist.

        The chat flow can write `config.json` before setup, so it alone
        does not count.
        """
        folder = store.contentos_dir(self.project)
        return (folder / "config.json").exists() and (folder / "creator.md").exists()

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

    def _refusal(self) -> Optional[Response]:
        """The 409 for Save or Close when the panel is finished or a search is running.

        Callers hold `self._lock`, so the check and the change that follows
        it are one step.
        """
        if self.finished is not None:
            return json_response(409, {"error": FINISHED_ERROR})
        if self.state == "running":
            return json_response(409, {"error": "A search is running. Wait for it to finish, then save or close."})
        return None

    def stop_if_idle(self, idle_s: float) -> None:
        """Finish the session when no accepted request came for `idle_s` and no search is running."""
        with self._lock:
            if self.finished is None and self.state != "running" and self.clock() - self.last_seen > idle_s:
                self.finished = {"saved": False, "picks": [], "settings": self.last_settings, "reason": "idle"}

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
            if self.finished is not None:
                return json_response(409, {"error": FINISHED_ERROR})
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
        with self._lock:
            refused = self._refusal()
            if refused is not None:
                return refused
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
                try:
                    # The watch list as it is now, not as it was when the panel opened.
                    current = list(store.load_discovery_config(self.project).get("competitors") or [])
                    added = [handle for handle in picks if handle not in current]
                    saved = setup.run_accounts(self.project, current + added)
                except (store.ConfigError, setup.SetupError) as exc:
                    return json_response(400, {"error": str(exc)})
                answer: Dict[str, Any] = {"saved": True, "picks": picks, "competitors": saved["competitors"]}
            else:
                path = store.contentos_dir(self.project) / PICKS_FILE_NAME
                store.write_json_atomic(path, {"picks": picks, "created_at": datetime.now(timezone.utc).isoformat()})
                answer = {"saved": True, "picks": picks, "picks_path": str(path)}
            self.finished = dict(answer, settings=self.last_settings)
            return json_response(200, answer)

    def _close(self, _payload: Dict[str, Any]) -> Response:
        with self._lock:
            refused = self._refusal()
            if refused is not None:
                return refused
            self.finished = {"saved": False, "picks": [], "settings": self.last_settings}
            return json_response(200, {"saved": False})


SESSION_FILE_NAME = "ui-session.json"
# The session file holds the token, so only the creator may read it.
SESSION_FILE_MODE = 0o600
MAX_BODY_BYTES = 1_000_000
# SIGTERM and SIGHUP end `serve` through its cleanup, where the platform has them.
STOP_SIGNALS = ("SIGTERM", "SIGHUP")
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def session_path(project: Path) -> Path:
    """`<project>/.contentos/ui-session.json`: where the skill finds the panel's link."""
    return store.contentos_dir(project) / SESSION_FILE_NAME


class PanelError(Exception):
    """The panel could not start, such as a port that will not open (exit 2)."""


class PanelServer(ThreadingHTTPServer):
    """The panel's server. `server_close()` waits for request threads to finish.

    ThreadingHTTPServer's request threads are daemons that `server_close()`
    never joins, so the process could end before the Save or Close answer
    reached the page. `_Handler.timeout` keeps a stalled request from
    holding the close up for long.
    """

    daemon_threads = False


def _stop(signum: int, _frame: Any) -> None:
    """A stop signal ends `serve` through its `finally`, so the session file goes."""
    raise SystemExit(128 + signum)


def _catch_stop_signals() -> Dict[int, Any]:
    """Route SIGTERM and SIGHUP to `_stop`; returns the handlers they had.

    Python only lets the main thread set signal handlers, so anywhere else
    this changes nothing.
    """
    previous: Dict[int, Any] = {}
    if threading.current_thread() is not threading.main_thread():
        return previous
    for name in STOP_SIGNALS:
        signum = getattr(signal, name, None)
        if signum is not None:
            previous[signum] = signal.signal(signum, _stop)
    return previous


def _restore_signals(previous: Dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


class _Handler(BaseHTTPRequestHandler):
    """Hands each request to the server's `App` and writes its answer back."""

    server_version = "ContentOS"
    sys_version = ""
    # Seconds a connection may stall; bounds how long closing the server waits.
    timeout = 10

    def _dispatch(self, method: str) -> None:
        app: App = self.server.app  # type: ignore[attr-defined]
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            response = json_response(413, {"error": "That request is too large."})
        else:
            body = self.rfile.read(length) if length else b""
            response = app.handle(method, self.path, dict(self.headers.items()), body)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if response.content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        self.wfile.write(response.body)

    def do_GET(self) -> None:  # noqa: N802 - the base class names it
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - the base class names it
        self._dispatch("POST")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class names it
        return


def serve(
    project: Path,
    cfg: Dict[str, Any],
    keywords: List[str],
    hashtags: List[str],
    web_entries: List[Dict[str, str]],
    seeds: List[str],
    mock: bool = False,
    port: int = 0,
    open_browser: bool = False,
    idle_minutes: float = 60.0,
    server_factory: Callable[..., Any] = PanelServer,
    clock: Callable[[], float] = time.monotonic,
    opener: Callable[[str], Any] = webbrowser.open,
) -> Dict[str, Any]:
    """Serve the panel until the creator saves or closes it, or it sits idle.

    Binds 127.0.0.1 only (port 0 lets the OS pick), prints `UI <url>`
    first, and writes `.contentos/ui-session.json` (readable only by the
    creator, since the link holds the token) so the skill can find the
    link. Raises PanelError when the port will not open. SIGTERM and
    SIGHUP end it with SystemExit(128 + the signal). The session file is
    removed on the way out, whatever happens. Returns the RESULT dict.
    """
    project = Path(project)
    token = secrets.token_urlsafe(32)
    try:
        server = server_factory(("127.0.0.1", port), _Handler)
    except (OSError, OverflowError) as exc:
        raise PanelError(f"Could not open the panel on port {port}: {exc}") from exc
    actual_port = server.server_address[1]
    app = App(project, cfg, token, actual_port, keywords, hashtags, web_entries, seeds, mock=mock, clock=clock)
    server.app = app
    server.timeout = 1.0
    url = f"http://127.0.0.1:{actual_port}/#t={token}"
    path = session_path(project)
    previous: Dict[int, Any] = {}
    try:
        previous = _catch_stop_signals()
        store.ensure_gitignore(project)
        store.write_json_atomic(
            path, {"url": url, "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()},
            mode=SESSION_FILE_MODE,
        )
        print(f"UI {url}", flush=True)
        if open_browser:
            opener(url)
        while app.finished is None:
            server.handle_request()
            app.stop_if_idle(idle_minutes * 60)
    finally:
        try:
            server.server_close()
        finally:
            _restore_signals(previous)
            path.unlink(missing_ok=True)
    return dict(app.finished, discovery_path=str(discover.discovery_path(project)))
