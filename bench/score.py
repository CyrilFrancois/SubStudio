# score.py — Scores a candidate subtitle against a human reference (chrF) and records it with its cost (minutes, peak RAM/CPU). Exports: score, main.
"""chrF is computed by sacrebleu (chrF2: character 6-grams, beta 2), on three alignments:

- window (the headline): both files are cut into fixed time windows (30 s by default) and each cue goes to the window
  holding its midpoint. Robust to different cue splits (ASR never segments like a human), and a timing drift larger
  than a window costs points, which is what a viewer sees.
- doc: the whole text as one segment; text quality only, blind to timing.
- cue: one segment per cue, only when both files have the same number of cues (a translation of a human file).

Usage:
  python -m bench.score --hyp out.fr.srt --ref human.fr.srt --label "turbo+translategemma-q4" \
      [--clip ID] [--measure run.json] [--window 30] [--out bench/results/results.jsonl]
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from sacrebleu.metrics import CHRF

from bench.subs import Cue, load_cues

_CHRF = CHRF()  # sacrebleu defaults: char_order=6, word_order=0, beta=2


def _chrf(hyps: list[str], refs: list[str]) -> float:
    return round(_CHRF.corpus_score(hyps, [refs]).score, 2)


def _windows(cues: list[Cue], window_ms: int) -> dict[int, str]:
    out: dict[int, list[str]] = {}
    for c in cues:
        out.setdefault((c.start_ms + c.end_ms) // 2 // window_ms, []).append(c.text)
    return {k: " ".join(v) for k, v in out.items()}


def score(hyp: list[Cue], ref: list[Cue], window_s: float = 30.0) -> dict:
    if not ref:
        raise ValueError("the reference has no cues: nothing to score against")
    window_ms = int(window_s * 1000)
    h_win, r_win = _windows(hyp, window_ms), _windows(ref, window_ms)
    keys = sorted(set(h_win) | set(r_win))
    result = {
        "chrf_window": _chrf([h_win.get(k, "") for k in keys], [r_win.get(k, "") for k in keys]),
        "chrf_doc": _chrf([" ".join(c.text for c in hyp)], [" ".join(c.text for c in ref)]),
        "chrf_cue": _chrf([c.text for c in hyp], [c.text for c in ref]) if len(hyp) == len(ref) else None,
        "window_s": window_s,
        "hyp_cues": len(hyp),
        "ref_cues": len(ref),
        # Share of the reference's speech windows where the candidate shows anything at all: a silent gap is the
        # failure a viewer notices first, and chrF alone hides where it is.
        "ref_windows_covered": round(sum(1 for k in r_win if k in h_win) / len(r_win), 3),
    }
    return result


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hyp", required=True, type=Path, help="candidate subtitle (SRT/ASS/VTT)")
    p.add_argument("--ref", required=True, type=Path, help="human reference subtitle, same language")
    p.add_argument("--label", required=True, help="what produced the candidate, e.g. 'turbo+translategemma-q4'")
    p.add_argument("--lang", default=None, help="ISO 639-1 of both files; decodes a non-UTF-8 file (default: from the name)")
    p.add_argument("--clip", default=None, help="bench clip id")
    p.add_argument("--measure", type=Path, default=None, help="JSON written by bench.measure for the same run")
    p.add_argument("--window", type=float, default=30.0, help="window length in seconds")
    p.add_argument("--out", type=Path, default=Path(__file__).parent / "results" / "results.jsonl")
    args = p.parse_args(argv)

    hyp, hyp_enc = load_cues(args.hyp, args.lang)
    ref, ref_enc = load_cues(args.ref, args.lang)
    row = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "label": args.label,
           "clip": args.clip, "hyp": args.hyp.name, "ref": args.ref.name,
           "hyp_encoding": hyp_enc, "ref_encoding": ref_enc, **score(hyp, ref, args.window)}
    if args.measure:
        m = json.loads(args.measure.read_text(encoding="utf-8"))
        row.update({k: m[k] for k in ("minutes", "peak_rss_gb", "peak_cpu_threads", "mean_cpu_threads",
                                      "within_budget", "exit_code")})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False))
    return row


if __name__ == "__main__":
    main(sys.argv[1:])
