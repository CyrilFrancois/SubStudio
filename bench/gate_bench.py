# gate_bench.py — Proves the quality gate both ways on real tracks: accepts what ffsubsync measures in sync, rejects seeded bad copies. Exports: seeded_copies, main.
"""For every text subtitle track inside the corpus videos (read only):
  1. the gate's verdict (backend/core/gate) against the video's detected speech;
  2. an independent measure: ffsubsync's offset (core/acquire/align, WebRTC VAD, not the gate's Silero);
  3. for each track ffsubsync calls in sync, seeded bad copies, each of which the gate must reject for its reason.
Rows go to bench/results/gate.jsonl; speech spans are cached per video in bench/gate_work/speech/.

Usage: python -m bench.gate_bench "C:/Videos/Ghosts/Saison 5" [--only s05e07] [--no-oracle]
"""
import argparse
import datetime as dt
import json
import sys
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))  # the gate is backend code: import it as main.py does

from core.acquire.align import align  # noqa: E402
from core.gate import detect_speech, evaluate, read_audio  # noqa: E402
from core.subtitles import Cue, load_file, to_srt  # noqa: E402

from bench.media import ffmpeg, probe  # noqa: E402

WORK = ROOT / "bench" / "gate_work"
OUT = ROOT / "bench" / "results" / "gate.jsonl"
ORACLE_SYNC_S = 0.5   # same limit as the gate's offset and drift checks
THIRDS = ((0, 1 / 3), (2 / 3, 1))  # a whole-file offset hides a mid-episode jump (S05E21): measure both ends too


def _shift(cues, ms):
    return [replace(c, start_ms=max(0, c.start_ms + ms), end_ms=max(0, c.end_ms + ms)) for c in cues]


def _stretch(cues, factor):
    return [replace(c, start_ms=round(c.start_ms * factor), end_ms=round(c.end_ms * factor)) for c in cues]


def seeded_copies(cues: list[Cue]) -> dict[str, tuple[list[Cue], str, str | None, str]]:
    """name -> (cues, language wanted, tag the track carries, the check that must fail), from an English track that
    passed. Each is a fault seen in real downloads."""
    end = cues[-1].end_ms
    mojibake = [replace(c, text=c.text.replace("'", "’").encode("utf-8").decode("cp1252", "replace")) for c in cues]
    return {
        "shift+3s": (_shift(cues, 3000), "en", "en", "offset_ms"),
        "shift-1.5s": (_shift(cues, -1500), "en", "en", "offset_ms"),
        "framerate-25/23.976": (_stretch(cues, 25 / 23.976), "en", "en", "drift_ms"),
        "truncated-60%": ([c for c in cues if c.end_ms <= end * 0.6], "en", "en", "speech_covered"),
        "labelled-french": (cues, "en", "fr", "language"),
        "english-for-french": (cues, "fr", None, "language"),
        "mojibake": (mojibake, "en", "en", "broken_chars"),
        "durations-halved": ([replace(c, end_ms=c.start_ms + (c.end_ms - c.start_ms) // 2) for c in cues], "en",
                             "en", "within_cps"),
    }


def _speech(video: Path, key: str) -> tuple[list, float]:
    cache = WORK / "speech" / f"{key}.json"
    if cache.exists():
        return [tuple(s) for s in json.loads(cache.read_text())], 0.0
    t0 = time.monotonic()
    spans = detect_speech(read_audio(str(video)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(spans))
    return spans, round(time.monotonic() - t0, 1)


def _tracks(corpus: Path, only: str | None):
    for video in sorted(corpus.glob("*.mkv")):
        key = "s05" + video.name.lower().split("s05")[1][:3]
        if only and key != only:
            continue
        info = probe(video)
        subs = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
        for k, s in enumerate(subs):
            if s["codec_name"] in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"):
                continue
            # Copied in its own format (ASS stays ASS: styles, {\tags}, \N) and read by the backend's own reader,
            # as the pipeline will; a conversion to SRT first would judge ffmpeg's rendering instead.
            ext = {"ass": ".ass", "ssa": ".ssa", "subrip": ".srt", "webvtt": ".vtt", "mov_text": ".srt"}[s["codec_name"]]
            srt = WORK / "tracks" / f"{key}.t{k}{ext}"
            if not srt.exists():
                srt.parent.mkdir(parents=True, exist_ok=True)
                codec = "srt" if s["codec_name"] == "mov_text" else "copy"
                ffmpeg("-i", str(video), "-map", f"0:s:{k}", "-c:s", codec, str(srt))
            yield video, key, k, s, srt, int(float(info["format"]["duration"]) * 1000)


def _episode_wav(video: Path, key: str) -> Path:
    # ffsubsync given a VIDEO first aligns to the longest subtitle track inside it (its default `subs_then_webrtc`),
    # which here is the very track being judged: it read 0.0 on every track (reviewer, 2026-11-19). Audio only.
    wav = WORK / "audio" / f"{key}.wav"
    if not wav.exists():
        wav.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg("-threads", "1", "-i", str(video), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", str(wav))
    return wav


def _oracle(wav: Path, cues: list[Cue], track: Path, duration_ms: int) -> dict:
    """ffsubsync (WebRTC VAD) against the audio alone, on the whole file and on its first and last thirds:
    independent of the gate's Silero and of every subtitle track inside the video."""
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        r = align(str(wav), str(track), str(Path(tmp) / "out.srt"))
        out["oracle_offset_s"], out["oracle_confident"] = r.offset_seconds, r.synced
        ends = []
        for a, b in THIRDS:
            a_ms, b_ms = int(duration_ms * a), int(duration_ms * b)
            part_wav, part = Path(tmp) / f"{a_ms}.wav", Path(tmp) / f"{a_ms}.srt"
            ffmpeg("-ss", str(a_ms / 1000), "-t", str((b_ms - a_ms) / 1000), "-i", str(wav), "-c", "copy", str(part_wav))
            part.write_text(to_srt([replace(c, start_ms=c.start_ms - a_ms, end_ms=c.end_ms - a_ms)
                                    for c in cues if a_ms <= c.start_ms < b_ms]), encoding="utf-8")
            rp = align(str(part_wav), str(part), str(Path(tmp) / f"{a_ms}.out.srt"))
            ends.append(rp.offset_seconds if rp.synced else None)
        out["oracle_first_third_s"], out["oracle_last_third_s"] = ends
    out["oracle_in_sync"] = bool(
        out["oracle_confident"] and None not in ends
        and all(abs(x) <= ORACLE_SYNC_S for x in [out["oracle_offset_s"], *ends])
        and abs(ends[1] - ends[0]) <= ORACLE_SYNC_S)
    return out


def _row(verdict) -> dict:
    return {"passed": verdict.passed, "reason": verdict.reason,
            "checks": {c.name: c.value for c in verdict.checks},
            "failed": [c.name for c in verdict.checks if not c.passed]}


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("corpus", type=Path)
    p.add_argument("--only", default=None, help="one episode key, e.g. s05e07")
    p.add_argument("--no-oracle", action="store_true", help="skip ffsubsync (fast re-run after a threshold change)")
    args = p.parse_args(argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with OUT.open("a", encoding="utf-8") as out:
        for video, key, k, stream, srt, dur in _tracks(args.corpus, args.only):
            speech, vad_s = _speech(video, key)
            cues = load_file(srt)
            label = stream.get("tags", {}).get("language")
            row = {"at": at, "track": f"{key}.t{k}", "codec": stream["codec_name"], "label": label,
                   "title": stream.get("tags", {}).get("title"), "cues": len(cues), "vad_seconds": vad_s,
                   **_row(evaluate(cues, speech, "en", label, dur))}  # the corpus is English (OV)
            if not args.no_oracle:
                row.update(_oracle(_episode_wav(video, key), cues, srt, dur))
                row["gate_agrees"] = row["passed"] == row["oracle_in_sync"]
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{row['track']:10} gate={'PASS' if row['passed'] else 'FAIL'} "
                  f"oracle_in_sync={row.get('oracle_in_sync')} {row['checks']} {row['reason'] or ''}", flush=True)
            if row["passed"]:
                for name, (bad, wanted, tag, must_fail) in seeded_copies(cues).items():
                    v = evaluate(bad, speech, wanted, tag, dur)
                    seeded = {"at": at, "track": f"{key}.t{k}", "seeded": name, "must_fail": must_fail, **_row(v),
                              "rejected_for_the_right_reason": must_fail in [c.name for c in v.checks if not c.passed]}
                    out.write(json.dumps(seeded, ensure_ascii=False) + "\n")
                    print(f"   {name:22} {'REJECTED' if not v.passed else 'ACCEPTED'} "
                          f"failed={seeded['failed']} right_reason={seeded['rejected_for_the_right_reason']}",
                          flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
