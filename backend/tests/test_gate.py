# test_gate.py — The quality gate on synthetic speech: in-sync passes, each seeded fault fails for its own reason, measures have the right sign and unit. Exports: tests only.
import random
from dataclasses import replace

import pytest

from bench.gate_bench import seeded_copies
from core.gate import SpeechError, evaluate, measure_sync, measure_text, read_audio
from core.gate.text import missing_ids
from core.subtitles import Cue

# Apostrophes as in real English captions: the seeded mojibake copy breaks them.
LINES = ["I think we'd better go back to the house now.", "Where did you put the keys this morning?",
         "Nobody tells me anything around here.", "That's the worst idea you've ever had.",
         "Wait, are you saying the ghosts can hear us?", "We need to talk about what happened last night.",
         "I'm not going to apologise for that.", "Can you please stop moving the furniture?"]


def _episode(seed=1, minutes=20):
    """Irregular talk/silence pattern (a periodic one would give several equal correlation peaks) and one cue per
    speech span, in sync, readable at about 15 characters a second."""
    rnd = random.Random(seed)
    speech, cues, t = [], [], 5_000
    while t < minutes * 60_000:
        talk = rnd.randint(1_500, 4_000)
        speech.append((t, t + talk))
        # Never the same line twice in a row: that is a real duplicate, which the gate counts.
        text = rnd.choice([l for l in LINES if not cues or l != cues[-1].text])
        cues.append(Cue(len(cues) + 1, t, t + talk, text))
        t += talk + rnd.randint(300, 6_000)
    return cues, speech


def test_in_sync_subtitle_passes_with_no_reason():
    cues, speech = _episode()
    v = evaluate(cues, speech, "eng", "eng")
    assert v.passed, v.reason
    assert v.reason is None


@pytest.mark.parametrize("shift_ms", [3_000, -1_500, 700])
def test_offset_is_the_shift_to_add_in_ms(shift_ms):
    cues, speech = _episode()
    late = [replace(c, start_ms=c.start_ms + shift_ms, end_ms=c.end_ms + shift_ms) for c in cues]
    assert abs(measure_sync(late, speech).offset_ms + shift_ms) <= 10  # one 10 ms frame


def test_sound_descriptions_are_not_expected_on_speech():
    cues, speech = _episode()
    noises = [Cue(1000 + i, s[1] + 50, s[1] + 250, "[door creaks]") for i, s in enumerate(speech[:-1])]
    mixed = sorted(cues + noises, key=lambda c: c.start_ms)
    assert measure_sync(mixed, speech).on_speech == measure_sync(cues, speech).on_speech


@pytest.mark.parametrize("name", ["shift+3s", "shift-1.5s", "framerate-25/23.976", "truncated-60%",
                                  "labelled-french", "english-for-french", "mojibake", "durations-halved"])
def test_each_seeded_fault_is_rejected_for_its_own_reason(name):
    cues, speech = _episode()
    bad, wanted, tag, must_fail = seeded_copies(cues)[name]
    v = evaluate(bad, speech, wanted, tag)
    assert not v.passed
    assert must_fail in [c.name for c in v.checks if not c.passed], v.reason


def test_reason_is_one_readable_sentence():
    cues, speech = _episode()
    late = [replace(c, start_ms=c.start_ms + 3_000, end_ms=c.end_ms + 3_000) for c in cues]
    assert evaluate(late, speech, "en").reason == "out of sync: shifted -3.0 s from the speech"


def test_unlabelled_track_is_judged_by_its_text():
    # Ghosts S05E22's English track carries no language tag: right text, so it passes; asked as French, it fails.
    cues, speech = _episode()
    assert evaluate(cues, speech, "en", None).passed
    v = evaluate(cues, speech, "fr", None)
    assert not v.passed and v.reason == "wanted fr but the text is en"


def test_wanted_language_must_be_a_language():
    cues, speech = _episode()
    with pytest.raises(ValueError, match="not a language"):
        evaluate(cues, speech, "xx-nonsense")


def test_ass_track_is_measured_on_its_text_not_its_markup(tmp_path):
    # The Ghosts « English (SDH) » tracks are ASS: override tags and the ASS line break must neither count as
    # characters nor break a cue. Built with chr() so no escape sequence can be mangled on the way to disk.
    from core.subtitles import load_file
    bs, nl = chr(92), chr(10)
    cues, speech = _episode(minutes=3)
    events = nl.join(
        f"Dialogue: 0,{_ass_time(c.start_ms)},{_ass_time(c.end_ms)},Default,,0,0,0,,"
        + "{" + bs + "an8}{" + bs + "i1}" + c.text.replace(" ", bs + "N", 1) + "{" + bs + "i0}" for c in cues)
    ass = tmp_path / "t.ass"
    ass.write_text(_ASS_HEAD + events + nl, encoding="utf-8")
    read = load_file(ass)
    assert [c.text.replace(nl, " ") for c in read] == [c.text for c in cues]
    assert all(bs not in c.text and "{" not in c.text for c in read)
    assert evaluate(read, speech, "en", "eng").passed


_ASS_HEAD = chr(10).join([
    "[Script Info]", "ScriptType: v4.00+", "", "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic, Alignment",
    "Style: Default,Arial,20,&H00FFFFFF,0,0,2", "", "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", ""])


def _ass_time(ms):
    return f"{ms // 3_600_000}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 10:02d}"


def test_text_measures():
    cues, _ = _episode()
    m = measure_text(cues + [replace(cues[-1], id=9_999)], max_cps=35, max_line=47)
    assert (m.language, m.broken_chars, m.duplicates) == ("en", 0, 1)
    # « Café au lait, déjà vu » after two classic faults: a lost byte (U+FFFD) and UTF-8 read as cp1252 (é -> Ã©).
    broken = ("Caf" + chr(0xFFFD) + " au lait, d" + "é".encode("utf-8").decode("cp1252") + "j"
              + "à".encode("utf-8").decode("cp1252", "replace") + " vu")
    assert measure_text([Cue(1, 0, 2_000, broken)], 35, 47).broken_chars == 3
    # A correct accented line is not broken.
    assert measure_text([Cue(1, 0, 2_000, "Café au lait, déjà vu, ça va ?")], 35, 47).broken_chars == 0


def test_translation_id_set_must_match_exactly():
    cues, speech = _episode()
    assert missing_ids([c.id for c in cues], cues) == set()
    assert missing_ids([c.id for c in cues], cues[:-1]) == {cues[-1].id}
    v = evaluate(cues[:-1], speech, "en", source=cues)
    assert not v.passed and v.reason.startswith("translation does not match its source")


def test_lines_left_in_the_source_language_are_caught_by_id():
    # A « French » track with 20 % of its lines still English: one detection over the whole text still says French.
    cues, speech = _episode()
    french = ["Je pense qu'on devrait rentrer à la maison.", "Où as-tu mis les clés ce matin ?",
              "Personne ne me dit jamais rien ici.", "C'est la pire idée que tu aies jamais eue."]
    half_done = [replace(c, text=c.text if i % 5 == 0 else french[i % 4]) for i, c in enumerate(cues)]
    v = evaluate(half_done, speech, "fr", source=cues)
    assert [c.name for c in v.checks if not c.passed] == ["untranslated"]
    left = [c.id for i, c in enumerate(cues) if i % 5 == 0]
    assert v.reason.startswith(f"{len(left)} lines left in the source language (cues {left[0]}, {left[1]}")
    names = [replace(c, text="Jay !") for c in cues]  # under 3 words: the same in both languages, not a fault
    from core.gate.text import untranslated
    assert untranslated([replace(c, text="Jay !") for c in cues], names) == []


def test_drift_is_not_measured_on_a_short_track_rather_than_reported_zero():
    cues, speech = _episode(minutes=1)
    m = measure_sync(cues, speech)
    assert m.drift_ms is None
    assert evaluate(cues, speech, "en").checks[1].passed


def test_cues_out_of_order_are_measured_in_time_order():
    cues, speech = _episode()
    shuffled = list(cues)
    random.Random(3).shuffle(shuffled)
    assert measure_sync(shuffled, speech) == measure_sync(cues, speech)


def test_no_speech_or_no_dialogue_is_an_error_not_a_verdict():
    cues, speech = _episode()
    with pytest.raises(ValueError, match="no speech"):
        measure_sync(cues, [])
    with pytest.raises(ValueError, match="no dialogue"):
        measure_sync([Cue(1, 0, 1_000, "[music]")], speech)


def test_video_without_audio_raises(media):
    with pytest.raises(SpeechError):
        read_audio(str(media["no_audio"]))
