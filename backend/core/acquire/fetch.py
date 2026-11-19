# fetch.py — rung 2 end to end: download human subtitles for a video, re-time each to its audio, keep the first the gate passes. Exports: fetch, Acquired, FetchResult.
import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional

from .align import AlignError, align
from .download import MAX_CANDIDATES, download_subtitles

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
    # Per language, every candidate tried and turned down, with why (« gestdown #1: timing moves -5.7 s … »).
    rejected: Dict[str, List[str]] = field(default_factory=dict)


def fetch(video_path: str, languages: Iterable[str], work_dir: str, env: Optional[dict] = None,
          accept: Optional[Callable[[str, str], Optional[str]]] = None,
          per_language: int = MAX_CANDIDATES) -> FetchResult:
    """Download, re-time, and judge each candidate until one is kept: per language, the best match first, then the
    next ones (up to `per_language`). `accept(lang, srt_path)` is the quality gate: None keeps the candidate, a
    sentence turns it down. Languages with nothing kept are absent; ProvidersUnavailable propagates.

    `work_dir` is SubStudio's per-video work folder, never the video's own folder: nothing is written beside the source.
    """
    kept: Dict[str, Acquired] = {}

    def try_one(lang: str, d) -> Optional[str]:
        if d.hash_match:
            # Cut for this exact file: aligning would spend CPU to confirm an offset of zero.
            path, offset = d.path, 0.0
        else:
            dest = os.path.join(work_dir, f"aligned.{os.path.basename(d.path)}")
            try:
                r = align(video_path, d.path, dest)
            except AlignError as e:
                return f"could not be re-timed: {e}"
            if not r.synced:
                return "could not be matched to the audio"
            path, offset = r.output_path, r.offset_seconds
            logger.info(f"{lang}: {os.path.basename(d.path)} re-timed by {offset:+.2f} s")
        why = accept(lang, path) if accept else None
        if why:
            return why
        kept[lang] = Acquired(lang, path, d.provider, d.hash_match, True, offset)
        return None

    report = download_subtitles(video_path, languages, os.path.join(work_dir, "downloaded"), env,
                                try_one=try_one, per_language=per_language)
    return FetchResult(kept, report.failed_providers, report.rejected)
