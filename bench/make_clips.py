# make_clips.py — Cuts bench clips (video excerpt without subtitles, 16 kHz mono audio, optional human references) from videos listed in a manifest. Exports: build_clip, main.
"""The source videos are only read, never written. Clips land in bench/clips/<id>/ (gitignored):
  clip.mkv    the excerpt, re-encoded small (frame-accurate cut, every subtitle stream dropped), for pipeline runs
  audio.wav   16 kHz mono PCM of the same window, for ASR candidates
  ref.<lang>.srt  human references cut to the same window and shifted to start at 0 (only when given)

Manifest (JSON, a list; keep real paths in bench/clips.local.json, which is gitignored):
  [{"id": "tos-en", "video": "D:/Test/Tears of Steel.mkv", "start_s": 60, "duration_s": 300,
    "source_lang": "en",
    "refs": {"en": {"file": "D:/Test/tos.en.srt"}, "fr": {"stream": 1}}}]
A ref is either a subtitle file or the index of a TEXT subtitle stream inside the video (0 = first subtitle stream).
`refs` may be empty: subtitle tracks already in a video are not trusted as references unless a human made them.
`audio_stream` (0 = first audio stream) is required when the video has more than one audio track: picking the first
could transcribe a dub and score it against the original's reference.

Usage: python -m bench.make_clips bench/clips.local.json [--only ID]
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

from bench.media import ffmpeg, probe
from bench.subs import load_cues

CLIPS_DIR = Path(__file__).parent / "clips"


def _ms_to_srt(ms: int) -> str:
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _window_srt(ref_file: Path, lang: str, start_ms: int, end_ms: int) -> str:
    """Cues overlapping [start, end), shifted so the clip starts at 0 and clamped to the clip."""
    cues, _ = load_cues(ref_file, lang)
    kept = [c for c in cues if c.end_ms > start_ms and c.start_ms < end_ms]
    if not kept:
        raise ValueError(f"{ref_file.name} has no cue between {start_ms} and {end_ms} ms: pick another window")
    blocks = []
    for i, c in enumerate(kept, 1):
        a = max(c.start_ms, start_ms) - start_ms
        b = min(c.end_ms, end_ms) - start_ms
        blocks.append(f"{i}\n{_ms_to_srt(a)} --> {_ms_to_srt(b)}\n{c.text}\n")
    return "\n".join(blocks)


def _extract_stream(video: Path, index: int, out: Path) -> None:
    subs = [s for s in probe(video)["streams"] if s["codec_type"] == "subtitle"]
    if index >= len(subs):
        raise ValueError(f"{video.name} has {len(subs)} subtitle stream(s); index {index} does not exist")
    codec = subs[index]["codec_name"]
    if codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"):
        raise ValueError(f"subtitle stream {index} of {video.name} is a bitmap ({codec}); it cannot be a text reference")
    ffmpeg("-i", str(video), "-map", f"0:s:{index}", "-c:s", "srt", str(out))


def build_clip(entry: dict, clips_dir: Path = CLIPS_DIR) -> Path:
    video = Path(entry["video"])
    if not video.is_file():
        raise FileNotFoundError(f"clip {entry['id']}: {video} does not exist")
    start_s, dur_s = float(entry["start_s"]), float(entry["duration_s"])
    audio_count = sum(1 for s in probe(video)["streams"] if s["codec_type"] == "audio")
    if audio_count == 0:
        raise ValueError(f"clip {entry['id']}: {video.name} has no audio track")
    if "audio_stream" not in entry and audio_count > 1:
        raise ValueError(f"clip {entry['id']}: {video.name} has {audio_count} audio tracks; set audio_stream")
    audio = f"0:a:{int(entry.get('audio_stream', 0))}"
    out = clips_dir / entry["id"]
    out.mkdir(parents=True, exist_ok=True)
    window = ["-ss", str(start_s), "-t", str(dur_s), "-i", str(video)]
    # Re-encoded rather than stream-copied: a copy cut starts at the previous keyframe, so the clip and its
    # references would disagree by up to several seconds. Decoder and encoder each get 2 threads: an encoder-only
    # -threads 4 peaked at 4.5 threads on an HEVC source (measured 2026-11-19), over D-0001's budget of 4.
    ffmpeg("-threads", "2", *window, "-map", "0:v:0", "-map", audio, "-sn", "-map_metadata", "-1", "-threads", "2",
           "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
           "-c:a", "aac", "-b:a", "128k", str(out / "clip.mkv"))
    ffmpeg(*window, "-map", audio, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out / "audio.wav"))
    start_ms, end_ms = int(start_s * 1000), int((start_s + dur_s) * 1000)
    for lang, ref in entry.get("refs", {}).items():
        with tempfile.TemporaryDirectory() as tmp:
            if "file" in ref:
                src = Path(ref["file"])
            else:
                src = Path(tmp) / "ref.srt"
                _extract_stream(video, int(ref["stream"]), src)
            (out / f"ref.{lang}.srt").write_text(_window_srt(src, lang, start_ms, end_ms), encoding="utf-8")
    meta = {k: entry[k] for k in ("id", "start_s", "duration_s", "source_lang")} | {
        "video_name": video.name, "refs": sorted(entry.get("refs", {}))}
    (out / "clip.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("manifest", type=Path)
    p.add_argument("--only", default=None, help="build a single clip id")
    args = p.parse_args(argv)
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    for entry in entries:
        if args.only and entry["id"] != args.only:
            continue
        print(f"{entry['id']}: {build_clip(entry)}")


if __name__ == "__main__":
    main(sys.argv[1:])
