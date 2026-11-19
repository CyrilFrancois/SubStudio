# verdict.py — The quality gate: runs the sync and text measures on one subtitle and returns pass/fail with one sentence saying why. Exports: THRESHOLDS, Check, Verdict, evaluate.
"""« Properly synced? Good subtitle file? » (doc/user-process.md §3), answered by measurement only.

THRESHOLDS is the one place a limit lives; bench/gate_bench.py sets and re-checks them on the Ghosts S05 tracks
and their seeded bad copies (doc/gate-bench.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ..acquire.plan import normalize_language
from ..subtitles import Cue
from .speech import Span
from .sync import measure_sync
from .text import measure_text, missing_ids, untranslated

THRESHOLDS = {
    # Set on the 17 text tracks of Ghosts S05 (16 in sync by ffsubsync on the whole file and on both ends, 1 drifting)
    # and 128 seeded bad copies, 2026-11-19, re-checked on faster-whisper 1.2.1; doc/gate-bench.md has the numbers. In-sync tracks measured:
    # |offset| ≤ 340 ms, |drift| ≤ 240 ms, on_speech ≥ 0.83, speech_covered ≥ 0.98, within_cps ≥ 0.97.
    "max_offset_ms": 500,        # |shift| a viewer starts to notice
    "max_drift_ms": 500,         # change of offset between the two halves; S05E21 measures -590 (ffsubsync: -670)
    "min_on_speech": 0.75,       # share of dialogue-cue time on detected speech; the spec's 0.85 rejected 3-5 good tracks
    "min_speech_covered": 0.90,  # share of speech time with a cue within 1 s; a 60 % truncation gives ~0.6
    # Broadcast captions are near-verbatim: on the 17 Ghosts S05 tracks the median is 16-18 cps but the 95th
    # percentile is 25-32 cps (measured 2026-11-19), so the spec's « 20 cps » would reject every human track. 35 cps
    # still catches broken timing (cues cut short), which pushes most cues far past it.
    "max_cps": 35.0,             # reading speed of one cue
    "min_within_cps": 0.95,      # share of dialogue cues at or under max_cps
    "max_line_chars": 47,
    "max_long_line_share": 0.05,
    "max_duplicate_share": 0.02,
    "max_untranslated_share": 0.02,  # cues of 3+ words identical to their source cue (names and « OK. » are exempt)
}


@dataclass(frozen=True)
class Check:
    name: str
    value: object
    limit: object
    passed: bool
    why: str  # the sentence shown when this check fails


@dataclass(frozen=True)
class Verdict:
    passed: bool
    checks: List[Check] = field(default_factory=list)

    @property
    def reason(self) -> Optional[str]:
        """The first failed check's sentence: what Cyril reads next to a rejected subtitle."""
        return next((c.why for c in self.checks if not c.passed), None)


def evaluate(cues: Sequence[Cue], speech: Sequence[Span], language: str, label: Optional[str] = None,
             duration_ms: Optional[int] = None, source: Optional[Sequence[Cue]] = None,
             limits: Optional[dict] = None, timing_from_audio: bool = False) -> Verdict:
    """Gate one subtitle against the speech of its video, for the cell `language` (the language wanted, any tag
    form). `label` is the tag the track carries, if any: a missing tag is not a fault (Ghosts S05E22's English
    track has none), a wrong one is. `source` is given for a translation: its id set must equal the source's, and
    no cue may be left as the source text. `timing_from_audio` is True when the cue times were made from this
    video's own audio (a Whisper transcript, or a translation of one): when the speech has too few pauses to measure
    an offset, such timing passes, while a kept or downloaded track's cannot be shown in sync and does not."""
    t = {**THRESHOLDS, **(limits or {})}
    s = measure_sync(cues, speech, duration_ms)
    x = measure_text(cues, t["max_cps"], t["max_line_chars"])
    lines = sum(len(c.text.split("\n")) for c in cues)
    wanted = normalize_language(language)
    if wanted is None:
        raise ValueError(f"not a language: {language!r}")
    tagged = normalize_language(label)
    language_ok = x.language == wanted and tagged in (None, wanted)
    if s.offset_ms is None:  # too few speech pauses to locate a shift (a short clip): not measured, not « 0 »
        offset_check = Check("offset_ms", None, t["max_offset_ms"], timing_from_audio,
                             "timing not checked: too little speech in this video to measure the sync")
    else:
        offset_check = Check("offset_ms", s.offset_ms, t["max_offset_ms"], abs(s.offset_ms) <= t["max_offset_ms"],
                             f"out of sync: shifted {s.offset_ms / 1000:+.1f} s from the speech")
    checks = [
        offset_check,
        # Not measurable (too few cues per half) is shown as None, not as 0: the offset and on-speech checks still apply.
        Check("drift_ms", s.drift_ms, t["max_drift_ms"],
              s.drift_ms is None or abs(s.drift_ms) <= t["max_drift_ms"],
              f"timing moves {(s.drift_ms or 0) / 1000:+.1f} s between the first and second half"),
        Check("on_speech", s.on_speech, t["min_on_speech"], s.on_speech >= t["min_on_speech"],
              f"only {s.on_speech:.0%} of the subtitle time is on speech"),
        Check("speech_covered", s.speech_covered, t["min_speech_covered"],
              s.speech_covered >= t["min_speech_covered"],
              f"incomplete: {1 - s.speech_covered:.0%} of the speech has no subtitle"),
        Check("language", x.language, wanted, language_ok,
              f"wanted {wanted}, labelled {tagged or 'with no language'}, but the text is "
              f"{x.language or 'undetectable'}" if tagged else
              f"wanted {wanted} but the text is {x.language or 'undetectable'}"),
        Check("within_cps", x.within_cps, t["min_within_cps"], x.within_cps >= t["min_within_cps"],
              f"too fast to read: {1 - x.within_cps:.0%} of lines over {t['max_cps']:g} characters a second"),
        Check("long_line_share", round(x.long_lines / lines, 3), t["max_long_line_share"],
              x.long_lines / lines <= t["max_long_line_share"],
              f"{x.long_lines} lines longer than {t['max_line_chars']} characters"),
        Check("broken_chars", x.broken_chars, 0, x.broken_chars == 0,
              f"{x.broken_chars} broken characters (wrong text encoding)"),
        Check("duplicate_share", round(x.duplicates / x.cues, 3), t["max_duplicate_share"],
              x.duplicates / x.cues <= t["max_duplicate_share"], f"{x.duplicates} repeated lines"),
    ]
    if source is not None:
        gap = missing_ids([c.id for c in source], cues)
        checks.append(Check("id_set", len(gap), 0, not gap,
                            f"translation does not match its source: {len(gap)} lines missing or extra"))
        left = untranslated(source, cues)
        checks.append(Check("untranslated", len(left), t["max_untranslated_share"],
                            len(left) / len(cues) <= t["max_untranslated_share"],
                            f"{len(left)} lines left in the source language (cues "
                            f"{', '.join(map(str, left[:10]))}{', …' if len(left) > 10 else ''})"))
    return Verdict(all(c.passed for c in checks), checks)
