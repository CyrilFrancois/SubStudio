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
    orch.translator = translator
    orch.translator.get_context_profile = lambda name: "notes"
    job = main.VideoJob(name=video.name, path=str(video), out=list(out), workflowMode="hybrid", syncOffset=0.25)
    opts = main.GlobalOptions(generateSRT=True, muxIntoMkv=True, cleanUp=False)
    asyncio.run(orch.execute_pipeline(job, opts, 0, 1))
    return event_manager.state_cache[str(video)]


def test_sidecar_to_mkv_keeps_timings_accents_and_clips_past_the_end(clip):
    translator = Upper()
    status = run(clip, translator)
    assert status["status"] == "done" and "FR ✓" in status["message"], status
    assert translator.unloads == 1  # the model is freed before the next video's transcription
    out = clip.parent / f"{clip.stem}_SubStudio.mkv"
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                       "-select_streams", "s", str(out)], capture_output=True, text=True).stdout)
    assert [s["tags"].get("language") for s in probe["streams"]] == ["fra"]
    title = probe["streams"][0]["tags"].get("title")
    assert title == "SubStudio fr" and is_substudio_track(kind="embedded", title=title)
    track = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:s:0", "-f", "srt", "-"],
                           capture_output=True).stdout.decode("utf-8").replace("\r\n", "\n")
    assert "00:00:00,750 --> 00:00:01,450\n[fr] BONJOUR HÉLÈNE." in track          # +0.25 s sync, accents kept
    assert "00:00:01,750 --> 00:00:02,650\n" in track
    assert "TROP TARD" not in track                                               # starts after the clip ends
    sidecar_out = clip.with_suffix(".fr.srt").read_text(encoding="utf-8")
    assert "TROP TARD" in sidecar_out and all(len(l) <= 50 for l in sidecar_out.splitlines())


class FrenchBroken(Upper):
    def translate(self, cues, target, context):
        out = super().translate(cues, target, context)
        return out[1:] if target == "fr" else out


def test_a_language_that_cannot_be_translated_fails_the_video_and_writes_nothing(clip):
    translator = DropsOne()
    status = run(clip, translator)
    assert status["status"] == "error" and "fr:" in status["message"], status
    assert translator.unloads == 1  # freed on failure too
    assert not (clip.parent / f"{clip.stem}_SubStudio.mkv").exists()
    assert not clip.with_suffix(".fr.srt").exists()


def test_a_language_the_muxer_cannot_title_fails_before_any_work(clip):
    called = []
    class Spy(Upper):
        def translate(self, cues, target, context):
            called.append(target)
            return super().translate(cues, target, context)
    status = run(clip, Spy(), out=("fre",))
    assert status["status"] == "error" and "fre" in status["message"], status
    assert called == []


def test_one_failed_language_does_not_fail_the_others(clip):
    # D-0002: one verdict per video × language. EN passes and ships; FR fails alone, with its reason.
    status = run(clip, FrenchBroken(), out=("en", "fr"))
    assert status["status"] == "error", status
    assert "EN ✓ translated from" in status["message"] and "FR failed: fr:" in status["message"]
    assert clip.with_suffix(".en.srt").exists() and not clip.with_suffix(".fr.srt").exists()
    out = clip.parent / f"{clip.stem}_SubStudio.mkv"
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                       "-select_streams", "s", str(out)], capture_output=True, text=True).stdout)
    assert [s["tags"].get("language") for s in probe["streams"]] == ["eng"]
