# test_provenance.py — The « made by SubStudio » rule: old tracks never match, new ones always do. Exports: tests only.

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.provenance import is_substudio_track, substudio_track_title, track_key  # noqa: E402


def test_new_muxed_title_round_trips():
    assert is_substudio_track(kind="embedded", title=substudio_track_title("fr"))


@pytest.mark.parametrize("title", [
    "AI FR",              # what the pre-2026-11-19 muxer wrote
    "English (SDH)",      # release tracks in Cyril's Ghosts corpus
    "SDH", None, "",
    "SubStudio FR",       # wrong case: not our exact format
    "SubStudio fr (old)",
])
def test_other_titles_are_existing(title):
    assert not is_substudio_track(kind="embedded", title=title)


def test_sidecar_needs_the_manifest(tmp_path):
    srt = tmp_path / "Movie.fr.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nSalut\n", encoding="utf-8")
    assert not is_substudio_track(kind="sidecar", path=str(srt))
    record = [track_key(kind="sidecar", path=str(srt))]
    assert is_substudio_track(kind="sidecar", path=str(srt), manifest=record)
    # The same file named differently (case, relative form) is still the same key on Windows.
    assert is_substudio_track(kind="sidecar", path=str(srt).upper() if os.name == "nt" else str(srt), manifest=record)


def test_title_rejects_a_three_letter_code():
    with pytest.raises(ValueError):
        substudio_track_title("fre")


# --- D-0002 file-name marker ------------------------------------------------------------------

from core.provenance import name_marker, parse_name_marker, strip_name_marker  # noqa: E402


def test_marker_round_trip_keeps_order_and_drops_duplicates():
    stem = "Ghosts S05E01 " + name_marker(["en", "en", "fr"])  # OV resolved to EN, then EN
    assert stem == "Ghosts S05E01 (Sub-EN,FR)"
    assert parse_name_marker(stem) == ["en", "fr"]
    assert strip_name_marker(stem) == "Ghosts S05E01"


@pytest.mark.parametrize("stem", [
    "Ghosts.2021.S05E02.1080p.HEVC.x265-MeGusta[EZTVx.to]",  # Cyril's corpus: no marker
    "Movie (Sub-)", "Movie (Sub-ENG)", "Movie (sub-EN)", "Movie (Sub-EN) extra",
    "Movie (Sub-OV,EN)",  # the literal OV is never written (Cyril, 2026-11-19)
])
def test_no_marker(stem):
    assert parse_name_marker(stem) is None
    assert strip_name_marker(stem) == stem


@pytest.mark.parametrize("bad", [["fre"], ["ov"], []])
def test_marker_rejects_unresolved_codes(bad):
    with pytest.raises(ValueError):
        name_marker(bad)


# --- tracks the gate accepted and left untouched ----------------------------------------------


def test_gate_accepted_embedded_track_counts_only_through_the_record():
    sdh = dict(kind="embedded", title="English (SDH)", stream_index=2)
    assert not is_substudio_track(**sdh)
    assert not is_substudio_track(**sdh, manifest=[track_key(kind="embedded", stream_index=3)])
    assert is_substudio_track(**sdh, manifest=[track_key(kind="embedded", stream_index=2)])
