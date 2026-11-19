# D-0004 Speech-to-text stays on Whisper large-v3-turbo; Parakeet is not adopted

**Date:** 2026-11-19 · **Decided by:** Claude (workspace-2d, ASR task) on its own measurements, within D-0001 and
D-0002 · **Status:** accepted; reopened by the evidence named below.

**Context.** Rung 4 of D-0002 transcribes the audio in its original language (OV) when no usable subtitle exists.
`feat/asr` moved the default from Whisper `medium` to faster-whisper 1.2.1 `large-v3-turbo` (int8, Silero VAD,
4 threads). The open question was whether NVIDIA Parakeet TDT 0.6B v3 (via onnx-asr 0.12.0, int8), reputed several
times faster on CPU, should replace it or serve as a fast path.

**Measured** (`bench/asr_bench.py`, one engine per process, 4 threads, same options as `backend/core/transcriber.py`
for the long files; rows in `bench/results/asr.jsonl`). WER on 40 public labelled clips per language (MLS French,
LibriSpeech test-clean English); RTF = processing time / audio time, lower is faster; long files are 8 to 10 min of
continuous speech (LibriVox English and French, and a Ghosts S05E02 excerpt, speed only: the video's own subtitles are
not references).

| | Whisper turbo | Parakeet v3 |
|---|---:|---:|
| WER French (10.4 min) | **3.8 %** | 8.0 % |
| WER English (6.3 min) | **0.5 %** | 0.7 % |
| RTF long, English | 0.93 | **0.21** |
| RTF long, French | 1.14 | **0.24** |
| RTF long, Ghosts episode | 0.80 | **0.32** |
| Model load | 20.7 s | **5.2 s** |
| Peak RAM | **1.8 GB** | 2.5 GB |
| Spoken-language detection | yes, with probability | none |
| Per-segment confidence (what the quality gate reads) | avg_logprob, no_speech_prob, compression ratio | none |

The host was shared with other threads during both runs (total CPU load read 100% during an earlier attempt), so
the absolute speeds are pessimistic; both engines ran under the same conditions on the same day.

**Decision.** Whisper large-v3-turbo stays the only speech-to-text engine. Parakeet is not added, not even as an
English fast path.

**Why.**
- **French quality.** Parakeet's French error rate is twice turbo's, and its errors are of the worst kind for
  subtitles: on the LibriVox French reading it switched words into English (« To nos enregistrement font partie du
  domaine public », « For plus d'information »).
- **The pipeline needs what only Whisper gives.** D-0002 needs the OV detected before planning, and the quality gate
  reads per-segment confidence. Parakeet as called here returns neither, so it could only run after a Whisper pass
  anyway.
- **Speed is not the binding constraint.** Turbo runs at roughly real time on a 45-minute episode (about 36 to 50 min
  at the measured 0.8 to 1.1), which fits D-0001's overnight batches, and rung 4 is reached only when no human subtitle
  was found.

**Consequences.** One ASR engine, one model in RAM, no onnx-asr dependency in the backend. A 45-minute episode
reaching rung 4 costs about 40 min of the 4-thread budget.

**What would reopen it.**
- A run where rung-4 transcription time is the bottleneck Cyril complains about. Try turbo with `beam_size=1` first
  (not measured; usually much faster for a small quality cost), then Parakeet on English-only audio.
- A Parakeet runtime that exposes language detection and per-segment confidence.
- A French WER for Parakeet within one point of turbo's on the same clips.
