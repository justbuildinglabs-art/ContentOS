"""Tests for lib/transcribe.py: 0.3.0 transcripts (design spec, "0.3.0 changes").

Nothing here runs a real whisper-cli or ffmpeg, and nothing touches the
network. `shutil.which` is patched per test, `runner` is a small fake
that records argv and drops the files a real ffmpeg/whisper-cli call
would leave behind, and the Apify path uses a scripted transport local
to this module. NoNetworkTestCase is a second line of defense.
"""
from __future__ import annotations

import copy
import json
import subprocess
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

from tests.helpers import PRE_WEEKLY_CONFIG, NoNetworkTestCase, REPO_ROOT, run_cli, temp_project

# tests.helpers inserts SCRIPTS_DIR onto sys.path as an import side effect,
# so these imports must come after it.
from lib import apify, research, store, transcribe, video  # noqa: E402
from lib.env import Keys  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "fixtures"
NO_GLOBAL_ENV = {"CONTENTOS_CONFIG_DIR": ""}
FIXTURE_HANDLES = ["sproutapp", "habitlab", "dailywins", "ghostaccount"]

# A canned whisper-cli `-ocsv` output: milliseconds, quoted text, with a
# doubled quote inside one segment (whisper.cpp escapes `"` as `""`).
SAMPLE_CSV = (
    "start,end,text\n"
    "0,2200,\" Stop scrolling if you keep breaking streaks.\"\n"
    "2200,5400,\" I use a \"\"two minute\"\" rule.\"\n"
    "75000,79000,\" That is the whole trick.\"\n"
)


def _cfg(**overrides: Any) -> Dict[str, Any]:
    cfg = copy.deepcopy(store.DEFAULT_CONFIG)
    cfg["competitors"] = ["acct1"]
    cfg.update(overrides)
    return cfg


def _reel(shortcode: str, video_status: str = "ok") -> Dict[str, Any]:
    return {
        "shortCode": shortcode,
        "url": f"https://www.instagram.com/reel/{shortcode}/",
        "duration_s": 20.0,
        "video_status": video_status,
        "frames_status": "ok",
        "transcript_status": "pending",
    }


def _outliers_doc(selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"selected": selected, "backfill": [], "excluded": [], "account_status": {}, "baselines": {}}


def _which(present: List[str]):
    """A fake shutil.which that finds only the names in `present`."""
    return lambda name: f"/usr/local/bin/{name}" if name in present else None


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: Any = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeRunner:
    """Fake subprocess.run: ffmpeg writes the wav, whisper writes `<prefix>.csv`."""

    def __init__(self, csv_text: str = SAMPLE_CSV, whisper_rc: int = 0, ffmpeg_rc: int = 0) -> None:
        self.csv_text = csv_text
        self.whisper_rc = whisper_rc
        self.ffmpeg_rc = ffmpeg_rc
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str], **_kwargs: Any) -> _Proc:
        self.calls.append(list(cmd))
        if Path(cmd[0]).name == "ffmpeg":
            if self.ffmpeg_rc == 0:
                Path(cmd[-1]).write_bytes(b"RIFF-fake-wav")
            return _Proc(self.ffmpeg_rc)
        if self.whisper_rc == 0:
            prefix = cmd[cmd.index("-of") + 1]
            Path(prefix + ".csv").write_text(self.csv_text, encoding="utf-8")
        return _Proc(self.whisper_rc)


class _ScriptedTransport:
    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def request_json(self, method, url, headers=None, json_body=None, params=None):
        self.calls.append({"method": method, "url": url, "json_body": json_body, "params": params})
        if not self._responses:
            raise AssertionError("scripted transport exhausted")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _apify_responses(items: List[dict]) -> List[Any]:
    return [
        {"data": {"id": "run-t", "status": "READY", "defaultDatasetId": "ds-t"}},
        {"data": {"id": "run-t", "status": "SUCCEEDED", "defaultDatasetId": "ds-t"}},
        items,
    ]


class FindToolsTests(NoNetworkTestCase):
    def test_find_whisper_prefers_whisper_cli_then_whisper_cpp(self) -> None:
        with mock.patch("shutil.which", _which(["whisper-cli", "whisper-cpp"])):
            self.assertEqual(transcribe.find_whisper(), "/usr/local/bin/whisper-cli")
        with mock.patch("shutil.which", _which(["whisper-cpp"])):
            self.assertEqual(transcribe.find_whisper(), "/usr/local/bin/whisper-cpp")
        with mock.patch("shutil.which", _which([])):
            self.assertIsNone(transcribe.find_whisper())

    def test_find_model_order_config_env_default(self) -> None:
        with temp_project() as tmp:
            cfg_model = tmp / "cfg.bin"
            env_model = tmp / "env.bin"
            home = tmp / "home"
            default_model = home / ".cache" / "contentos" / "whisper" / "ggml-base.en.bin"
            default_model.parent.mkdir(parents=True)
            for path in (cfg_model, env_model, default_model):
                path.write_bytes(b"ggml")
            environ = {"CONTENTOS_WHISPER_MODEL": str(env_model), "HOME": str(home)}

            self.assertEqual(
                transcribe.find_model({"whisper_model": str(cfg_model)}, environ), cfg_model
            )
            self.assertEqual(transcribe.find_model({"whisper_model": ""}, environ), env_model)
            self.assertEqual(transcribe.find_model({}, {"HOME": str(home)}), default_model)

            # A configured path that does not exist is not a model.
            self.assertIsNone(
                transcribe.find_model({"whisper_model": str(tmp / "missing.bin")}, {"HOME": str(home)})
            )
            self.assertIsNone(transcribe.find_model({}, {"HOME": str(tmp / "nohome")}))


class ParseAndFormatTests(NoNetworkTestCase):
    def test_timestamp_format(self) -> None:
        self.assertEqual(transcribe.format_timestamp(0), "[0:00]")
        self.assertEqual(transcribe.format_timestamp(2.2), "[0:02]")
        self.assertEqual(transcribe.format_timestamp(75.9), "[1:15]")
        self.assertEqual(transcribe.format_timestamp(600), "[10:00]")

    def test_parse_whisper_csv(self) -> None:
        segments = transcribe.parse_whisper_csv(SAMPLE_CSV)
        self.assertEqual(
            segments,
            [
                (0.0, "Stop scrolling if you keep breaking streaks."),
                (2.2, 'I use a "two minute" rule.'),
                (75.0, "That is the whole trick."),
            ],
        )

    def test_parse_whisper_csv_skips_blank_and_bad_rows(self) -> None:
        text = "start,end,text\n\n0,1000,\"  \"\nnot,a,row\n1000,2000,\" ok\"\n"
        self.assertEqual(transcribe.parse_whisper_csv(text), [(1.0, "ok")])

    def test_parse_whisper_stdout_fallback(self) -> None:
        text = (
            "[00:00:00.000 --> 00:00:02.200]   Stop scrolling.\n"
            "[00:01:15.000 --> 00:01:19.000]   That is it.\n"
        )
        self.assertEqual(
            transcribe.parse_whisper_stdout(text), [(0.0, "Stop scrolling."), (75.0, "That is it.")]
        )

    def test_format_lines(self) -> None:
        text = transcribe.format_lines([(0.0, "Hi."), (75.0, "Bye.")])
        self.assertEqual(text, "[0:00] Hi.\n[1:15] Bye.\n")


class TranscribeLocalTests(NoNetworkTestCase):
    def test_command_shapes_and_output_file(self) -> None:
        with temp_project() as tmp:
            mp4 = tmp / "A1.mp4"
            mp4.write_bytes(b"mp4")
            out = tmp / "transcripts" / "A1.txt"
            runner = _FakeRunner()
            status = transcribe.transcribe_local(
                mp4, out, "/usr/local/bin/whisper-cli", Path("/m/ggml-base.en.bin"),
                ffmpeg="/usr/local/bin/ffmpeg", runner=runner,
            )
            self.assertEqual(status, "ok")
            self.assertEqual(
                out.read_text(encoding="utf-8"),
                "[0:00] Stop scrolling if you keep breaking streaks.\n"
                "[0:02] I use a \"two minute\" rule.\n"
                "[1:15] That is the whole trick.\n",
            )

        ffmpeg_cmd, whisper_cmd = runner.calls
        self.assertEqual(ffmpeg_cmd[0], "/usr/local/bin/ffmpeg")
        self.assertIn(str(mp4), ffmpeg_cmd)
        self.assertEqual(ffmpeg_cmd[ffmpeg_cmd.index("-ar") + 1], "16000")
        self.assertEqual(ffmpeg_cmd[ffmpeg_cmd.index("-ac") + 1], "1")
        self.assertTrue(ffmpeg_cmd[-1].endswith(".wav"))
        self.assertEqual(whisper_cmd[0], "/usr/local/bin/whisper-cli")
        self.assertEqual(whisper_cmd[whisper_cmd.index("-m") + 1], "/m/ggml-base.en.bin")
        self.assertEqual(whisper_cmd[whisper_cmd.index("-f") + 1], ffmpeg_cmd[-1])
        self.assertIn("-ocsv", whisper_cmd)

    def test_a_video_with_no_audio_track_is_none_not_failed(self) -> None:
        # Instagram sometimes serves a video-only stream. There is nothing
        # to hear, which is "none", not a transcription failure.
        with temp_project() as tmp:
            mp4 = tmp / "A1.mp4"
            mp4.write_bytes(b"mp4")
            out = tmp / "A1.txt"

            def no_audio(cmd, **_kw):
                return _Proc(234, stderr=b"[out#0/wav] Output file does not contain any stream\n")

            status = transcribe.transcribe_local(
                mp4, out, "whisper-cli", Path("/m.bin"), ffmpeg="ffmpeg", runner=no_audio
            )
        self.assertEqual(status, "none")
        self.assertFalse(out.exists())

    def test_failures_are_statuses_not_exceptions(self) -> None:
        with temp_project() as tmp:
            mp4 = tmp / "A1.mp4"
            mp4.write_bytes(b"mp4")
            out = tmp / "A1.txt"
            args = (mp4, out, "whisper-cli", Path("/m.bin"))

            self.assertEqual(
                transcribe.transcribe_local(*args, ffmpeg="ffmpeg", runner=_FakeRunner(ffmpeg_rc=1)),
                "failed",
            )
            self.assertEqual(
                transcribe.transcribe_local(*args, ffmpeg="ffmpeg", runner=_FakeRunner(whisper_rc=1)),
                "failed",
            )

            def boom(cmd, **_kw):
                raise subprocess.TimeoutExpired(cmd, 1)

            self.assertEqual(transcribe.transcribe_local(*args, ffmpeg="ffmpeg", runner=boom), "failed")

            def missing(cmd, **_kw):
                raise FileNotFoundError(cmd[0])

            self.assertEqual(transcribe.transcribe_local(*args, ffmpeg="ffmpeg", runner=missing), "failed")
            self.assertFalse(out.exists())

            # No speech at all: nothing written, status none.
            self.assertEqual(
                transcribe.transcribe_local(
                    *args, ffmpeg="ffmpeg", runner=_FakeRunner(csv_text="start,end,text\n")
                ),
                "none",
            )
            self.assertFalse(out.exists())


class ApifyInputAndOutputTests(NoNetworkTestCase):
    def test_transcript_input_names_the_reel_urls_and_include_transcript(self) -> None:
        body = apify.build_transcript_input(
            ["https://www.instagram.com/reel/A1/", "https://www.instagram.com/reel/B2/"]
        )
        self.assertIs(body["includeTranscript"], True)
        self.assertIn("https://www.instagram.com/reel/A1/", json.dumps(body))
        self.assertIn("https://www.instagram.com/reel/B2/", json.dumps(body))

    def test_segments_from_item_handles_field_names_and_shapes(self) -> None:
        self.assertEqual(
            transcribe.segments_from_item({"transcript": "Hello there.\nSecond line."}),
            [(0.0, "Hello there."), (0.0, "Second line.")],
        )
        self.assertEqual(
            transcribe.segments_from_item({"transcriptText": "Only text."}), [(0.0, "Only text.")]
        )
        self.assertEqual(
            transcribe.segments_from_item(
                {"transcript": [{"start": 0, "text": "A"}, {"startTime": 61.5, "text": " B "}, "C"]}
            ),
            [(0.0, "A"), (61.5, "B"), (0.0, "C")],
        )
        self.assertEqual(transcribe.segments_from_item({"transcript": ""}), [])
        self.assertEqual(transcribe.segments_from_item({"caption": "no transcript"}), [])

    def test_item_shortcode_from_field_or_url(self) -> None:
        self.assertEqual(transcribe.item_shortcode({"shortCode": "A1"}), "A1")
        self.assertEqual(
            transcribe.item_shortcode({"url": "https://www.instagram.com/reel/B2/?x=1"}), "B2"
        )
        self.assertEqual(
            transcribe.item_shortcode({"inputUrl": "https://www.instagram.com/p/C3/"}), "C3"
        )
        self.assertIsNone(transcribe.item_shortcode({}))


class PlanAndEstimateTests(NoNetworkTestCase):
    def test_estimate_transcripts_cost(self) -> None:
        self.assertEqual(apify.estimate_transcripts_cost(20, 180, 0.02), 1.2)
        self.assertEqual(apify.estimate_transcripts_cost(20, 180, 0.0), 0.0)

    def test_estimate_cost_adds_transcripts_to_total(self) -> None:
        est = apify.estimate_cost(2, 30, transcripts_usd=1.2)
        self.assertEqual(est.transcripts_usd, 1.2)
        self.assertEqual(est.total_usd, round(0.162 + 0.0054 + 1.2, 4))
        self.assertEqual(apify.estimate_cost(2, 30).transcripts_usd, 0.0)

    def test_apify_planned_only_when_paid_path_would_run(self) -> None:
        on = dict(apify_transcripts=True, apify_transcript_usd_per_min=0.02)
        with mock.patch.object(transcribe, "local_available", return_value=False):
            self.assertTrue(transcribe.apify_planned(_cfg(**on)))
            self.assertTrue(transcribe.apify_planned(_cfg(transcripts="apify", **on)))
            self.assertFalse(transcribe.apify_planned(_cfg(transcripts="local", **on)))
            self.assertFalse(transcribe.apify_planned(_cfg(transcripts="off", **on)))
            self.assertFalse(transcribe.apify_planned(_cfg()))
        with mock.patch.object(transcribe, "local_available", return_value=True):
            self.assertFalse(transcribe.apify_planned(_cfg(**on)))
            # "apify" mode skips the local backend on purpose.
            self.assertTrue(transcribe.apify_planned(_cfg(transcripts="apify", **on)))

    def test_estimate_usd_is_zero_unless_planned(self) -> None:
        on = _cfg(apify_transcripts=True, apify_transcript_usd_per_min=0.02)
        with mock.patch.object(transcribe, "local_available", return_value=False):
            self.assertEqual(transcribe.estimate_usd(on), 1.2)
        with mock.patch.object(transcribe, "local_available", return_value=True):
            self.assertEqual(transcribe.estimate_usd(on), 0.0)


class TranscriptsForSelectedTests(NoNetworkTestCase):
    def _run(self, run_dir: Path, doc: Dict[str, Any], cfg: Dict[str, Any], **kwargs: Any):
        kwargs.setdefault("log", lambda _m: None)
        return transcribe.transcripts_for_selected(run_dir, doc, cfg, **kwargs)

    def test_off_sets_none_everywhere(self) -> None:
        with temp_project() as run_dir:
            doc = _outliers_doc([_reel("A1"), _reel("B2")])
            statuses = self._run(run_dir, doc, _cfg(transcripts="off"), runner=_FakeRunner())
            written = store.read_json(run_dir / "02-outliers.json")
        self.assertEqual(statuses, {"A1": "none", "B2": "none"})
        self.assertEqual([r["transcript_status"] for r in written["selected"]], ["none", "none"])

    def test_local_path_writes_files_and_statuses(self) -> None:
        with temp_project() as tmp:
            run_dir = tmp / "run"
            model = tmp / "ggml.bin"
            model.write_bytes(b"ggml")
            video.video_path(run_dir, "A1").parent.mkdir(parents=True)
            video.video_path(run_dir, "A1").write_bytes(b"mp4")
            doc = _outliers_doc([_reel("A1"), _reel("B2", video_status="failed"), _reel("C3")])
            runner = _FakeRunner()
            with mock.patch("shutil.which", _which(["whisper-cli", "ffmpeg"])):
                statuses = self._run(run_dir, doc, _cfg(whisper_model=str(model)), runner=runner)
            text = (run_dir / "transcripts" / "A1.txt").read_text(encoding="utf-8")
            written = store.read_json(run_dir / "02-outliers.json")
        # A1 transcribed; B2 has no video; C3's video_status says ok but the
        # file is gone, which is also "none", never a crash.
        self.assertEqual(statuses, {"A1": "ok", "B2": "none", "C3": "none"})
        self.assertTrue(text.startswith("[0:00] Stop scrolling"))
        self.assertEqual(
            [r["transcript_status"] for r in written["selected"]], ["ok", "none", "none"]
        )

    def test_existing_transcript_is_kept(self) -> None:
        with temp_project() as tmp:
            run_dir = tmp / "run"
            model = tmp / "ggml.bin"
            model.write_bytes(b"ggml")
            video.video_path(run_dir, "A1").parent.mkdir(parents=True)
            video.video_path(run_dir, "A1").write_bytes(b"mp4")
            existing = run_dir / "transcripts" / "A1.txt"
            existing.parent.mkdir(parents=True)
            existing.write_text("[0:00] Kept.\n", encoding="utf-8")
            runner = _FakeRunner()
            with mock.patch("shutil.which", _which(["whisper-cli", "ffmpeg"])):
                statuses = self._run(
                    run_dir, _outliers_doc([_reel("A1")]), _cfg(whisper_model=str(model)), runner=runner
                )
            self.assertEqual(existing.read_text(encoding="utf-8"), "[0:00] Kept.\n")
        self.assertEqual(statuses, {"A1": "ok"})
        self.assertEqual(runner.calls, [])

    def test_one_failed_reel_does_not_stop_the_rest(self) -> None:
        with temp_project() as tmp:
            run_dir = tmp / "run"
            model = tmp / "ggml.bin"
            model.write_bytes(b"ggml")
            for sc in ("A1", "B2"):
                video.video_path(run_dir, sc).parent.mkdir(parents=True, exist_ok=True)
                video.video_path(run_dir, sc).write_bytes(b"mp4")

            calls = {"n": 0}
            good = _FakeRunner()

            def flaky(cmd, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("disk hiccup")
                return good(cmd, **kw)

            with mock.patch("shutil.which", _which(["whisper-cli", "ffmpeg"])):
                statuses = self._run(
                    run_dir, _outliers_doc([_reel("A1"), _reel("B2")]),
                    _cfg(whisper_model=str(model)), runner=flaky,
                )
        self.assertEqual(statuses, {"A1": "failed", "B2": "ok"})

    def test_no_local_and_no_apify_is_none_with_one_hint(self) -> None:
        logs: List[str] = []
        with temp_project() as run_dir:
            with mock.patch("shutil.which", _which(["ffmpeg"])):
                statuses = self._run(
                    run_dir, _outliers_doc([_reel("A1"), _reel("B2")]), _cfg(), log=logs.append
                )
        self.assertEqual(statuses, {"A1": "none", "B2": "none"})
        self.assertEqual(len(logs), 1)
        self.assertIn("whisper", logs[0])
        self.assertNotIn("\u2014", logs[0])

    def test_apify_fallback_maps_items_by_shortcode(self) -> None:
        items = [
            {"shortCode": "A1", "transcript": "Hello from A1."},
            {"url": "https://www.instagram.com/reel/B2/", "transcriptText": ""},
        ]
        transport = _ScriptedTransport(_apify_responses(items))
        cfg = _cfg(apify_transcripts=True, apify_transcript_usd_per_min=0.02)
        with temp_project() as run_dir:
            with mock.patch("shutil.which", _which([])):
                statuses = self._run(
                    run_dir, _outliers_doc([_reel("A1"), _reel("B2")]), cfg,
                    token="tok", transport=transport, sleep=lambda _s: None,
                )
            text = (run_dir / "transcripts" / "A1.txt").read_text(encoding="utf-8")
            written = store.read_json(run_dir / "02-outliers.json")
        self.assertEqual(statuses, {"A1": "apify", "B2": "none"})
        self.assertEqual(text, "[0:00] Hello from A1.\n")
        self.assertEqual([r["transcript_status"] for r in written["selected"]], ["apify", "none"])

        start = transport.calls[0]
        self.assertEqual(start["method"], "POST")
        self.assertIn("apify~instagram-reel-scraper", start["url"])
        self.assertIs(start["json_body"]["includeTranscript"], True)
        self.assertEqual(start["params"]["maxTotalChargeUsd"], cfg["apify_max_charge_usd"])

    def test_apify_run_failure_marks_failed_never_raises(self) -> None:
        transport = _ScriptedTransport(
            [
                {"data": {"id": "run-t", "status": "READY", "defaultDatasetId": "ds-t"}},
                {"data": {"id": "run-t", "status": "FAILED", "defaultDatasetId": "ds-t"}},
            ]
        )
        cfg = _cfg(apify_transcripts=True, apify_transcript_usd_per_min=0.02)
        with temp_project() as run_dir:
            with mock.patch("shutil.which", _which([])):
                statuses = self._run(
                    run_dir, _outliers_doc([_reel("A1")]), cfg,
                    token="tok", transport=transport, sleep=lambda _s: None,
                )
        self.assertEqual(statuses, {"A1": "failed"})

    def test_apify_without_token_is_none_and_makes_no_call(self) -> None:
        transport = _ScriptedTransport([])
        cfg = _cfg(apify_transcripts=True, apify_transcript_usd_per_min=0.02)
        with temp_project() as run_dir:
            with mock.patch("shutil.which", _which([])):
                statuses = self._run(
                    run_dir, _outliers_doc([_reel("A1")]), cfg, token=None, transport=transport
                )
        self.assertEqual(statuses, {"A1": "none"})
        self.assertEqual(transport.calls, [])

    def test_mock_copies_fixture_when_present(self) -> None:
        with temp_project() as run_dir:
            with mock.patch("shutil.which", _which([])):
                statuses = self._run(
                    run_dir, _outliers_doc([_reel("DWN003"), _reel("ZZZ999")]), _cfg(),
                    mock=True, fixtures_dir=FIXTURES_DIR,
                )
            copied = (run_dir / "transcripts" / "DWN003.txt").read_text(encoding="utf-8")
        self.assertEqual(statuses, {"DWN003": "ok", "ZZZ999": "none"})
        self.assertEqual(
            copied, (FIXTURES_DIR / "transcripts" / "DWN003.txt").read_text(encoding="utf-8")
        )
        for line in copied.splitlines():
            self.assertRegex(line, r"^\[\d+:\d\d\] \S")

    def test_count(self) -> None:
        self.assertEqual(
            transcribe.count({"a": "ok", "b": "none", "c": "apify", "d": "none"}),
            {"ok": 1, "apify": 1, "none": 2, "failed": 0},
        )


def _write_config(project: Path, overrides: Dict[str, Any]) -> None:
    """Write `<project>/.contentos/config.json` with `PRE_WEEKLY_CONFIG` overlaid with `overrides`."""
    config_dir = project / ".contentos"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(
        json.dumps(dict(PRE_WEEKLY_CONFIG, **overrides)), encoding="utf-8"
    )


def _mock_research(project: Path, **extra: Any) -> Dict[str, Any]:
    cfg = store.load_config(project)
    with redirect_stdout(StringIO()):
        return research.run_research(
            project=project, cfg=cfg, keys=Keys(apify=None, source=None, warnings=[]),
            mock=True, yes=True, estimate_only=False, resume=None, log=lambda _m: None, **extra,
        )


class ResearchWiringTests(NoNetworkTestCase):
    def test_mock_research_transcribes_selected_reels(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": FIXTURE_HANDLES})
            result = _mock_research(project)
            run_dir = Path(result["run_dir"])
            outliers_doc = store.read_json(run_dir / "02-outliers.json")
            run_doc = store.read_json(run_dir / "run.json")
            self.assertTrue((run_dir / "transcripts" / "DWN003.txt").exists())
            self.assertTrue((run_dir / "transcripts" / "HAB001.txt").exists())

        # 0.4.0 soft cap: select_outliers now selects 11 fixture reels
        # instead of 9 (see tests/test_research.py's
        # test_mock_research_writes_01_02_and_run_json), so 9 of them
        # (not 7) get transcript_status "none".
        self.assertEqual(result["transcripts"], {"ok": 2, "apify": 0, "none": 9, "failed": 0})
        self.assertEqual(run_doc["stages"]["research"]["transcripts"], result["transcripts"])
        by_sc = {r["shortCode"]: r["transcript_status"] for r in outliers_doc["selected"]}
        self.assertEqual(by_sc["DWN003"], "ok")
        self.assertEqual(by_sc["HAB001"], "ok")
        self.assertEqual(by_sc["DWN006"], "none")

    def test_no_download_leaves_transcripts_pending_and_none(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": FIXTURE_HANDLES})
            result = _mock_research(project, no_download=True)
            outliers_doc = store.read_json(Path(result["run_dir"]) / "02-outliers.json")
        self.assertIsNone(result["transcripts"])
        for reel in outliers_doc["selected"]:
            self.assertEqual(reel["transcript_status"], "pending")

    def test_estimate_includes_transcripts_only_when_paid_path_planned(self) -> None:
        with temp_project() as project:
            _write_config(
                project,
                {
                    "competitors": ["sproutapp", "habitlab"],
                    "apify_transcripts": True,
                    "apify_transcript_usd_per_min": 0.02,
                },
            )
            cfg = store.load_config(project)
            for local, expected in ((False, 1.2), (True, 0.0)):
                with self.subTest(local=local):
                    with mock.patch.object(transcribe, "local_available", return_value=local):
                        with self.assertRaises(research.ConfirmationRequired) as ctx:
                            research.run_research(
                                project=project, cfg=cfg, keys=Keys(None, None, []), mock=True,
                                yes=False, estimate_only=False, resume=None, log=lambda _m: None,
                            )
                    payload = ctx.exception.payload
                    self.assertEqual(payload["transcripts_usd"], expected)
                    self.assertEqual(payload["total_usd"], round(0.1674 + expected, 4))

    def test_transcripts_cost_counts_against_the_cap(self) -> None:
        with temp_project() as project:
            _write_config(
                project,
                {
                    "competitors": ["sproutapp"],
                    "apify_transcripts": True,
                    "apify_transcript_usd_per_min": 0.02,
                    "apify_max_charge_usd": 1.0,
                },
            )
            cfg = store.load_config(project)
            with mock.patch.object(transcribe, "local_available", return_value=False):
                with self.assertRaises(research.CostCapExceeded) as ctx:
                    research.run_research(
                        project=project, cfg=cfg, keys=Keys(None, None, []), mock=True,
                        yes=True, estimate_only=False, resume=None, log=lambda _m: None,
                    )
        self.assertFalse(ctx.exception.payload["within_cap"])


class TranscribeCliTests(NoNetworkTestCase):
    def test_transcribe_subcommand_backfills_a_run(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": FIXTURE_HANDLES})
            result = _mock_research(project)
            run_dir = Path(result["run_dir"])
            (run_dir / "transcripts" / "DWN003.txt").unlink()

            code, out, err = run_cli(
                ["transcribe", "--run", "latest", "--mock", "--project", str(project)],
                env=NO_GLOBAL_ENV,
            )
            self.assertEqual(code, 0, err)
            self.assertTrue((run_dir / "transcripts" / "DWN003.txt").exists())
        payload = json.loads(out)
        self.assertEqual(payload["run_id"], run_dir.name)
        # 0.4.0 soft cap: see the comment in
        # test_mock_research_transcribes_selected_reels.
        self.assertEqual(payload["transcripts"], {"ok": 2, "apify": 0, "none": 9, "failed": 0})

    def test_transcribe_unresolvable_run_exits_2(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["sproutapp"]})
            code, out, err = run_cli(
                ["transcribe", "--run", "nope", "--mock", "--project", str(project)],
                env=NO_GLOBAL_ENV,
            )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())

    def test_transcribe_missing_outliers_exits_2(self) -> None:
        with temp_project() as project:
            _write_config(project, {"competitors": ["sproutapp"]})
            cfg = store.load_config(project)
            run_dir = store.init_run(project, cfg, "mock")
            code, _out, err = run_cli(
                ["transcribe", "--run", run_dir.name, "--mock", "--project", str(project)],
                env=NO_GLOBAL_ENV,
            )
        self.assertEqual(code, 2)
        self.assertIn("02-outliers.json", err)


class TranscribeSpendGuardTests(NoNetworkTestCase):
    """A paid Apify transcript run asks first, exactly like research does."""

    def _run(self, project: Path, extra_args: list) -> tuple:
        import contentos
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = contentos.main(["transcribe", "--run", "latest", "--project", str(project)] + extra_args)
        return code, out.getvalue(), err.getvalue()

    def _paid_project(self, project: Path) -> None:
        _write_config(
            project,
            {
                "competitors": FIXTURE_HANDLES,
                "apify_transcripts": True,
                "apify_transcript_usd_per_min": 0.02,
            },
        )
        _mock_research(project)

    def test_paid_backend_without_yes_prints_the_estimate_and_exits_3(self) -> None:
        with temp_project() as project:
            self._paid_project(project)
            with mock.patch.object(transcribe, "local_available", return_value=False), \
                    mock.patch.object(transcribe, "run_transcribe") as run:
                code, out, _err = self._run(project, [])
        self.assertEqual(code, 3)
        run.assert_not_called()
        self.assertGreater(json.loads(out)["transcripts_usd"], 0)

    def test_paid_backend_with_yes_runs(self) -> None:
        with temp_project() as project:
            self._paid_project(project)
            with mock.patch.object(transcribe, "local_available", return_value=False), \
                    mock.patch.object(transcribe, "run_transcribe", return_value={"run_id": "x"}) as run:
                code, _out, _err = self._run(project, ["--yes"])
        self.assertEqual(code, 0)
        run.assert_called_once()

    def test_free_backends_never_ask(self) -> None:
        with temp_project() as project:
            self._paid_project(project)
            with mock.patch.object(transcribe, "local_available", return_value=True), \
                    mock.patch.object(transcribe, "run_transcribe", return_value={"run_id": "x"}) as run:
                code, _out, _err = self._run(project, [])
        self.assertEqual(code, 0)
        run.assert_called_once()


class DiagnoseWhisperTests(NoNetworkTestCase):
    def test_diagnose_reports_whisper_and_model(self) -> None:
        from lib import env

        with temp_project() as project:
            model = project / "m.bin"
            model.write_bytes(b"ggml")
            environ = {"CONTENTOS_CONFIG_DIR": "", "CONTENTOS_WHISPER_MODEL": str(model)}
            with mock.patch("shutil.which", _which(["whisper-cpp"])):
                result = env.diagnose(project, environ=environ)
            self.assertIs(result["whisper"], True)
            self.assertEqual(result["whisper_model"], str(model))

            with mock.patch("shutil.which", _which([])):
                result = env.diagnose(project, environ={"CONTENTOS_CONFIG_DIR": ""})
            self.assertIs(result["whisper"], False)
            self.assertIsNone(result["whisper_model"])

    def test_diagnose_uses_the_config_model_path(self) -> None:
        from lib import env

        with temp_project() as project:
            model = project / "cfg-model.bin"
            model.write_bytes(b"ggml")
            _write_config(project, {"competitors": ["a"], "whisper_model": str(model)})
            result = env.diagnose(project, environ={"CONTENTOS_CONFIG_DIR": ""})
        self.assertEqual(result["whisper_model"], str(model))
