# correct.py — rung 4's OV correction pass: re-transcribe the doubtful segments, fix names against the looked-up glossary, keep the better of before/after by the gate. Exports: flagged_spans, merge_rerun, fix_names, correct_ov.
"""
doc/user-process.md §2, rung 4: « evaluate and do it again to correct it in OV ». What is computable is computed:
which segments are doubtful comes from Whisper's own scores and the reading speed, the replacement is kept only
when the re-run is more confident, names are matched to the looked-up glossary (core/context), and the gate (core/gate)
decides between the first transcript and the corrected one. The names pass changes text only, never a timing.
"""
import difflib
import logging
import re
from dataclasses import replace
from typing import Callable, List, Optional, Sequence, Tuple

from .asr_result import AsrResult, AsrSegment
from .subtitles import Cue

logger = logging.getLogger("SubStudio.Correct")

# Whisper's own thresholds (faster-whisper's defaults: log_prob_threshold -1.0, compression_ratio_threshold 2.4,
# no_speech_threshold 0.6), so « doubtful » means what the model itself would have retried.
MIN_AVG_LOGPROB = -1.0
MAX_COMPRESSION = 2.4
MAX_NO_SPEECH = 0.6
PAD_S = 0.3        # context around a doubtful segment, so the re-run hears whole words
MIN_NAME_RATIO = 0.84  # « Higgintoot » ← « Higginstoot » (0.95) yes; « Jay » ← « Day » never (names under 4 letters are left)


def _doubtful(s: AsrSegment, max_cps: float, min_logprob: float) -> bool:
    chars = len(s.text.strip())
    cps = chars / max(s.end - s.start, 0.1)
    return (s.avg_logprob < min_logprob or s.compression_ratio > MAX_COMPRESSION
            or (s.no_speech_prob > MAX_NO_SPEECH and chars > 0) or cps > max_cps)


def flagged_spans(asr: AsrResult, max_cps: float, min_logprob: float = MIN_AVG_LOGPROB) -> List[Tuple[float, float]]:
    """The doubtful segments as padded, merged spans in seconds, ready for transcribe(spans=...). `min_logprob` is
    Whisper's own -1.0; only a bench lowers it, to exercise the re-hear path, and says so in its report."""
    spans: List[Tuple[float, float]] = []
    for s in asr.segments:
        if not _doubtful(s, max_cps, min_logprob):
            continue
        a, b = max(0.0, s.start - PAD_S), min(asr.duration or s.end + PAD_S, s.end + PAD_S)
        if spans and a <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], b))
        else:
            spans.append((a, b))
    return spans


def merge_rerun(asr: AsrResult, rerun: AsrResult, spans: Sequence[Tuple[float, float]]) -> Tuple[AsrResult, int]:
    """The first transcript with each span's segments replaced by the re-run's, only where the re-run is more
    confident (higher mean avg_logprob). Returns the merged result and how many spans were replaced."""
    def inside(seg, a, b):
        mid = (seg.start + seg.end) / 2
        return a <= mid <= b

    keep, replaced = list(asr.segments), 0
    for a, b in spans:
        old = [s for s in keep if inside(s, a, b)]
        new = [s for s in rerun.segments if inside(s, a, b) and s.text.strip()]
        if not old or not new:
            continue
        if sum(s.avg_logprob for s in new) / len(new) <= sum(s.avg_logprob for s in old) / len(old):
            continue
        keep = [s for s in keep if s not in old] + new
        replaced += 1
    keep.sort(key=lambda s: s.start)
    return replace(asr, segments=keep), replaced


_WORD = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)


def fix_names(cues: List[Cue], glossary: Sequence[str]) -> Tuple[List[Cue], List[Tuple[str, str]]]:
    """Capitalised words that are a near miss of a glossary name (one word, 4+ letters, same initial) take its
    spelling. Text only. Returns the cues and every change as (heard, written), so a run can show what it did.
    Only capitalised words: Whisper capitalises what it heard as a name; a lower-case word is a common word, and
    rewriting one into a name made the bench worse (S05E07, 2026-11-19: WER 9.69 % -> 10.03 %)."""
    names = {g.lower(): g for g in glossary if " " not in g and len(g) >= 4}
    if not names:
        return cues, []
    changes: List[Tuple[str, str]] = []
    out = []
    for c in cues:
        def sub(m):
            w = m.group(0)
            if len(w) < 4 or not w[0].isupper() or w.lower() in names:
                return w
            best = difflib.get_close_matches(w.lower(), names, n=1, cutoff=MIN_NAME_RATIO)
            if best and best[0][0] == w.lower()[0]:
                changes.append((w, names[best[0]]))
                return names[best[0]]
            return w
        out.append(replace(c, text=_WORD.sub(sub, c.text)))
    return out, changes


def _failures(verdict) -> int:
    return sum(1 for ch in verdict.checks if not ch.passed)


def correct_ov(asr: AsrResult, rerun: Optional[Callable[[List[Tuple[float, float]]], AsrResult]],
               glossary: Sequence[str], judge: Callable[[List[Cue]], object], max_cps: float,
               min_logprob: float = MIN_AVG_LOGPROB) -> Tuple[List[Cue], str]:
    """The better of the first transcript and its correction, by the gate (`judge` → Verdict): a passing one beats a
    failing one, then fewer failed checks; a tie keeps the correction. Returns the cues and one line saying what
    happened (for the log and the cell's attempts)."""
    first = asr.to_cues()
    spans = flagged_spans(asr, max_cps, min_logprob) if rerun else []
    merged, replaced = (merge_rerun(asr, rerun(spans), spans) if spans else (asr, 0))
    corrected, changes = fix_names(merged.to_cues(), glossary)
    renamed = len(changes)
    if not replaced and not renamed:
        return first, f"correction: nothing to correct ({len(spans)} doubtful spans)"
    before, after = judge(first), judge(corrected)
    better = (after.passed, -_failures(after)) >= (before.passed, -_failures(before))
    shown = f" ({', '.join(f'{a}->{b}' for a, b in changes[:8])})" if changes else ""
    note = (f"correction: {replaced}/{len(spans)} doubtful spans re-transcribed, {renamed} names fixed{shown}; "
            f"gate {'passed' if before.passed else 'failed'} → {'passed' if after.passed else 'failed'}; "
            f"kept the {'corrected' if better else 'first'} transcript")
    logger.info(note)
    return (corrected if better else first), note
