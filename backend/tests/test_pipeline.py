# test_pipeline.py — execute_pipeline end to end on a generated clip: cp1252 sidecar → cues → translation → MKV, and a failed language. Exports: none (pytest).
"""Runs the shipped orchestrator with real ffmpeg; only the translation backend is a fake (no model, no key)."""

import asyncio
import json
import shutil
import subprocess

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("faster_whisper")
if not shutil.which("ffmpeg"):
    pytest.skip("ffmpeg not on PATH", allow_module_level=True)

import main  # noqa: E402
from core.events import event_manager  # noqa: E402
from core.provenance import is_substudio_track  # noqa: E402
from core.gate import Verdict  # noqa: E402
PASS = Verdict(True)

# Cue 3 starts after the 3-second clip ends and must not reach the MKV.
SIDECAR = ("1\n00:00:00,500 --> 00:00:01,200\nBonjour Hélène.\n\n"
           "2\n00:00:01,500 --> 00:00:02,400\nÇa va très bien, merci beaucoup pour cette soirée formidable.\n\n"
           "3\n00:00:05,000 --> 00:00:06,000\nTrop tard.\n")


class Upper:
    name, batch_size = "upper", 30

    def __init__(self):
        self.unloads = 0

    def translate(self, cues, target, context):
        return [{"id": c["id"], "text": f"[{target}] {c['text'].upper()}"} for c in reversed(cues)]

    def unload(self):
        self.unloads += 1


class DropsOne(Upper):
    def translate(self, cues, target, context):
        return super().translate(cues, target, context)[1:]


@pytest.fixture
def clip(tmp_path):
    video = tmp_path / "Mon film été.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video)],
                   check=True)
    video.with_suffix(".srt").write_bytes(SIDECAR.encode("cp1252"))
    return video


def run(video, translator, out=("fr",)):
    orch = main.PipelineOrchestrator()
    # The clips carry a tone, not speech: these tests check the cells' plumbing; core/gate has its own tests.
    orch.gate = lambda *a, **k: PASS
    orch.translator = translator
    job = main.VideoJob(name=video.name, path=str(video), out=list(out), workflowMode="hybrid", syncOffset=0.25)
    opts = main.GlobalOptions()
    asyncio.run(orch.execute_pipeline(job, opts, 0, 1))
    return event_manager.state_cache[str(video)]


def test_sidecar_to_mkv_keeps_timings_accents_and_clips_past_the_end(clip):
    translator = Upper()
    status = run(clip, translator)
    assert status["status"] == "done" and "FR ✓" in status["message"], status
    assert translator.unloads == 1  # the model is freed before the next video's transcription
    out = clip.parent / f"{clip.stem} (Sub-FR).mkv"                              # updated in place, renamed
    assert out.is_file() and not clip.exists()
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                       "-select_streams", "s", str(out)], capture_output=True, text=True).stdout)
    assert [s["tags"].get("language") for s in probe["streams"]] == ["fre"]
    title = probe["streams"][0]["tags"].get("title")
    assert {k.upper(): v for k, v in probe["streams"][0]["tags"].items()}["SUBSTUDIO_ORIGIN"] == "translated"
    assert title == "SubStudio fr" and is_substudio_track(kind="embedded", title=title)
    track = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:s:0", "-f", "srt", "-"],
                           capture_output=True).stdout.decode("utf-8").replace("\r\n", "\n")
    assert "00:00:00,750 --> 00:00:01,450\n[fr] BONJOUR HÉLÈNE." in track          # +0.25 s sync, accents kept
    assert "00:00:01,750 --> 00:00:02,650\n" in track
    assert "TROP TARD" not in track                                               # starts after the clip ends
    assert all(len(l) <= 50 for l in track.splitlines())                         # lines wrapped before writing
    assert not clip.with_suffix(".fr.srt").exists()                               # no sidecar copy (D-0002 §4)


class FrenchBroken(Upper):
    def translate(self, cues, target, context):
        out = super().translate(cues, target, context)
        return out[1:] if target == "fr" else out


def test_a_language_that_cannot_be_translated_fails_the_video_and_writes_nothing(clip):
    translator = DropsOne()
    status = run(clip, translator)
    assert status["status"] == "error" and "fr:" in status["message"], status
    assert translator.unloads == 1  # freed on failure too
    assert sorted(p.name for p in clip.parent.iterdir()) == [clip.name, clip.with_suffix(".srt").name]
    assert clip.read_bytes()                                                      # nothing passed: untouched


def test_a_language_that_cannot_be_named_fails_before_any_work(clip):
    # « fre » is no longer invalid: the planner reads it as French (ISO 639-2/B), like any MKV tag. A code that
    # names no language still fails the video before a single cue is translated.
    called = []
    class Spy(Upper):
        def translate(self, cues, target, context):
            called.append(target)
            return super().translate(cues, target, context)
    status = run(clip, Spy(), out=("xx",))
    assert status["status"] == "error" and "xx" in status["message"], status
    assert called == []


def test_one_failed_language_does_not_fail_the_others(clip):
    # D-0002: one verdict per video × language. EN passes and ships; FR fails alone, with its reason.
    status = run(clip, FrenchBroken(), out=("en", "fr"))
    assert status["status"] == "error", status
    assert "EN ✓ translated from" in status["message"] and "FR failed: fr:" in status["message"]
    assert not list(clip.parent.glob("*.??.srt"))                                  # no sidecar copies
    out = clip.parent / f"{clip.stem} (Sub-EN).mkv"                               # the passed language only
    assert out.is_file() and not clip.exists()
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                       "-select_streams", "s", str(out)], capture_output=True, text=True).stdout)
    assert [s["tags"].get("language") for s in probe["streams"]] == ["eng"]


@pytest.mark.parametrize("probability, tag", [(0.95, "fre"), (0.5, None)])
def test_ov_detected_with_confidence_tags_the_untagged_audio(clip, probability, tag):
    from types import SimpleNamespace
    orch = main.PipelineOrchestrator()
    orch.gate = lambda *a, **k: PASS
    orch.translator = Upper()
    # The clip's audio is an untagged tone: OV is « detected » as French, as Whisper would on real speech.
    orch.transcriber = SimpleNamespace(detect_video_language=lambda path, job=None: ("fr", probability))
    job = main.VideoJob(name=clip.name, path=str(clip), out=["ov", "en"], workflowMode="hybrid", syncOffset=0.25)
    asyncio.run(orch.execute_pipeline(job, main.GlobalOptions(), 0, 1))
    out = orch.runs[str(clip)].output_path
    assert out, "the en translation should have been written"
    audio = [s for s in json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                                   out], capture_output=True).stdout)["streams"]
             if s["codec_type"] == "audio"][0]
    assert audio.get("tags", {}).get("language", None) in ((tag,) if tag else (None, "und"))
