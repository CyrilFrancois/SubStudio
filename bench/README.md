# SubStudio bench

**What this answers:** how a candidate model (ASR or translation) is measured on this PC before it becomes the
default, so the choice in `doc/architecture-review-2026-11-19.md` §4.2 is made on numbers, not on claims.
For Claude threads and Cyril. Budget: `doc/decisions/D-0001` (≤ 4 threads, ≤ 7 GB RAM).

## Setup (native Windows, Git Bash, from the repo root)

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r bench/requirements.txt
.venv/Scripts/python.exe -B -m pytest -q          # 35 tests, ~20 s; needs ffmpeg (winget Gyan.FFmpeg)
```
ffmpeg is found on PATH, in `FFMPEG_DIR`, or in the current user PATH from the registry (so a process started
before the winget install still finds it).

## What is here

| File | Does |
|---|---|
| `make_fixtures.py` | Seeds the edge cases that bit SubStudio: no audio, PGS-only, non-ASCII name, cp1252 sidecar, subs past the end, zero bytes. Synthetic sources only. `python -m bench.make_fixtures <dir>` |
| `make_clips.py` | Cuts bench clips from a manifest: `clip.mkv` (360p, no subtitle stream), `audio.wav` (16 kHz mono), optional `ref.<lang>.srt`. Sources are only read. |
| `measure.py` | Runs a command; records minutes, peak RAM, peak / sustained (p95 of 1-s means) / mean CPU in threads, and `within_budget`. `--watch llama-server.exe` adds a server process that does the real work. |
| `score.py` | chrF of a candidate against a human reference, appended as one row to `bench/results/results.jsonl`. |
| `asr_bench.py` | Speech-to-text candidates, one engine per process (`--engine whisper:large-v3-turbo` or `parakeet`, 4 threads): WER on public labelled clips (`<media>/fr`, `<media>/en` with `refs.json`), RTF on clips and on each `<media>/*_long.mp3`, load time, peak RAM, appended to `--out`. Prints a line every 10 clips. Extra deps for Parakeet: `onnx-asr onnxruntime`. Result: `results/asr.jsonl`, `doc/decisions/D-0004`. |
| `asr_smoke.py` | Runs the production `VideoTranscriber` on a folder of test videos: no audio and silence must raise their named errors, speech must give a language, speech spans, words and a valid cue list; a re-run on two spans must stay inside them; no temp wav may be left. `python bench/asr_smoke.py <videos dir>` (loads turbo: take `heavy`). |
| `subs.py` | Subtitle reader shared by the above: UTF-8, else the language's Windows code page, strict. |
| `translate/` | Translation candidates on 150 public EN→FR subtitle lines (`opensubs_en_fr_150.jsonl`): `bench_translate.py --model tg-q4 --mode batch --ngl 99` starts llama-server (4 threads) or NLLB/CT2, records s/cue, tok/s, peak RAM, chrF, lost ids in `translate/results.jsonl`, and refuses a model that does not fit in free RAM; `run_all.sh` runs every candidate under the `heavy` lease; `rescore.py` compares on common cues. Extra deps: `ctranslate2 tokenizers jinja2 gguf`. Result: `doc/decisions/D-0003`. |

## The bench set

`bench/clips.local.json` (gitignored: it names Cyril's media) lists three 5-min excerpts from `C:\Videos\Ghosts\Saison 5\`,
one per encoding family: S05E01 H.264 720p HDTV, S05E02 HEVC 1080p, S05E07 H.264 1080p WEB, each from 2:00 to 7:00.
`python -m bench.make_clips bench/clips.local.json` rebuilds them into `bench/clips/<id>/`.

**English references (2026-11-19):** the « English (SDH) » tracks of S05E02 and S05E07 are human broadcast captions
that the quality gate measured in sync, agreeing with ffsubsync (`doc/gate-bench.md`). Their manifest entries carry
`"refs": {"en": {"stream": 0}}`, cut to the clip window as `bench/clips/<id>/ref.en.srt`. S05E01 has no subtitle
track, so it measures speed and footprint only.

**First quality score (2026-11-19, `bench/results/results.jsonl`):** SubStudio's production English transcription
(`core.transcriber.VideoTranscriber`, Whisper turbo int8 + VAD, lines wrapped at the gate's 47 characters) against
those references:

| Clip | chrF window | chrF doc | doc, SDH speaker names and sound cues removed | Minutes / 5-min clip | Peak RAM | Peak / mean threads |
|---|---|---|---|---|---|---|
| S05E02 HEVC 1080p | 85.9 | 87.2 | 90.7 | 4.9 | 1.9 GB | 4.06 / 3.17 |
| S05E07 H.264 1080p WEB | 83.0 | 84.5 | 89.2 | 4.3 | 1.9 GB | 4.09 / 3.52 |

Every 30 s window of the reference has text from the candidate. The scorer was checked both ways: a reference
against itself reads 100, S05E02's transcript against S05E07's reference reads 23.6. Most of the remaining gap is
caption style (speaker names, sound cues, captions shortening speech), not mishearing. The meter marks both runs
`within_budget: false` for a peak just over 4 threads (4.06–4.09), while the mean stays at 3.2–3.5.

**French references and the first French score (2026-11-19, `bench/results/results.jsonl`):** the human French
subtitle of S05E02 and S05E07 was fetched through SubStudio's own download rung (`core.acquire.fetch`: gestdown,
re-timed, accepted only if the quality gate passes it; offsets -0.14 s and -0.20 s), cut to the clip window as
`bench/clips/<id>/ref.fr.srt` (133 and 128 cues). TranslateGemma 4B Q4_K_M then translated each clip through the
shipped path (`get_context` + `make_translator()` → `LlamaCppTranslator`, `translate_cues`, wrapped at 47
characters), once from the human English captions and once from SubStudio's own turbo English transcript (the full
« no subtitle anywhere » path):

| Clip | English source | chrF window | chrF doc | Minutes / 5-min clip | Peak RAM | p95 / mean threads |
|---|---|---|---|---|---|---|
| S05E02 HEVC 1080p | human captions | 59.4 | 66.4 | 19.8 | 4.4 GB | 0.50 / 0.38 |
| S05E02 HEVC 1080p | SubStudio transcript | 57.0 | 65.1 | 14.2 | 4.7 GB | 0.58 / 0.37 |
| S05E07 H.264 1080p WEB | human captions | 59.0 | 66.0 | 16.2 | 4.8 GB | 0.51 / 0.38 |
| S05E07 H.264 1080p WEB | SubStudio transcript | 54.1 | 63.2 | 21.2 | 4.6 GB | 0.48 / 0.38 |

All four runs are within budget, every reference window is covered, no line was left in English. The scorer was
checked again: the French reference against itself reads 100, S05E07's translation against S05E02's reference reads
26.2. Translating our own transcript costs 2.4 to 4.9 chrF against translating human captions. Speed is
llama-server's, not the CPU's: about 0.4 threads on average, so the minutes are mostly waiting on the model's
batches (several batches were split and retried for an empty or missing line); D-0003's speed question stays open
(TODOLIST « Measure local translation speed on a quiet PC »). `bench.score` does not copy `p95_cpu_threads` into
its rows yet; the figures above are from the `run.fr.*.json` meter files.

## Measuring one candidate

```bash
# 1. Run the candidate under the meter, through the heavy lease (one model at a time on this PC; it waits for the
#    lease and the free RAM, runs, always releases; exit 3 = not run)
python3 -I C:/Users/cyril/Documents/Workspace/doc/_tools/threads.py with heavy --min-free-mb 2500 -- \
  .venv/Scripts/python.exe -m bench.measure --out bench/clips/s05e02-hevc-1080p/run.json -- <command producing out.en.srt>
# 2. Score it against the human reference of the same clip and language
.venv/Scripts/python.exe -m bench.score --clip s05e02-hevc-1080p --label "turbo-int8-vad" \
  --hyp out.en.srt --ref bench/clips/s05e02-hevc-1080p/ref.en.srt --measure bench/clips/s05e02-hevc-1080p/run.json
```

**Reading a row:** `chrf_window` is the headline (text quality and timing within 30 s windows, robust to a
different cue split); `chrf_doc` is text only; `chrf_cue` exists only when cue counts match (translation of a human
file); `ref_windows_covered` is the share of the reference's speech windows where the candidate shows anything.
`within_budget: false` disqualifies a candidate as a default, whatever its score. It is judged on RAM peak ≤ 7 GB
and **sustained** CPU, `p95_cpu_threads` ≤ 4: the 95th percentile of the CPU use averaged over 1-s windows (D-0001,
clarified 2026-11-19: « 4 threads » is the runner's own setting and its sustained load). `peak_cpu_threads`, the
highest single sample, stays in the row for information: Whisper turbo set to 4 threads peaks at 4.06–4.09 for an
instant (its Python thread, an ffmpeg decode) while averaging 3.2–3.5. Re-run under this rule (S05E07 clip,
2026-11-19, a busier PC): p95 3.63, peak 3.97, mean 2.71 threads, 1.9 GB, 5.7 min: within budget.

First measurement, 2026-11-19: cutting the three clips took 5.0 min, peak 2.88 threads, 0.17 GB (an encoder-only
`-threads 4` had peaked at 4.5 threads; decoder and encoder are now capped at 2 each).
