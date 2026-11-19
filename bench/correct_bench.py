# correct_bench.py — measures rung 4's OV correction pass on a bench clip: WER and chrF against a human reference (a gate-passed track of the source episode, cut to the clip), and the gate, before and after. Exports: none (script).
"""
Usage (backend venv, ffmpeg on PATH, the `heavy` lease held: it loads Whisper):
    python -m bench.correct_bench <clip_dir> <episode video | full-episode subtitle> [--track N] [--min-logprob X ...]
<clip_dir> holds clip.mkv + clip.json (start_s, duration_s, video_name); <source_video> is the full episode whose
embedded text track N (default 0) passed the gate and serves as the reference, cut to the clip's window.
--min-logprob lowers Whisper's own -1.0 doubt flag to exercise the re-hear path on a clip turbo is sure of; the
report then says the flags were lowered, so the result is never read as the production setting's.
Writes nothing beside the media: the reference is extracted to a temp folder.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "backend")]

from core.context import get_context  # noqa: E402
from core.correct import MIN_AVG_LOGPROB, correct_ov, flagged_spans  # noqa: E402
from core.gate import THRESHOLDS, detect_speech, evaluate, read_audio  # noqa: E402
from core.gate.speech import SAMPLE_RATE  # noqa: E402
from core.subtitles import Cue, load_file  # noqa: E402
from core.transcriber import VideoTranscriber  # noqa: E402
from bench.score import score  # noqa: E402


def _words(cues):
    import re
    return re.findall(r"[a-z0-9']+", " ".join(c.text for c in cues).lower())


def wer(hyp, ref) -> float:
    """Word error rate (Levenshtein over words / reference length)."""
    h, r = _words(hyp), _words(ref)
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / max(len(r), 1)


def reference(source: str, track: int, start_s: float, duration_s: float):
    """The human reference cut to the clip's window: `source` is the full episode (its embedded text track `track`)
    or a ready subtitle file covering the whole episode (e.g. a gate-passed download)."""
    if source.lower().endswith((".srt", ".ass", ".ssa", ".vtt")):
        full = load_file(source)
    else:
        out = Path(tempfile.mkdtemp(prefix="substudio-ref-")) / "ref.srt"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-map", f"0:s:{track}", "-c:s", "srt", str(out)],
                       check=True)
        full = load_file(out)
    a, b = start_s * 1000, (start_s + duration_s) * 1000
    return [Cue(c.id, int(c.start_ms - a), int(c.end_ms - a), c.text) for c in full
            if c.start_ms >= a and c.end_ms <= b]


def main(clip_dir: str, source: str, track: int = 0, settings=(MIN_AVG_LOGPROB,)) -> None:
    """One transcription of the clip, then the correction pass once per doubt setting (each row labelled)."""
    meta = json.loads((Path(clip_dir) / "clip.json").read_text(encoding="utf-8"))
    clip = str(Path(clip_dir) / "clip.mkv")
    ref = reference(source, track, meta["start_s"], meta["duration_s"])
    audio = read_audio(clip)
    speech, dur_ms = detect_speech(audio), int(len(audio) * 1000 / SAMPLE_RATE)
    tr = VideoTranscriber()
    t0 = time.time()
    asr = tr.transcribe(video_path=clip, file_id=clip, on_progress=lambda *a: None, current_file=1, total_files=1)
    t_first = time.time() - t0
    glossary = get_context(meta["video_name"], asr.to_cues()).glossary

    def rerun(spans):
        return tr.transcribe(video_path=clip, file_id=clip, on_progress=lambda *a: None, current_file=1,
                             total_files=1, language=asr.language, spans=spans)

    judge = lambda cues: evaluate(cues, speech, asr.language, duration_ms=dur_ms)
    first = asr.to_cues()
    v0 = judge(first)
    base = {"wer": round(wer(first, ref), 4), "chrf": score(first, ref).get("chrf_window"),
            "gate": v0.passed, "gate_reason": v0.reason}
    for min_logprob in settings:
        t1 = time.time()
        corrected, note = correct_ov(asr, rerun, glossary, judge, THRESHOLDS["max_cps"], min_logprob=min_logprob)
        v = judge(corrected)
        print(json.dumps({
            "clip": meta["id"], "model": asr.model, "ref_lines": len(ref), "first_s": round(t_first, 1),
            "flags": "production" if min_logprob == MIN_AVG_LOGPROB else f"LOWERED for the bench: min_logprob {min_logprob}",
            "doubtful_spans": len(flagged_spans(asr, THRESHOLDS["max_cps"], min_logprob)),
            "correction_s": round(time.time() - t1, 1), "note": note, "first": base,
            "corrected": {"wer": round(wer(corrected, ref), 4), "chrf": score(corrected, ref).get("chrf_window"),
                          "gate": v.passed, "gate_reason": v.reason},
        }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("clip_dir")
    ap.add_argument("source", help="the full episode (its embedded track is the reference) or a full-episode subtitle")
    ap.add_argument("--track", type=int, default=0)
    ap.add_argument("--min-logprob", type=float, action="append",
                    help="doubt setting(s); repeat for several; default: production (-1.0)")
    a = ap.parse_args()
    main(a.clip_dir, a.source, a.track, tuple(a.min_logprob or [MIN_AVG_LOGPROB]))
