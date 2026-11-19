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
| `measure.py` | Runs a command; records minutes, peak RAM, peak and mean CPU in threads, and `within_budget`. `--watch llama-server.exe` adds a server process that does the real work. |
| `score.py` | chrF of a candidate against a human reference, appended as one row to `bench/results/results.jsonl`. |
| `asr_bench.py` | Speech-to-text candidates, one engine per process (`--engine whisper:large-v3-turbo` or `parakeet`, 4 threads): WER on public labelled clips (`<media>/fr`, `<media>/en` with `refs.json`), RTF on clips and on each `<media>/*_long.mp3`, load time, peak RAM, appended to `--out`. Prints a line every 10 clips. Extra deps for Parakeet: `onnx-asr onnxruntime`. Result: `results/asr.jsonl`, `doc/decisions/D-0004`. |
| `asr_smoke.py` | Runs the production `VideoTranscriber` on a folder of test videos: no audio and silence must raise their named errors, speech must give a language, speech spans, words and a valid cue list; a re-run on two spans must stay inside them; no temp wav may be left. `python bench/asr_smoke.py <videos dir>` (loads turbo: take `heavy`). |
| `subs.py` | Subtitle reader shared by the above: UTF-8, else the language's Windows code page, strict. |
| `translate/` | Translation candidates on 150 public EN→FR subtitle lines (`opensubs_en_fr_150.jsonl`): `bench_translate.py --model tg-q4 --mode batch --ngl 99` starts llama-server (4 threads) or NLLB/CT2, records s/cue, tok/s, peak RAM, chrF, lost ids in `translate/results.jsonl`, and refuses a model that does not fit in free RAM; `run_all.sh` runs every candidate under the `heavy` lease; `rescore.py` compares on common cues. Extra deps: `ctranslate2 tokenizers jinja2 gguf`. Result: `doc/decisions/D-0003`. |

## The bench set

`bench/clips.local.json` (gitignored: it names Cyril's media) lists three 5-min excerpts from `C:\Videos\Ghosts\Saison 5\`,
one per encoding family: S05E01 H.264 720p HDTV, S05E02 HEVC 1080p, S05E07 H.264 1080p WEB, each from 2:00 to 7:00.
`python -m bench.make_clips bench/clips.local.json` rebuilds them into `bench/clips/<id>/`.

**No reference subtitles yet.** The subtitle tracks inside those videos were made by an older SubStudio and are not
references (SubStudio/CLAUDE.md, « Phase and what it forbids »). Until human subtitles for the same episodes are
added to a manifest entry's `refs`, the clips measure speed and footprint, not quality.

## Measuring one candidate

```bash
# 1. Run the candidate under the meter (take `threads.py lease heavy` first: one model at a time on this PC)
.venv/Scripts/python.exe -m bench.measure --out bench/clips/s05e02-hevc-1080p/run.json -- <command producing out.en.srt>
# 2. Score it against the human reference of the same clip and language
.venv/Scripts/python.exe -m bench.score --clip s05e02-hevc-1080p --label "turbo-int8-vad" \
  --hyp out.en.srt --ref bench/clips/s05e02-hevc-1080p/ref.en.srt --measure bench/clips/s05e02-hevc-1080p/run.json
```

**Reading a row:** `chrf_window` is the headline (text quality and timing within 30 s windows, robust to a
different cue split); `chrf_doc` is text only; `chrf_cue` exists only when cue counts match (translation of a human
file); `ref_windows_covered` is the share of the reference's speech windows where the candidate shows anything.
`within_budget: false` disqualifies a candidate as a default, whatever its score.

First measurement, 2026-11-19: cutting the three clips took 5.0 min, peak 2.88 threads, 0.17 GB (an encoder-only
`-threads 4` had peaked at 4.5 threads; decoder and encoder are now capped at 2 each).
