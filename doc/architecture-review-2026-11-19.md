# SubStudio: architecture review and target design (2026-11-19)

**What this answers:** where SubStudio stands today, what is wrong with its architecture, and the target design under
Cyril's constraint of 2026-11-19: **the best models, free, running locally, using at most half the computer.**
The ordered work list lives in the workspace `TODOLIST.MD`, « [SubStudio] ». Model facts carry their source and date;
speeds are **hypotheses until the benchmark task measures them on this PC**.

## 1. Current state (code read on branch `feat/gemini-and-stop-job`, 2fcb5e6)

- `main` (131e55b) still translates with **OpenAI `gpt-4o-mini`**. The branch `feat/gemini-and-stop-job` (6 commits,
  2026-11-18, unmerged, no DONELIST entry, so unverified) switches to Gemini over REST and adds: stop a job, a Sync
  input per video, a per-folder scan cache, embedded text-subtitle extraction. `main` is an ancestor of the branch
  (fast-forward). The project rules (`.claude/rules/engineering.md`) already describe the branch, not `main`.
- No `.env` on this PC, no Gemini or OpenAI key in `SECRETS.MD`: **the app cannot translate here today.**
- No tests at all (no pytest, no fixture media).

Pipeline today: filename → LLM « story bible » → Whisper (or a sidecar `.srt`) → LLM translation 30 SRT blocks per call
→ FFmpeg mux into `{stem}_SubStudio.mkv` next to the source.

## 2. The host budget (measured 2026-11-19)

| Resource | Machine | Half (the budget) |
|---|---|---|
| CPU | AMD Ryzen 5 7520U, 4 cores / 8 threads (Zen 2) | **4 threads** |
| RAM | 15 GB; Docker's VM sees 7.9 GB | **≤ 7 GB for the whole stack** |
| GPU | Radeon 610M integrated, no CUDA | none usable from Docker on Windows |

Consequences: one heavy model loaded at a time (ASR **or** translation, never both), stages run sequentially and
unload, and the budget is enforced mechanically (`cpus: 4`, `mem_limit` in `docker-compose.yml`, `OMP_NUM_THREADS=4`,
llama.cpp `--threads 4`), not by hope.

## 3. Architectural problems, by impact

1. **SRT is passed around as a raw string and re-parsed by four different regexes** (`main.split_long_lines`,
   `muxer.process_srt_content`, `translator` splitting on `\n\n`, `subtitle_processor` timestamps). The LLM receives
   indices and timestamps and is *asked* to keep them: a hope, not a mechanism. One dropped or merged block
   desynchronises everything after it with no error.
   **Fix:** one typed cue list (`pysubs2`) from input to output. The model receives only `[{id, text}]` and returns
   texts; Python re-attaches the timings by id and **rejects a batch whose id set differs** (retry, then split the
   batch). Timing corruption becomes impossible, not unlikely.
2. **A failed translation batch silently ships the source language** (`refine_and_translate` appends the untranslated
   batch). The job ends « done » with a half-translated track. **Fix:** retry, split, then fail the language loudly;
   never mark it done.
3. **Encoding:** sidecar subtitles are read `utf-8, errors="ignore"`; French subtitles are often cp1252, so every
   accent is deleted silently. **Fix:** detect the charset (`charset-normalizer`, via `pysubs2`).
4. **The Whisper cache volume is probably mounted at the wrong path:** `/root/.cache/whisper`, while faster-whisper
   downloads through Hugging Face to `/root/.cache/huggingface` **(hypothesis: check with `docker volume inspect` after a
   run)**. If true, every container recreation re-downloads the model (1.5 GB for large).
5. **Whisper's `initial_prompt` gets the whole story bible.** Whisper keeps only the last ~224 tokens of a prompt and a
   long prose prompt is a known cause of hallucinated text **(hypothesis from Whisper's design; to measure)**. Feed it a
   short list of names only.
6. **State lives in memory:** a restart, or `uvicorn --reload` on any backend save, loses every job and all
   intermediate work. A re-translation re-runs a 40-minute transcription. **Fix:** a small SQLite job table plus a
   per-video work folder caching each stage's artifact (audio, source cues, each translation), keyed by file hash and
   settings. Stages become resumable and idempotent.
7. **Model choice is wrong for this hardware:** default `medium` on 4 Zen 2 cores is slow and not the best quality;
   `faster-whisper==1.0.3` is pinned below the release that added `turbo` (1.1.0; latest 1.2.1, 2025-10-31,
   https://pypi.org/project/faster-whisper/, checked 2026-11-19). No VAD filter, so silences produce hallucinated lines.
8. **The output doubles the library's disk use:** every video is copied into a full new MKV. Plex, Jellyfin, VLC and mpv
   all read a sidecar `Movie.fr.srt` **(hypothesis: true for those players' default settings)**. ~~Make sidecar the
   default and muxing an option.~~ Cyril chose instead to update the source file in place with a `(Sub-…)` name
   marker (`user-process.md` §4), which also avoids the doubling.
9. **Smaller ones:** the ISO 639 map is hand-written and partial (use `langcodes`); `types.ts` mirrors backend JSON by
   hand (generate from FastAPI's OpenAPI); the scanner runs ffprobe on every video on every scan (cache by
   path + mtime + size); the `srt` workflow mode means the same as `hybrid`.

## 4. Target design

### 4.1 The subtitle ladder: the best subtitle is the one a human already wrote

**Superseded in part by Cyril's process, `user-process.md` (D-0002), which wins:** every rung ends at a quality gate
(synced? good file?), a pre-existing subtitle is evaluated rather than trusted, ASR gets an OV correction pass, and the
output is the source file updated in place by a verified swap (confirmed by Cyril), not a sidecar. The rungs below stay valid.

For each video and each wanted language, take the first rung that succeeds:

1. **Already there:** an embedded text track or sidecar in the target language → nothing to do (report it).
2. **Downloaded in the target language:** search by file hash, then by title, on free providers
   (OpenSubtitles.com REST API by `moviehash`, a free consumer key; `subliminal` aggregates several providers). Then
   **re-time it to the audio** with `ffsubsync` (local, CPU-light), because a downloaded file often drifts by a release.
3. **A human subtitle in another language** (embedded, sidecar or downloaded, preferring the original language) →
   translate it locally.
4. **No subtitle anywhere** → local ASR, then translate.

Rungs 2 and 3 skip ASR entirely and give better text than any ASR. Quota figures for OpenSubtitles are third-party
reports (200 downloads a day for a free account, https://apidog.com/blog/opensubtitles-api, unverified): check
https://opensubtitles.stoplight.io before relying on them.

### 4.2 Local models within half the machine (candidates; the benchmark decides)

| Stage | Candidate | Why | Footprint (approx.) |
|---|---|---|---|
| ASR, default | Whisper **large-v3-turbo** (`turbo`), faster-whisper ≥ 1.2, int8, `vad_filter=True`, 4 threads | near large-v3 quality, 99 languages, word timestamps | ~1.5–2 GB RAM |
| ASR, fast path for its 25 European languages | **NVIDIA Parakeet TDT 0.6B v3** via `onnx-asr` (CPU, int8) | reported much faster than Whisper on CPU with timestamps (https://github.com/istupakov/onnx-asr; speeds are vendor/blog claims) | ~1–2 GB · **Measured 2026-11-19: not adopted** (French WER 8.0 % vs turbo 3.8 %, no language detection or segment confidence; 2.5–4.7× faster on long audio): D-0004 |
| Translation, default | **TranslateGemma 4B** GGUF on `llama.cpp` server, Q8_0 (or Q4_K_M) | translation-tuned Gemma 3, 55 languages (https://arxiv.org/pdf/2601.09012); Q4_K_M matched bf16 on 47/74 test sentences vs 71/74 for Q8_0 (https://huggingface.co/bullerwins/translategemma-4b-it-GGUF) | 4.1 GB (Q8) / 2.5 GB (Q4) |
| Translation, challengers | Tencent **HY-MT1.5-1.8B** (smaller, faster), Qwen3-4B-Instruct (general), NLLB-200-distilled-1.3B on CTranslate2 (fast, no context, CC-BY-NC: personal use only) | compared in https://arxiv.org/html/2602.11961 | 1–3 GB |
| Re-timing | `ffsubsync` | aligns a subtitle to the audio track | light |

All free and open-weight. **Cyril's constraint rules out cloud translation (Gemini) as the default**; the Gemini code on
the branch can stay as an optional backend behind the same interface.

**Speed estimate (hypothesis):** 4B at Q4 on 4 Zen 2 threads, roughly 8–10 tokens/s; a 45-minute episode (~700 cues,
~7 000 output tokens) ≈ 12–15 min per language, plus turbo ASR ≈ 0.5–1× real time. Fine overnight, slow for one video.
Measured numbers replace this paragraph once the benchmark runs.

### 4.3 Context without a big model

A 4B model cannot « identify the show from its filename »: it would invent a cast. Replace the story bible with
**facts that can be looked up or computed:** the title from the filename (`guessit`), the proper nouns found in the
source cues (capitalised words that recur), and optionally a cast list from TMDB's free API. Send a glossary
(names that must not be translated) plus the previous 2–3 cues as context with each batch. Cache it per series.

### 4.4 Shape of the backend

```
scan → plan (choose a ladder rung per language) → acquire (download | extract | ASR) → align (ffsubsync)
     → translate (cue list, batched, validated by id) → write (sidecar, optional mux)
```
Each stage: a pure function `input artifact → output artifact`, cached in the work folder, recorded in SQLite.
One worker process, one heavy model loaded at a time. Model runners are separate services (llama.cpp server,
ASR) so they can be swapped, measured and capped individually.

### 4.5 How we will know it worked

- **Share of a batch that ends with every wanted language as a valid subtitle** (target: 100 % or a named error each).
- **Translation quality:** chrF against a reference on videos that already carry human subtitles in both languages
  (the bench set), plus Cyril's blind preference on 20 cues.
- **Minutes per 45-min episode** and peak RAM/CPU, within the budget of section 2.
