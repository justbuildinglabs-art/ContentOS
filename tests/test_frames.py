"""Tests for lib/frames.py: Stage 1 -- research, step 8's keyframes.

Task 12 adds `ffmpeg_available`, `frame_times`, `extract_frames` (one
ffmpeg subprocess call per timestamp, idempotent), and
`frames_for_selected` (per-reel status decision plus the atomic
02-outliers.json rewrite), then wires `frames_for_selected` into
`lib/research.py`'s research tail and adds the `frames` subcommand
(`contentos.py`) for re-cutting keyframes on an existing run, including
`--refresh-expired`. See the design spec's "Stage 1 -- research" step 8
and `.superpowers/sdd/trying-to-make-a-clever-ritchie/task-12-brief.md`
for the exact interface.

Nothing here calls a real ffmpeg or touches the network: `extract_frames`'s
`runner` is always a small fake that records argv and writes (or withholds)
a placeholder output file, mirroring test_video.py's `_ScriptedDownloader`;
`--refresh-expired`'s own Apify call is exercised with a small scripted
transport local to this module (mirroring test_video.py's
`_ScriptedApifyTransport`, kept local so this module does not reach into
another test module's internals) plus a fake downloader. NoNetworkTestCase
is also a second line of defense throughout.
"""
from __future__ import annotations

import copy
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

from tests.helpers import NoNetworkTestCase, REPO_ROOT, run_cli, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import frames, http, research, store, video  # noqa: E402
from lib.env import Keys  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"

NO_GLOBAL_ENV = {"CONTENTOS_CONFIG_DIR": ""}


def _cfg(**overrides: Any) -> Dict[str, Any]:
    """A valid research config dict, defaulted from store.DEFAULT_CONFIG."""
    cfg = copy.deepcopy(store.DEFAULT_CONFIG)
    cfg["competitors"] = ["acct1"]
    cfg.update(overrides)
    return cfg


def _reel(
    shortcode: str, video_status: str = "ok", duration_s: Optional[float] = 15.0, **extra: Any
) -> Dict[str, Any]:
    """A minimal 02-outliers.json reel entry, as it looks right after step 7."""
    reel: Dict[str, Any] = {
        "shortCode": shortcode,
        "duration_s": duration_s,
        "video_status": video_status,
        "frames_status": "pending",
    }
    reel.update(extra)
    return reel


def _outliers_doc(selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A minimal 02-outliers.json document: only what frames_for_selected reads/writes."""
    return {
        "selected": selected,
        "backfill": [],
        "excluded": [],
        "account_status": {},
        "baselines": {},
    }


def _write_config(project: Path, overrides: Dict[str, Any]) -> None:
    """Write `<project>/.contentos/config.json` with exactly `overrides`."""
    config_dir = project / ".contentos"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(overrides), encoding="utf-8")


def _mock_keys() -> Keys:
    """A Keys value with no resolved token, as --mock needs none."""
    return Keys(apify=None, source=None, warnings=[])


def _run_research_capturing(**kwargs: Any) -> Dict[str, Any]:
    """Call research.run_research in-process, swallowing its stdout prints.

    `run_research` prints a per-account summary and a `RESULT` line as
    a side effect (that is `contentos.py`'s CLI output, not this test's
    concern) -- without this, those prints leak straight into the test
    run's own stdout. Mirrors test_video.py's/test_research.py's own
    `_run_research_capturing` helper, duplicated here so this module
    does not reach into another test module's internals.
    """
    kwargs.setdefault("log", lambda _m: None)
    with redirect_stdout(StringIO()):
        return research.run_research(**kwargs)


class _FakeCompletedProcess:
    """Just enough of subprocess.CompletedProcess for extract_frames to read."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class _RecordingRunner:
    """Fake `runner`: records argv, optionally drops a placeholder output file.

    Mirrors test_video.py's `_ScriptedDownloader` -- never touches a real
    process. With `write=True` (the default) and `returncode=0`, the
    frame's own output path (the command's last argument) gets a few
    placeholder bytes, matching what a real successful `ffmpeg` call
    would leave behind; `write=False` mimics a call that reports success
    or failure without ever producing a file.
    """

    def __init__(self, returncode: int = 0, write: bool = True) -> None:
        self.returncode = returncode
        self.write = write
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str], **_kwargs: Any) -> _FakeCompletedProcess:
        self.calls.append(cmd)
        if self.write and self.returncode == 0:
            Path(cmd[-1]).write_bytes(b"fake-jpeg-bytes")
        return _FakeCompletedProcess(self.returncode)


class FrameTimesTests(NoNetworkTestCase):
    def test_frame_times_for_4s_30s_90s(self) -> None:
        self.assertEqual(
            frames.frame_times(4.0, 8), [0.0, 1.0, 2.0, 3.0, 3.13, 3.25, 3.38, 3.5]
        )
        self.assertEqual(
            frames.frame_times(30.0, 8), [0.0, 1.0, 2.0, 3.0, 9.63, 16.25, 22.88, 29.5]
        )
        self.assertEqual(
            frames.frame_times(90.0, 8), [0.0, 1.0, 2.0, 3.0, 24.63, 46.25, 67.88, 89.5]
        )

    def test_frame_times_short_and_unknown_duration(self) -> None:
        # 1 s or less: only 0.0 is safely before duration - 0.5.
        self.assertEqual(frames.frame_times(1.0, 8), [0.0])
        self.assertEqual(frames.frame_times(0.5, 8), [0.0])
        self.assertEqual(frames.frame_times(0.0, 8), [0.0])
        # Unknown duration: the fixed points only, never padded further.
        self.assertEqual(frames.frame_times(None, 8), [0.0, 1.0, 2.0, 3.0])

    def test_frame_times_never_exceeds_n(self) -> None:
        self.assertEqual(len(frames.frame_times(90.0, 8)), 8)
        self.assertEqual(len(frames.frame_times(None, 8)), 4)


class ExtractFramesTests(NoNetworkTestCase):
    def test_ffmpeg_command_shape_via_fake_runner(self) -> None:
        with temp_project() as tmp:
            mp4 = tmp / "reel.mp4"
            mp4.write_bytes(b"fake-mp4")
            out_dir = tmp / "frames" / "AAA001"
            runner = _RecordingRunner()

            result = frames.extract_frames(mp4, out_dir, [0.0, 3.0], 1024, runner=runner)

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.paths), 2)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(
            runner.calls[0],
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", "0.00",
                "-i", str(mp4),
                "-frames:v", "1",
                "-vf", "scale='if(gt(iw,ih),min(iw,1024),-2)':'if(gt(iw,ih),-2,min(ih,1024))'",
                "-q:v", "4",
                str(out_dir / "f01.jpg"),
            ],
        )
        second_call = runner.calls[1]
        self.assertEqual(second_call[5], "3.00")
        self.assertEqual(second_call[-1], str(out_dir / "f02.jpg"))

    def test_skips_existing_frames(self) -> None:
        with temp_project() as tmp:
            mp4 = tmp / "reel.mp4"
            mp4.write_bytes(b"fake-mp4")
            out_dir = tmp / "frames" / "AAA001"
            out_dir.mkdir(parents=True)
            existing = out_dir / "f01.jpg"
            existing.write_bytes(b"already-here")
            runner = _RecordingRunner()

            result = frames.extract_frames(mp4, out_dir, [0.0, 1.0], 1024, runner=runner)

            still_there = existing.read_bytes()

        # Only the missing frame (f02) triggers a call; f01 is left alone.
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][-1], str(out_dir / "f02.jpg"))
        self.assertEqual(still_there, b"already-here")
        self.assertEqual(result.status, "ok")
        self.assertEqual({p.name for p in result.paths}, {"f01.jpg", "f02.jpg"})

    def test_failed_when_ffmpeg_fails_every_frame(self) -> None:
        with temp_project() as tmp:
            mp4 = tmp / "reel.mp4"
            mp4.write_bytes(b"fake-mp4")
            out_dir = tmp / "frames" / "AAA001"
            runner = _RecordingRunner(returncode=1, write=False)

            result = frames.extract_frames(mp4, out_dir, [0.0, 1.0, 2.0], 1024, runner=runner)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.paths, [])
        self.assertEqual(len(runner.calls), 3)
        self.assertFalse((out_dir / "f01.jpg").exists())

    def test_missing_ffmpeg_binary_is_failed_not_a_crash(self) -> None:
        with temp_project() as tmp:
            mp4 = tmp / "reel.mp4"
            mp4.write_bytes(b"fake-mp4")
            out_dir = tmp / "frames" / "AAA001"

            def _no_such_binary(cmd: List[str], **_kwargs: Any) -> Any:
                raise FileNotFoundError("no such file: ffmpeg")

            result = frames.extract_frames(mp4, out_dir, [0.0, 1.0], 1024, runner=_no_such_binary)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.paths, [])


class FramesForSelectedTests(NoNetworkTestCase):
    def _run_dir(self, project_dir: Path) -> Path:
        return store.init_run(project_dir, _cfg(), "mock")

    def test_cover_only_when_ffmpeg_missing(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel = _reel("AAA001")
            cover = video.cover_path(run_dir, "AAA001")
            cover.parent.mkdir(parents=True, exist_ok=True)
            cover.write_bytes(b"cover-bytes")
            outliers_doc = _outliers_doc([reel])

            def _boom(cmd: List[str], **_kwargs: Any) -> Any:
                raise AssertionError("ffmpeg must never be invoked when unavailable")

            with mock.patch("lib.frames.ffmpeg_available", return_value=False):
                statuses = frames.frames_for_selected(
                    run_dir, outliers_doc, _cfg(), runner=_boom, log=lambda _m: None
                )

        self.assertEqual(statuses, {"AAA001": "cover_only"})
        self.assertEqual(reel["frames_status"], "cover_only")

    def test_failed_when_ffmpeg_missing_and_no_cover(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel = _reel("AAA001")
            outliers_doc = _outliers_doc([reel])

            with mock.patch("lib.frames.ffmpeg_available", return_value=False):
                statuses = frames.frames_for_selected(
                    run_dir, outliers_doc, _cfg(), log=lambda _m: None
                )

        self.assertEqual(statuses, {"AAA001": "failed"})

    def test_logs_ffmpeg_hint_exactly_once_per_call(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel_a = _reel("AAA001")
            reel_b = _reel("BBB001")
            outliers_doc = _outliers_doc([reel_a, reel_b])
            logs: List[str] = []

            with mock.patch("lib.frames.ffmpeg_available", return_value=False):
                frames.frames_for_selected(run_dir, outliers_doc, _cfg(), log=logs.append)

        hint_lines = [line for line in logs if "ffmpeg not found" in line]
        self.assertEqual(len(hint_lines), 1, logs)

    def test_no_video_when_video_status_not_ok(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel = _reel("AAA001", video_status="too_large")
            outliers_doc = _outliers_doc([reel])

            def _boom(cmd: List[str], **_kwargs: Any) -> Any:
                raise AssertionError("ffmpeg must never run for a reel with no video")

            statuses = frames.frames_for_selected(
                run_dir, outliers_doc, _cfg(), runner=_boom, log=lambda _m: None
            )

        self.assertEqual(statuses, {"AAA001": "no_video"})
        self.assertEqual(reel["frames_status"], "no_video")

    def test_status_written_into_outliers_json(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel_a = _reel("AAA001", video_status="ok")
            reel_b = _reel("BBB001", video_status="failed")
            store.write_json_atomic(
                run_dir / "02-outliers.json", _outliers_doc([reel_a, reel_b])
            )
            outliers_doc = store.read_json(run_dir / "02-outliers.json")
            runner = _RecordingRunner()

            with mock.patch("lib.frames.ffmpeg_available", return_value=True):
                frames.frames_for_selected(
                    run_dir, outliers_doc, _cfg(), runner=runner, log=lambda _m: None
                )

            on_disk = store.read_json(run_dir / "02-outliers.json")

        self.assertEqual(on_disk["selected"][0]["shortCode"], "AAA001")
        self.assertEqual(on_disk["selected"][0]["frames_status"], "ok")
        self.assertEqual(on_disk["selected"][1]["shortCode"], "BBB001")
        self.assertEqual(on_disk["selected"][1]["frames_status"], "no_video")
        # Untouched by frames_for_selected.
        self.assertEqual(on_disk["excluded"], [])
        self.assertEqual(on_disk["account_status"], {})

    def test_mock_copies_fixture_frames_and_skips_existing(self) -> None:
        with temp_project() as project_dir:
            run_dir = self._run_dir(project_dir)
            reel = _reel("AAA001")
            out_dir = run_dir / "frames" / "AAA001"
            out_dir.mkdir(parents=True)
            (out_dir / "f01.jpg").write_bytes(b"already-here")
            outliers_doc = _outliers_doc([reel])

            def _boom(cmd: List[str], **_kwargs: Any) -> Any:
                raise AssertionError("mock mode must never invoke ffmpeg")

            statuses = frames.frames_for_selected(
                run_dir,
                outliers_doc,
                _cfg(),
                mock=True,
                fixtures_dir=FIXTURES_DIR,
                runner=_boom,
                log=lambda _m: None,
            )

            f01_bytes = (out_dir / "f01.jpg").read_bytes()
            f08_exists = (out_dir / "f08.jpg").exists()

        self.assertEqual(statuses, {"AAA001": "ok"})
        self.assertEqual(f01_bytes, b"already-here")  # untouched, not re-copied
        self.assertTrue(f08_exists)


class ResearchWiringTests(NoNetworkTestCase):
    def test_research_mock_copies_fixture_frames(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})
            cfg = store.load_config(project_dir)

            result = _run_research_capturing(
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
            by_shortcode = {reel["shortCode"]: reel for reel in selected}

            # Existence checks must happen before temp_project() cleans up
            # the directory on exit -- only the booleans survive below.
            ok_reels = [reel for reel in selected if reel["video_status"] == "ok"]
            frame_files_exist = [
                (run_dir / "frames" / reel["shortCode"] / "f01.jpg").exists() for reel in ok_reels
            ]

        self.assertTrue(ok_reels, "fixture must have at least one downloadable reel")
        self.assertTrue(all(frame_files_exist), frame_files_exist)
        for reel in ok_reels:
            self.assertEqual(reel["frames_status"], "ok")

        # HAB004 is the fixture's deliberately-too-long reel (videoDuration
        # 400s, over the default max_video_seconds=180) -- video_status
        # "too_large" means frames_for_selected must never touch ffmpeg for
        # it, landing on "no_video" instead of "ok" or "cover_only".
        self.assertIn("HAB004", by_shortcode)
        self.assertEqual(by_shortcode["HAB004"]["video_status"], "too_large")
        self.assertEqual(by_shortcode["HAB004"]["frames_status"], "no_video")

        not_ok = [reel for reel in selected if reel["video_status"] != "ok"]
        self.assertEqual(
            result["frames"],
            {"ok": len(ok_reels), "cover_only": 0, "failed": 0, "no_video": len(not_ok)},
        )

    def test_no_download_leaves_frames_pending_and_result_frames_none(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})
            cfg = store.load_config(project_dir)

            result = _run_research_capturing(
                project=project_dir,
                cfg=cfg,
                keys=_mock_keys(),
                mock=True,
                yes=True,
                estimate_only=False,
                resume=None,
                no_download=True,
            )

            run_dir = Path(result["run_dir"])
            outliers_doc = store.read_json(run_dir / "02-outliers.json")
            frames_dir_exists = (run_dir / "frames").exists()

        self.assertIsNone(result["frames"])
        self.assertFalse(frames_dir_exists)
        statuses = [reel["frames_status"] for reel in outliers_doc["selected"]]
        self.assertTrue(statuses, "fixture must select at least one reel")
        self.assertTrue(all(status == "pending" for status in statuses))


class FramesCliTests(NoNetworkTestCase):
    def test_frames_subcommand_prints_json_summary(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})

            code, _out, err = run_cli(
                ["research", "--mock", "--yes", "--project", str(project_dir)],
                env=NO_GLOBAL_ENV,
            )
            self.assertEqual(code, 0, err)

            code, out, err = run_cli(
                ["frames", "--mock", "--run", "latest", "--project", str(project_dir)],
                env=NO_GLOBAL_ENV,
            )

        self.assertEqual(code, 0, err)
        lines = [line for line in out.splitlines() if line.strip()]
        payload = json.loads(lines[-1])
        self.assertEqual(set(payload), {"run_id", "frames"})
        self.assertEqual(set(payload["frames"]), {"ok", "cover_only", "failed", "no_video"})

    def test_frames_unresolvable_run_exits_2(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})

            code, out, err = run_cli(
                ["frames", "--mock", "--run", "does-not-exist", "--project", str(project_dir)],
                env=NO_GLOBAL_ENV,
            )

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())

    def test_frames_missing_outliers_exits_2(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["sproutapp"]})
            cfg = store.load_config(project_dir)
            run_dir = store.init_run(project_dir, cfg, "mock")

            code, out, err = run_cli(
                ["frames", "--mock", "--run", run_dir.name, "--project", str(project_dir)],
                env=NO_GLOBAL_ENV,
            )

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("02-outliers.json", err)


class _ScriptedApifyTransport:
    """Returns/raises each scripted response from `.request_json` in order.

    Mirrors test_video.py's `_ScriptedApifyTransport` (itself mirroring
    test_research.py's `_ScriptedTransport`), kept local here so this
    module does not reach into another test module's internals.
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


class _RecordingDownloader:
    """Fake `downloader`: records (url, dest, max_bytes), always "ok".

    Mirrors test_video.py's `_ScriptedDownloader`'s "ok" branch -- a
    few sentinel bytes land at `dest` so a later `extract_frames` call
    (via `frames_for_selected`) has a real file path to pass to its
    runner, exactly like a real download would leave behind.
    """

    def __init__(self) -> None:
        self.calls: List[Any] = []

    def __call__(self, url: str, dest: Path, max_bytes: int, **_kwargs: Any) -> http.DownloadResult:
        self.calls.append((url, Path(dest), max_bytes))
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fresh-video-bytes")
        return http.DownloadResult("ok", len(b"fresh-video-bytes"), 200)


class RunFramesRefreshExpiredTests(NoNetworkTestCase):
    def test_refresh_expired_updates_video_status_then_extracts(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acct1"]})
            cfg = store.load_config(project_dir)
            run_dir = store.init_run(project_dir, cfg, "live")

            reel = _reel("AAA001", video_status="expired")
            store.write_json_atomic(run_dir / "02-outliers.json", _outliers_doc([reel]))

            transport = _ScriptedApifyTransport(
                [
                    {"data": {"id": "run-1", "status": "READY", "defaultDatasetId": "ds-1"}},
                    {"data": {"id": "run-1", "status": "SUCCEEDED", "defaultDatasetId": "ds-1"}},
                    [{"shortCode": "AAA001", "videoUrl": "https://fresh.example/AAA001.mp4"}],
                ]
            )
            downloader = _RecordingDownloader()
            ffmpeg_runner = _RecordingRunner()
            keys = Keys(apify="tok-123", source="env", warnings=[])

            # Patched so this test's outcome never depends on whether the
            # machine running it actually has ffmpeg installed -- only
            # ffmpeg_runner (a fake) is ever allowed to "run" it.
            with mock.patch("lib.frames.ffmpeg_available", return_value=True):
                result = frames.run_frames(
                    project_dir,
                    run_dir.name,
                    cfg,
                    keys,
                    mock=False,
                    refresh_expired=True,
                    transport=transport,
                    downloader=downloader,
                    runner=ffmpeg_runner,
                    log=lambda _m: None,
                )

            on_disk = store.read_json(run_dir / "02-outliers.json")

        # The refresh call used the reel's own token and shortCode; the
        # download landed on video_path with the configured MB cap.
        self.assertEqual(len(downloader.calls), 1)
        url, dest, max_bytes = downloader.calls[0]
        self.assertEqual(url, "https://fresh.example/AAA001.mp4")
        self.assertEqual(dest, video.video_path(run_dir, "AAA001"))
        self.assertEqual(max_bytes, cfg["max_video_mb"] * 1024 * 1024)
        self.assertEqual(transport.calls[0]["headers"], {"Authorization": "Bearer tok-123"})

        # video_status flips from "expired" to the download's own "ok",
        # and frames_for_selected then extracts (real ffmpeg is faked).
        self.assertEqual(on_disk["selected"][0]["video_status"], "ok")
        self.assertEqual(on_disk["selected"][0]["frames_status"], "ok")
        self.assertEqual(
            result,
            {"run_id": run_dir.name, "frames": {"ok": 1, "cover_only": 0, "failed": 0, "no_video": 0}},
        )

    def test_refresh_expired_also_refreshes_a_blocked_reel(self) -> None:
        # Instagram's CDN answers an expired signed URL with 403, which
        # lib/http.py maps to "blocked", not "expired". Refreshing only
        # the literal "expired" status would leave the common case
        # untouched and the reel stuck at no_video.
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acct1"]})
            cfg = store.load_config(project_dir)
            run_dir = store.init_run(project_dir, cfg, "live")

            reel = _reel("BLK001", video_status="blocked")
            store.write_json_atomic(run_dir / "02-outliers.json", _outliers_doc([reel]))

            transport = _ScriptedApifyTransport(
                [
                    {"data": {"id": "run-3", "status": "READY", "defaultDatasetId": "ds-3"}},
                    {"data": {"id": "run-3", "status": "SUCCEEDED", "defaultDatasetId": "ds-3"}},
                    [{"shortCode": "BLK001", "videoUrl": "https://fresh.example/BLK001.mp4"}],
                ]
            )
            downloader = _RecordingDownloader()
            ffmpeg_runner = _RecordingRunner()

            with mock.patch("lib.frames.ffmpeg_available", return_value=True):
                result = frames.run_frames(
                    project_dir,
                    run_dir.name,
                    cfg,
                    Keys(apify="tok-123", source="env", warnings=[]),
                    mock=False,
                    refresh_expired=True,
                    transport=transport,
                    downloader=downloader,
                    runner=ffmpeg_runner,
                    log=lambda _m: None,
                )

            on_disk = store.read_json(run_dir / "02-outliers.json")

        self.assertEqual(len(downloader.calls), 1)
        self.assertEqual(downloader.calls[0][0], "https://fresh.example/BLK001.mp4")
        self.assertEqual(on_disk["selected"][0]["video_status"], "ok")
        self.assertEqual(on_disk["selected"][0]["frames_status"], "ok")
        self.assertEqual(result["frames"]["ok"], 1)

    def test_refresh_expired_leaves_reel_expired_when_no_fresh_url(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acct1"]})
            cfg = store.load_config(project_dir)
            run_dir = store.init_run(project_dir, cfg, "live")

            reel = _reel("ZZZ999", video_status="expired")
            store.write_json_atomic(run_dir / "02-outliers.json", _outliers_doc([reel]))

            transport = _ScriptedApifyTransport(
                [
                    {"data": {"id": "run-2", "status": "READY", "defaultDatasetId": "ds-2"}},
                    {"data": {"id": "run-2", "status": "SUCCEEDED", "defaultDatasetId": "ds-2"}},
                    [],  # no items -- refresh_video_url returns None
                ]
            )

            def _boom_downloader(url: str, dest: Path, max_bytes: int, **_kwargs: Any) -> Any:
                raise AssertionError("no fresh URL means download_to_file must never be called")

            result = frames.run_frames(
                project_dir,
                run_dir.name,
                cfg,
                Keys(apify="tok-123", source="env", warnings=[]),
                mock=False,
                refresh_expired=True,
                transport=transport,
                downloader=_boom_downloader,
                log=lambda _m: None,
            )

            on_disk = store.read_json(run_dir / "02-outliers.json")

        self.assertEqual(on_disk["selected"][0]["video_status"], "expired")
        self.assertEqual(on_disk["selected"][0]["frames_status"], "no_video")
        self.assertEqual(result["frames"], {"ok": 0, "cover_only": 0, "failed": 0, "no_video": 1})

    def test_refresh_expired_skipped_under_mock(self) -> None:
        with temp_project() as project_dir:
            _write_config(project_dir, {"competitors": ["acct1"]})
            cfg = store.load_config(project_dir)
            run_dir = store.init_run(project_dir, cfg, "mock")

            reel = _reel("AAA001", video_status="expired")
            store.write_json_atomic(run_dir / "02-outliers.json", _outliers_doc([reel]))

            def _boom_transport(*_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("--mock must never call the Apify transport")

            result = frames.run_frames(
                project_dir,
                run_dir.name,
                cfg,
                _mock_keys(),
                mock=True,
                refresh_expired=True,
                transport=_boom_transport,
                fixtures_dir=FIXTURES_DIR,
                log=lambda _m: None,
            )

        # Still "expired" (never refreshed) -> frames_for_selected's own
        # not-"ok" handling, "no_video", same as without the flag at all.
        self.assertEqual(result["frames"], {"ok": 0, "cover_only": 0, "failed": 0, "no_video": 1})


if __name__ == "__main__":
    unittest.main()
