# track.py — Reads back the subtitle track SubStudio wrote into a video: its cues and its origin and reviewed tags. Exports: SubStudioTrack, read_track.
"""What a person reviews is what shipped: the « SubStudio <lang> » track inside the video (core/provenance), read with
ffmpeg straight to stdout, so nothing is ever written next to a video in the library. Cue ids are the track's
positions, 1..n, the order the swap wrote them in."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..provenance import ORIGIN_TAG, REVIEWED_TAG, REVIEWED_YES, substudio_track_title
from ..subtitles import Cue, SubtitleReadError, parse
from .media import OutputError, _probe


@dataclass(frozen=True)
class SubStudioTrack:
    cues: List[Cue]
    origin: Optional[str]     # provenance.ORIGINS, or None for a track written before the tag existed
    reviewed: bool


def read_track(video: Path, lang: str) -> Optional[SubStudioTrack]:
    """The video's « SubStudio <lang> » track, or None when it has none. Raises OutputError when the video cannot be
    probed, holds that track twice, or the track cannot be decoded: a failed read is never « no subtitle »."""
    title = substudio_track_title(lang)
    subs = [s for s in _probe(Path(video)).get("streams", []) if s.get("codec_type") == "subtitle"]
    found = [s for s in subs if s.get("tags", {}).get("title") == title]
    if not found:
        return None
    if len(found) > 1:
        raise OutputError(f"the video holds {len(found)} « {title} » tracks; it cannot say which one to review")
    stream = found[0]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-map", f"0:{stream['index']}",
           "-f", "srt", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise OutputError(f"the « {title} » track cannot be read: "
                          f"{result.stderr.decode('utf-8', 'replace').strip()[:200]}")
    try:
        cues = parse(result.stdout.decode("utf-8"), "srt")  # ffmpeg's SRT muxer always writes UTF-8
    except (UnicodeDecodeError, SubtitleReadError) as e:
        raise OutputError(f"the « {title} » track cannot be read: {e}") from e
    tags = {k.upper(): v for k, v in stream.get("tags", {}).items()}
    return SubStudioTrack(cues=cues, origin=tags.get(ORIGIN_TAG), reviewed=tags.get(REVIEWED_TAG) == REVIEWED_YES)
