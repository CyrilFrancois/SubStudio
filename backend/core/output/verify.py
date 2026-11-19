# verify.py — The check the verified swap depends on: the temp file against the source, before it replaces anything. Exports: none (core/output/inplace.py calls _verify).
"""Same video, audio, covers, attachments and chapters; duration within DURATION_TOLERANCE_S; every kept subtitle
track still there; each new track present once, tagged with its language, origin and reviewed mark, and decoding
to the expected number of cues. Moved unchanged from inplace.py (2026-11-19, file over 400 lines)."""

import subprocess
from pathlib import Path
from typing import Collection, Dict, Mapping, Optional

import pysubs2

from ..acquire.plan import normalize_language
from ..provenance import ORIGIN_TAG, REVIEWED_TAG, REVIEWED_YES, substudio_track_title
from .media import OutputError, _duration, _kept_subs, _probe, _signature, _sub_key

DURATION_TOLERANCE_S = 1.0


def _decoded_cue_count(path: Path, sub_index: int) -> int:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", f"0:s:{sub_index}", "-f", "srt", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise OutputError(f"the new subtitle track #{sub_index} cannot be decoded")
    return len(pysubs2.SSAFile.from_string(result.stdout.decode("utf-8", "replace"), format_="srt").events)


def _verify(source_info: dict, tmp: Path, expected: Dict[str, int], origins: Optional[Mapping[str, str]] = None,
            reviewed: Collection[str] = (), audio_tag: Optional[str] = None) -> None:
    """The check the swap depends on. Raises OutputError on the first difference. With `audio_tag`, the first audio
    stream must now carry that language and nothing else of the audio may differ."""
    out = _probe(tmp)
    before, after = _signature(source_info), _signature(out)
    if audio_tag and before["audio"]:
        codec, _, flags = before["audio"][0]
        before["audio"][0] = (codec, audio_tag, flags)
    for part in before:
        if before[part] != after[part]:
            raise OutputError(f"the new file's {part} differ from the source ({after[part]} vs {before[part]})")
    drift = abs(_duration(out) - _duration(source_info))
    if drift > DURATION_TOLERANCE_S:
        raise OutputError(f"the new file's duration differs from the source by {drift:.1f} s")

    subs = [s for s in out.get("streams", []) if s.get("codec_type") == "subtitle"]
    kept_before = [_sub_key(s) for s in _kept_subs(source_info, set(expected))]
    kept_after = [_sub_key(s) for s in subs[:len(kept_before)]]
    if kept_after != kept_before or len(subs) != len(kept_before) + len(expected):
        raise OutputError(f"the existing subtitle tracks were not all kept ({kept_after} vs {kept_before})")
    for lang, count in expected.items():
        title = substudio_track_title(lang)
        found = [i for i, s in enumerate(subs) if s.get("tags", {}).get("title") == title]
        if len(found) != 1:
            raise OutputError(f"the new file has {len(found)} « {title} » tracks instead of one")
        tag = subs[found[0]].get("tags", {}).get("language")
        if normalize_language(tag) != lang:
            raise OutputError(f"the « {title} » track is tagged {tag!r}")
        tags = {k.upper(): v for k, v in subs[found[0]].get("tags", {}).items()}
        if origins is not None and tags.get(ORIGIN_TAG) != origins[lang]:
            raise OutputError(f"the « {title} » track records origin {tags.get(ORIGIN_TAG)!r} instead of {origins[lang]!r}")
        if (tags.get(REVIEWED_TAG) == REVIEWED_YES) != (lang in reviewed):
            raise OutputError(f"the « {title} » track's reviewed mark is {tags.get(REVIEWED_TAG)!r}, expected "
                              f"{REVIEWED_YES if lang in reviewed else 'none'}")
        decoded = _decoded_cue_count(tmp, found[0])
        if decoded != count:
            raise OutputError(f"the « {title} » track decodes to {decoded} cues instead of {count}")
