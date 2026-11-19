# D-0003 Local translator: TranslateGemma 4B Q4_K_M on llama.cpp, iGPU, batches of 10 cues

**Date:** 2026-11-19 · **Decided by:** Claude on the bench (task 3, « [SubStudio] Local translator »), within D-0001 ·
**Status:** accepted, open to the re-bench named below.

**Context.** D-0001 rules out a paid cloud model as the default and caps the stack at 4 threads and about 7 GB of RAM.
The architecture review (§4.2) named five candidates. This record holds the measured comparison and the pick.

**How it was measured.** `bench/translate/bench_translate.py` on 150 English subtitle lines with human French
references (OpenSubtitles, via `loicmagne/open-subtitles-bitext-mining`, en-fr, rows 1–150), EN→FR, greedy decoding.
Every llama.cpp run starts its own native server (b12532, Vulkan build) with `--threads 4 -c 4096`. Raw rows:
`bench/translate/results.jsonl`; outputs per cue: `bench/translate/out/` (local only, gitignored);
`bench/translate/rescore.py` scores every run on the cues all runs answered.

**Host conditions (read every number in this light).** Measured on Cyril's laptop **while it was in use**: about 10
Claude threads, VS Code and ImmA's Docker stack, host CPU 92–100 % before each run, 1.9–5.2 GB of RAM free, up to
3.9 GB of swap in use. Speeds on a quiet machine will be higher, by an amount not measured yet.

## Results (2026-11-19)

| Candidate | Mode | s / cue | Gen tok/s | Peak RAM | chrF (150) | chrF (135 common) | Lost ids |
|---|---|---:|---:|---:|---:|---:|---|
| **TranslateGemma 4B Q4_K_M** | 10 cues / call, iGPU | **5.5** | 3.3 | 3.9 GB | **45.5** | **46.3** | 0 / 15 batches |
| TranslateGemma 4B Q4_K_M | 1 cue / call, iGPU | 7.9 | 3.4 | 3.4 GB | 42.1 | 42.3 | 0 |
| Qwen3-4B-Instruct-2507 Q4_K_M | 10 cues / call, iGPU | 5.1 | 3.2 | 3.7 GB | 44.3 | 44.3 | 0 / 15 |
| HY-MT1.5-1.8B Q8_0 | 10 cues / call, iGPU | 3.6 | 4.6 | 2.7 GB | 38.3 | 42.6 | **2 / 15** (15 cues) |
| NLLB-200-distilled-1.3B, CT2 int8 | 16 sentences, CPU 4 thr., beam 4 | 7.3 | n/a | 1.7 GB ¹ | 50.8 | 50.7 | 0 |
| TranslateGemma 4B Q8_0 | — | — | — | needs 4.8 GB | **not run**: 4.3 GB free | | |

Peak RAM is the model process's resident set. ¹ During the NLLB run free system RAM dropped by 4.1 GB (1.1–1.6 GB
for the llama.cpp runs); another thread's VAD job overlapped it, so how much of that is NLLB's is not known.
The first row of `results.jsonl` is a 20-cue HY-MT smoke test, not part of the comparison.
**Lines echoed untranslated:** TranslateGemma returned 3 of 150 lines unchanged (« Lame. », « ♪ », « Assassin ! »),
Qwen3 4, HY-MT 2. The translator does not reject an echo (« OK. », a name, « ♪ » are legitimate); the quality gate's
« no cue left in the source language » check is what catches a real miss.

CPU vs iGPU (llama-bench, HY-MT 1.8B Q8, same busy host): CPU 4 threads 13.5 tok/s prompt, 5.0 tok/s generation;
Vulkan iGPU 85.5 / 4.7. **The iGPU generates as fast and reads prompts 6× faster while leaving the CPU free** (llama.cpp
runs used 0.2–0.26 of a CPU on average), which is what « half the machine » asks for. Its memory is system RAM
(UMA), so it still counts in the 7 GB.

## Decision

**TranslateGemma 4B Q4_K_M, all layers on the integrated GPU, 10 cues per call as `id|text` lines.** Implemented
as `backend/core/translators/llamacpp.py`; Gemini stays as `TRANSLATOR=gemini`, opt-in.

Why not the others:
- **NLLB** scores highest but is not chosen. (1) It makes **meaning errors on short lines**, which dominate subtitles:
  « What? » → « Je ne sais pas. », « Fuck. » → « C'est pas vrai. ». (2) Its lead is probably inflated: it reproduced the
  human reference word for word on 8 of 69 lines of 6+ words, against 2 for TranslateGemma, consistent with OPUS
  OpenSubtitles being in its training data **(hypothesis)**. (3) One sentence at a time, no glossary or context, which
  rung 3 of the user process needs. (4) CC-BY-NC weights. (5) It was not faster here.
- **Qwen3 4B** is 1.2 chrF points behind, within the noise of 150 lines; it is the challenger once context is sent
  (it follows instructions; TranslateGemma's template takes only the text).
- **HY-MT 1.8B** lost the id prefix on 2 of 15 batches and scores lowest on common cues.
- **Q8_0** did not fit in the RAM free on this PC during the day; Q4_K_M is the default until a quiet-machine run shows
  Q8 fits and is worth +1.6 GB. The model card reports Q4_K_M matching bf16 on 47/74 sentences against 71/74 for Q8_0
  (https://huggingface.co/bullerwins/translategemma-4b-it-GGUF, read 2026-11-19).
- Per-cue calls are slower and worse (no neighbouring lines): batches stay at 10.

## Consequences

- At 5.5 s per cue on a busy PC, a 45-minute episode (~700 cues) takes **about 1 hour per language**: an overnight
  batch, not an interactive one. A quiet-machine measurement is owed before quoting a time to Cyril.
- The model is loaded only during a video's translation and unloaded before the next transcription (`unload()`).
- The bench set is generic OpenSubtitles lines from many shows: references are loose (subtitles condense), so chrF
  ~45 is a ranking, not an absolute quality. It says nothing about context use.

## What would reverse it

- A re-bench **with context** (episode summary + glossary + previous cues, rung 3 of `doc/user-process.md`) where
  Qwen3 4B or another model beats TranslateGemma on Ghosts episodes with good references.
- A quiet-machine run where Q8_0 fits within D-0001's 7 GB and gains clearly on the bench.
- Cyril's blind preference on 20 cues going against the chrF ranking.
