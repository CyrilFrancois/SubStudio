# resync_referee.py — Second opinion on every re-synced subtitle the gate now passes: ffsubsync, which shares no code with the gate or resync, measures each third on its own. Exports: main.
"""A re-sync is fitted on the same speech spans the gate then judges, so a pass could be the fit grading itself.
ffsubsync detects speech its own way (webrtc VAD on the audio) and correlates independently: each third of the
re-synced file must land within REFEREE_MAX_S of where ffsubsync would put it.

Usage (backend venv, ffmpeg on PATH): python -m bench.resync_referee <download_bench work dir> ["C:/Videos/Ghosts/Saison 5"]
Reads bench/results/resync.jsonl (latest row per file); rows go to bench/results/resync_referee.jsonl."""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from core.acquire.align import AlignError, align  # noqa: E402
from core.subtitles import load_file, to_srt  # noqa: E402

RESULTS = ROOT / "bench" / "results" / "resync.jsonl"
OUT = ROOT / "bench" / "results" / "resync_referee.jsonl"
REFEREE_MAX_S = 0.5   # the gate's max_offset_ms, so the referee is no looser than the gate


def main(work: str, corpus: str = "C:/Videos/Ghosts/Saison 5") -> None:
    videos = {"s05" + v.name.lower().split("s05")[1][:3]: v for v in Path(corpus).glob("*.mkv")}
    latest = {}
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        latest[(r["episode"], r["file"])] = r
    todo = [r for r in latest.values() if r["before"] != "passed" and r["after"] == "passed"
            and r["segments"] and len(r["segments"]) > 1]
    tmp = Path(tempfile.mkdtemp(prefix="substudio-referee-"))
    with OUT.open("a", encoding="utf-8") as out:
        for r in sorted(todo, key=lambda r: (r["episode"], r["file"])):
            f = Path(work) / r["episode"] / r["file"].replace("aligned.", "resynced.", 1)
            cues = sorted(load_file(str(f)), key=lambda c: c.start_ms)
            k = len(cues) // 3
            thirds = [cues[:k], cues[k:2 * k], cues[2 * k:]]
            offsets = []
            for i, part in enumerate(thirds, start=1):
                src = tmp / f"{r['episode']}.{r['lang']}.third{i}.srt"
                src.write_text(to_srt(part), encoding="utf-8")
                try:
                    a = align(str(videos[r["episode"]]), str(src), str(tmp / f"out.{src.name}"))
                    offsets.append(a.offset_seconds if a.synced else None)
                except AlignError as e:
                    offsets.append(f"error: {e}")
            ok = all(isinstance(o, (int, float)) and abs(o) <= REFEREE_MAX_S for o in offsets)
            row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "episode": r["episode"], "file": r["file"],
                   "lang": r["lang"], "thirds_offset_s": offsets, "referee_passed": ok}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print(r["episode"], r["lang"], offsets, "PASS" if ok else "FAIL", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
