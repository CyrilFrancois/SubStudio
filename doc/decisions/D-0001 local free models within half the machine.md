# D-0001 Local, free models within half the machine

**Date:** 2026-11-19 · **Decided by:** Cyril · **Status:** accepted

**Context.** SubStudio called a paid cloud LLM for translation (OpenAI on `main`, Gemini on
`feat/gemini-and-stop-job`) and ran Whisper `medium` with no resource cap. The host is a laptop: Ryzen 5 7520U
(4 cores / 8 threads), 15 GB RAM, integrated Radeon, no CUDA (measured 2026-11-19).

**Decision.** « I want to use the best models, for free, that can run locally (without eating all the computer
resources, max half the computer) » (Cyril, 2026-11-19). Every model on the default path is open-weight, free and
local; the stack is capped at 4 threads and about 7 GB RAM, enforced in `docker-compose.yml` and in each runner's
thread setting. A cloud backend may exist only as an opt-in option behind the same interface.

**Consequences.** Translation moves to a small local model (TranslateGemma 4B class on llama.cpp; the benchmark task
picks the winner); one heavy model is loaded at a time; a 45-minute episode is expected to take tens of minutes, not
seconds, so batches are an overnight job. Downloading human subtitles first (free providers) becomes the main quality
lever. Details: `doc/architecture-review-2026-11-19.md`.

**What would reverse it.** A measured translation quality on the bench set that Cyril finds unacceptable at every
local candidate, or a run time he will not accept.
