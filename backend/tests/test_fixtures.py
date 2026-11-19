# test_fixtures.py — Proves each seeded media fixture really has the property its name claims, read back by ffprobe or from its bytes. Exports: tests only.
import subprocess

import pytest

from bench.make_fixtures import SECONDS, H, W
from bench.media import _binary, ffmpeg, probe
from bench.subs import load_cues


def _streams(path, kind):
    return [s for s in probe(path)["streams"] if s["codec_type"] == kind]


def test_no_audio_has_video_and_no_audio(media):
    assert len(_streams(media["no_audio"], "video")) == 1
    assert _streams(media["no_audio"], "audio") == []


def test_pgs_only_has_one_bitmap_track_and_no_text_track(media):
    subs = _streams(media["pgs_only"], "subtitle")
    assert [s["codec_name"] for s in subs] == ["hdmv_pgs_subtitle"]
    assert subs[0]["tags"]["language"] == "fre"
    assert len(_streams(media["pgs_only"], "audio")) == 1


def _white_pixels_at(path, seconds):
    # Burn the PGS track onto black and count white pixels in one frame: "it decodes" alone passed a broken
    # bitmap (one line short) on 2026-11-19, so the test looks at what a player would draw.
    cmd = [_binary("ffmpeg"), "-v", "error", "-i", str(path), "-filter_complex",
           f"color=black:s={W}x{H}:r=10:d={SECONDS}[bg];[bg][0:s:0]overlay=eof_action=pass",
           "-ss", str(seconds), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    frame = subprocess.run(cmd, capture_output=True, check=True).stdout
    assert len(frame) == W * H
    return sum(1 for b in frame if b > 200)


def test_pgs_track_renders_its_box_at_the_right_times(media):
    # Cues are 1.0-3.0 s and 3.5-4.5 s (make_fixtures); each edge is checked 0.1 s either side, so a shift of
    # more than one frame (10 fps) fails. The 1 s shift -copyts fixed failed here first.
    box = (W // 2) * 40  # the full 320x40 box
    expected = {0.9: 0, 1.1: box, 2.9: box, 3.1: 0, 3.4: 0, 3.6: box, 4.4: box, 4.6: 0}
    seen = {t: _white_pixels_at(media["pgs_only"], t) for t in expected}
    assert seen == expected


def test_pgs_cannot_become_srt(media, tmp_path):
    # The boundary the muxer must respect: bitmap subtitles are copied, never converted to text.
    with pytest.raises(RuntimeError, match="(?i)subtitle encoding|bitmap"):
        ffmpeg("-i", str(media["pgs_only"]), "-map", "0:s:0", "-c:s", "srt", str(tmp_path / "x.srt"))


def test_non_ascii_name_survives_and_probes(media):
    path = media["non_ascii"]
    assert path.exists() and not path.name.isascii() and " " in path.name
    assert "東京" in path.name and "é" in path.name
    assert len(_streams(path, "audio")) == 1


def test_cp1252_sidecar_is_not_utf8_and_reads_back_with_accents(media):
    srt = media["cp1252_sidecar"].with_suffix(".fr.srt")
    raw = srt.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert b"\x9c" in raw and b"\x80" in raw  # œ and € only exist in cp1252, not Latin-1
    cues, encoding = load_cues(srt)
    assert encoding == "cp1252"
    assert cues[0].text == "L'été déjà fini, ça coûte 5 € !"
    assert cues[1].text == "Une œuvre à Noël, naïve et sûre."


def test_subs_past_end_has_cues_beyond_the_video(media):
    video = media["subs_past_end"]
    duration_ms = float(probe(video)["format"]["duration"]) * 1000
    assert abs(duration_ms - SECONDS * 1000) < 200
    cues, _ = load_cues(video.with_suffix(".en.srt"))
    inside = [c for c in cues if c.end_ms <= duration_ms]
    straddling = [c for c in cues if c.start_ms < duration_ms < c.end_ms]
    beyond = [c for c in cues if c.start_ms >= duration_ms]
    assert (len(inside), len(straddling), len(beyond)) == (1, 1, 1)


def test_zero_bytes_is_empty_and_ffprobe_refuses_it(media):
    assert media["zero_bytes"].stat().st_size == 0
    with pytest.raises(RuntimeError):
        probe(media["zero_bytes"])


def test_generation_is_reproducible(media, tmp_path):
    # Seeded means the same bytes every run with the same ffmpeg; a change in a fixture is then a real change.
    from bench.make_fixtures import build_all
    again = build_all(tmp_path)
    for key in ("no_audio", "pgs_only", "subs_past_end"):
        assert again[key].read_bytes() == media[key].read_bytes(), key
