# Quality gate: how its limits were set, and the proof both ways (2026-11-19)

**What this answers:** why the gate (`backend/core/gate`, `doc/user-process.md` §3) accepts or rejects a subtitle,
where each limit comes from, and whether it can be trusted to keep good subtitles and redo bad ones. For the threads
that wire it into the pipeline, and for Cyril. Rerun: `python -m bench.gate_bench "C:/Videos/Ghosts/Saison 5"`
(Git Bash, worktree root, ffmpeg on PATH; ~9 min, reads the videos only).

## The question Cyril settled first

Every one of the 17 text subtitle tracks inside `C:\Videos\Ghosts\Saison 5` (15 episodes; 8 ASS, 9 SubRip; titles
« English (SDH) », « SDH » or none) reads as human broadcast captioning (speaker names, two-speaker dashes, sound
cues), and none matches what the old SubStudio muxer wrote (a `language=` tag only, SRT only, a `_SubStudio.mkv` name).
Cyril chose « check them first » (2026-11-19): the gate must **accept the ones measured in sync and reject the rest**.

## How « measured in sync » was established independently

The gate measures with Silero VAD (faster-whisper's) and its own cross-correlation. The referee is a different tool
and VAD: **ffsubsync** (WebRTC VAD), run against the episode's **audio only**, on the whole file and on its first and
last thirds; in sync = every one of the three within 0.5 s and the two ends within 0.5 s of each other.

Two traps found on the way, both fixed:
- **ffsubsync given the video aligns to a subtitle track inside it** (its default `subs_then_webrtc`), so it compared
  each track with itself and read 0.0 on all 17. Audio-only it reads 0.39 s on S05E18 (gate: 0.33 s). The same default
  affects `core/acquire/align.py` (rung 2 re-timing): reported to its owner.
- **A whole-file offset hides a jump inside an episode.** S05E21 reads +0.25 s overall, but +0.22 s on its first
  third and −0.45 s on its last; the gate's drift check (offset of the second half minus the first) sees −0.59 s (−0.78 s on faster-whisper 1.0.3).

## Result

| | Count | Gate agrees with the referee |
|---|---|---|
| Real tracks | 17 | **17 / 17**: 16 accepted (in sync), S05E21 rejected (« timing moves −0.8 s between the first and second half ») |
| Seeded bad copies of the 16 good tracks | 128 (8 faults × 16) | **128 / 128 rejected, each for its intended check** |

Seeded faults: shifted +3 s and −1.5 s (offset), 25/23.976 frame-rate stretch (drift), cut at 60 % (coverage), tagged
French (language), English asked as French (language), UTF-8 read as cp1252 (encoding), durations halved (reading
speed). On the 16 good tracks the gate's offset and ffsubsync's whole-file offset differ by at most 0.12 s.
Cost of a full run: 10.4 min, peak 2.4 threads, 0.70 GB (`bench/measure.py`), within D-0001.
**Re-checked on faster-whisper 1.2.1** (main's version since 2026-11-19; its Silero build gives slightly different
spans): same verdicts, 17 / 17 and 128 / 128; numbers below are from that run, `bench/results/gate.jsonl`.

## The limits (`THRESHOLDS`, `backend/core/gate/verdict.py`)

| Check | Limit | Good tracks measured | Why this value |
|---|---|---|---|
| \|offset\| | ≤ 500 ms | ≤ 340 ms | where a viewer starts to notice |
| \|drift\| (second half − first) | ≤ 500 ms | ≤ 240 ms | S05E21: 590 ms (ffsubsync, by thirds: 670 ms); frame-rate faults: tens of seconds |
| dialogue-cue time on speech | ≥ 75 % | 83.5–92 % | the spec's guessed 85 % rejected 3 good tracks (5 on 1.0.3) |
| speech covered (cue within 1 s) | ≥ 90 % | 98.2–100 % | a 60 % truncation gives ~60 % |
| cues ≤ 35 cps | ≥ 95 % | 97.4–99.8 % | human captions: median 16–18 cps, 95th percentile 25–32: the guessed « 20 cps » rejected all 17 |
| lines > 47 chars | ≤ 5 % | 0 % | |
| broken characters | 0 | 0 | U+FFFD, UTF-8 read as cp1252, `?` inside a word |
| consecutive duplicates | ≤ 2 % | ≤ 0.3 % | |
| translation id set / untranslated | exact / ≤ 2 % | (no translation in this corpus) | by id, exactly; lines under 3 words exempt |

Sound-only cues (« [laughs] », « (door closes) », « ♪ ») are excluded from the sync measures. Drift needs 20 dialogue
cues **and 40 speech spans** per half; below either it is reported as not measured (None), not as 0. The speech
spans are what locate a half's offset: the 15 cached episodes hold 71–161 per half, while a 5-min clip whose
dialogue barely pauses (S05E07 120–420 s, ~22 per half) let each half's offset land on noise, and Whisper's own
transcript of it read « timing moves −53.5 s » (fixed 2026-11-19; a peak-sharpness guard was rejected because it
also hid the seeded frame-rate faults). Re-run after the fix: same verdicts, 17 / 17 and 128 / 128.

## Limits of this proof

- One series, one language (English), one episode length (21 min), all broadcast captions. A downloaded fan subtitle,
  a 45-min drama or a French track has not been measured; the thresholds should be re-checked when such a corpus exists.
- The limits hold for speech spans from `core.gate.detect_speech` (300 ms silences split spans, 100 ms padding).
  `AsrResult.speech` (`core/transcriber.py` on main 78dc6d3) uses `VadOptions(min_silence_duration_ms=500)` with the
  default 400 ms padding: wider spans, so a higher on-speech share and a different coverage. Feed the gate its own
  spans, or re-measure the limits on ASR spans before sharing them.
- « Fluent » (a model's judgement on a sample) is not built: the spec gates it behind the measured checks anyway.
