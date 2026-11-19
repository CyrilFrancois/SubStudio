# names_score.py — name accuracy of the saved outputs: does a name the human translator kept survive the model? Exports: main, kept_names.
"""  .venv/Scripts/python.exe -X utf8 bench/translate/names_score.py

A « name » here is a source word capitalised mid-sentence (not at a sentence start, not ALL-CAPS, not « I ») that
the human reference ALSO contains verbatim: the reference proves it is a name that stays untranslated. A run scores
the share of those names found verbatim in its translation. Computed from the cues, no model.
"""
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
_WORD = re.compile(r"[^\W\d_][\w'’-]*")
_SENTENCE_END = ".!?…:\"-—♪[("
_NOT_NAMES = {"I", "OK", "Oh"}  # « Oh » after a dash is an interjection the reference keeps, not a name


def kept_names(en: str, ref: str):
    flat = " ".join(en.split())
    out = []
    for m in _WORD.finditer(flat):
        w = re.sub(r"['’]s$", "", m.group().rstrip("'’-"))
        before = flat[:m.start()].rstrip()
        if not before or before[-1] in _SENTENCE_END or not w[0].isupper() or w.isupper() or w in _NOT_NAMES:
            continue
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", ref):
            out.append(w)
    return out


def present(name: str, hyp: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", hyp) is not None


def score(rows):
    total = hit = 0
    lost = []
    for r in rows:
        for n in kept_names(r["en"], r["ref"]):
            total += 1
            if present(n, r["hyp"]):
                hit += 1
            else:
                lost.append((r["id"], n, r["hyp"]))
    return total, hit, lost


def main():
    files = [Path(a) for a in sys.argv[1:]] or sorted(OUT.glob("*.jsonl"))
    for f in files:
        rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
        total, hit, lost = score(rows)
        print(f"{f.stem:32} names {hit}/{total}" + (f" = {100 * hit / total:.0f}%" if total else ""))
        for i, n, h in lost:
            print(f"    lost #{i} {n!r}: {h[:90]!r}")


if __name__ == "__main__":
    main()
