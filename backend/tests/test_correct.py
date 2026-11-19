# test_correct.py — rung 4's OV correction pass with fake Whisper results: which segments are doubtful, when a re-run replaces them, the names fix, and the gate choosing between before and after. Exports: none (pytest).
from core.asr_result import AsrResult, AsrSegment, AsrWord
from core.correct import correct_ov, fix_names, flagged_spans, merge_rerun
from core.gate import Check, Verdict
from core.subtitles import Cue


def seg(start, end, text, logprob=-0.3, no_speech=0.05, comp=1.4):
    words, t = [], start
    parts = text.split()
    step = (end - start) / max(len(parts), 1)
    for w in parts:
        words.append(AsrWord(t, t + step * 0.9, w, 0.9))
        t += step
    return AsrSegment(start, end, text, logprob, no_speech, comp, words)


def result(*segments):
    return AsrResult(model="fake", language="en", language_probability=0.98, duration=60.0,
                     speech=[(s.start, s.end) for s in segments], segments=list(segments))


def test_doubtful_segments_are_flagged_padded_and_merged():
    asr = result(seg(1, 3, "Good morning everyone."),
                 seg(4, 6, "uh the gost of", logprob=-1.4),                 # low confidence
                 seg(6.2, 8, "the the the the the the", comp=3.1),           # repetition loop
                 seg(20, 21, "Thank you for watching!", no_speech=0.9),      # text over silence
                 seg(30, 30.5, "This line is far too long to be read in half a second."))  # too fast
    spans = flagged_spans(asr, max_cps=35)
    assert spans == [(3.7, 8.3), (19.7, 21.3), (29.7, 30.8)]                 # 4–6 and 6.2–8 merged


def test_a_rerun_replaces_a_span_only_when_more_confident():
    asr = result(seg(1, 3, "Good morning."), seg(4, 6, "the gost of Isak", logprob=-1.4))
    better = result(seg(4.1, 5.9, "the ghost of Isaac", logprob=-0.4))
    worse = result(seg(4.1, 5.9, "teh gost", logprob=-1.8))
    merged, n = merge_rerun(asr, better, [(3.7, 6.3)])
    assert n == 1 and [s.text for s in merged.segments] == ["Good morning.", "the ghost of Isaac"]
    merged, n = merge_rerun(asr, worse, [(3.7, 6.3)])
    assert n == 0 and merged.segments[1].text == "the gost of Isak"


def test_names_take_the_glossary_spelling_text_only():
    cues = [Cue(1, 1000, 2500, "Higginstoot and Samanta are here."), Cue(2, 3000, 4000, "Jay says hi to Day.")]
    out, changes = fix_names(cues, ["Isaac Higgintoot", "Higgintoot", "Samantha", "Jay"])
    assert [c.text for c in out] == ["Higgintoot and Samantha are here.", "Jay says hi to Day."]  # « Day » left alone
    assert changes == [("Higginstoot", "Higgintoot"), ("Samanta", "Samantha")]
    assert [(c.start_ms, c.end_ms) for c in out] == [(1000, 2500), (3000, 4000)]


def test_a_lower_case_word_is_never_turned_into_a_name():
    # Whisper capitalises what it heard as a name; « samanta » in lower case is not one (the S05E07 bench lesson).
    out, changes = fix_names([Cue(1, 0, 2000, "the samanta of it")], ["Samantha"])
    assert out[0].text == "the samanta of it" and changes == []


def _verdict(passed, failures=0):
    return Verdict(passed, [Check(f"c{i}", 0, 0, False, "bad") for i in range(failures)])


def test_the_gate_keeps_the_corrected_transcript_when_it_is_better():
    asr = result(seg(1, 3, "Good morning."), seg(4, 6, "the gost", logprob=-1.4))
    rerun = lambda spans: result(seg(4.1, 5.9, "the ghost", logprob=-0.4))
    judge = lambda cues: _verdict("ghost" in " ".join(c.text for c in cues), 0 if "ghost" in " ".join(c.text for c in cues) else 2)
    cues, note = correct_ov(asr, rerun, [], judge, max_cps=35)
    assert "the ghost" in " ".join(c.text for c in cues) and "kept the corrected" in note


def test_the_gate_keeps_the_first_transcript_when_the_correction_is_worse():
    asr = result(seg(1, 3, "Good morning."), seg(4, 6, "the gost", logprob=-1.4))
    rerun = lambda spans: result(seg(4.1, 5.9, "the ghost", logprob=-0.4))
    judge = lambda cues: _verdict("gost" in " ".join(c.text for c in cues))
    cues, note = correct_ov(asr, rerun, [], judge, max_cps=35)
    assert "the gost" in " ".join(c.text for c in cues) and "kept the first" in note


def test_nothing_doubtful_means_no_rerun_and_the_first_transcript():
    calls = []
    asr = result(seg(1, 3, "Good morning everyone."))
    cues, note = correct_ov(asr, lambda spans: calls.append(spans), [], lambda c: _verdict(True), max_cps=35)
    assert calls == [] and note.startswith("correction: nothing to correct") and cues[0].text == "Good morning everyone."


def test_a_near_miss_with_another_first_letter_is_left_alone():
    # Close enough by ratio (0.86) but a different word may simply be meant: only same-initial misses are fixed.
    out, changes = fix_names([Cue(1, 0, 2000, "Elberta sings.")], ["Alberta"])
    assert out[0].text == "Elberta sings." and changes == []


def test_a_bench_can_lower_the_confidence_flag_to_exercise_the_rerun():
    asr = result(seg(1, 3, "Good morning everyone.", logprob=-0.6))
    assert flagged_spans(asr, max_cps=35) == []                       # Whisper's own -1.0: not doubtful
    assert flagged_spans(asr, max_cps=35, min_logprob=-0.5) == [(0.7, 3.3)]


def test_the_note_shows_which_names_changed():
    asr = result(seg(1, 3, "Hello Samanta."))
    _, note = correct_ov(asr, None, ["Samantha"], lambda c: _verdict(True), max_cps=35)
    assert "1 names fixed (Samanta->Samantha)" in note
