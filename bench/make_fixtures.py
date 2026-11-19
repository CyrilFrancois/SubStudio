# make_fixtures.py — Seeds the edge-case media that have bitten SubStudio (no audio, PGS only, non-ASCII name, cp1252 SRT, subs past the end, zero bytes). Exports: build_all, FIXTURES.
"""Every fixture is generated from ffmpeg's synthetic sources (testsrc2 + sine), so a run is reproducible and no
real media is ever needed or touched. Usage: python -m bench.make_fixtures <out_dir>
"""
import json
import sys
from pathlib import Path

from bench.media import ffmpeg
from bench.pgs import write_sup

W, H = 640, 360
SECONDS = 5

# Each name maps to what the fixture must show; backend/tests checks every claim against ffprobe or the bytes.
FIXTURES = {
    "no_audio": "no_audio.mkv",
    "pgs_only": "pgs_only.mkv",
    "non_ascii": "Épisode 01 – l'été à Zürich 東京.mkv",
    "cp1252_sidecar": "cp1252 sidecar.mkv",
    "subs_past_end": "subs_past_end.mkv",
    "zero_bytes": "zero_bytes.mkv",
}

# œ and € exist in cp1252 but not in Latin-1, so a reader that guesses Latin-1 is caught as well as one that drops bytes.
CP1252_SRT = (
    "1\r\n00:00:00,500 --> 00:00:02,000\r\nL'été déjà fini, ça coûte 5 € !\r\n\r\n"
    "2\r\n00:00:02,500 --> 00:00:04,500\r\nUne œuvre à Noël, naïve et sûre.\r\n\r\n"
)

PAST_END_SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nInside the video.\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\nStraddles the end.\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\nEntirely past the end.\n\n"
)

_SOURCES = ["-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=10:duration={SECONDS}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:duration={SECONDS}"]
# bitexact keeps encoder version strings out of the file, so the same ffmpeg yields the same bytes.
_EXACT = ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1"]
_CODECS = ["-c:v", "mpeg4", "-q:v", "8", "-c:a", "aac", "-b:a", "48k"]


def _av(out: Path) -> None:
    ffmpeg(*_SOURCES, "-map", "0:v", "-map", "1:a", *_CODECS, *_EXACT, str(out))


def build_all(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {key: out_dir / name for key, name in FIXTURES.items()}

    ffmpeg(*_SOURCES[:4], "-map", "0:v", "-c:v", "mpeg4", "-q:v", "8", *_EXACT, str(paths["no_audio"]))

    sup = out_dir / "pgs_only.sup"
    write_sup(sup, W, H, [(1000, 3000), (3500, 4500)])
    # -copyts: without it ffmpeg moves each input's first timestamp to 0, which shifted the PGS cues 1 s early.
    ffmpeg("-copyts", *_SOURCES, "-f", "sup", "-i", str(sup), "-map", "0:v", "-map", "1:a", "-map", "2:s",
           *_CODECS, "-c:s", "copy", "-metadata:s:s:0", "language=fre", *_EXACT, str(paths["pgs_only"]))
    sup.unlink()

    _av(paths["non_ascii"])

    _av(paths["cp1252_sidecar"])
    paths["cp1252_sidecar"].with_suffix(".fr.srt").write_bytes(CP1252_SRT.encode("cp1252"))

    _av(paths["subs_past_end"])
    paths["subs_past_end"].with_suffix(".en.srt").write_text(PAST_END_SRT, encoding="utf-8", newline="\n")

    paths["zero_bytes"].write_bytes(b"")

    (out_dir / "manifest.json").write_text(
        json.dumps({k: p.name for k, p in paths.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m bench.make_fixtures <out_dir>")
    for key, path in build_all(Path(sys.argv[1])).items():
        print(f"{key}: {path}")
