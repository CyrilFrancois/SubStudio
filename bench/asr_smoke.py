# asr_smoke.py — Run the production VideoTranscriber on test videos and check the outcomes. Exports: main (CLI).
"""
    python -I bench/asr_smoke.py <dir with test videos>

Expects in <dir>: one or more speech videos (any name), « no audio.mkv » and « silence.mkv ».
Speech videos must yield an AsrResult (language + probability, VAD speech, scored segments) and a valid SRT, and a
re-run on two speech spans must stay inside them; the two others must raise a named error, never return empty.
Exit 1 on any unexpected outcome. Never point it at a real library: extraction writes <stem>.tmp.wav next to
the video (and deletes it).
"""
import os, re, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from core.transcriber import VideoTranscriber, names_prompt  # noqa: E402
from core.subtitles import to_srt  # noqa: E402

d = sys.argv[1]
fails = []

# names_prompt: dedup, cap, None when empty
assert names_prompt([]) is None and names_prompt(["  ", ""]) is None
assert names_prompt(["Sam", "sam", "Jay"]) == "Names: Sam, Jay."
assert len(names_prompt([f"Name{i}" for i in range(200)])) <= 300 + len("Names: .")

TS = re.compile(r"^(\d\d):(\d\d):(\d\d),(\d{3}) --> (\d\d):(\d\d):(\d\d),(\d{3})$")
t = VideoTranscriber()
progress = []
on_progress = lambda fid, st, pct, msg: progress.append(pct)  # noqa: E731
NAMES = ["Sam", "Jay", "Hetty", "Thorfinn", "Isaac"]


def check_srt(name, srt):
    blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
    prev_end = -1.0
    for i, b in enumerate(blocks, 1):
        lines = b.split("\n")
        m = TS.match(lines[1]) if len(lines) > 2 else None
        if lines[0] != str(i) or not m:
            return f"{name}: malformed block {i}: {b!r}"
        g = list(map(int, m.groups()))
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        if e < s or s < prev_end - 0.001:
            return f"{name}: block {i} times go backwards ({s} -> {e}, previous end {prev_end})"
        prev_end = e
    return None if blocks else f"{name}: empty SRT"


for name in sorted(os.listdir(d)):
    if not name.endswith(".mkv"):
        continue
    path = os.path.join(d, name)
    expect_error = name in ("no audio.mkv", "silence.mkv")
    t0 = time.perf_counter()
    try:
        r = t.transcribe(path, path, on_progress, 1, 1, names=NAMES)
    except Exception as e:
        print(f"[{name}] raised in {time.perf_counter()-t0:.0f}s: {e}")
        if not expect_error:
            fails.append(name)
        continue
    dt = time.perf_counter() - t0
    if expect_error:
        fails.append(f"{name}: expected a named error, got {len(r.segments)} segments")
        continue
    srt = to_srt(r.to_cues())
    print(f"[{name}] {dt:.0f}s for {r.duration:.0f}s audio; lang {r.language} ({r.language_probability:.2f}); "
          f"{len(r.speech)} speech spans; {len(r.segments)} segments; "
          f"worst avg_logprob {min(s.avg_logprob for s in r.segments):.2f}")
    print("   first cues:", srt.strip().split("\n\n")[:3])
    err = check_srt(name, srt)
    if err:
        fails.append(err)
    if not (0 < r.language_probability <= 1) or not r.speech or not all(s.words for s in r.segments):
        fails.append(f"{name}: AsrResult missing language probability, speech or words")
    if os.path.exists(os.path.splitext(path)[0] + ".tmp.wav"):
        fails.append(f"{name}: temp wav left behind")

    # Correction re-run: only the given spans, language known (no detection)
    spans = r.speech[:2]
    r2 = t.transcribe(path, path, on_progress, 1, 1, language=r.language, spans=spans)
    outside = [s for s in r2.segments if not any(a - 0.5 <= s.start and s.end <= b + 0.5 for a, b in spans)]
    print(f"   re-run on {spans}: {len(r2.segments)} segments, {len(outside)} outside; "
          f"{[s.text for s in r2.segments][:2]}")
    if not r2.segments or outside or r2.language_probability != 1.0:
        fails.append(f"{name}: span re-run gave {len(r2.segments)} segments, {len(outside)} outside the spans")

print("progress values seen:", sorted(set(progress))[:5], "...", max(progress, default=None))
print("FAIL:" if fails else "SMOKE OK", fails)
sys.exit(1 if fails else 0)
