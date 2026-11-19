# library.py — Coverage scan: videos under MEDIA_ROOT x wanted languages, ffprobe cached by path+mtime+size. Exports: scan_library, video_path, video_id.

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import langcodes

from core.provenance import (FROM_TAG, ORIGIN_TAG, ORIGINS, OV, REJECTED, REVIEWED_TAG, REVIEWED_YES, UNTRANSLATED_TAG,
                             is_substudio_track, parse_cue_ids, parse_name_marker, track_key)

from .config import MEDIA_ROOT
from .schemas import LanguageCoverage, LibraryResponse, LibraryVideo, Settings, SubtitleTrack

logger = logging.getLogger("SubStudio.Library")

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm")
SUB_EXT = (".srt", ".vtt", ".ass", ".ssa")
SIDECAR_DIRS = ("", "Subs", "Subtitles")
# D-0001: at most 4 threads for the whole stack; ffprobe is light, but 4 at once is the ceiling.
PROBE_WORKERS = 4
PROBE_TIMEOUT_S = 20


def video_id(path: str) -> str:
    """Stable, URL-safe id: the path never travels in a URL, so it is never mis-encoded."""
    return hashlib.sha1(os.path.normcase(path).encode("utf-8")).hexdigest()[:16]


def _language(tag: Optional[str]) -> Optional[str]:
    """'eng' / 'fre' / 'fr-FR' -> ISO 639-1; None for absent, 'und' and non-language parts ('forced', 'SDH')."""
    if not tag:
        return None
    try:
        code = langcodes.Language.get(tag).language
    except ValueError:  # LanguageTagError subclasses ValueError
        return None
    if not code or len(code) != 2 or not langcodes.tag_is_valid(code):
        return None
    return code


@dataclass(frozen=True)
class _Probe:
    duration_s: Optional[float]
    streams: Tuple[dict, ...]
    error: Optional[str]


_cache: Dict[Tuple[str, int, int], _Probe] = {}
_cache_lock = threading.Lock()


def _probe(path: str) -> _Probe:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_entries",
           # All tags: the SubStudio origin tag's case is whatever the muxer wrote.
           "format=duration:stream=index,codec_type,codec_name:stream_tags", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=PROBE_TIMEOUT_S, check=True)
        data = json.loads(out.stdout or "{}")
    except subprocess.TimeoutExpired:
        return _Probe(None, (), f"ffprobe timed out after {PROBE_TIMEOUT_S} s")
    except subprocess.CalledProcessError as e:
        return _Probe(None, (), f"Not readable as a video ({_ffprobe_reason(e.stderr, path)})")
    except (OSError, json.JSONDecodeError) as e:
        return _Probe(None, (), f"ffprobe could not run: {e}")
    duration = data.get("format", {}).get("duration")
    return _Probe(float(duration) if duration else None, tuple(data.get("streams", [])), None)


def _ffprobe_reason(stderr: Optional[str], path: str) -> str:
    """First stderr line without its '[matroska,webm @ 0000…]' prefix or the path: 'EBML header parsing failed'."""
    for line in (stderr or "").splitlines():
        line = re.sub(r"^\[[^\]]*\]\s*", "", line.strip()).replace(path, "").strip(" :")
        if line:
            return line[:120]
    return "ffprobe gave no reason"


def _probe_cached(path: str, st: os.stat_result) -> _Probe:
    if st.st_size == 0:
        return _Probe(None, (), "Empty file (0 bytes)")
    key = (os.path.normcase(path), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    result = _probe(path)
    if result.error:
        logger.error(f"Probe failed for {path}: {result.error}")
        if result.error.startswith("ffprobe timed out") or result.error.startswith("ffprobe could not run"):
            return result  # may be transient (busy disk, ffprobe missing): retried next scan
    # A corrupt file stays corrupt until it changes, and a change changes the key: cache it too.
    with _cache_lock:
        _cache[key] = result
    return result


def _prune_cache(keep: set):
    """Forget probes of files that are gone or changed, so the cache never outgrows the library."""
    with _cache_lock:
        for k in [k for k in _cache if k not in keep]:
            del _cache[k]


def _walk(root: str, warnings: List[str]) -> List[Tuple[str, os.stat_result]]:
    found: List[Tuple[str, os.stat_result]] = []

    def on_error(err: OSError):
        # os.walk swallows these by default; a folder we cannot read must be visible, not absent.
        warnings.append(f"Cannot read folder {err.filename}: {err.strerror}")
        logger.error(warnings[-1])

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames, key=str.lower):
            if name.lower().endswith(VIDEO_EXT):
                path = os.path.join(dirpath, name)
                try:
                    found.append((path, os.stat(path)))
                except OSError as e:
                    warnings.append(f"Cannot read {path}: {e.strerror}")
                    logger.error(warnings[-1])
    return found


# Common sidecar flags that langcodes would otherwise read as languages ('hi' = Hindi).
SIDECAR_FLAGS = {"hi", "sdh", "cc", "forced", "default"}


def _sidecar_language(parts: List[str]) -> Optional[str]:
    """Movie.fr.srt, Movie.HI.en.srt, Movie.fr.forced.srt -> the language part. A flag counts as a
    language only when it is the only candidate (Movie.hi.srt stays Hindi)."""
    candidates = [p for p in parts if _language(p)]
    real = [p for p in candidates if p.lower() not in SIDECAR_FLAGS]
    return (real or candidates or [None])[0]


def _sidecars(video: str, warnings: List[str]) -> List[SubtitleTrack]:
    folder, name = os.path.split(video)
    stem = os.path.splitext(name)[0].lower()
    tracks: List[SubtitleTrack] = []
    for sub in SIDECAR_DIRS:
        d = os.path.join(folder, sub) if sub else folder
        if not os.path.isdir(d):
            continue
        try:
            names = sorted(os.listdir(d), key=str.lower)
        except OSError as e:
            # One unreadable Subs/ folder must not fail the library: say it, list the rest.
            msg = f"Cannot read folder {d}: {e.strerror}"
            if msg not in warnings:
                warnings.append(msg)
                logger.error(msg)
            continue
        for f in names:
            low = f.lower()
            # The stem must be followed by a dot: Aliens.fr.srt never belongs to Alien.mkv.
            if not low.endswith(SUB_EXT) or not low.startswith(stem + "."):
                continue
            parts = f[len(stem):].split(".")[1:-1]
            tag = _sidecar_language(parts)
            tracks.append(SubtitleTrack(kind="sidecar", language=_language(tag), language_tag=tag,
                                        path=os.path.join(d, f), codec=low.rsplit(".", 1)[-1],
                                        counted=False))
    return tracks


def _embedded(probe: _Probe) -> List[SubtitleTrack]:
    tracks = []
    for s in probe.streams:
        if s.get("codec_type") != "subtitle":
            continue
        tags = {k.upper(): v for k, v in (s.get("tags") or {}).items()}
        tag = tags.get("LANGUAGE")
        origin = (tags.get(ORIGIN_TAG) or "").lower()
        tracks.append(SubtitleTrack(kind="embedded", language=_language(tag), language_tag=tag,
                                    stream_index=s.get("index"), codec=s.get("codec_name"),
                                    title=tags.get("TITLE"), origin=origin if origin in ORIGINS else None,
                                    reviewed=(tags.get(REVIEWED_TAG) or "").lower() == REVIEWED_YES,
                                    from_language=_language(tags.get(FROM_TAG)),
                                    untranslated=len(parse_cue_ids(tags.get(UNTRANSLATED_TAG))), counted=False))
    return tracks


def _audio_language(probe: _Probe) -> Optional[str]:
    """OV = the first audio track's language tag (D-0002 §1). Untagged -> None: the job detects it."""
    for s in probe.streams:
        if s.get("codec_type") == "audio":
            tags = {k.upper(): v for k, v in (s.get("tags") or {}).items()}
            return _language(tags.get("LANGUAGE"))
    return None


def _verdicts(path: str, st: os.stat_result) -> Dict[str, str]:
    """The quality gate's verdicts on the subtitles of this exact file (path + mtime + size), as
    {track_key: origin} for an accepted one (an ORIGINS value) or {track_key: REJECTED} for a refused one.
    Written by the job store: api/job_store.verdicts, valid only while the file keeps the mtime and size it
    was judged at. A track absent from the record has not been checked."""
    from .job_store import job_store
    return job_store().verdicts(path, st)


# REJECTED (the verdict-record value of a refused subtitle) comes from core/provenance: the pipeline writes it.
NOT_CHECKED = "not checked yet: the quality check runs when the video is started"
REJECTED_REASON = "the quality check rejected it"


def _judge(track: SubtitleTrack, settings: Settings, record: Dict[str, str]) -> SubtitleTrack:
    """Decide whether a found track counts as coverage, and say why not when it does not.

    D-0002 (Cyril, 2026-11-19): a subtitle this SubStudio did not make counts only once the quality gate
    passed it, which the verdict record says; until then it is « not checked yet », never present.
    With count_existing off, it is ignored outright."""
    accepted = {k: v for k, v in record.items() if v in ORIGINS}
    mine = is_substudio_track(kind=track.kind, title=track.title, stream_index=track.stream_index,
                              path=track.path, manifest=accepted)
    key = None
    try:
        key = track_key(kind=track.kind, stream_index=track.stream_index, path=track.path)
    except ValueError:
        pass
    if mine and track.origin is None and accepted.get(key):
        track = track.model_copy(update={"origin": accepted[key]})
    titled = re.match(r"^SubStudio ([a-z]{2})$", track.title or "")
    if track.language is None:
        reason = "no language tag, so it cannot cover any language"
    elif titled and titled.group(1) != track.language:
        mine, reason = False, f"its title says {titled.group(1)} but its language tag says {track.language}"
    elif not mine and not settings.count_existing:
        reason = "not made by this SubStudio, and Settings does not count existing subtitles"
    elif track.kind == "embedded" and track.codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"):
        reason = "image subtitles cannot be read as text"
    elif not mine and record.get(key) == REJECTED:
        reason = REJECTED_REASON
    elif not mine:
        reason = NOT_CHECKED
    else:
        reason = None
    return track.model_copy(update={"made_by_substudio": mine, "counted": reason is None, "reason": reason})


def _coverage(wanted: str, audio: Optional[str], tracks: List[SubtitleTrack], probe_error: Optional[str]) -> LanguageCoverage:
    if wanted == OV and audio is None:
        state = "unknown" if probe_error else "missing"
        return LanguageCoverage(language=OV, state=state,
                                reason=probe_error or "audio language not tagged: detected when the job runs")
    lang = audio if wanted == OV else wanted
    counted = [t for t in tracks if t.counted and t.language == lang]
    if counted:
        # Only SubStudio tracks count now (made, or kept through the verdict record); « existing » stays in
        # the contract for records written before the gate.
        best = next((t for t in counted if t.made_by_substudio), counted[0])
        origin = best.origin if best.made_by_substudio else "existing"
        ours = best.made_by_substudio
        return LanguageCoverage(language=wanted, resolved=lang, state="passed", origin=origin, path=best.path,
                                reviewed=ours and best.reviewed, from_language=best.from_language if ours else None,
                                untranslated=best.untranslated if ours else 0)
    if probe_error:
        # A track inside the video (ours included) may exist unseen: not « missing ».
        return LanguageCoverage(language=wanted, resolved=lang, state="unknown", reason=probe_error)
    unchecked = [t for t in tracks if t.language == lang and t.reason == NOT_CHECKED]
    if unchecked:
        kinds = " + ".join(sorted({t.kind for t in unchecked}))
        n = f" ({len(unchecked)})" if len(unchecked) > 1 else ""
        return LanguageCoverage(language=wanted, resolved=lang, state="unchecked",
                                reason=f"not checked yet: {lang} {kinds}{n}; the quality check runs when the video is started")
    ignored = [t for t in tracks if not t.counted and t.language == lang]
    reason = None
    rejected = [t for t in ignored if t.reason == REJECTED_REASON]
    if rejected:
        kinds = " + ".join(sorted({t.kind for t in rejected}))
        reason = f"rejected by the quality check: {lang} {kinds}; a new one is made when the video is started"
    elif ignored:
        kinds = " + ".join(sorted({t.kind for t in ignored}))
        reason = f"ignored: {lang} {kinds}" + (f" ({len(ignored)})" if len(ignored) > 1 else "")
    return LanguageCoverage(language=wanted, resolved=lang, state="missing", reason=reason)


def _build(path: str, st: os.stat_result, probe: _Probe, settings: Settings, warnings: List[str]) -> LibraryVideo:
    record = _verdicts(path, st)
    tracks = [_judge(t, settings, record) for t in _embedded(probe) + _sidecars(path, warnings)]
    audio = _audio_language(probe)
    coverage = [_coverage(lang, audio, tracks, probe.error) for lang in settings.languages]
    rel = os.path.relpath(os.path.dirname(path), MEDIA_ROOT)
    name = os.path.basename(path)
    return LibraryVideo(
        id=video_id(path), path=path, name=name,
        folder="" if rel == "." else rel.replace(os.sep, "/"),
        size_bytes=st.st_size, mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        duration_s=probe.duration_s, audio_language=audio,
        # Validated = every wanted language is a SubStudio track; the name marker alone is only a claim.
        validated=all(c.state == "passed" and c.origin != "existing" for c in coverage),
        marker_languages=parse_name_marker(os.path.splitext(name)[0]),
        coverage=coverage, tracks=tracks, probe_error=probe.error,
    )

_paths: Dict[str, str] = {}


def scan_library(settings: Settings) -> LibraryResponse:
    """Blocking (ffprobe): call through asyncio.to_thread."""
    if not os.path.isdir(MEDIA_ROOT):
        raise FileNotFoundError(f"Media folder not found: {MEDIA_ROOT}")
    warnings: List[str] = []
    entries = _walk(MEDIA_ROOT, warnings)
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        probes = list(pool.map(lambda e: _probe_cached(*e), entries))
    videos = [_build(p, st, pr, settings, warnings) for (p, st), pr in zip(entries, probes)]
    _prune_cache({(os.path.normcase(p), st.st_mtime_ns, st.st_size) for p, st in entries})
    _paths.clear()
    _paths.update({v.id: v.path for v in videos})
    return LibraryResponse(root=MEDIA_ROOT, languages=settings.languages,
                           scanned_at=datetime.now(timezone.utc), videos=videos, warnings=warnings)


def audio_language(path: str) -> Optional[str]:
    """What « ov » stands for in this video: its first audio track's language tag, from the same cached probe as the
    Library. None when untagged or unreadable. Blocking (ffprobe on a cache miss)."""
    return _audio_language(_probe_cached(path, os.stat(path)))


def remember(path: str) -> str:
    """Registers a video a write just renamed (the swap's new marker), so its new id resolves before the next scan."""
    vid = video_id(path)
    _paths[vid] = path
    return vid


def video_path(vid: str) -> Optional[str]:
    """Resolve an id from the last scan; None when unknown (the caller answers 404)."""
    return _paths.get(vid)
