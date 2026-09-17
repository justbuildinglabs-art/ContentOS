"""Tests for lib/video.py: Stage 1 -- research, step 7's downloads with backfill.

Task 11 adds `video_path`/`cover_path`, `download_selected` (per-reel
video + cover download, promoting from `backfill` on failure, rewriting
`02-outliers.json`), and `refresh_video_url` (one Apify re-scrape for an
expired CDN URL), then wires `download_selected` into
`lib/research.py`'s `run_research`. See the design spec's "Stage 1 --
research" step 7 and
`.superpowers/sdd/trying-to-make-a-clever-ritchie/task-11-brief.md` for
the exact interface.

Nothing here touches the network: every `download_selected` test
injects a fake `downloader` (`_ScriptedDownloader`, or for
`test_skips_existing_file`, `_existing_file_only_downloader`, a
stand-in that mimics `http.download_to_file`'s own "already exists"
shortcut without ever touching urllib), and the `refresh_video_url`
tests use a scripted Apify transport mirroring test_research.py's
`_ScriptedTransport` (kept local here so this module does not reach
into another test module's internals). NoNetworkTestCase is also a
second line of defense.
"""
from __future__ import annotations

import copy
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests.helpers import NoNetworkTestCase, REPO_ROOT, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import http, research, store, video  # noqa: E402
from lib.env import Keys  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"


def _reel(shortcode: str, duration_s: Optional[float] = 15.0, **extra: Any) -> Dict[str, Any]:
    """A minimal 02-outliers.json reel entry, as research.py's `_pending` writes it."""
    reel: Dict[str, Any] = {
        "shortCode": shortcode,
        "duration_s": duration_s,
        "videoUrl": f"https://example.invalid/videos/{shortcode}.mp4",
        "displayUrl": f"https://example.invalid/covers/{shortcode}.jpg",
        "video_status": "pending",
        "frames_status": "pending",
    }
    reel.update(extra)
    return reel


def _outliers_doc(
    selected: List[Dict[str, Any]], backfill: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """A minimal 02-outliers.json document: only what download_selected reads/writes."""
    return {
        "selected": selected,
        "backfill": backfill if backfill is not None else [],
        "excluded": [],
        "account_status": {},
        "baselines": {},
    }


def _cfg(**overrides: Any) -> Dict[str, Any]:
    """A valid research config dict, defaulted from store.DEFAULT_CONFIG."""
    cfg = copy.deepcopy(store.DEFAULT_CONFIG)
    cfg["competitors"] = ["acct1"]
    cfg.update(overrides)
    return cfg


class _ScriptedDownloader:
    """Returns a scripted `http.DownloadResult` per URL; never touches the network.

    `results` maps url -> status. A status of "ok" also writes a few
    sentinel bytes to `dest`, mirroring what a real download would
    leave behind; any other status writes nothing, matching
    `http.download_to_file`, which never leaves partial output on
    failure.
    """

    def __init__(self, results: Dict[str, str]) -> None:
        self._results = results
        self.calls: List[str] = []

    def __call__(self, url: str, dest: Path, max_bytes: int, **_kwargs: Any) -> http.DownloadResult:
        self.calls.append(url)
        status = self._results[url]
        if status == "ok":
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake-bytes")
            return http.DownloadResult("ok", len(b"fake-bytes"), 200)
        return http.DownloadResult(status, 0, 403 if status == "blocked" else 404)


def _existing_file_only_downloader(url: str, dest: Path, max_bytes: int, **_kwargs: Any) -> http.DownloadResult:
    """A downloader that only "succeeds" when `dest` already has content.

    Proves `download_selected` calls `downloader` unconditionally and
    trusts it to shortcut an existing file -- exactly
    `http.download_to_file`'s own "never re-download an existing
    non-empty file" contract. If `download_selected` ever tried to
    re-fetch an already-downloaded file, this raises instead of
    silently touching a network it does not have.
    """
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return http.DownloadResult("ok", dest.stat().st_size, None)
    raise AssertionError(f"unexpected download attempt for {url}")


class DownloadSelectedTests(NoNetworkTestCase):
    def _run_dir(self, project_dir: Path) -> Path:
        return store.init_run(project_dir, _cfg(), "mock")

    def test_skips_existing_file(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel = _reel("AAA001")
            video_dest = video.video_path(run_dir, "AAA001")
            cover_dest = video.cover_path(run_dir, "AAA001")
            video_dest.parent.mkdir(parents=True, exist_ok=True)
            video_dest.write_bytes(b"already-here")
            cover_dest.parent.mkdir(parents=True, exist_ok=True)
            cover_dest.write_bytes(b"already-here-too")

            outliers_doc = _outliers_doc([reel])
            report = video.download_selected(
                run_dir,
                outliers_doc,
                _cfg(),
                False,
                downloader=_existing_file_only_downloader,
                log=lambda _m: None,
            )

            video_bytes = video_dest.read_bytes()
            cover_bytes = cover_dest.read_bytes()

        self.assertEqual(report.ok, ["AAA001"])
        self.assertEqual(report.skipped, [])
        self.assertEqual(report.replaced_from_backfill, [])
        self.assertEqual(reel["video_status"], "ok")
        self.assertEqual(reel["cover_status"], "ok")
        # Untouched: still exactly the sentinel bytes written before the call.
        self.assertEqual(video_bytes, b"already-here")
        self.assertEqual(cover_bytes, b"already-here-too")

    def test_too_large_by_duration_skips_download(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            cfg = _cfg(max_video_seconds=60)
            reel = _reel("BBB001", duration_s=999.0)

            def _boom(url: str, dest: Path, max_bytes: int, **_kwargs: Any) -> http.DownloadResult:
                raise AssertionError(f"downloader must not be called for {url}")

            outliers_doc = _outliers_doc([reel])
            report = video.download_selected(
                run_dir, outliers_doc, cfg, False, downloader=_boom, log=lambda _m: None
            )

            video_exists = video.video_path(run_dir, "BBB001").exists()
            cover_exists = video.cover_path(run_dir, "BBB001").exists()

        self.assertEqual(report.ok, [])
        self.assertEqual(report.skipped, [{"shortCode": "BBB001", "status": "too_large"}])
        self.assertEqual(reel["video_status"], "too_large")
        self.assertNotIn("cover_status", reel)
        self.assertFalse(video_exists)
        self.assertFalse(cover_exists)

    def test_failed_download_promotes_backfill_and_records_replacement(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel_a = _reel("AAA001")
            reel_c = _reel("CCC001")
            downloader = _ScriptedDownloader(
                {
                    reel_a["videoUrl"]: "failed",
                    reel_a["displayUrl"]: "ok",
                    reel_c["videoUrl"]: "ok",
                    reel_c["displayUrl"]: "ok",
                }
            )
            outliers_doc = _outliers_doc([reel_a], backfill=[reel_c])

            report = video.download_selected(
                run_dir, outliers_doc, _cfg(), False, downloader=downloader, log=lambda _m: None
            )

        self.assertEqual(report.ok, ["CCC001"])
        self.assertEqual(report.skipped, [{"shortCode": "AAA001", "status": "failed"}])
        self.assertEqual(report.replaced_from_backfill, [{"failed": "AAA001", "promoted": "CCC001"}])
        self.assertEqual(outliers_doc["backfill"], [])
        self.assertEqual([r["shortCode"] for r in outliers_doc["selected"]], ["AAA001", "CCC001"])
        self.assertEqual(reel_a["video_status"], "failed")
        self.assertEqual(reel_c["video_status"], "ok")
        # original_count is 1 here, and that one original reel did fail
        # (even though its backfill replacement then succeeded) -- 1 of 1
        # is "more than half", so the warning still fires.
        self.assertEqual(len(report.warnings), 1)

    def test_stops_when_pool_exhausted_and_warns_over_half_failed(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel_a = _reel("AAA001")
            reel_b = _reel("BBB001")
            reel_c = _reel("CCC001")
            downloader = _ScriptedDownloader(
                {
                    reel_a["videoUrl"]: "failed",
                    reel_a["displayUrl"]: "failed",
                    reel_b["videoUrl"]: "failed",
                    reel_b["displayUrl"]: "failed",
                    reel_c["videoUrl"]: "failed",
                    reel_c["displayUrl"]: "failed",
                }
            )
            outliers_doc = _outliers_doc([reel_a, reel_b], backfill=[reel_c])

            report = video.download_selected(
                run_dir, outliers_doc, _cfg(), False, downloader=downloader, log=lambda _m: None
            )

        self.assertEqual(report.ok, [])
        self.assertEqual([s["shortCode"] for s in report.skipped], ["AAA001", "BBB001", "CCC001"])
        # Only one promotion happens: the backfill pool had exactly one reel,
        # so the second and third failures (BBB001, the promoted CCC001) find
        # nothing left to promote -- this is what "stops" means here.
        self.assertEqual(report.replaced_from_backfill, [{"failed": "AAA001", "promoted": "CCC001"}])
        self.assertEqual(outliers_doc["backfill"], [])
        self.assertEqual(len(outliers_doc["selected"]), 3)
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("downloadedVideo", report.warnings[0])
        self.assertIn("apify/instagram-reel-scraper", report.warnings[0])

    def test_mock_copies_fixture_video_and_cover(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel = _reel("MOCK001")
            outliers_doc = _outliers_doc([reel])

            def _boom(url: str, dest: Path, max_bytes: int, **_kwargs: Any) -> http.DownloadResult:
                raise AssertionError("mock mode must never call the real downloader")

            report = video.download_selected(
                run_dir,
                outliers_doc,
                _cfg(),
                True,
                downloader=_boom,
                fixtures_dir=FIXTURES_DIR,
                log=lambda _m: None,
            )

            video_bytes = video.video_path(run_dir, "MOCK001").read_bytes()
            cover_bytes = video.cover_path(run_dir, "MOCK001").read_bytes()

        self.assertEqual(report.ok, ["MOCK001"])
        self.assertEqual(reel["video_status"], "ok")
        self.assertEqual(reel["cover_status"], "ok")
        self.assertEqual(video_bytes, (FIXTURES_DIR / "sample.mp4").read_bytes())
        self.assertEqual(cover_bytes, (FIXTURES_DIR / "sample_cover.jpg").read_bytes())

    def test_statuses_written_into_outliers_json(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel_a = _reel("AAA001")
            reel_c = _reel("CCC001")
            store.write_json_atomic(
                run_dir / "02-outliers.json", _outliers_doc([reel_a], backfill=[reel_c])
            )
            outliers_doc = store.read_json(run_dir / "02-outliers.json")

            downloader = _ScriptedDownloader(
                {
                    outliers_doc["selected"][0]["videoUrl"]: "failed",
                    outliers_doc["selected"][0]["displayUrl"]: "failed",
                    reel_c["videoUrl"]: "ok",
                    reel_c["displayUrl"]: "ok",
                }
            )

            video.download_selected(
                run_dir, outliers_doc, _cfg(), False, downloader=downloader, log=lambda _m: None
            )

            on_disk = store.read_json(run_dir / "02-outliers.json")

        self.assertEqual(on_disk["selected"][0]["shortCode"], "AAA001")
        self.assertEqual(on_disk["selected"][0]["video_status"], "failed")
        self.assertEqual(on_disk["selected"][0]["cover_status"], "failed")
        self.assertEqual(on_disk["selected"][1]["shortCode"], "CCC001")
        self.assertEqual(on_disk["selected"][1]["video_status"], "ok")
        self.assertEqual(on_disk["selected"][1]["cover_status"], "ok")
        self.assertEqual(on_disk["backfill"], [])
        # Untouched by download_selected.
        self.assertEqual(on_disk["excluded"], [])
        self.assertEqual(on_disk["account_status"], {})


class _ScriptedApifyTransport:
    """Returns/raises each scripted response from `.request_json` in order.

    Mirrors test_research.py's `_ScriptedTransport`, kept local here so
    this module does not reach into another test module's internals.
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
            {"method": method, "url": url, "headers": headers, "json_body": json_body, "params": params}
        )
        if not self._responses:
            raise AssertionError("scripted transport exhausted")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class RefreshVideoUrlTests(NoNetworkTestCase):
    def test_refresh_video_url_input_shape(self) -> None:
        transport = _ScriptedApifyTransport(
            [
                {"data": {"id": "run-refresh-1", "status": "READY", "defaultDatasetId": "ds-refresh-1"}},
                {
                    "data": {
                        "id": "run-refresh-1", "status": "SUCCEEDED", "defaultDatasetId": "ds-refresh-1"
                    }
                },
                [{"shortCode": "AAA001", "videoUrl": "https://fresh.example/AAA001.mp4"}],
            ]
        )

        result = video.refresh_video_url("tok-123", "AAA001", transport, log=lambda _m: None)

        self.assertEqual(result, "https://fresh.example/AAA001.mp4")
        post_call = transport.calls[0]
        self.assertEqual(post_call["method"], "POST")
        self.assertEqual(
            post_call["json_body"],
            {
                "directUrls": ["https://www.instagram.com/reel/AAA001/"],
                "resultsType": "posts",
                "resultsLimit": 1,
            },
        )
        self.assertEqual(
            post_call["params"],
            {"maxTotalChargeUsd": 0.05, "maxItems": 1, "timeout": 120, "waitForFinish": 0},
        )
        self.assertEqual(post_call["headers"], {"Authorization": "Bearer tok-123"})

        poll_call = transport.calls[1]
        self.assertEqual(poll_call["method"], "GET")
        self.assertEqual(poll_call["url"], "https://api.apify.com/v2/actor-runs/run-refresh-1")

        items_call = transport.calls[2]
        self.assertEqual(items_call["url"], "https://api.apify.com/v2/datasets/ds-refresh-1/items")

    def test_refresh_video_url_returns_none_on_apify_failure(self) -> None:
        transport = _ScriptedApifyTransport(
            [
                {"data": {"id": "run-x", "status": "READY", "defaultDatasetId": "ds-x"}},
                {"data": {"id": "run-x", "status": "FAILED", "defaultDatasetId": "ds-x"}},
            ]
        )
        logs: List[str] = []

        result = video.refresh_video_url("tok", "ZZZ999", transport, log=logs.append)

        self.assertIsNone(result)
        self.assertTrue(any("ZZZ999" in line for line in logs))

    def test_refresh_video_url_returns_none_when_no_items(self) -> None:
        transport = _ScriptedApifyTransport(
            [
                {"data": {"id": "run-y", "status": "READY", "defaultDatasetId": "ds-y"}},
                {"data": {"id": "run-y", "status": "SUCCEEDED", "defaultDatasetId": "ds-y"}},
                [],
            ]
        )

        result = video.refresh_video_url("tok", "NOPE001", transport, log=lambda _m: None)

        self.assertIsNone(result)


def _write_config(project: Path, overrides: Dict[str, Any]) -> None:
    """Write `<project>/.contentos/config.json` with exactly `overrides`."""
    config_dir = project / ".contentos"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(overrides), encoding="utf-8")


def _mock_keys() -> Keys:
    """A Keys value with no resolved token, as --mock needs none."""
    return Keys(apify=None, source=None, warnings=[])


def _run_research_capturing(**kwargs: Any):
    """Call research.run_research in-process, capturing stdout and logs."""
    logs: List[str] = []
    kwargs.setdefault("log", logs.append)
    buffer = StringIO()
    with redirect_stdout(buffer):
        result = research.run_research(**kwargs)
    return result, buffer.getvalue(), logs


class ResearchDownloadWiringTests(NoNetworkTestCase):
    def test_research_mock_downloads_by_default_and_no_download_skips(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})
            cfg = store.load_config(project_dir)

            result, _stdout, _logs = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
            )

            run_dir = Path(result["run_dir"])
            outliers_doc = store.read_json(run_dir / "02-outliers.json")
            selected = outliers_doc["selected"]
            # The shared fixture data deliberately includes one reel
            # (HAB004) whose videoDuration is 400s, well over the default
            # max_video_seconds=180 -- so this end-to-end mock run also
            # exercises the too_large-by-duration skip, not just the
            # happy path. Only the reels that actually downloaded should
            # have files; the rest is covered by test_video.py's other,
            # more targeted download_selected tests.
            downloaded = [r for r in selected if r["video_status"] == "ok"]
            not_downloaded = [r for r in selected if r["video_status"] != "ok"]
            video_files_exist = [
                (run_dir / "videos" / f"{reel['shortCode']}.mp4").exists() for reel in downloaded
            ]
            cover_files_exist = [
                (run_dir / "frames" / reel["shortCode"] / "cover.jpg").exists() for reel in downloaded
            ]
            videos_summary = result["videos"]

        self.assertTrue(selected, "fixture must select at least one reel")
        self.assertTrue(downloaded, "fixture must have at least one downloadable reel")
        self.assertTrue(all(video_files_exist))
        self.assertTrue(all(cover_files_exist))
        self.assertEqual(
            videos_summary, {"ok": len(downloaded), "failed": len(not_downloaded), "promoted": 0}
        )

        with temp_project() as project_dir2:
            _write_config(project_dir2, {"competitors": ["sproutapp"]})
            cfg2 = store.load_config(project_dir2)

            result2, _stdout2, _logs2 = _run_research_capturing(
                project=project_dir2,
                cfg=cfg2,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
                no_download=True,
            )

            run_dir2 = Path(result2["run_dir"])
            outliers_doc2 = store.read_json(run_dir2 / "02-outliers.json")
            videos_dir_exists = (run_dir2 / "videos").exists()
            statuses2 = [reel["video_status"] for reel in outliers_doc2["selected"]]

        self.assertIsNone(result2["videos"])
        self.assertFalse(videos_dir_exists)
        self.assertTrue(statuses2, "fixture must select at least one reel")
        self.assertTrue(all(status == "pending" for status in statuses2))


if __name__ == "__main__":
    unittest.main()
