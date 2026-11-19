# download_bench.py — How many episodes get a human subtitle that passes the gate, with the best candidate only (before) and with the next candidates tried (after). Exports: main.
"""Read-only on the corpus: rung 2 only (search, download, re-time, gate) into a work folder outside the library; no
swap, no model. One search per episode serves both counts: candidates are tried in the same order, so « before »
(best candidate only) passes exactly when the first candidate was kept.

Usage (backend venv, ffmpeg on PATH, network): python -m bench.download_bench "C:/Videos/Ghosts/Saison 5" [en fr]
Rows go to bench/results/download.jsonl; speech spans are shared with gate_bench (bench/gate_work/speech/).
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from core.acquire.fetch import fetch  # noqa: E402
from core.acquire.download import MAX_CANDIDATES  # noqa: E402
from core.gate import evaluate  # noqa: E402
from core.subtitles import load_file  # noqa: E402
from bench.gate_bench import _speech  # noqa: E402

OUT = ROOT / "bench" / "results" / "download.jsonl"


def _duration_ms(video: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, check=True)
    return int(float(r.stdout.strip()) * 1000)


def main(corpus: str, langs=("en", "fr")) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="substudio-dlbench-"))
    totals = {l: {"before": 0, "after": 0} for l in langs}
    videos = sorted(Path(corpus).glob("*.mkv"))
    with OUT.open("a", encoding="utf-8") as out:
        for video in videos:
            key = "s05" + video.name.lower().split("s05")[1][:3]
            speech, _ = _speech(video, key)
            dur = _duration_ms(video)
            accept = lambda lang, path: (lambda v: None if v.passed else v.reason)(
                evaluate(load_file(path), speech, lang, duration_ms=dur))
            t0 = time.monotonic()
            try:
                r = fetch(str(video), list(langs), str(work_root / key), env={}, accept=accept,
                          per_language=MAX_CANDIDATES)
                error = None
            except Exception as e:  # one episode's failure is a row, not the end of the bench
                r, error = None, f"{type(e).__name__}: {e}"
            row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "episode": key, "seconds": round(time.monotonic() - t0),
                   "error": error, "langs": {}}
            for lang in langs:
                rejected = r.rejected.get(lang, []) if r else []
                kept = bool(r and lang in r.subtitles)
                before = kept and not rejected                     # the first candidate was the one kept
                totals[lang]["before"] += before
                totals[lang]["after"] += kept
                row["langs"][lang] = {"before": before, "after": kept, "rejected": rejected,
                                      "provider": r.subtitles[lang].provider if kept else None,
                                      "offset_s": r.subtitles[lang].offset_seconds if kept else None}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print(key, {l: (row["langs"][l]["before"], row["langs"][l]["after"], len(row["langs"][l]["rejected"]))
                        for l in langs}, error or "", flush=True)
    print(json.dumps({"episodes": len(videos), "passing": totals, "max_candidates": MAX_CANDIDATES}))


if __name__ == "__main__":
    main(sys.argv[1], tuple(sys.argv[2:]) or ("en", "fr"))
