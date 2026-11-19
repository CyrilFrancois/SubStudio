# test_cells.py — one verdict per video × language (D-0002): origins, same-language kept, retry of one cell, cleanUp guard, cell frames. Exports: none (pytest).

import asyncio
import shutil
import subprocess

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("faster_whisper")
if not shutil.which("ffmpeg"):
    pytest.skip("ffmpeg not on PATH", allow_module_level=True)

import main  # noqa: E402
from core.acquire.types import Rung  # noqa: E402
from core.cells import Cell, CellState, Origin, Source, produce_cell  # noqa: E402
from core.events import event_manager  # noqa: E402
from core.subtitles import Cue, TranslationContext, detect_language  # noqa: E402

# Enough English for language detection (20+ words).
ENGLISH = [
    "Good morning, everyone, and welcome to the house.",
    "I think the ghosts are hiding something from us.",
    "We need to talk about what happened last night in the kitchen.",
    "Nobody leaves this room until we find out the truth.",
]
SIDECAR_EN = "".join(f"{i}\n00:00:0{i - 1},000 --> 00:00:0{i - 1},900\n{t}\n\n" for i, t in enumerate(ENGLISH, start=1))


class Tag:
    name, batch_size = "tag", 30

    def __init__(self, broken=()):
        self.broken = set(broken)
        self.calls = []

    def translate(self, cues, target, context):
        self.calls.append(target)
        out = [{"id": c["id"], "text": f"[{target}] {c['text']}"} for c in cues]
        return out[1:] if target in self.broken else out


@pytest.fixture
def clip(tmp_path):
    video = tmp_path / "episode.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=5",
                    "-f", "lavfi", "-i", "sine=duration=5", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video)],
                   check=True)
    video.with_suffix(".srt").write_text(SIDECAR_EN, encoding="utf-8")
    return video


def frames(video):
    seen = []
    original = event_manager.emit_cells
    event_manager.emit_cells = lambda fid, cells: seen.append([dict(c) for c in cells]) if fid == str(video) else None
    return seen, lambda: setattr(event_manager, "emit_cells", original)


def process(orch, video, out, clean_up=False):
    job = main.VideoJob(name=video.name, path=str(video), out=list(out), workflowMode="hybrid")
    opts = main.GlobalOptions(generateSRT=True, muxIntoMkv=True, cleanUp=clean_up)
    asyncio.run(orch.execute_pipeline(job, opts, 0, 1))
    return job, opts


def orchestrator(translator):
    orch = main.PipelineOrchestrator()
    orch.translator = translator
    orch.translator.get_context_profile = lambda name: ""
    return orch


def test_each_language_gets_its_own_verdict_and_origin(clip):
    seen, restore = frames(clip)
    try:
        orch = orchestrator(Tag(broken={"fr"}))
        process(orch, clip, ("en", "de", "fr"))
    finally:
        restore()
    cells = {c["language"]: c for c in orch.runs[str(clip)].to_json()}
    assert cells["en"] == {"language": "en", "state": "passed", "origin": "kept", "from_language": None,
                           "origin_label": "kept", "rung": "existing", "reason": "", "attempts": []}
    assert cells["de"]["state"] == "passed" and cells["de"]["origin_label"] == "translated from en"
    assert (cells["de"]["origin"], cells["de"]["from_language"]) == ("translated", "en")
    assert cells["fr"]["state"] == "failed" and cells["fr"]["reason"].startswith("fr: tag cannot translate cue")
    assert orch.translator.calls.count("en") == 0          # already English: kept, never sent to a model
    assert [c["state"] for c in seen[0]] == ["pending"] * 3 and seen[-1] == list(cells.values())
    status = event_manager.state_cache[str(clip)]
    assert status["status"] == "error" and "EN ✓ kept" in status["message"] and "FR failed" in status["message"]


def test_retry_one_cell_reuses_the_source_and_rebuilds_the_output(clip):
    orch = orchestrator(Tag(broken={"fr"}))
    job, opts = process(orch, clip, ("de", "fr"))
    assert not clip.with_suffix(".fr.srt").exists()
    orch.translator.broken.clear()
    orch.translator.calls.clear()
    asyncio.run(orch.retry_cell(job, "fr", opts))
    assert orch.translator.calls == ["fr"]                 # only the failed cell is redone
    assert orch.runs[str(clip)].all_passed()
    assert "[fr] Good morning" in clip.with_suffix(".fr.srt").read_text(encoding="utf-8")
    out = clip.parent / f"{clip.stem}_SubStudio.mkv"
    tags = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream_tags=language",
                           "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    assert tags == ["deu", "fra"]
    assert event_manager.state_cache[str(clip)]["status"] == "done"


def test_clean_up_never_deletes_the_source_while_a_language_failed(clip):
    # The source is what a retry starts from. (Temp clip only: never cleanUp on real media.)
    process(orchestrator(Tag(broken={"fr"})), clip, ("de", "fr"), clean_up=True)
    assert clip.exists()


def test_a_transcribed_source_in_the_wanted_language_is_transcribed_not_translated():
    cues = [Cue(id=i + 1, start_ms=i * 1000, end_ms=i * 1000 + 900, text=t) for i, t in enumerate(ENGLISH)]
    assert detect_language(cues) == "en"
    source = Source(cues=cues, rung=Rung.ASR, language="en", context=TranslationContext())
    same, other = Cell("en"), Cell("de")
    produce_cell(same, source, Tag())
    produce_cell(other, source, Tag())
    assert (same.state, same.origin.label()) == (CellState.PASSED, "transcribed")
    assert other.origin == Origin(Rung.ASR, "en") and other.origin.label() == "transcribed, translated from en"


def test_too_little_text_has_no_detected_language():
    assert detect_language([Cue(id=1, start_ms=0, end_ms=500, text="Hi!")]) is None


def test_retry_and_cells_routes_refuse_a_video_never_processed():
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        body = {"video": {"name": "x.mkv", "path": "/data/never.mkv"}, "language": "fr", "globalOptions": {}}
        assert client.post("/api/retry", json=body).status_code == 404
        assert client.get("/api/cells//data/never.mkv").status_code == 404


def test_retry_is_refused_while_another_job_runs_or_when_the_source_is_gone(clip):
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        orch = main.orchestrator
        orch.translator = Tag(broken={"fr"})
        orch.translator.get_context_profile = lambda name: ""
        process(orch, clip, ("de", "fr"))
        body = {"video": {"name": clip.name, "path": str(clip)}, "language": "fr", "globalOptions": {}}
        orch.active_jobs.add("another video")
        assert client.post("/api/retry", json=body).status_code == 409
        orch.active_jobs.discard("another video")
        moved = clip.with_suffix(".moved")
        clip.rename(moved)
        assert client.post("/api/retry", json=body).status_code == 409
        moved.rename(clip)
        assert client.get(f"/api/cells/{clip}").json()["cells"][1]["state"] == "failed"


def test_an_abort_mid_cell_ends_it_failed_not_running():
    from core.jobs import JobAborted, JobHandle
    cues = [Cue(id=i + 1, start_ms=i * 1000, end_ms=i * 1000 + 900, text=t) for i, t in enumerate(ENGLISH)]
    from core.acquire.types import LanguagePlan, Step
    from core.ladder import Ladder, LadderDeps
    run = main.VideoRun(path="v", cells={"de": Cell("de")},
                        plans={"de": LanguagePlan("de", [Step(Rung.TRANSLATE, "de", from_languages=("en",))])},
                        human={"en": Source(cues=cues, rung=Rung.EXISTING, language="en", context=TranslationContext())})
    handle = JobHandle("v")

    class AbortsDuringFirstCall(Tag):
        batch_size = 1
        def translate(self, cues, target, context):
            handle.abort()
            return super().translate(cues, target, context)

    orch = orchestrator(AbortsDuringFirstCall())
    ladder = Ladder(run, LadderDeps(translator=orch.translator, transcribe=lambda: ([], None),
                                    load_embedded=lambda i: [], check_abort=handle.raise_if_aborted))
    with pytest.raises(JobAborted):
        asyncio.run(orch._run_cell(run, ladder, "de", handle, "[t]"))
    assert (run.cells["de"].state, run.cells["de"].reason) == (CellState.FAILED, "cancelled by the user")


def test_a_transcription_uses_the_language_whisper_heard(tmp_path):
    # Two words are far too little for text detection; Whisper's own 0.97 decides: EN kept as « transcribed ».
    from core.asr_result import AsrResult, AsrSegment, AsrWord
    video = tmp_path / "short.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video)],
                   check=True)
    words = [AsrWord(0.5, 0.9, "Hello", 0.9), AsrWord(1.0, 1.4, "there.", 0.9)]
    asr = AsrResult(model="fake", language="en", language_probability=0.97, duration=3.0, speech=[(0.5, 1.4)],
                    segments=[AsrSegment(0.5, 1.4, "Hello there.", -0.2, 0.01, 1.1, words)])
    orch = orchestrator(Tag())
    orch.transcriber.transcribe = lambda **kw: asr
    job = main.VideoJob(name=video.name, path=str(video), out=["en", "de"], workflowMode="whisper")
    asyncio.run(orch.execute_pipeline(job, main.GlobalOptions(generateSRT=True, muxIntoMkv=False), 0, 1))
    cells = {c["language"]: c for c in orch.runs[str(video)].to_json()}
    assert cells["en"]["origin"] == "transcribed" and orch.translator.calls == ["de"]
    assert cells["de"]["origin_label"] == "transcribed, translated from en"
    assert (cells["en"]["origin"], cells["de"]["origin"], cells["de"]["from_language"]) == ("transcribed", "translated", "en")
