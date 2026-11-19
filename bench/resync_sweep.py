# resync_sweep.py — Sweeps resync.CHANGE_PENALTY on a download_bench work dir: passing tracks cut (must be 0), rejected ones fixed, segments at the reach limit (noise). Exports: none (script).
"""Sweep resync's penalty: how many already-passing tracks get cut (must be 0), how many rejected ones pass, and
whether any segment sits at the reach edge (noise). Usage: python -m bench.resync_sweep <download_bench work dir> 300,600,1000 [verbose]"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))
import core.acquire.resync as rs  # noqa: E402
from core.gate import evaluate  # noqa: E402
from core.subtitles import load_file  # noqa: E402
from bench.gate_bench import _speech  # noqa: E402

WORK = Path(sys.argv[1])
CORPUS = Path("C:/Videos/Ghosts/Saison 5")
videos = {"s05" + v.name.lower().split("s05")[1][:3]: v for v in CORPUS.glob("*.mkv")}
cells = []
for folder in sorted(WORK.iterdir()):
    if folder.name not in videos:
        continue
    sp, _ = _speech(videos[folder.name], folder.name)
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(videos[folder.name])], capture_output=True, text=True, check=True)
    dur = int(float(d.stdout) * 1000)
    for f in sorted(folder.glob("aligned.*.1.srt")):   # the distinct timings are enough: #1 per language
        lang = f.name.split(".")[-3]
        cues = load_file(str(f))
        cells.append((folder.name, lang, cues, sp, dur, evaluate(cues, sp, lang, duration_ms=dur).passed))

for penalty in [int(p) for p in sys.argv[2].split(",")]:
    rs.CHANGE_PENALTY = penalty
    cut_good, fixed, edge, lines = 0, 0, 0, []
    for ep, lang, cues, sp, dur, ok in cells:
        r = rs.resync(cues, sp, dur)
        segs = r.segments if r else []
        at_edge = any(abs(s) >= rs.REACH_MS - 100 for _, s in segs)
        edge += at_edge
        after = evaluate(r.cues, sp, lang, duration_ms=dur).passed if r else ok
        if ok:
            cut_good += len(segs) > 1
        else:
            fixed += after
        lines.append(f"  {ep} {lang} {'ok ' if ok else 'BAD'}->{'ok ' if after else 'BAD'} "
                     f"{[(round(a / 1000), s) for a, s in segs]}")
    bad = sum(1 for c in cells if not c[5])
    print(f"penalty {penalty}: good tracks cut {cut_good}/{len(cells) - bad}, rejected fixed {fixed}/{bad}, "
          f"segments at reach edge {edge}")
    if len(sys.argv) > 3:
        print("\n".join(lines))
