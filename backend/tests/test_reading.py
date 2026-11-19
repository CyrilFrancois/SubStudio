# test_reading.py — A too-fast line SubStudio wrote gets the pause after it (S05E12 French: 91 % readable → over 95 %), never a moved start, an overlap or a changed id. Exports: none (pytest).

import json
from pathlib import Path

from core.gate import THRESHOLDS
from core.subtitles import Cue, ease_reading
from core.subtitles.reading import MAX_CUE_MS, MIN_GAP_MS

CPS = THRESHOLDS["max_cps"]
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "s05e12_fr_timings.json").read_text(encoding="utf-8"))


def real():
    return [Cue(i, s, e, t) for i, s, e, t in FIXTURE["cues"]]


def within(cues):
    """The gate's own reading-speed share (core/gate/text.measure_text), on every cue."""
    ok = sum(1 for c in cues if len(c.text.replace("\n", "")) / ((c.end_ms - c.start_ms) / 1000) <= CPS)
    return ok / len(cues)


def test_real_s05e12_french_becomes_readable():
    before = real()
    assert within(before) < THRESHOLDS["min_within_cps"]            # the gate's verdict that day: 9 % too fast
    after = ease_reading(before, CPS, audio_end_ms=FIXTURE["duration_ms"])
    assert within(after) >= THRESHOLDS["min_within_cps"]
    assert [(c.id, c.start_ms, c.text) for c in after] == [(c.id, c.start_ms, c.text) for c in before]
    assert all(a.end_ms >= b.end_ms for a, b in zip(after, before))   # an end only moves later
    assert all(n.start_ms - c.end_ms >= MIN_GAP_MS for c, n in zip(after, after[1:])
               if c.end_ms != before[after.index(c)].end_ms)       # a moved end stops before the next line
    assert all(c.end_ms <= FIXTURE["duration_ms"] for c in after)


def test_real_s05e12_french_still_mostly_on_speech():
    # Extending into silence lowers « share of cue time on speech »; the gate needs 0.75, measured 0.931 before.
    from core.gate.sync import measure_sync
    after = ease_reading(real(), CPS, audio_end_ms=FIXTURE["duration_ms"])
    spans = [tuple(s) for s in FIXTURE["speech"]]
    assert measure_sync(after, spans).on_speech >= THRESHOLDS["min_on_speech"]


def test_a_readable_line_is_left_exactly_as_it_was():
    c = Cue(1, 0, 2000, "Bonjour.")
    assert ease_reading([c], CPS) == [c]


def test_no_silence_after_means_no_change_and_no_overlap():
    # Back-to-back lines: nothing can be gained without overlapping the next one, so nothing moves.
    fast = Cue(1, 0, 500, "x" * 40)
    nxt = Cue(2, 520, 3000, "Suite.")
    assert ease_reading([fast, nxt], CPS) == [fast, nxt]


def test_an_end_stops_at_the_gap_the_cap_and_the_audio():
    fast = "x" * 70                                                  # needs 2001 ms at 35 cps
    gap = ease_reading([Cue(1, 0, 500, fast), Cue(2, 1500, 3000, "ok")], CPS)
    assert gap[0].end_ms == 1500 - MIN_GAP_MS                        # as far as the silence allows
    huge = ease_reading([Cue(1, 0, 500, "x" * 1000)], CPS)
    assert huge[0].end_ms == MAX_CUE_MS                              # never on screen longer than the cap
    audio = ease_reading([Cue(1, 0, 500, fast)], CPS, audio_end_ms=1200)
    assert audio[0].end_ms == 1200                                   # never past the end of the video
    exact = ease_reading([Cue(1, 0, 500, fast), Cue(2, 9000, 9500, "ok")], CPS)
    assert exact[0].end_ms == 2001                                   # only as much as it needs


def test_the_ladder_gives_more_time_to_lines_it_made_never_to_a_human_track():
    from core.acquire.types import ExistingSub
    from test_ladder import ENGLISH, gated, go
    fast = [Cue(i + 1, i * 2000, i * 2000 + 800, t) for i, t in enumerate(ENGLISH)]      # ~60 characters a second
    run, ladder, *_ = gated(["en", "fr"], [ExistingSub("en", "embedded", "0")], original="en", downloads=False)
    ladder.deps.load_embedded = lambda i: fast
    seen, inner = {}, ladder.deps.gate

    def spy(cues_, speech, lang, **k):
        seen[lang] = list(cues_)
        return inner(cues_, speech, lang, **k)
    ladder.deps.gate = spy
    go(run, ladder)
    assert run.cells["en"].origin.kind() == "kept" and run.cells["fr"].origin.kind() == "translated"
    assert [c.end_ms for c in seen["en"]] == [c.end_ms for c in fast]                     # a human track as it is
    assert [c.start_ms for c in seen["fr"]] == [c.start_ms for c in fast]
    assert all(f.end_ms > e.end_ms for f, e in zip(seen["fr"], fast))                      # ours: more time to read


def test_a_same_language_human_source_reaches_the_gate_untouched():
    # The path the guard exists for: a cell filled by produce_cell from a human subtitle already in its language.
    import asyncio
    from core.acquire.types import Rung
    from core.cells import Cell, Source
    from core.subtitles import TranslationContext
    from test_ladder import ENGLISH, gated
    fast = [Cue(i + 1, i * 2000, i * 2000 + 800, t) for i, t in enumerate(ENGLISH)]
    run, ladder, *_ = gated(["en"], original="en", downloads=False)
    seen, inner = [], ladder.deps.gate
    ladder.deps.gate = lambda cues_, speech, lang, **k: seen.append(list(cues_)) or inner(cues_, speech, lang, **k)
    human = Source(cues=fast, rung=Rung.EXISTING, language="en", context=TranslationContext())
    assert asyncio.run(ladder._produce(run.cells["en"], human, None))
    assert run.cells["en"].origin.kind() == "kept" and [c.end_ms for c in seen[0]] == [c.end_ms for c in fast]
