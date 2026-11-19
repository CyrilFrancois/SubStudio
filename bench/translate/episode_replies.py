# episode_replies.py — one whole episode through the shipped path (translate_cues + LlamaCppTranslator): how many replies are rejected, how many batches split, which cues stay untranslated. Exports: main.
"""  cd backend && <venv>/Scripts/python.exe -X utf8 ../bench/translate/episode_replies.py <source.srt> <target> <out_dir>

The case it was written for: Ghosts S05E12 FR (2026-11-19). Before fix/translator-replies the backend log of that
run shows 65 rejected replies and 31 split batches, then the language failed on cue 272. This runs the same source
file through the current code and counts the same warnings, so the two numbers compare like for like.
Loads the model: run it only as `python3 -I <workspace>/doc/_tools/threads.py with heavy --min-free-mb 4096 -- …`
(one heavy model at a time, D-0001; doc/04).
"""
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from core.subtitles import TranslationContext, detect_language, load_file, to_srt, translate_cues  # noqa: E402
from core.translators.llamacpp import LlamaCppTranslator  # noqa: E402


class Counter(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.wrong, self.splits, self.raw = [], 0, []

    def emit(self, record):
        msg = record.getMessage()
        if "wrongly:" in msg:
            self.wrong.append(msg.split("wrongly: ", 1)[1])
        elif msg.startswith("Splitting cues"):
            self.splits += 1
        elif msg.startswith("Unparseable reply"):
            self.raw.append(msg)


def main(src: str, target: str, out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cues = load_file(src)
    counter = Counter()
    logging.getLogger("SubStudio").addHandler(counter)
    logging.getLogger("SubStudio").setLevel(logging.INFO)
    t = LlamaCppTranslator()
    ctx = TranslationContext(title=Path(src).stem, source_lang=detect_language(cues))
    left = {}
    t0 = time.time()
    try:
        result = translate_cues(cues, target, ctx, t, left_in_source=left, retry_delay_s=1)
        failed = None
    except Exception as e:  # the outcome to report, not to hide
        result, failed = None, f"{type(e).__name__}: {e}"
    finally:
        t.unload()
    wall = time.time() - t0
    if result:
        (out / f"{target}.srt").write_text(to_srt(result), encoding="utf-8")
    summary = {"source": Path(src).name, "cues": len(cues), "target": target, "wall_s": round(wall),
               "s_per_cue": round(wall / len(cues), 2), "rejected_replies": len(counter.wrong),
               "splits": counter.splits, "left_in_source": left, "failed": failed,
               "reasons": sorted({w.split(":")[0][:40] for w in counter.wrong}),
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "raw_replies.txt").write_text("\n".join(counter.raw), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:4])
