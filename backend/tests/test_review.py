# test_review.py — Per-line flags of the Review screen agree with the quality gate's own measures. Exports: tests only.

from api.review import flag_cues
from api.schemas import Cue, CueList
from api.stub_cues import SAMPLE
from core.gate import THRESHOLDS
from core.gate.text import measure_text
from core.subtitles import Cue as CoreCue


def _sample() -> CueList:
    return CueList(video_id="v", language="fr", origin="translated", from_language="en", cues=[
        Cue(id=i + 1, start_ms=a, end_ms=b, text=t, source_text=s) for i, (a, b, s, t) in enumerate(SAMPLE)])


def _flagged(lst: CueList):
    return {c.id: c.flags for c in lst.cues if c.flags}


def test_each_seeded_defect_is_flagged_and_nothing_else():
    assert _flagged(flag_cues(_sample())) == {
        6: ["too_fast", "long_line"], 9: ["long_line"], 12: ["untranslated"], 15: ["broken_text"], 18: ["duplicate"]}


def test_limits_are_the_gates():
    lim = flag_cues(_sample()).limits
    assert (lim.max_cps, lim.max_line_chars) == (THRESHOLDS["max_cps"], THRESHOLDS["max_line_chars"])


def test_counts_agree_with_the_gate_on_the_same_cues():
    out = flag_cues(_sample())
    core = [CoreCue(id=c.id, start_ms=c.start_ms, end_ms=c.end_ms, text=c.text) for c in out.cues]
    m = measure_text(core, THRESHOLDS["max_cps"], THRESHOLDS["max_line_chars"])
    flags = [f for c in out.cues for f in c.flags]
    long_lines = sum(1 for c in out.cues for line in c.text.split("\n") if len(line) > THRESHOLDS["max_line_chars"])
    assert flags.count("long_line") == long_lines == m.long_lines
    assert flags.count("duplicate") == m.duplicates
    assert round(1 - flags.count("too_fast") / len(out.cues), 3) == m.within_cps


def test_a_clean_track_has_no_flag_and_a_cue_without_source_is_never_untranslated():
    lst = CueList(video_id="v", language="en", origin="transcribed", cues=[
        Cue(id=1, start_ms=0, end_ms=2000, text="This is a fine line."),
        Cue(id=2, start_ms=2500, end_ms=4500, text="So is this one, here.")])
    assert _flagged(flag_cues(lst)) == {}
