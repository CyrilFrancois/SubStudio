# test_artifacts.py — Finished work is never redone: a transcription is reused for the same audio (the swap included), translated lines survive a failed language. Exports: none (pytest).

import asyncio
import shutil
import subprocess

import pytest

from core.artifacts import ArtifactCache, artifacts, audio_fingerprint
from core.cells import Cell, CellState, Source
from core.acquire.types import Rung
from core.subtitles import BatchMismatch, Cue, TranslationContext, TranslationFailed, translate_cues

if not shutil.which("ffmpeg"):
    pytest.skip("ffmpeg not on PATH", allow_module_level=True)

LINES = [f"This is line number {i} of the episode, spoken aloud." for i in range(1, 13)]


def cues():
    return [Cue(id=i, start_ms=i * 1000, end_ms=i * 1000 + 800, text=t) for i, t in enumerate(LINES, start=1)]


class Model:
    """A translator that refuses some ids forever (like TranslateGemma on cue 272) and records what it was sent."""
    name, batch_size = "model", 4

    def __init__(self, refuse=()):
        self.refuse, self.sent = set(refuse), []

    def translate(self, batch, target, context):
        self.sent.append([c["id"] for c in batch])
        if any(c["id"] in self.refuse for c in batch):
            raise BatchMismatch("unnumbered line in the reply")
        return [{"id": c["id"], "text": f"[{target}] {c['text']}"} for c in batch]

    def unload(self):
        pass


def clip(path, freq=440, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=4"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration=4", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "mpeg4", "-shortest", str(path)], check=True)
    return path


# ---------------------------------------------------------------- the key of a transcription: the audio

def test_the_audio_fingerprint_survives_the_swap_and_tells_episodes_apart(tmp_path):
    from core.output import update_in_place
    a = clip(tmp_path / "Ep1.mkv")
    before = audio_fingerprint(str(a))
    out = update_in_place(a, {"fr": cues()[:2]}, ["fr"], origins={"fr": "translated"})
    assert out.path.name == "Ep1 (Sub-FR).mkv" and audio_fingerprint(str(out.path)) == before
    assert audio_fingerprint(str(clip(tmp_path / "Ep2.mkv", freq=880))) != before
    with pytest.raises(RuntimeError, match="no audio fingerprint"):
        audio_fingerprint(str(clip(tmp_path / "Mute.mkv", audio=False)))


# ---------------------------------------------------------------- translated lines

def test_a_language_that_fails_keeps_its_lines_and_the_rerun_sends_only_the_rest(tmp_path):
    cache = ArtifactCache(tmp_path)
    ctx = TranslationContext(source_lang="en")
    key = cache.lines_key(cues(), "fr", "model", ctx)
    first = Model(refuse={7})
    with pytest.raises(TranslationFailed, match="cue 7"):
        translate_cues(cues(), "fr", ctx, first, known=cache.load_lines(key),
                       on_texts=lambda t: cache.save_lines(key, t), retry_delay_s=0)
    kept = cache.load_lines(key)
    assert set(kept) == {1, 2, 3, 4, 5, 6}                             # everything before the bad line

    second = Model()
    done = translate_cues(cues(), "fr", ctx, second, known=cache.load_lines(key),
                          on_texts=lambda t: cache.save_lines(key, t), retry_delay_s=0)
    assert second.sent == [[7, 8, 9, 10], [11, 12]]                    # only the missing lines
    assert [c.text for c in done] == [f"[fr] {t}" for t in LINES]
    assert [(c.id, c.start_ms) for c in done] == [(c.id, c.start_ms) for c in cues()]


def test_lines_are_keyed_by_source_target_translator_and_episode_not_by_batch(tmp_path):
    k = ArtifactCache.lines_key
    ctx = TranslationContext(source_lang="en", series="Ghosts")
    base = k(cues(), "fr", "model", ctx)
    from dataclasses import replace
    assert k(cues(), "fr", "model", replace(ctx, previous=("a", "b"))) == base
    assert base != k(cues(), "de", "model", ctx)
    assert base != k(cues(), "fr", "other-model", ctx)
    assert base != k(cues(), "fr", "model", replace(ctx, series="Other"))
    assert base != k(cues()[:-1], "fr", "model", ctx)


def test_produce_cell_resumes_from_the_cache(tmp_path):
    from core.cells import produce_cell
    cache = ArtifactCache(tmp_path)
    source = Source(cues=cues(), rung=Rung.EXISTING, language="en", context=TranslationContext(source_lang="en"))
    cell = Cell("fr")
    produce_cell(cell, source, Model(refuse={12}), cache=cache)
    assert cell.state == CellState.FAILED and "cue 12" in cell.reason
    again, model = Cell("fr"), Model()
    produce_cell(again, source, model, cache=cache)
    assert again.state == CellState.PASSED and model.sent == [[12]]


def test_an_unwritable_cache_never_fails_the_work(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = ArtifactCache(blocker)                                     # a file where the folder should be
    cache.save_lines("ab" * 32, {1: "x"})                              # logged, not raised
    assert cache.load_lines("ab" * 32) == {}


# ---------------------------------------------------------------- the pipeline never transcribes the same audio twice

def test_a_rerun_reuses_the_transcription(tmp_path):
    pytest.importorskip("fastapi")
    import main
    from core.gate import Verdict

    video = clip(tmp_path / "episode.mkv")
    heard = []

    class Asr:
        language, language_probability = "en", 1.0

        def to_cues(self):
            return cues()

    def transcribe(**kw):
        heard.append(kw["video_path"])
        return Asr()

    orch = main.PipelineOrchestrator()
    orch.translator = Model()
    orch.transcriber.transcribe = transcribe
    orch.gate = lambda *a, **k: Verdict(False)                         # first run: nothing passes, nothing written
    job = main.VideoJob(name=video.name, path=str(video), out=["en"], workflowMode="hybrid")
    asyncio.run(orch.execute_pipeline(job, main.GlobalOptions(), 0, 1))
    assert len(heard) == 1 and video.is_file()

    orch.gate = lambda *a, **k: Verdict(True)                          # the rerun: same audio, same model
    asyncio.run(orch.execute_pipeline(job, main.GlobalOptions(), 0, 1))
    assert len(heard) == 1                                             # never transcribed again
    assert (tmp_path / "episode (Sub-EN).mkv").is_file()
    assert artifacts().load_asr(artifacts().asr_key(audio_fingerprint(str(tmp_path / "episode (Sub-EN).mkv")),
                                                    main.GlobalOptions().transcriptionEngine))
