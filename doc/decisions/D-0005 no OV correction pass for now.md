# D-0005 Rung 4 ships without the OV correction pass

**Date:** 2026-11-19 · **Decided by:** Claude (workspace-64, merging thread) on workspace-54's measurements, within
D-0002 · **Status:** accepted; reopened by the evidence named below.

**Context.** D-0002 (Cyril's process) asks, at rung 4, to « evaluate and do it again to correct it in OV » after
speech-to-text. workspace-54 built it on branch `feat/ov-correction` (dad70ec): re-run faster-whisper on segments it
doubts (avg_logprob < -1.0, compression ratio > 2.4, no-speech > 0.6 with text, too fast to read), keep a re-run only
if more confident, then fix near-miss names against the looked-up glossary (`core/context`), text only; the quality
gate keeps the better of first and corrected.

**Measured** (`bench/correct_bench.py`, 3 five-minute clips of Ghosts S05, Whisper turbo; references: gate-passed
English tracks for E07 and E02, a downloaded and re-timed track for E01; production flags and, labelled, a lowered
doubt threshold of -0.4):

| Clip | WER first | WER corrected (production) | WER corrected (lowered) | What changed |
|---|---:|---:|---:|---|
| S05E07 | 9.69 % | 9.81 % | 9.92 % | « Peter → Pete »; the one re-heard span got worse |
| S05E02 | 11.49 % | 11.49 % | 11.49 % | « Farnsby's → Farnsby » ×5; 3 spans flagged, no re-run more confident |
| S05E01 | 6.16 % | 6.16 % | 6.16 % | nothing flagged |

The gate passed every transcript before and after.

**Decision.** The production pipeline transcribes once and gates the result; no correction pass. The branch is kept,
not merged.

**Why.** It never lowered the error rate, and twice raised it: a near-miss to a glossary name is not evidence of a
mishearing (Peter is a real name), the word pattern stripped possessives, and turbo is rarely unsure on this show, so
re-hearing found nothing better.

**What would reverse it.** A signal that a word was misheard beyond « close to a glossary name » (for example the
transcript word is not itself a known name or dictionary word), shown to lower WER on more than one clip, with no
clip worse.
