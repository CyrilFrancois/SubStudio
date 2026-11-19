# test_output_order.py — Which subtitle a player picks after the swap: ours first in its language, default for the first wanted language, a rejected old track demoted, nothing deleted. Exports: tests only.
"""Generated clips in pytest's temp folder only: never C:\\Videos, never real media.

A player (mpv, VLC, Plex) given « English » picks the first English track, or the default one: on S05E21 the old
English track the gate rejected came first, so the bad subtitle was the one shown (workspace-54, 2026-11-19)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bench.media import ffmpeg  # noqa: E402
from core.output import OutputError, inplace, media, update_in_place  # noqa: E402
from core.subtitles import Cue  # noqa: E402

SECONDS = 5
_SRC = ["-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=10:duration={SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:duration={SECONDS}"]
_AV = ["-c:v", "mpeg4", "-q:v", "8", "-c:a", "aac", "-b:a", "48k"]
SRT = "1\n00:00:00,500 --> 00:00:02,000\nHello there.\n\n"
EN = [Cue(1, 500, 2000, "Hello."), Cue(2, 2500, 4000, "Goodbye.")]
FR = [Cue(1, 500, 2000, "Bonjour."), Cue(2, 2500, 4000, "Au revoir.")]


def _source(folder: Path, langs=("eng",)) -> Path:
    """An MKV whose subtitles are `langs`, each an old (not SubStudio) track, the first one marked default."""
    srt = folder / "old.srt"
    srt.write_text(SRT, encoding="utf-8")
    args = [*_SRC]
    for _ in langs:
        args += ["-i", str(srt)]
    args += ["-map", "0:v", "-map", "1:a"]
    for i, lang in enumerate(langs):
        args += ["-map", f"{i + 2}:s", f"-metadata:s:s:{i}", f"language={lang}",
                 f"-metadata:s:s:{i}", f"title=Old {lang}", f"-disposition:s:{i}", "default" if i == 0 else "0"]
    out = folder / "Ghosts S05E21.mkv"
    ffmpeg(*args, *_AV, "-c:s", "srt", str(out))
    srt.unlink()
    return out


def _subs(path: Path):
    """[(title, default)] of the subtitle tracks, in file order."""
    return [(s.get("tags", {}).get("title"), int(s.get("disposition", {}).get("default", 0)))
            for s in media._probe(path)["streams"] if s.get("codec_type") == "subtitle"]


def _upd(source, tracks, languages, **kw):
    kw.setdefault("origins", {lang: "downloaded" for lang in tracks})
    return update_in_place(source, tracks, languages, **kw)


def test_our_track_comes_first_in_its_language_and_a_rejected_one_loses_default(tmp_path):
    src = _source(tmp_path)
    assert _subs(src) == [("Old eng", 1)]                      # the fixture really has a default old track
    result = _upd(src, {"en": EN}, ["en"], rejected=[0])
    assert _subs(result.path) == [("SubStudio en", 1), ("Old eng", 0)]   # demoted, never deleted


def test_an_old_track_not_rejected_keeps_its_default(tmp_path):
    # No verdict says it is bad (a Review save after a restart): ours still comes first, the old one is left as is.
    result = _upd(_source(tmp_path), {"en": EN}, ["en"])
    assert _subs(result.path) == [("SubStudio en", 1), ("Old eng", 1)]


def test_a_kept_first_language_stays_the_default_and_ours_are_not(tmp_path):
    # English was kept (the gate passed it): only French is written, and it does not take the default.
    result = _upd(_source(tmp_path), {"fr": FR}, ["en", "fr"])
    assert _subs(result.path) == [("Old eng", 1), ("SubStudio fr", 0)]


def test_only_the_first_wanted_language_is_default(tmp_path):
    src = _source(tmp_path, langs=("eng", "fre"))
    result = _upd(src, {"en": EN, "fr": FR}, ["en", "fr"], rejected=[0, 1])
    assert _subs(result.path) == [("SubStudio en", 1), ("Old eng", 0), ("SubStudio fr", 0), ("Old fre", 0)]


def test_a_rejected_track_of_a_language_not_written_is_left_alone(tmp_path):
    # The English cell failed: nothing replaces the old track, so it keeps its flag even if the gate refused it.
    result = _upd(_source(tmp_path), {"fr": FR}, ["fr"], rejected=[0])
    assert _subs(result.path) == [("Old eng", 1), ("SubStudio fr", 1)]


def test_a_rerun_keeps_our_track_first_and_default(tmp_path):
    first = _upd(_source(tmp_path), {"en": EN}, ["en"], rejected=[0]).path
    again = _upd(first, {"en": EN[:1]}, ["en"])
    assert _subs(again.path) == [("SubStudio en", 1), ("Old eng", 0)]
    assert again.subs == {1: media.subtitle_streams(again.path)[1]}   # the old track's new place is reported


def test_the_check_refuses_a_file_in_the_old_order(tmp_path, monkeypatch):
    src = _source(tmp_path)
    real = inplace._sub_plan
    # The mux puts the kept tracks first (the old behaviour); the check plans from the source on its own.
    monkeypatch.setattr(inplace, "_sub_plan", lambda info, langs: sorted(real(info, langs), key=lambda x: isinstance(x, str)))
    with pytest.raises(OutputError, match="subtitle tracks"):
        _upd(src, {"en": EN}, ["en"], rejected=[0])
    assert _subs(src) == [("Old eng", 1)]


def test_the_check_refuses_a_file_whose_dispositions_were_not_written(tmp_path, monkeypatch):
    src = _source(tmp_path)
    real = inplace._mux_args
    monkeypatch.setattr(inplace, "_mux_args", lambda *a, **k: real(*a, **{**k, "default_lang": None, "demoted": ()}))
    with pytest.raises(OutputError, match="subtitle tracks"):
        _upd(src, {"en": EN}, ["en"], rejected=[0])
    assert _subs(src) == [("Old eng", 1)]


def test_write_run_demotes_the_tracks_the_gate_rejected(tmp_path):
    from core.acquire.types import Rung
    from core.cells import Cell, CellState, Origin, VideoRun
    from core.output import write_run
    run = VideoRun(path=str(_source(tmp_path)),
                   cells={"en": Cell("en", CellState.PASSED, Origin(Rung.DOWNLOAD), cues=EN)})
    run.judged[("embedded", "0")] = False
    assert _subs(write_run(run).path) == [("SubStudio en", 1), ("Old eng", 0)]


def test_a_retry_does_not_demote_by_the_old_positions(tmp_path):
    # After the first swap the gate's refs name positions in a file that has changed: a retry passes none, and the
    # flags the first swap wrote are copied as they are.
    from core.acquire.types import Rung
    from core.cells import Cell, CellState, Origin, VideoRun
    from core.output import write_run
    src = _source(tmp_path, langs=("fre", "eng"))
    run = VideoRun(path=str(src), cells={"fr": Cell("fr", CellState.PASSED, Origin(Rung.DOWNLOAD), cues=FR)})
    run.output_path = str(src)                           # as after a first write
    run.judged[("embedded", "0")] = False                # position 0 of the judged file: the old French track
    assert _subs(write_run(run).path) == [("SubStudio fr", 1), ("Old fre", 1), ("Old eng", 0)]
