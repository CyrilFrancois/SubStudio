# test_make_clips.py — Checks bench clip cutting on seeded media: window and shift of references, no subtitle stream kept, refusals. Exports: tests only.
import array
import wave

import pytest

from bench.make_clips import build_clip
from bench.media import ffmpeg, probe
from bench.subs import Cue, load_cues


def _entry(media, key, **extra):
    return {"id": key, "video": str(media[key]), "start_s": 2.0, "duration_s": 2.5, "source_lang": "fr", **extra}


def test_clip_is_cut_to_the_window_with_shifted_reference(media, tmp_path):
    ref = media["cp1252_sidecar"].with_suffix(".fr.srt")
    out = build_clip(_entry(media, "cp1252_sidecar", refs={"fr": {"file": str(ref)}}), tmp_path)
    info = probe(out / "clip.mkv")
    assert abs(float(info["format"]["duration"]) - 2.5) < 0.2
    assert [s["codec_type"] for s in info["streams"]].count("subtitle") == 0
    wav = [s for s in probe(out / "audio.wav")["streams"] if s["codec_type"] == "audio"][0]
    assert (wav["sample_rate"], wav["channels"]) == ("16000", 1)
    # Cue 1 (0.5-2.0 s) ends where the window starts: excluded. Cue 2 (2.5-4.5 s) becomes 0.5-2.5 s, UTF-8.
    cues, enc = load_cues(out / "ref.fr.srt")
    assert enc == "utf-8"
    assert cues == [Cue(500, 2500, "Une œuvre à Noël, naïve et sûre.")]


def test_existing_subtitle_tracks_are_dropped_from_the_clip(media, tmp_path):
    out = build_clip(_entry(media, "pgs_only"), tmp_path)
    assert [s["codec_type"] for s in probe(out / "clip.mkv")["streams"]].count("subtitle") == 0


def test_bitmap_stream_is_refused_as_a_reference(media, tmp_path):
    with pytest.raises(ValueError, match="bitmap"):
        build_clip(_entry(media, "pgs_only", refs={"fr": {"stream": 0}}), tmp_path)


def test_video_without_audio_is_refused(media, tmp_path):
    with pytest.raises(ValueError, match="no audio"):
        build_clip(_entry(media, "no_audio"), tmp_path)


def test_two_audio_tracks_need_an_explicit_choice(tmp_path):
    # Track 0 is a 440 Hz "dub", track 1 is silence: the clip must take the track the manifest names.
    video = tmp_path / "dub_first.mkv"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=5",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=5",
           "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-map", "0:v", "-map", "1:a", "-map", "2:a",
           "-t", "5", "-c:v", "mpeg4", "-c:a", "aac", str(video))
    entry = {"id": "dub", "video": str(video), "start_s": 1.0, "duration_s": 2.0, "source_lang": "en"}
    with pytest.raises(ValueError, match="2 audio tracks"):
        build_clip(entry, tmp_path / "clips")
    out = build_clip({**entry, "audio_stream": 1}, tmp_path / "clips")
    with wave.open(str(out / "audio.wav")) as w:
        frames = w.readframes(w.getnframes())
    samples = array.array("h", frames)
    assert len(samples) > 16000                        # about 2 s at 16 kHz
    assert max(abs(s) for s in samples) < 50           # silence: the second track, not the 440 Hz first one
