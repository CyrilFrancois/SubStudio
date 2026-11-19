# test_output.py — The verified in-place swap: the source survives every failure byte for byte, a success carries the new tracks and the (Sub-…) marker. Exports: tests only.
"""Generated clips in pytest's temp folder only: never C:\\Videos, never real media."""

import hashlib
import os
import sys
from collections import namedtuple
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bench.media import ffmpeg, probe  # noqa: E402
from core.output import OutputError, marked_name, update_in_place  # noqa: E402
from core.output import inplace, media, verify  # noqa: E402
from core.jobs import JobAborted  # noqa: E402
from core.subtitles import Cue  # noqa: E402

SECONDS = 5
_SRC = ["-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=10:duration={SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:duration={SECONDS}"]
_AV = ["-c:v", "mpeg4", "-q:v", "8", "-c:a", "aac", "-b:a", "48k"]
SRT = "1\n00:00:00,500 --> 00:00:02,000\nHello there.\n\n2\n00:00:02,500 --> 00:00:04,000\nGoodbye.\n\n"

FR = [Cue(1, 500, 2000, "Bonjour."), Cue(2, 2500, 4000, "Au revoir."), Cue(3, 4200, 4800, "Fin.")]
EN = [Cue(1, 500, 2000, "Hello."), Cue(2, 2500, 4000, "Goodbye.")]



def _upd(source, tracks, languages, **kw):
    """update_in_place with an origin for every new track (the pipeline always knows it)."""
    kw.setdefault("origins", {lang: "translated" for lang in tracks})
    return update_in_place(source, tracks, languages, **kw)

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _mkv_with_sdh(folder: Path, name="Ghosts S05E01.mkv", title="English (SDH)") -> Path:
    srt = folder / "sdh.srt"
    srt.write_text(SRT, encoding="utf-8")
    out = folder / name
    ffmpeg(*_SRC, "-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s", *_AV, "-c:s", "srt",
           "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", f"title={title}", str(out))
    srt.unlink()
    return out


def _mp4_with_mov_text(folder: Path) -> Path:
    srt = folder / "en.srt"
    srt.write_text(SRT, encoding="utf-8")
    out = folder / "Film.mp4"
    ffmpeg(*_SRC, "-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s", *_AV, "-c:s", "mov_text",
           "-metadata:s:s:0", "language=eng", str(out))
    srt.unlink()
    return out


def _subs(path: Path):
    return [(s.get("codec_name"), s.get("tags", {}).get("language"), s.get("tags", {}).get("title"))
            for s in probe(path)["streams"] if s["codec_type"] == "subtitle"]


def _no_temp(folder: Path) -> bool:
    return not list(folder.glob("*" + inplace.TMP_MARK + "*"))


# ---------------------------------------------------------------- names

@pytest.mark.parametrize("name, langs, expected", [
    ("Ghosts S05E01.mkv", ["en", "fr"], "Ghosts S05E01 (Sub-EN,FR).mkv"),
    ("Film.mp4", ["fr"], "Film (Sub-FR).mkv"),
    ("Ghosts S05E01 (Sub-EN).mkv", ["en", "fr"], "Ghosts S05E01 (Sub-EN,FR).mkv"),   # replaced, never stacked
    ("Ghosts S05E01 (Sub-EN,FR).mkv", ["eng", "fre", "en"], "Ghosts S05E01 (Sub-EN,FR).mkv"),
    ("Été à Zürich 東京.mkv", ["fr"], "Été à Zürich 東京 (Sub-FR).mkv"),
])
def test_marker_lists_actual_languages_once(name, langs, expected):
    assert marked_name(Path(name), langs) == expected


def test_marker_refuses_ov_unresolved():
    with pytest.raises(ValueError):
        marked_name(Path("x.mkv"), ["OV", "fr"])


# ---------------------------------------------------------------- success

def test_nothing_new_leaves_the_file_untouched_and_unrenamed(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    result = _upd(src, {}, ["en"])
    assert result.status == "untouched" and result.path == src
    assert _sha(src) == before and sorted(p.name for p in tmp_path.iterdir()) == [src.name]


def test_mkv_gets_new_tracks_marker_and_keeps_existing(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    av_before = media._signature(media._probe(src))
    result = _upd(src, {"fr": FR}, ["en", "fr"])

    assert result.status == "updated" and result.marker == "(Sub-EN,FR)"
    assert result.path.name == "Ghosts S05E01 (Sub-EN,FR).mkv"
    assert not src.exists() and _no_temp(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [result.path.name]
    assert media._signature(media._probe(result.path)) == av_before
    assert _subs(result.path) == [("subrip", "eng", "English (SDH)"), ("subrip", "fre", "SubStudio fr")]
    assert verify._decoded_cue_count(result.path, 1) == 3


def test_rerun_replaces_our_track_and_marker_never_stacks(tmp_path):
    first = _upd(_mkv_with_sdh(tmp_path), {"fr": FR}, ["en", "fr"]).path
    again = _upd(first, {"fr": FR[:2], "de": EN}, ["en", "fr", "de"])
    assert again.path.name == "Ghosts S05E01 (Sub-EN,FR,DE).mkv" and not first.exists()
    subs = _subs(again.path)
    assert [t for _, _, t in subs] == ["English (SDH)", "SubStudio fr", "SubStudio de"]
    assert verify._decoded_cue_count(again.path, 1) == 2   # the new fr, not the old one


def test_same_name_rerun_swaps_atomically_onto_the_source(tmp_path):
    first = _upd(_mkv_with_sdh(tmp_path), {"fr": FR}, ["en", "fr"]).path
    again = _upd(first, {"fr": FR[:1]}, ["en", "fr"])
    assert again.path == first and first.exists() and _no_temp(tmp_path)
    assert verify._decoded_cue_count(first, 1) == 1


def test_mp4_becomes_mkv_and_its_mov_text_survives_as_srt(tmp_path):
    src = _mp4_with_mov_text(tmp_path)
    result = _upd(src, {"fr": FR}, ["en", "fr"])
    assert result.path.name == "Film (Sub-EN,FR).mkv" and not src.exists() and _no_temp(tmp_path)
    assert _subs(result.path) == [("subrip", "eng", None), ("subrip", "fre", "SubStudio fr")]


def test_cues_past_the_end_are_dropped_and_counted_right(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    late = FR + [Cue(4, 6000, 7000, "Après la fin.")]
    result = _upd(src, {"fr": late}, ["en", "fr"])
    assert verify._decoded_cue_count(result.path, 1) == 3


# ---------------------------------------------------------------- failures leave the source byte-identical

def _assert_source_intact(src: Path, before: str):
    assert src.exists() and _sha(src) == before
    assert _no_temp(src.parent)
    assert sorted(p.name for p in src.parent.iterdir()) == [src.name]


def test_failed_verify_leaves_source_byte_identical(tmp_path, monkeypatch):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    real = inplace._mux_args

    def truncated(*args):   # a mux that silently loses the last 3 s: only the verify step can catch it
        cmd = real(*args)
        return cmd[:-3] + ["-t", "2"] + cmd[-3:]

    monkeypatch.setattr(inplace, "_mux_args", truncated)
    with pytest.raises(OutputError, match="duration"):
        _upd(src, {"fr": FR[:1]}, ["en", "fr"])
    _assert_source_intact(src, before)


def test_failed_verify_on_lost_audio(tmp_path, monkeypatch):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    real = inplace._mux_args

    def no_audio(*args):
        cmd = real(*args)
        i = cmd.index("0:1")       # the audio stream's map
        return cmd[:i - 1] + cmd[i + 1:]

    monkeypatch.setattr(inplace, "_mux_args", no_audio)
    with pytest.raises(OutputError, match="audio differ"):
        _upd(src, {"fr": FR}, ["en", "fr"])
    _assert_source_intact(src, before)


def test_ffmpeg_failure_leaves_source_byte_identical(tmp_path, monkeypatch):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    real = inplace._mux_args
    monkeypatch.setattr(inplace, "_mux_args", lambda *a: real(*a)[:-1] + ["-bogus-option", str(a[-1])])
    with pytest.raises(OutputError, match="ffmpeg"):
        _upd(src, {"fr": FR}, ["en", "fr"])
    _assert_source_intact(src, before)


def test_not_enough_disk_writes_nothing(tmp_path, monkeypatch):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(inplace.shutil, "disk_usage", lambda _: Usage(10, 10, 1))
    with pytest.raises(OutputError, match="free disk"):
        _upd(src, {"fr": FR}, ["en", "fr"])
    _assert_source_intact(src, before)


def test_existing_target_name_is_never_overwritten(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    other = tmp_path / "Ghosts S05E01 (Sub-EN,FR).mkv"
    other.write_bytes(b"someone else's file")
    with pytest.raises(OutputError, match="already exists"):
        _upd(src, {"fr": FR}, ["en", "fr"])
    assert other.read_bytes() == b"someone else's file" and src.exists() and _no_temp(tmp_path)


def test_every_cue_past_the_end_fails_loudly(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    with pytest.raises(OutputError, match="after the end"):
        _upd(src, {"fr": [Cue(1, 9000, 9500, "Trop tard.")]}, ["en", "fr"])
    _assert_source_intact(src, before)


def test_written_language_must_be_validated(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    with pytest.raises(ValueError):
        _upd(src, {"fr": FR}, ["en"])


# ---------------------------------------------------------------- review findings, 2026-11-19

def _mp4_with_cover(folder: Path) -> Path:
    cover = folder / "cover.jpg"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(cover))
    out = folder / "Poster.mp4"
    ffmpeg(*_SRC, "-i", str(cover), "-map", "0:v", "-map", "1:a", "-map", "2:v", *_AV, "-c:v:1", "mjpeg",
           "-disposition:v:1", "attached_pic", str(out))
    cover.unlink()
    return out


def _mkv_with_chapters(folder: Path) -> Path:
    meta = folder / "chapters.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=One\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=5000\ntitle=Two\n", encoding="utf-8")
    out = folder / "Chapters.mkv"
    ffmpeg(*_SRC, "-i", str(meta), "-map", "0:v", "-map", "1:a", "-map_chapters", "2", *_AV, str(out))
    meta.unlink()
    return out


def test_cover_art_survives_mp4_to_mkv(tmp_path):
    src = _mp4_with_cover(tmp_path)
    assert media._signature(media._probe(src))["covers"] == 1     # the fixture really has a cover
    result = _upd(src, {"fr": FR}, ["fr"])
    assert media._signature(media._probe(result.path))["covers"] == 1
    again = _upd(result.path, {"de": EN}, ["fr", "de"])     # an MKV cover survives a second swap too
    sig = media._signature(media._probe(again.path))
    assert sig["covers"] == 1 and [c for c, _ in sig["video"]] == ["mpeg4"]


def test_chapters_survive(tmp_path):
    src = _mkv_with_chapters(tmp_path)
    assert media._signature(media._probe(src))["chapters"] == 2
    result = _upd(src, {"fr": FR}, ["fr"])
    assert media._signature(media._probe(result.path))["chapters"] == 2


def test_lost_existing_subtitle_fails_verify(tmp_path, monkeypatch):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    monkeypatch.setattr(inplace, "_kept_subs", lambda info, langs: [])   # the mux forgets the SDH track…
    real_verify = inplace._verify

    def verify_against_truth(*args):  # …but the check knows it was there
        monkeypatch.undo()
        real_verify(*args)

    monkeypatch.setattr(inplace, "_verify", verify_against_truth)
    with pytest.raises(OutputError, match="subtitle tracks were not all kept"):
        _upd(src, {"fr": FR}, ["en", "fr"])
    _assert_source_intact(src, before)


def test_a_user_file_named_like_a_temp_is_never_touched(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    users = [tmp_path / "Ghosts S05E01.substudio-tmp.mkv", tmp_path / f"Ghosts S05E01{inplace.TMP_MARK}x.mkv"]
    for u in users:
        u.write_bytes(b"user file")
    with pytest.raises(OutputError, match="after the end"):
        _upd(src, {"fr": [Cue(1, 9000, 9500, "Trop tard.")]}, ["en", "fr"])
    _upd(src, {"fr": FR}, ["en", "fr"])
    assert all(u.read_bytes() == b"user file" for u in users)


def test_target_appearing_during_the_update_is_never_overwritten(tmp_path, monkeypatch):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    other = tmp_path / "Ghosts S05E01 (Sub-EN,FR).mkv"
    real_verify = inplace._verify

    def verify_then_race(*args):
        real_verify(*args)
        other.write_bytes(b"arrived meanwhile")

    monkeypatch.setattr(inplace, "_verify", verify_then_race)
    with pytest.raises(OutputError, match="appeared"):
        _upd(src, {"fr": FR}, ["en", "fr"])
    assert other.read_bytes() == b"arrived meanwhile" and _sha(src) == before and _no_temp(tmp_path)


@pytest.mark.parametrize("exc", [KeyboardInterrupt, lambda: JobAborted("x")])
def test_interrupt_or_cancel_leaves_source_byte_identical(tmp_path, monkeypatch, exc):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)

    def boom(*args):
        raise exc()

    monkeypatch.setattr(inplace, "_verify", boom)
    with pytest.raises((KeyboardInterrupt, JobAborted)):
        _upd(src, {"fr": FR}, ["en", "fr"])
    _assert_source_intact(src, before)


def test_same_language_twice_is_refused(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    with pytest.raises(ValueError, match="same language"):
        _upd(src, {"fr": FR, "fre": FR}, ["fr"])


def test_second_concurrent_update_of_one_source_is_refused(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    lock = inplace._lock_for(src)
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(OutputError, match="already being updated"):
            _upd(src, {"fr": FR}, ["en", "fr"])
    finally:
        lock.release()
    _assert_source_intact(src, before)



def test_origin_is_recorded_on_each_new_track(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    result = update_in_place(src, {"fr": FR, "de": EN}, ["en", "fr", "de"],
                             origins={"fr": "downloaded", "de": "transcribed"})
    tags = {s.get("tags", {}).get("title"): {k.upper(): v for k, v in s.get("tags", {}).items()}
            for s in media._probe(result.path)["streams"] if s["codec_type"] == "subtitle"}
    assert tags["SubStudio fr"]["SUBSTUDIO_ORIGIN"] == "downloaded"
    assert tags["SubStudio de"]["SUBSTUDIO_ORIGIN"] == "transcribed"
    assert "SUBSTUDIO_ORIGIN" not in tags["English (SDH)"]


@pytest.mark.parametrize("origins", [{}, {"fr": "kept"}, {"fr": "guessed"}])
def test_missing_or_invalid_origin_is_refused(tmp_path, origins):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    with pytest.raises(ValueError, match="origin"):
        update_in_place(src, {"fr": FR}, ["en", "fr"], origins=origins)
    _assert_source_intact(src, before)


def test_marker_matches_what_the_library_parses():
    from core.provenance import parse_name_marker
    assert parse_name_marker(Path(marked_name(Path("Ep (Sub-EN).mp4"), ["en", "fr"])).stem) == ["en", "fr"]


def test_update_says_where_each_kept_subtitle_is_now(tmp_path):
    # The SDH track is subtitle 0 of the source; on a rerun our old fr is replaced and gets no entry.
    first = _upd(_mkv_with_sdh(tmp_path), {"fr": FR}, ["en", "fr"])
    sdh = next(s["index"] for s in probe(first.path)["streams"] if s.get("tags", {}).get("title") == "English (SDH)")
    assert first.subs == {0: sdh}
    again = _upd(first.path, {"fr": FR[:2]}, ["en", "fr"])
    assert again.subs == {0: sdh}
    assert inplace.subtitle_streams(again.path) == {0: sdh, 1: sdh + 1}


def test_verdict_record_keys_the_file_as_it_is_now(tmp_path):
    from core.cells import VideoRun
    from core.output import verdict_record
    from core.provenance import REJECTED, track_key
    side = tmp_path / "Ep.en.srt"
    run = VideoRun(path=str(tmp_path / "Ep.mkv"), cells={})
    run.judged = {("embedded", "0"): True, ("embedded", "1"): False, ("embedded", "2"): True,
                  ("sidecar", str(side)): False}
    rec = verdict_record(run, {0: 3, 1: 5})                  # subtitle 2 was replaced by the update
    assert rec == {"stream:3": "kept", "stream:5": REJECTED, track_key(kind="sidecar", path=str(side)): REJECTED}


def test_library_and_pipeline_share_the_rejected_value():
    from api import library
    from core import provenance
    assert library.REJECTED is provenance.REJECTED


# ---------------------------------------------------------------- the OV audio tag

def _audio_tag(path: Path):
    a = [s for s in media._probe(path)["streams"] if s["codec_type"] == "audio"][0]
    return a.get("tags", {}).get("language")


def test_untagged_audio_gets_the_detected_language(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    assert _audio_tag(src) in (None, "und")                    # the fixture's audio really is untagged
    result = _upd(src, {"fr": FR}, ["en", "fr"], audio_language="en")
    assert _audio_tag(result.path) == "eng"                    # ISO 639-2, as the library and players read it


def test_tagged_audio_is_never_retagged(tmp_path):
    srt = tmp_path / "sdh.srt"
    srt.write_text(SRT, encoding="utf-8")
    src = tmp_path / "Film.mkv"
    ffmpeg(*_SRC, "-map", "0:v", "-map", "1:a", *_AV, "-metadata:s:a:0", "language=fre", str(src))
    result = _upd(src, {"en": EN}, ["en"], audio_language="en")
    assert _audio_tag(result.path) == "fre"


def test_the_audio_tag_alone_never_touches_a_video(tmp_path):
    src = _mkv_with_sdh(tmp_path)
    before = _sha(src)
    assert _upd(src, {}, ["en"], audio_language="en").status == "untouched"
    assert _sha(src) == before and _audio_tag(src) in (None, "und")


def test_write_run_tags_the_audio_with_what_the_run_detected(tmp_path):
    from core.acquire.types import Rung
    from core.cells import Cell, CellState, Origin, VideoRun
    from core.output import write_run
    src = _mkv_with_sdh(tmp_path)
    run = VideoRun(path=str(src), cells={"fr": Cell("fr", CellState.PASSED, Origin(Rung.TRANSLATE, "en"), cues=FR)})
    run.audio_tag = "en"
    assert _audio_tag(write_run(run).path) == "eng"
