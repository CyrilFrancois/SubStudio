# D-0001 Local, free models within half the machine

**Date:** 2026-11-19 · **Decided by:** Cyril · **Status:** accepted

**Context.** SubStudio called a paid cloud LLM for translation (OpenAI on `main`, Gemini on
`feat/gemini-and-stop-job`) and ran Whisper `medium` with no resource cap. The host is a laptop: Ryzen 5 7520U
(4 cores / 8 threads), 15 GB RAM, integrated Radeon, no CUDA (measured 2026-11-19).

**Decision.** « I want to use the best models, for free, that can run locally (without eating all the computer
resources, max half the computer) » (Cyril, 2026-11-19). Every model on the default path is open-weight, free and
local; the stack is capped at 4 threads and about 7 GB RAM, enforced in `docker-compose.yml` and in each runner's
thread setting. A cloud backend may exist only as an opt-in option behind the same interface.

**Clarified 2026-11-19 (workspace-64, on workspace-29's measurement).** « 4 threads » is each model runner's own
setting (CTranslate2 `cpu_threads=4`, llama.cpp `--threads 4`) and the sustained average. The Python thread and the
ffmpeg decoder add a brief overshoot (Whisper turbo: mean 3.2–3.5, peak 4.06–4.09 threads on two 5-min clips); it is
accepted rather than capping the model at 3 threads, which would slow every transcription for a sub-second peak.
Native runs are no longer capped by `docker-compose.yml`; the shared `heavy` lease with a free-RAM floor
(`threads.py with heavy --min-free-mb`) keeps one heavy job at a time instead.

**Consequences.** Translation moves to a small local model (TranslateGemma 4B class on llama.cpp; the benchmark task
picks the winner); one heavy model is loaded at a time; a 45-minute episode is expected to take tens of minutes, not
seconds, so batches are an overnight job. Downloading human subtitles first (free providers) becomes the main quality
lever. Details: `doc/architecture-review-2026-11-19.md`.

**What would reverse it.** A measured translation quality on the bench set that Cyril finds unacceptable at every
local candidate, or a run time he will not accept.
