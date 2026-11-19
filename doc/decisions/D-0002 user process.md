# D-0002 The user process: select, languages OV·EN·FR remembered, ladder with a quality gate, file updated in place

**Date:** 2026-11-19 · **Decided by:** Cyril · **Status:** accepted. In-place replacement confirmed by Cyril the same
day (via the Steward): « Yes, everywhere, after the check », `C:\Videos` included.

**Context.** The architecture review proposed a subtitle ladder (existing → download → translate → ASR) and sidecar
`.srt` files as the default output; the UI review proposed a coverage matrix. Neither said what Cyril himself does,
nor how a pre-existing subtitle is judged good.

**Decision.** Cyril's spec of 2026-11-19, written up in `doc/user-process.md`:
- Cyril selects one or more video files, then languages; default « Original Version, English, French », remembered
  between runs.
- Per video × language: keep a good existing subtitle → else download one online, re-time it → else translate a good
  subtitle with looked-up context (episode summary for a series) → else ASR in OV, evaluate, correct, then translate.
- Every result passes a gate: properly synced, good subtitle file.
- The video file is « quietly updated », with a marker like `(Sub-OV,EN,FR)` at the end of its name.
- Progress bar, validated files shown, top-quality UX.

**Consequences.** Sidecar-by-default is dropped in favour of in-place update, done only by the verified swap
(temp file → stream, duration ±1 s and track checks → atomic rename; a failed check leaves the source untouched;
`cleanUp=true` stays forbidden; rule in `CLAUDE.md` « Forbidden »); the
quality gate becomes its own work item and the key to `count_existing`; ASR gains a correction pass; language
settings move to backend-persisted settings.

**What would reverse it.** In-place replacement found to lose data or break a media server's library; or the gate
unable to tell good from bad subtitles on the bench set.
