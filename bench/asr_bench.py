# asr_bench.py — Benchmark one ASR engine on this machine: load time, speed (RTF), WER, peak RAM. Exports: main (CLI).
"""
Run one engine per process so its peak RAM is its own:
    python -I bench/asr_bench.py --engine whisper:turbo --media <dir> --out results.jsonl
    python -I bench/asr_bench.py --engine parakeet       --media <dir> --out results.jsonl

<dir> holds:
  fr/ en/        labelled clips + refs.json {filename: reference text}   (WER)
  *_long.mp3     long real-speech files, first --long-seconds used        (speed on a long file)

Threads are capped at --threads (default 4: half of this 8-thread laptop, D-0001).
"""
import argparse, glob, json, os, re, sys, threading, time, unicodedata

ap = argparse.ArgumentParser()
ap.add_argument("--engine", required=True)  # whisper:<size> | parakeet
ap.add_argument("--media", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--threads", type=int, default=4)
ap.add_argument("--long-seconds", type=float, default=600)
ap.add_argument("--clips", type=int, default=40)
args = ap.parse_args()

# Set before onnxruntime/ctranslate2 load: both read it at import.
os.environ["OMP_NUM_THREADS"] = str(args.threads)

import numpy as np  # noqa: E402
import psutil  # noqa: E402
from faster_whisper.audio import decode_audio  # noqa: E402  (PyAV: no ffmpeg binary needed)

PROC = psutil.Process()
peak = {"rss": 0}
cpu_samples = []


def _sample():
    PROC.cpu_percent(None)
    while True:
        peak["rss"] = max(peak["rss"], PROC.memory_info().rss)
        cpu_samples.append(PROC.cpu_percent(None))
        time.sleep(0.25)


threading.Thread(target=_sample, daemon=True).start()


def norm(text: str) -> list[str]:
    """Lowercase, drop accents-insensitive punctuation, keep letters/digits/apostrophes-split words."""
    t = unicodedata.normalize("NFC", text.lower()).replace("’", "'")
    t = re.sub(r"[^\w' ]+", " ", t)
    t = t.replace("'", " ")
    return t.split()


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


# ---- engines: each returns (load_fn, short_fn(audio, lang) -> text, long_fn(audio) -> list[(start, end, text)])
def whisper_engine(size):
    from faster_whisper import WhisperModel
    m = {}

    def load():
        m["w"] = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=args.threads)

    def short(audio, lang):
        segs, _ = m["w"].transcribe(audio, beam_size=5, language=lang, vad_filter=True,
                                    condition_on_previous_text=False)
        return " ".join(s.text for s in segs)

    def long(audio):
        # Same options as backend/core/transcriber.py (production path).
        segs, info = m["w"].transcribe(audio, beam_size=5, word_timestamps=True, vad_filter=True,
                                       condition_on_previous_text=False)
        return [(s.start, s.end, s.text) for s in segs], info.language

    return load, short, long


def parakeet_engine():
    import onnx_asr
    import onnxruntime as rt
    m = {}

    def load():
        so = rt.SessionOptions()
        so.intra_op_num_threads = args.threads
        so.inter_op_num_threads = 1
        base = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8", sess_options=so)
        vad = onnx_asr.load_vad("silero", sess_options=so)
        m["short"] = base
        m["long"] = base.with_vad(vad).with_timestamps()

    def short(audio, lang):
        return m["short"].recognize(audio)

    def long(audio):
        return [(r.start, r.end, r.text) for r in m["long"].recognize(audio)], None

    return load, short, long


if args.engine.startswith("whisper:"):
    load, short, long = whisper_engine(args.engine.split(":", 1)[1])
elif args.engine == "parakeet":
    load, short, long = parakeet_engine()
else:
    sys.exit(f"unknown engine {args.engine}")

result = {"engine": args.engine, "threads": args.threads, "date": time.strftime("%Y-%m-%d %H:%M")}

t0 = time.perf_counter()
load()
result["load_s"] = round(time.perf_counter() - t0, 1)
result["rss_after_load_mb"] = round(PROC.memory_info().rss / 2**20)

# WER on labelled clips
for lang in ("fr", "en"):
    d = os.path.join(args.media, lang)
    refs = json.load(open(os.path.join(d, "refs.json"), encoding="utf-8"))
    names = sorted(refs)[: args.clips]
    errs = words = 0
    audio_s = proc_s = 0.0
    samples = []
    for n in names:
        a = decode_audio(os.path.join(d, n), sampling_rate=16000)
        t0 = time.perf_counter()
        hyp = short(a, lang)
        proc_s += time.perf_counter() - t0
        audio_s += len(a) / 16000
        r, h = norm(refs[n]), norm(hyp)
        errs += edit_distance(r, h)
        words += len(r)
        if len(samples) < 2:
            samples.append({"ref": refs[n], "hyp": hyp.strip()})
        if (names.index(n) + 1) % 10 == 0:
            print(f"  {lang} {names.index(n) + 1}/{len(names)} clips, RTF so far {proc_s / audio_s:.3f}", flush=True)
    result[f"wer_{lang}"] = round(100 * errs / words, 1)
    result[f"rtf_clips_{lang}"] = round(proc_s / audio_s, 3)
    result[f"clips_{lang}"] = f"{len(names)} clips, {audio_s/60:.1f} min"
    result[f"samples_{lang}"] = samples
    print(lang, "WER", result[f"wer_{lang}"], "RTF", result[f"rtf_clips_{lang}"], flush=True)

# Speed on long real speech (the shape of a film or episode)
for f in sorted(glob.glob(os.path.join(args.media, "*_long.mp3"))):
    key = os.path.basename(f).split("_")[0]
    a = decode_audio(f, sampling_rate=16000)[: int(args.long_seconds * 16000)]
    t0 = time.perf_counter()
    segs, detected = long(a)
    dt = time.perf_counter() - t0
    result[f"rtf_long_{key}"] = round(dt / (len(a) / 16000), 3)
    result[f"long_{key}"] = {"audio_min": round(len(a) / 16000 / 60, 1), "proc_min": round(dt / 60, 1),
                             "segments": len(segs), "detected": detected,
                             "max_segment_s": round(max((e - s for s, e, _ in segs), default=0), 1),
                             "first": [t.strip() for _, _, t in segs[:3]]}
    print(key, "long RTF", result[f"rtf_long_{key}"], flush=True)

result["peak_rss_mb"] = round(peak["rss"] / 2**20)
busy = [c for c in cpu_samples if c > 5]
result["avg_cpu_pct_busy"] = round(sum(busy) / max(len(busy), 1))  # 100 = one full thread
with open(args.out, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(result, ensure_ascii=False) + "\n")
print(json.dumps({k: v for k, v in result.items() if not k.startswith(("samples", "long_"))}))
