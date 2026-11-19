# rescore.py — compares the candidates' saved outputs on the cues EVERY candidate answered, so a dropped batch does not hide quality. Exports: main.
"""  .venv/Scripts/python.exe -X utf8 bench/translate/rescore.py

chrF over all 150 cues punishes a candidate twice for a lost batch (once here, and once in the pipeline, which would
split and retry it). This scores every candidate on the same subset: the cues all of them returned non-empty.
It also counts answers left in English (a « translated » cue that is the source text), which chrF alone hides.
"""
import json
from pathlib import Path

import sacrebleu

OUT = Path(__file__).resolve().parent / "out"


def main():
    runs = {}
    for f in sorted(OUT.glob("*.jsonl")):
        rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
        if len(rows) == 150:
            runs[f.stem] = {r["id"]: r for r in rows}
    if not runs:
        raise SystemExit("no 150-cue output in bench/translate/out")
    common = set.intersection(*({i for i, r in rs.items() if r["hyp"].strip()} for rs in runs.values()))
    ids = sorted(common)
    print(f"{len(ids)} cues answered by all {len(runs)} runs\n")
    print(f"{'run':32} {'chrF all':>8} {'chrF common':>11} {'empty':>5} {'= source':>8}")
    for name, rs in runs.items():
        hyp_all = [rs[i]["hyp"] for i in sorted(rs)]
        ref_all = [rs[i]["ref"] for i in sorted(rs)]
        hyp = [rs[i]["hyp"] for i in ids]
        ref = [rs[i]["ref"] for i in ids]
        empty = sum(1 for h in hyp_all if not h.strip())
        same = sum(1 for i in rs if rs[i]["hyp"].strip() and rs[i]["hyp"].strip().lower() == rs[i]["en"].strip().lower())
        print(f"{name:32} {sacrebleu.corpus_chrf(hyp_all, [ref_all]).score:8.1f} "
              f"{sacrebleu.corpus_chrf(hyp, [ref]).score:11.1f} {empty:5} {same:8}")


if __name__ == "__main__":
    main()
