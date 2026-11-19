# test_asr.py — ASR pieces that need no model: names prompt, AsrResult → cues, VAD on silence, no-audio error. Exports: none (pytest).
import numpy as np
import pytest

from core.asr_result import AsrResult, AsrSegment, AsrWord
from core.subtitles import to_srt
from core.transcriber import VideoTranscriber, names_prompt, speech_timeline


def test_names_prompt_dedups_caps_and_is_none_when_empty():
    assert names_prompt([]) is None
    assert names_prompt(["  ", ""]) is None
    assert names_prompt(["Sam", "sam", " Jay "]) == "Names: Sam, Jay."
    long = names_prompt([f"Name{i}" for i in range(200)])
    assert len(long) <= 300 + len("Names: .")


def _result(words):
    return AsrResult("turbo", "en", 0.97, 4000.0, [(0.0, 1.0)],
                     [AsrSegment(words[0].start, words[-1].end, "", -0.2, 0.01, 1.2, words)])


def test_to_cues_breaks_on_sentence_end_and_keeps_ms_timings():
    cues = _result([AsrWord(0.0, 0.4, " Hello", 0.9), AsrWord(0.5, 0.9, " there.", 0.9),
                    AsrWord(3600.2, 3601.5, " Late", 0.8)]).to_cues()
    assert [(c.id, c.start_ms, c.end_ms, c.text) for c in cues] == [
        (1, 0, 900, "Hello there."), (2, 3600200, 3601500, "Late")]
    assert "01:00:00,200 --> 01:00:01,500" in to_srt(cues)


def test_to_cues_splits_long_text_on_two_lines_and_skips_blank_words():
    words = [AsrWord(i * 0.3, i * 0.3 + 0.2, w, 0.9)
             for i, w in enumerate(" the quick brown fox jumps over the lazy dog".split(" "))]
    cues = _result(words).to_cues()
    assert len(cues) == 1 and cues[0].text.count("\n") == 1
    assert all(len(line) <= 30 for line in cues[0].text.split("\n"))


def test_vad_finds_no_speech_in_silence():
    assert speech_timeline(np.zeros(16000 * 3, dtype=np.float32)) == []


def test_no_audio_track_is_a_named_error_before_any_model_loads(media):
    t = VideoTranscriber()
    with pytest.raises(Exception, match="audio track"):
        t.transcribe(str(media["no_audio"]), "id", lambda *a: None, 1, 1)
    assert t.model is None  # failed before loading 1.5 GB of Whisper
