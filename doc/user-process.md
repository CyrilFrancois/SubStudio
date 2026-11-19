# SubStudio: the user process (Cyril's spec of 2026-11-19)

**What this answers:** what Cyril does in SubStudio and what SubStudio does for each selected video, from the
selection to the validated file. It is the product spec every SubStudio thread builds against. It sits above
`architecture-review-2026-11-19.md` (how the backend does it) and `ux-review-2026-11-19.md` (how the screens show it);
where they disagree with this file, this file wins and they get fixed. Decision: `decisions/D-0002 user process.md`.

Cyril's words (2026-11-19, translated where needed) are quoted « like this »; everything else is Claude's reading,
marked **(inferred)** or **(hypothesis)** where it goes beyond the words.

## 1. What Cyril does: three steps

1. **Select one or several video files**, which may or may not already carry subtitles (embedded or sidecar).
   Selection works on files, not only on whole folders.
2. **Select the subtitle languages.** Default: **« Original Version », « English », « French »**. The choice is
   **remembered between runs**: « if the user changes it, it's because he wants those languages ».
   - Stored by the backend (settings file in the data volume), not only in the browser, so a new tab or another
     browser opens on the same choice **(inferred)**.
   - **OV is the language of the audio**, detected (the audio stream's language tag, else ASR language detection on
     a short sample). « OV can already be in English or French »: then OV and that language are **one track**,
     produced and checked once **(inferred)**.
3. **Start**, then watch progress. Nothing else is asked: no engine, model or workflow choice on the main path.

## 2. What SubStudio does, for each video and each wanted language

The order is Cyril's. Each rung ends at the **quality gate** (§3); a rung whose result fails the gate falls through to
the next one.

| # | Situation | What SubStudio does | Gate |
|---|---|---|---|
| 1 | A subtitle in that language is already in the file (or next to it) | Evaluate it. Good → **nothing to do, the file stays unchanged.** | sync + quality |
| 2 | Missing or failed rung 1 | **Search online and download** it in that language (normally better quality than any machine output); re-time it to the audio (`ffsubsync`) and inject it. | sync + quality |
| 3 | Not found online, but a **good** subtitle exists in another language (prefer OV) | **Translate it locally**, « knowing the context of the video »: title, and for a series episode the series, season, episode and an episode summary (looked up, never invented by the model). | quality + translation checks |
| 4 | No good subtitle anywhere, online or local | **Speech-to-text in OV**, then **evaluate and run a correction pass** on the OV text (« evaluate and do it again to correct it in OV »), then translate the corrected OV into the other languages (rung 3). | sync + quality, before and after correction |

Notes:
- Rung 2 runs **for OV too**: a human OV subtitle downloaded beats ASR, and becomes the source for rung 3.
- A good subtitle found at rung 1 or 2 in one language is the source for the others (rung 3), so ASR runs only when
  no language has a usable subtitle.
- The correction pass of rung 4 was built and benched (re-run of ASR on flagged segments + name fixes from the
  glossary, text only) and **made no transcript better** on 3 clips (WER equal or worse). **Rung 4 ships without it**
  (D-0005): the gate still evaluates the transcript, and a failing one fails its cell with the reason. The code stays
  on branch `feat/ov-correction` until a better signal is found.

## 3. The quality gate: what « good » means

Cyril asks two questions of every subtitle: **« properly synced? good subtitle file? »**. The gate answers them by
measurement first, by a model only for what needs judgement (workspace rule: a computable value is computed).

| Check | Measured how | Threshold |
|---|---|---|
| Synced | offset of the cues against detected speech (Silero VAD, 10 ms cross-correlation), its change between the two halves, share of dialogue-cue time on speech | **set on the bench:** \|offset\| ≤ 0.5 s, \|change\| ≤ 0.5 s, ≥ 75 % on speech (good human tracks: ≥ 83 %; the guessed 85 % rejected 3 of them) |
| Right language | language identification on the cue text vs the language wanted; a track's tag, when present, must agree (a missing tag is no fault) | must match |
| Complete | share of speech time with a cue within 1 s (no 10-minute gap while people talk) | **set on the bench:** ≥ 90 % (good tracks: ≥ 98 %) |
| Readable | characters per second, line length, duplicated cues, valid encoding (no U+FFFD, no UTF-8 read as cp1252, no `?` inside a word) | **set on the bench:** ≤ 35 cps on 95 % of cues; the guessed 20 cps rejected every human broadcast caption (their 95th percentile is 25–32 cps) |
| Translated | every cue id present; no cue of 3+ words left identical to its source cue | exact; ≤ 2 % untranslated |
| Fluent (judgement) | a local model scores a sample of cues; gated by the checks above, never alone | to define on the bench |

Implemented in `backend/core/gate` (one limit table, `THRESHOLDS` in `verdict.py`); how each limit was set and the
proof both ways (17 real tracks, 128 seeded bad copies): `doc/gate-bench.md`.

Each video × language ends with a **verdict and its origin** (`kept`, `downloaded`, `translated from <lang>`,
`transcribed`) and, when it failed, **one sentence saying why**.

Relation to the existing rule `count_existing` (CLAUDE.md « Phase »): today every pre-existing subtitle is ignored.
With the gate, rung 1 **evaluates** them instead. The 15 « English (SDH) » tracks of `C:\Videos` look like real release
captions (Cyril, 2026-11-19): the bench checks their timing against the audio first, and **in sync they are English
references the gate must accept**. The gate is proven on the bench in both directions before `count_existing` changes:
it accepts those in-sync release tracks, and rejects seeded bad ones (shifted timing, wrong language, broken
encoding). A gate that passes everything would silently skip work; one that rejects good tracks would redo it.

## 4. The result: the file, quietly updated

« Ideally, the video files are quietly replaced/updated so the user doesn't see any change on the files themselves,
except maybe a `(Sub-OV,EN,FR)` at the end of the file name to validate the subtitles. »

- **Target:** the same file, same folder, with the new subtitle tracks inside, and a name marker listing the validated
  languages, for example `Ghosts S05E01 (Sub-OV,EN,FR).mkv`. No `{stem}_SubStudio.mkv` copy left beside it, so the
  library does not double in size.
- **How it is made safe (inferred, mechanism to build):** write the new file next to the source under a temporary
  name; verify it (same video and audio streams, same duration within 1 s, every new subtitle track present and
  decodable); only then swap it in with an atomic rename. A failed verification leaves the source untouched. Needs free
  disk equal to one video during the swap.
- **The marker lists the actual languages** (Cyril, 2026-11-19): `(Sub-EN,FR)` for an English-spoken video, never
  the literal `OV`, and each language once.
- **A video already good at rung 1 in every wanted language is left exactly as it was:** no new content, **no marker,
  no rename** (Cyril, 2026-11-19). The UI still shows it as validated.
- **Not MKV** (mp4): **converted to `.mkv`** by the same verified swap (Cyril, 2026-11-19), because MP4 only holds
  `mov_text` subtitles. The extension changes; the `.mp4` is removed only once the `.mkv` has passed the check.
- **Settled 2026-11-19:** Cyril confirmed in-place replacement everywhere, `C:\Videos` included, « after the check ».
  The verified swap above is the only allowed way (CLAUDE.md « Forbidden »); sidecar-by-default is dropped.

## 5. What Cyril sees: « the whole UX and UI need to be top »

- **Selection:** file picker and library checkboxes, multi-select, search.
- **Languages:** chips with the remembered default `OV · EN · FR`; OV shows the detected language (« OV = EN »).
- **Progress:** one overall bar for the batch and, per video, a stage stepper
  (Check → Download → Translate / Transcribe → Correct → Write) whose bar never goes backwards; ETA from measured speeds.
- **Validated files:** a check per video × language with its origin (`✓ kept`, `✓ downloaded`, `✓ translated`,
  `✓ transcribed`); videos already validated are visibly marked and skipped by « Start ».
- **Failures:** one sentence per failed video × language, and a retry for that one cell.
- Screen details and the backend contract: `ux-review-2026-11-19.md` §2–3.

## 6. Open questions (for Cyril)

1. ~~In-place replacement~~: answered 2026-11-19, yes everywhere after the check (§4).
2. ~~Marker~~: answered 2026-11-19: no marker on an untouched file; the actual languages, `(Sub-EN,FR)` (§4).
3. ~~MP4 sources~~: answered 2026-11-19: convert to `.mkv` (§4).

All answered. Residual risk, not a question: a rename or `.mp4` → `.mkv` may make Plex or Jellyfin treat the file as
new and lose its watch history **(hypothesis, untested)**.

## 7. How we will know it worked

- From opening the app to a running batch on 22 episodes: **select, (languages already set), Start: ≤ 3 clicks**.
- Every video × language ends `✓ <origin>` or a one-sentence failure: **100 %**, none silently skipped.
- Share of cells filled by rungs 1–2 (human subtitles) vs 3–4 (machine), reported per batch.
- A video whose subtitles were already good is **byte-identical and keeps its name** afterwards.
