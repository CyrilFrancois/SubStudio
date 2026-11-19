# fetch.py — rung 2 end to end: download human subtitles for a video, then re-time each to its audio. Exports: fetch, Acquired, FetchResult.
import logging
import os
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Optional

from .align import AlignError, align
from .download import download_subtitles

logger = logging.getLogger("SubStudio.Acquire")


@dataclass(frozen=True)
class Acquired:
    language: str
    path: str               # the subtitle to use: re-timed when alignment succeeded, else as downloaded
    provider: str
    hash_match: bool
    synced: bool            # timings fitted to this video's speech (or cut for this exact file)
    offset_seconds: Optional[float] = None


@dataclass(frozen=True)
class FetchResult:
    subtitles: Dict[str, Acquired]
    # Non-empty: some providers errored, so a missing language is « not found where we could look » (worth a retry
    # later), not « no human subtitle exists ».
    failed_providers: FrozenSet[str] = frozenset()


def fetch(video_path: str, languages: Iterable[str], work_dir: str, env: Optional[dict] = None) -> FetchResult:
    """Download then align. Languages with no acceptable subtitle are absent; ProvidersUnavailable propagates.

    `work_dir` is SubStudio's per-video work folder, never the video's own folder: nothing is written beside the source.
    """
    report = download_subtitles(video_path, languages, os.path.join(work_dir, "downloaded"), env)
    out: Dict[str, Acquired] = {}
    for lang, d in report.subtitles.items():
        if d.hash_match:
            # Cut for this exact file: aligning would spend CPU to confirm an offset of zero.
            out[lang] = Acquired(lang, d.path, d.provider, True, True, 0.0)
            continue
        dest = os.path.join(work_dir, f"aligned.{lang}.srt")
        try:
            r = align(video_path, d.path, dest)
        except AlignError as e:
            # The subtitle is still a human one; ship it with its own timings and say so, rather than lose it.
            logger.error(f"{lang}: re-timing failed, keeping downloaded timings: {e}")
            out[lang] = Acquired(lang, d.path, d.provider, False, False)
            continue
        out[lang] = Acquired(lang, r.output_path, d.provider, False, r.synced, r.offset_seconds)
        logger.info(f"{lang}: re-timed by {r.offset_seconds:+.2f} s" if r.synced else
                    f"{lang}: alignment untrustworthy, downloaded timings kept")
    return FetchResult(out, report.failed_providers)
