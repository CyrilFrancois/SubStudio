# test_gate_drift.py — the gate's sync checks on little speech: a short clip whose speech barely pauses has no measurable drift (it read « -53.5 s » on a transcript of that very audio) nor offset (a transcript passes, a human track does not), a real drift on a full-length track is still caught. Exports: none (pytest).
import random

import pytest

import core.gate.sync as sync
from core.gate import evaluate
from core.subtitles import Cue


def short_clip_with_transcript(seed):
    """5 min of speech that barely pauses (S05E07 120-420 s: ~45 spans, some 24 s long) and Whisper-like lines:
    back to back, 1.4-4 s, their boundaries blind to the pauses."""
    r = random.Random(seed)
    t, speech = 0, []
    while t < 300_000:
        d = r.choice([r.randint(1000, 4000), r.randint(8000, 24000)])
        speech.append((t, min(t + d, 300_000)))
        t += d + r.randint(200, 900)
    cues, t = [], 0
    while t < 296_000:
        d = r.randint(1400, 4000)
        cues.append(Cue(len(cues) + 1, t, t + d - r.randint(0, 400), LINES[len(cues) % len(LINES)]))
        t += d
    return cues, speech


# English, so the language check passes and a verdict turns on timing alone.
LINES = ["I told you the house was haunted.", "Where did you put the keys?", "We need to talk about dinner.",
         "Nobody is going into the basement.", "That is the worst idea ever.", "Can you hear them right now?",
         "She said she would be back soon.", "Why is the door open again?"]


def full_episode_track(seed, second_half_shift_ms=0):
    """20 min of dialogue with ordinary pauses and a caption track on it, its second half optionally shifted."""
    r = random.Random(seed)
    t, speech = 2_000, []
    while t < 1_200_000:
        d = r.randint(800, 5000)
        speech.append((t, t + d))
        t += d + r.randint(300, 3000)
    cues = []
    for i, (a, b) in enumerate(speech):
        shift = second_half_shift_ms if a >= 600_000 else 0
        j = r.randint(-200, 200)
        cues.append(Cue(i + 1, a + j + shift, b + j + shift, "some words"))
    return cues, speech


@pytest.mark.parametrize("seed", range(6))
def test_a_short_clip_has_no_measurable_drift_instead_of_a_false_one(seed, monkeypatch):
    cues, speech = short_clip_with_transcript(seed)
    monkeypatch.setattr(sync, "MIN_HALF_SPEECH_SPANS", 0)       # the measure before the fix
    assert abs(sync.measure_sync(cues, speech, 300_000).drift_ms) > 20_000  # the fixture reproduces the bug
    monkeypatch.undo()
    m = sync.measure_sync(cues, speech, 300_000)
    assert m.drift_ms is None
    v = evaluate(cues, speech, "en", duration_ms=300_000)
    assert next(c for c in v.checks if c.name == "drift_ms").passed


@pytest.mark.parametrize("seed", range(6))
def test_a_short_clip_has_no_measurable_offset_so_a_transcript_passes_and_a_human_track_does_not(seed, monkeypatch):
    cues, speech = short_clip_with_transcript(seed)
    monkeypatch.setattr(sync, "MIN_SPEECH_SPANS", 0)             # the measure before the fix
    assert abs(sync.measure_sync(cues, speech, 300_000).offset_ms) > 500  # in sync, yet read as shifted: noise
    monkeypatch.undo()
    assert sync.measure_sync(cues, speech, 300_000).offset_ms is None
    # Whisper's own cues on this audio: nothing measured is wrong, so it passes.
    v = evaluate(cues, speech, "en", duration_ms=300_000, timing_from_audio=True)
    assert v.passed, v.reason
    # The very same cues as a kept or downloaded track: their sync cannot be shown, so they are not kept.
    v = evaluate(cues, speech, "en", duration_ms=300_000)
    assert not v.passed and v.reason == "timing not checked: too little speech in this video to measure the sync"
    # Shifted 3 s, they still fail as a human track (the offset could not tell, the verdict does not guess).
    late = [Cue(c.id, c.start_ms + 3000, c.end_ms + 3000, c.text) for c in cues]
    assert not evaluate(late, speech, "en", duration_ms=303_000).passed


@pytest.mark.parametrize("seed", range(3))
def test_a_full_length_track_in_sync_reads_no_drift(seed):
    cues, speech = full_episode_track(seed)
    assert abs(sync.measure_sync(cues, speech, 1_205_000).drift_ms) <= 100


@pytest.mark.parametrize("seed", range(3))
def test_a_full_length_track_that_drifts_is_still_caught(seed):
    cues, speech = full_episode_track(seed, second_half_shift_ms=3_000)
    drift = sync.measure_sync(cues, speech, 1_210_000).drift_ms
    assert drift is not None and abs(drift + 3_000) <= 200          # the offset to ADD to the second half: -3 s
    v = evaluate(cues, speech, "en", duration_ms=1_210_000)
    check = next(c for c in v.checks if c.name == "drift_ms")
    assert not v.passed and not check.passed and "timing moves" in check.why
