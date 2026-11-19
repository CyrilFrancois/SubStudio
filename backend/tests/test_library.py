# test_library.py — Coverage matrix on seeded files: ignored, gate-accepted, ours, OV, unreadable. Exports: tests only.

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api import library  # noqa: E402
from api.schemas import Settings  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg/ffprobe on PATH")

SRT = "1\n00:00:00,500 --> 00:00:02,000\nHello\n\n"


def _mkv(path, *, audio_lang=None, subs=()):
    """A 2-second MKV; subs = [(language, title, origin_tag_or_None)]."""
    srt = path.parent / "s.srt"
    srt.write_text(SRT, encoding="utf-8")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=2",
           "-f", "lavfi", "-i", "sine=duration=2"]
    for _ in subs:
        cmd += ["-i", str(srt)]
    cmd += ["-map", "0", "-map", "1"] + sum((["-map", str(i + 2)] for i in range(len(subs))), [])
    cmd += ["-c:v", "libx264", "-c:a", "aac", "-c:s", "srt"]
    if audio_lang:
        cmd += ["-metadata:s:a:0", f"language={audio_lang}"]
    for i, (lang, title, origin) in enumerate(subs):
        cmd += [f"-metadata:s:s:{i}", f"language={lang}", f"-metadata:s:s:{i}", f"title={title}"]
        if origin:
            cmd += [f"-metadata:s:s:{i}", f"SUBSTUDIO_ORIGIN={origin}"]
    subprocess.run(cmd + [str(path)], check=True)
    srt.unlink()


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(library, "_cache", {})
    return tmp_path


def _scan(languages, count_existing=False):
    lib = library.scan_library(Settings(languages=languages, count_existing=count_existing))
    return {v.name: v for v in lib.videos}


def _cells(video):
    return {c.language: (c.state, c.origin, c.resolved) for c in video.coverage}


def test_release_sdh_track_is_listed_but_not_counted(root):
    _mkv(root / "ep.mkv", audio_lang="eng", subs=[("eng", "English (SDH)", None)])
    v = _scan(["ov", "en", "fr"])["ep.mkv"]
    assert _cells(v) == {"ov": ("missing", None, "en"), "en": ("missing", None, "en"), "fr": ("missing", None, "fr")}
    assert v.coverage[1].reason == "ignored: en embedded"
    assert v.tracks[0].counted is False and not v.validated


def test_gate_accepted_track_counts_as_kept_through_the_record(root, monkeypatch):
    _mkv(root / "ep.mkv", audio_lang="eng", subs=[("eng", "English (SDH)", None)])
    monkeypatch.setattr(library, "_verdicts", lambda path, st: {"stream:2": "kept"})
    v = _scan(["ov", "en"])["ep.mkv"]
    assert _cells(v) == {"ov": ("passed", "kept", "en"), "en": ("passed", "kept", "en")}
    assert v.validated  # OV = EN: one track covers both columns


def test_substudio_track_carries_its_origin(root):
    _mkv(root / "Film (Sub-FR).mkv", audio_lang="eng", subs=[("fre", "SubStudio fr", "translated")])
    v = _scan(["fr"])["Film (Sub-FR).mkv"]
    assert _cells(v) == {"fr": ("passed", "translated", "fr")}
    assert v.validated and v.marker_languages == ["fr"]


def test_an_existing_subtitle_is_not_checked_yet_until_the_gate_judged_it(root, monkeypatch):
    # D-0002 (Cyril, 2026-11-19): count_existing ON counts a pre-existing subtitle only once the gate passed it.
    _mkv(root / "ep.mkv", audio_lang="eng", subs=[("eng", "English (SDH)", None)])
    monkeypatch.setattr(library, "_verdicts", lambda path, st: {})
    v = _scan(["en"], count_existing=True)["ep.mkv"]
    assert _cells(v) == {"en": ("unchecked", None, "en")}
    assert v.coverage[0].reason.startswith("not checked yet: en embedded")
    assert v.tracks[0].counted is False and not v.validated


def test_a_subtitle_the_gate_rejected_leaves_the_language_missing(root, monkeypatch):
    _mkv(root / "ep.mkv", audio_lang="eng", subs=[("eng", "English (SDH)", None)])
    monkeypatch.setattr(library, "_verdicts", lambda path, st: {"stream:2": library.REJECTED})
    v = _scan(["en"], count_existing=True)["ep.mkv"]
    assert _cells(v) == {"en": ("missing", None, "en")}
    assert v.tracks[0].reason == "the quality check rejected it" and not v.tracks[0].made_by_substudio
    assert v.coverage[0].reason.startswith("rejected by the quality check: en embedded")


def test_count_existing_off_ignores_even_a_gate_accepted_record(root, monkeypatch):
    # The record makes the track SubStudio's own (kept), so it still counts: OFF only ignores unjudged ones.
    _mkv(root / "ep.mkv", audio_lang="eng", subs=[("eng", "English (SDH)", None), ("fre", "French", None)])
    monkeypatch.setattr(library, "_verdicts", lambda path, st: {"stream:2": "kept"})
    v = _scan(["en", "fr"], count_existing=False)["ep.mkv"]
    assert _cells(v) == {"en": ("passed", "kept", "en"), "fr": ("missing", None, "fr")}
    assert v.coverage[1].reason == "ignored: fr embedded"


def test_untagged_audio_leaves_ov_to_the_job(root):
    _mkv(root / "ep.mkv")
    c = _scan(["ov"])["ep.mkv"].coverage[0]
    assert (c.state, c.resolved) == ("missing", None)
    assert "detected when the job runs" in c.reason


def test_unreadable_file_is_unknown_not_missing(root):
    (root / "broken.mkv").write_bytes(b"")
    (root / "garbage.mkv").write_bytes(b"not a video at all" * 50)
    lib = _scan(["en"])
    assert lib["broken.mkv"].coverage[0].state == "unknown"
    assert lib["broken.mkv"].probe_error == "Empty file (0 bytes)"
    garbage = lib["garbage.mkv"]
    assert garbage.coverage[0].state == "unknown"
    assert garbage.probe_error.startswith("Not readable as a video (") and "@ 0" not in garbage.probe_error


# --- reviewer findings, 2026-11-19 ------------------------------------------------------------

def test_a_sidecar_never_belongs_to_a_video_whose_name_is_its_prefix(root):
    _mkv(root / "Alien.mkv", audio_lang="eng")
    _mkv(root / "Aliens.mkv", audio_lang="eng")
    (root / "Aliens.fr.srt").write_text(SRT, encoding="utf-8")
    lib = _scan(["fr"], count_existing=True)
    assert lib["Alien.mkv"].tracks == [] and lib["Alien.mkv"].coverage[0].state == "missing"
    assert lib["Aliens.mkv"].coverage[0].state == "unchecked"


def test_hearing_impaired_flag_is_not_hindi(root):
    _mkv(root / "Movie.mkv", audio_lang="eng")
    (root / "Movie.HI.en.srt").write_text(SRT, encoding="utf-8")
    (root / "Movie.hi.srt").write_text(SRT, encoding="utf-8")
    langs = sorted(t.language for t in _scan(["en"])["Movie.mkv"].tracks)
    assert langs == ["en", "hi"]  # the lone « hi » stays Hindi; « HI » beside « en » is a flag


def test_substudio_title_must_agree_with_the_language_tag(root):
    _mkv(root / "ep.mkv", audio_lang="eng", subs=[("eng", "SubStudio fr", "translated")])
    v = _scan(["en", "fr"])["ep.mkv"]
    assert [c.state for c in v.coverage] == ["missing", "missing"]
    assert "title says fr" in v.tracks[0].reason


def test_an_unreadable_subs_folder_is_a_warning_not_a_failure(root, monkeypatch):
    _mkv(root / "ep.mkv", audio_lang="eng")
    (root / "Subs").mkdir()
    real = os.listdir

    def deny(path):
        if os.path.basename(str(path)) == "Subs":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(library.os, "listdir", deny)
    lib = library.scan_library(Settings(languages=["en"]))
    assert [v.name for v in lib.videos] == ["ep.mkv"]
    assert any("Cannot read folder" in w and "Subs" in w for w in lib.warnings)
