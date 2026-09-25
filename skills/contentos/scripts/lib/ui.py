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
HANDOFF_FILE_NAME = "ui-handoff.json"
HANDOFF_VERSION = 1
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
    "/api/search-again",
    "/api/close",
)
LOG_LINES_KEPT = 200
FINISHED_ERROR = "This panel is finished. Go back to Claude."
STALE_ERROR = "This page is from before Claude's last search, so it reloads now."
# The panel's words for a check-only scan with nothing to check (0.6.1).
CHECK_ONLY_NEEDS_CARDS = (
    "To check only Claude's finds, add at least one creator first, or tick Also search Instagram for more creators."
)


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


# 0.6.1: where a card came from, and how many the panel holds.
CARD_ORIGINS = ("claude", "you", "instagram", "similar")
MAX_CARDS = 120


def cards_from_finds(entries: List[Dict[str, Any]], new: bool = False) -> List[Dict[str, Any]]:
    """Claude's web finds as unticked cards (design spec, "0.6.1 changes")."""
    return [dict(entry, origin="claude", kept=False, removed=False, new=new) for entry in entries]


def normalize_cards(raw: Any) -> List[Dict[str, Any]]:
    """Clean the cards the page sends: the find fields plus origin, kept, removed, and new.

    A card whose handle is not an Instagram handle, or repeats an earlier
    card, is left out. An unknown origin reads as "claude". Anything the
    page adds beyond these fields (such as its scan status) is dropped,
    and at most MAX_CARDS are kept. A value that is not a list gives [].
    """
    if not isinstance(raw, list):
        return []
    cards: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        finds, _warnings = discover.normalize_web_entries([item])
        if not finds or finds[0]["handle"] in seen:
            continue
        origin = item.get("origin")
        cards.append(dict(
            finds[0],
            origin=origin if origin in CARD_ORIGINS else "claude",
            kept=item.get("kept") is True,
            removed=item.get("removed") is True,
            new=item.get("new") is True,
        ))
        seen.add(finds[0]["handle"])
        if len(cards) == MAX_CARDS:
            break
    return cards


def _search_instagram(payload: Dict[str, Any], default: bool) -> bool:
    """The page's "Also search Instagram" checkbox; only an explicit false turns it off."""
    value = payload.get("search_instagram", default)
    return value is not False


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
        cards: Optional[List[Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        search_instagram: bool = True,
        done: bool = False,
        notes: Optional[List[str]] = None,
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
        self.cards = normalize_cards(cards) if cards is not None else cards_from_finds(web_entries)
        self.search_instagram = search_instagram
        self.notes = list(notes or [])
        self.state = "idle"
        self.log: List[str] = []
        self.error: Optional[str] = None
        self.summary: Optional[Dict[str, Any]] = None
        self.last_settings: Dict[str, Any] = {key: (settings or cfg)[key] for key in DIAL_KEYS}
        # The dials as the page last sent them; an idle handoff keeps these (0.6.1).
        self.page_settings: Dict[str, Any] = dict(self.last_settings)
        self.finished: Optional[Dict[str, Any]] = None
        # True once a scan finished in this session, even if a later one failed (the handoff's has_results).
        self.had_results = False
        # Each panel process has its own generation. A page loaded from an
        # earlier one (a second tab left open across Search again) sends
        # the old value and is told to reload, so it cannot overwrite the
        # resumed panel's cards (0.6.1).
        self.generation = secrets.token_hex(8)
        self.last_seen = clock()
        self._lock = threading.Lock()
        path = discover.discovery_path(self.project)
        if done and path.exists():
            # A resumed panel whose scan already ran shows its results again (0.6.1).
            self.state = "done"
            self.summary = discover.result_line(store.read_json(path), self.project)
            self.had_results = True

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
            ("POST", "/api/search-again"): self._search_again,
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

    def _inputs(
        self, payload: Dict[str, Any]
    ) -> Tuple[List[str], List[str], List[Dict[str, Any]], bool]:
        keywords = discover.normalize_keywords(_strings(payload.get("keywords", self.keywords)))
        hashtags = discover.normalize_hashtags(_strings(payload.get("hashtags", self.hashtags)))
        default_web = [card for card in self.cards if not card["removed"]]
        try:
            web, _warnings = discover.normalize_web_entries(payload.get("web", default_web))
        except discover.DiscoverError:
            web = []
        return keywords, hashtags, web, _search_instagram(payload, self.search_instagram)

    def _cost(
        self, cfg: Dict[str, Any], keywords: List[str], hashtags: List[str], web: List[Dict[str, str]],
        search_instagram: bool,
    ) -> Dict[str, Any]:
        seeds, format_accounts, _warnings = discover.never_recommended(cfg, self.seeds)
        checked_web = discover.web_handles(web, seeds + format_accounts)
        cost = discover.estimate(cfg, len(keywords), len(hashtags), len(seeds), len(checked_web), search_instagram)
        cap = cfg["apify_max_charge_usd"]
        # The page shows `note` in place of the cost when a check-only scan has nothing to check.
        note = CHECK_ONLY_NEEDS_CARDS if not search_instagram and not checked_web else None
        return dict(cost, cap_usd=cap, within_cap=cost["total_usd"] <= cap, note=note)

    def _log(self, message: str) -> None:
        with self._lock:
            self.log.append(message)
            del self.log[:-LOG_LINES_KEPT]

    def _fail(self, message: str) -> None:
        with self._lock:
            self.state, self.error = "error", message

    def _refusal(self) -> Optional[Response]:
        """The 409 for Save, Close, or Search again when the panel is finished or a search is running.

        Callers hold `self._lock`, so the check and the change that follows
        it are one step.
        """
        if self.finished is not None:
            return json_response(409, {"error": FINISHED_ERROR})
        if self.state == "running":
            return json_response(409, {"error": "A search is running. Wait for it to finish first."})
        return None

    def _stale(self, payload: Dict[str, Any]) -> Optional[Response]:
        """The 409 for a page loaded from an earlier panel; a payload with no generation is not checked."""
        sent = payload.get("generation")
        if sent is not None and sent != self.generation:
            return json_response(409, {"error": STALE_ERROR, "stale": True})
        return None

    def _known(self, cards: List[Dict[str, Any]]) -> List[str]:
        """Every handle the next Claude search must skip: the cards (removed too), seeds, and format accounts."""
        seeds, format_accounts, _warnings = discover.never_recommended(self.cfg, self.seeds)
        known: List[str] = []
        for handle in [card["handle"] for card in cards] + seeds + format_accounts:
            if handle not in known:
                known.append(handle)
        return known

    def _write_handoff(self, settings: Dict[str, Any]) -> Path:
        """Write `ui-handoff.json` from the panel's current state. Callers hold `self._lock`."""
        path = handoff_path(self.project)
        store.write_json_atomic(path, {
            "version": HANDOFF_VERSION,
            "token": self.token,
            "port": self.port,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "keywords": self.keywords,
            "hashtags": self.hashtags,
            "seeds": self.seeds,
            "cards": self.cards,
            "settings": settings,
            "search_instagram": self.search_instagram,
            "has_results": self.had_results,
        }, mode=SESSION_FILE_MODE)
        return path

    def stop_if_idle(self, idle_s: float) -> None:
        """Finish the session when no accepted request came for `idle_s` and no search is running."""
        with self._lock:
            if self.finished is None and self.state != "running" and self.clock() - self.last_seen > idle_s:
                finished: Dict[str, Any] = {"saved": False, "picks": [], "settings": self.last_settings,
                                            "reason": "idle"}
                try:
                    finished["handoff_path"] = str(self._write_handoff(self.page_settings))
                except OSError as exc:
                    # The panel still ends with its RESULT; it just cannot be reopened.
                    finished["warning"] = f"The panel could not be saved to reopen later: {exc}"
                self.finished = finished

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
            "cards": self.cards,
            "search_instagram": self.search_instagram,
            "notes": self.notes,
            "finished": self.finished is not None,
            "watch_list": list(self.cfg.get("competitors") or []),
            "state": self.state,
            "generation": self.generation,
        })

    def _estimate(self, payload: Dict[str, Any]) -> Response:
        stale = self._stale(payload)
        if stale is not None:
            return stale
        try:
            cfg = self._config_with(payload)
        except store.ConfigError as exc:
            return json_response(400, {"error": str(exc), "code": codes.EXIT_USAGE})
        keywords, hashtags, web, search_instagram = self._inputs(payload)
        with self._lock:
            # The page calls this on every change, so an idle timeout's
            # handoff holds what the creator last saw (0.6.1). A finished
            # panel keeps what it handed off.
            if self.finished is not None:
                return json_response(200, self._cost(cfg, keywords, hashtags, web, search_instagram))
            if "keywords" in payload:
                self.keywords = keywords
            if "hashtags" in payload:
                self.hashtags = hashtags
            if "cards" in payload:
                self.cards = normalize_cards(payload["cards"])
            self.search_instagram = search_instagram
            self.page_settings = {key: cfg[key] for key in DIAL_KEYS}
        return json_response(200, self._cost(cfg, keywords, hashtags, web, search_instagram))

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
            keywords, hashtags, web, search_instagram = self._inputs(payload)
            seeds, format_accounts, _warnings = discover.never_recommended(cfg, self.seeds)
            checked_web = discover.web_handles(web, seeds + format_accounts)
            if not search_instagram and not checked_web:
                return json_response(400, {"error": CHECK_ONLY_NEEDS_CARDS, "code": codes.EXIT_USAGE})
            if not (keywords or hashtags or checked_web or seeds):
                return json_response(400, {
                    "error": "Add a keyword phrase, a hashtag, or a handle first.", "code": codes.EXIT_USAGE,
                })
            cost = self._cost(cfg, keywords, hashtags, web, search_instagram)
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
            self.page_settings = dict(self.last_settings)
            self.search_instagram = search_instagram
            if payload.get("remember") is True and self._set_up():
                try:
                    store.update_config_keys(self.project, self.last_settings)
                except store.ConfigError as exc:
                    return json_response(400, {"error": str(exc), "code": codes.EXIT_USAGE})
            self.state, self.log, self.error, self.summary = "running", [], None, None
        self.runner(lambda: self._work(cfg, keys, keywords, hashtags, web, search_instagram))
        return json_response(202, {"state": "running"})

    def _work(
        self, cfg: Dict[str, Any], keys: env.Keys, keywords: List[str], hashtags: List[str],
        web: List[Dict[str, str]], search_instagram: bool,
    ) -> None:
        try:
            doc = self.run_discover(
                self.project, cfg, keys, hashtags=hashtags, keywords=keywords, seeds=self.seeds,
                web_entries=web, mock=self.mock, yes=True, log=self._log, search_instagram=search_instagram,
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
            self.had_results = True

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
            handoff_path(self.project).unlink(missing_ok=True)
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

    def _search_again(self, payload: Dict[str, Any]) -> Response:
        """Hand the turn back to Claude for another web search (design spec, "0.6.1 changes")."""
        with self._lock:
            refused = self._refusal() or self._stale(payload)
            if refused is not None:
                return refused
            try:
                cfg = self._config_with(payload)
            except store.ConfigError as exc:
                return json_response(400, {"error": str(exc), "code": codes.EXIT_USAGE})
            keywords, hashtags, _web, search_instagram = self._inputs(payload)
            self.keywords, self.hashtags, self.search_instagram = keywords, hashtags, search_instagram
            if "cards" in payload:
                self.cards = normalize_cards(payload["cards"])
            self.last_settings = {key: cfg[key] for key in DIAL_KEYS}
            self.page_settings = dict(self.last_settings)
            try:
                path = self._write_handoff(self.last_settings)
            except OSError:
                return json_response(500, {"error": "Could not save the panel for Claude. Try again."})
            self.finished = {
                "saved": False, "picks": [], "settings": self.last_settings, "next": "claude_search",
                "keywords": keywords, "hashtags": hashtags, "known": self._known(self.cards),
                "handoff_path": str(path),
            }
            return json_response(200, {"next": "claude_search"})

    def _close(self, _payload: Dict[str, Any]) -> Response:
        with self._lock:
            refused = self._refusal()
            if refused is not None:
                return refused
            handoff_path(self.project).unlink(missing_ok=True)
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


def handoff_path(project: Path) -> Path:
    """`<project>/.contentos/ui-handoff.json`: the panel's state while Claude searches again (0.6.1)."""
    return store.contentos_dir(project) / HANDOFF_FILE_NAME


class HandoffError(Exception):
    """There is no panel to reopen, or its handoff cannot be read (exit 2)."""


def load_handoff(project: Path) -> Dict[str, Any]:
    """Read `ui-handoff.json` for `ui --resume` (design spec, "0.6.1 changes")."""
    path = handoff_path(project)
    if not path.exists():
        raise HandoffError("Nothing to resume: there is no panel to reopen. Start a new one without --resume.")
    try:
        doc = store.read_json(path)
    except (OSError, ValueError) as exc:
        raise HandoffError(f"Could not read {path}: {exc}") from exc
    if (not isinstance(doc, dict) or doc.get("version") != HANDOFF_VERSION
            or not isinstance(doc.get("token"), str) or not doc["token"]):
        raise HandoffError(f"{path} is not a panel this version can reopen. Start a new one without --resume.")
    return doc


def merge_finds(
    cards: List[Dict[str, Any]], entries: List[Dict[str, Any]], known: List[str]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Add a new Claude search's finds to the cards: earlier cards lose `new`, new ones get it.

    A find already on a card (removed ones too) or in `known` is skipped.
    The panel holds MAX_CARDS; finds past that are left out with a warning.
    """
    merged = [dict(card, new=False) for card in cards]
    have = {card["handle"] for card in merged} | set(known)
    left_out = 0
    for entry in entries:
        if entry["handle"] in have:
            continue
        if len(merged) >= MAX_CARDS:
            left_out += 1
            continue
        merged.extend(cards_from_finds([entry], new=True))
        have.add(entry["handle"])
    warnings = [f"The panel holds {MAX_CARDS} creators, so {left_out} new finds were left out."] if left_out else []
    return merged, warnings


def resume_session(
    project: Path, cfg: Dict[str, Any], handoff: Dict[str, Any], new_entries: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Everything `serve` needs to reopen the handed-off panel, with the new finds added.

    Settings that no longer validate fall back to the config's, and a port
    that is not a real port becomes 0 (the OS picks one).
    """
    seeds = _strings(handoff.get("seeds"))
    watch, format_accounts, _warnings = discover.never_recommended(cfg, seeds)
    cards, notes = merge_finds(normalize_cards(handoff.get("cards")), new_entries, watch + format_accounts)
    raw_settings = handoff.get("settings") if isinstance(handoff.get("settings"), dict) else {}
    settings = {key: raw_settings.get(key, cfg[key]) for key in DIAL_KEYS}
    try:
        store.check_discovery_config(dict(cfg, **settings))
    except store.ConfigError:
        settings = {key: cfg[key] for key in DIAL_KEYS}
    port = handoff.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        port = 0
    return {
        "token": handoff["token"],
        "port": port,
        "keywords": discover.normalize_keywords(_strings(handoff.get("keywords"))),
        "hashtags": discover.normalize_hashtags(_strings(handoff.get("hashtags"))),
        "seeds": seeds,
        "cards": cards,
        "settings": settings,
        "search_instagram": handoff.get("search_instagram") is not False,
        "done": handoff.get("has_results") is True and discover.discovery_path(project).exists(),
        "notes": notes,
    }


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
    resume: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Serve the panel until the creator saves or closes it, or it sits idle.

    Binds 127.0.0.1 only (port 0 lets the OS pick), prints `UI <url>`
    first, and writes `.contentos/ui-session.json` (readable only by the
    creator, since the link holds the token) so the skill can find the
    link. Raises PanelError when the port will not open. SIGTERM and
    SIGHUP end it with SystemExit(128 + the signal). The session file is
    removed on the way out, whatever happens. Returns the RESULT dict.
    `resume` (from `resume_session`) reopens a handed-off panel: same
    token, the same port when it opens (else a new one), its cards and
    settings, and the handoff file is removed once the panel is up.
    """
    project = Path(project)
    resume = resume or {}
    token = resume.get("token") or secrets.token_urlsafe(32)
    try:
        server = server_factory(("127.0.0.1", port), _Handler)
    except (OSError, OverflowError) as exc:
        if not resume or port == 0:
            raise PanelError(f"Could not open the panel on port {port}: {exc}") from exc
        # The page's old port is taken: open a new one, and the skill hands over the new link.
        try:
            server = server_factory(("127.0.0.1", 0), _Handler)
        except (OSError, OverflowError) as retry:
            raise PanelError(f"Could not open the panel on port 0: {retry}") from retry
    actual_port = server.server_address[1]
    app = App(
        project, cfg, token, actual_port, keywords, hashtags, web_entries, seeds, mock=mock, clock=clock,
        cards=resume.get("cards"), settings=resume.get("settings"),
        search_instagram=resume.get("search_instagram", True), done=resume.get("done", False),
        notes=resume.get("notes"),
    )
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
        if resume:
            handoff_path(project).unlink(missing_ok=True)
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
