# resync_bench.py — Does split-aware re-sync turn gate-rejected downloads into passing ones, without touching good ones? Exports: main.
"""Reads the re-timed downloads a download_bench run left (aligned.<stem>.<lang>.<n>.srt per episode folder), applies
core.acquire.resync, and runs the UNCHANGED gate before and after. Read-only on the library; no model (speech spans
come from bench/gate_work/speech, shared with gate_bench).

Usage (backend venv): python -m bench.resync_bench <download_bench work dir> ["C:/Videos/Ghosts/Saison 5"]
Rows go to bench/results/resync.jsonl; the re-synced files are written next to the inputs as resynced.<…>.srt."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from core.acquire.resync import resync  # noqa: E402
from core.gate import evaluate  # noqa: E402
from core.subtitles import load_file, to_srt  # noqa: E402
from bench.gate_bench import _speech  # noqa: E402

OUT = ROOT / "bench" / "results" / "resync.jsonl"


def _duration_ms(video: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, check=True)
    return int(float(r.stdout.strip()) * 1000)


def main(work: str, corpus: str = "C:/Videos/Ghosts/Saison 5") -> None:
    videos = {"s05" + v.name.lower().split("s05")[1][:3]: v for v in Path(corpus).glob("*.mkv")}
    counts = {"rejected_before": 0, "passed_after": 0, "passed_before": 0, "broken_after": 0, "cut": 0}
    with OUT.open("a", encoding="utf-8") as out:
        for folder in sorted(Path(work).iterdir()):
            key = folder.name
            if key not in videos:
                continue
            speech, _ = _speech(videos[key], key)
            dur = _duration_ms(videos[key])
            for f in sorted(folder.glob("aligned.*.srt")):
                lang = f.name.split(".")[-3]
                cues = load_file(str(f))
                before = evaluate(cues, speech, lang, duration_ms=dur)
                t0 = time.monotonic()
                r = resync(cues, speech, dur)
                seconds = round(time.monotonic() - t0, 2)
                after = evaluate(r.cues, speech, lang, duration_ms=dur) if r else None
                if r and len(r.segments) > 1:
                    counts["cut"] += 1
                    (f.parent / f.name.replace("aligned.", "resynced.", 1)).write_text(to_srt(r.cues), encoding="utf-8")
                if before.passed:
                    counts["passed_before"] += 1
                    counts["broken_after"] += bool(after and not after.passed)
                else:
                    counts["rejected_before"] += 1
                    counts["passed_after"] += bool(after and after.passed)
                row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "episode": key, "file": f.name, "lang": lang,
                       "before": before.reason or "passed", "after": (after.reason or "passed") if after else None,
                       "segments": r.segments if r else None, "seconds": seconds}
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(key, f.name.split(".")[-3:-1], "|", row["before"][:60], "->", (row["after"] or "-")[:60],
                      "|", [(round(a / 1000), b) for a, b in (r.segments if r else [])], flush=True)
    print(json.dumps(counts))


if __name__ == "__main__":
    main(*sys.argv[1:])
