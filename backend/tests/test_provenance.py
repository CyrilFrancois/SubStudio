# test_provenance.py — The « made by SubStudio » rule: old tracks never match, new ones always do. Exports: tests only.

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.provenance import is_substudio_track, substudio_track_title  # noqa: E402


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
    assert is_substudio_track(kind="sidecar", path=str(srt), manifest=[str(srt)])


def test_title_rejects_a_three_letter_code():
    with pytest.raises(ValueError):
        substudio_track_title("fre")
