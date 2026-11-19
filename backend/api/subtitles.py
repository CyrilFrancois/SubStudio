# subtitles.py — The Review screen's cues: read from the video's SubStudio track, edited text written back by the verified swap. Exports: SubtitleError, editing, get_cue_list, save_cue_list.
"""GET/PUT /api/subtitles/{video_id}/{lang} on the real data (routes.py calls these through asyncio.to_thread).

- The cues are the video's « SubStudio <lang> » track (core/output.read_track): what shipped, after a restart too.
  Ids are the track's positions 1..n.
- source_text comes only from the run the pipeline keeps in memory for /api/retry: the cell's cue at the same
  position with the same timing gives the source cue's id, and the source (the human subtitle or the transcript the
  cell was translated from) gives its text. No run (a restart, another session), no source_text.
- PUT takes the text of each cue and nothing else: ids and timings must equal the track's, else the track changed
  since it was loaded (409). The new track goes through core/output.update_in_place, the one way to replace a video
  (CLAUDE.md « Forbidden »), with the reviewed tag; the run's cell gets the same text, so a later retry never
  rebuilds the file from the old one.
- An edit is not re-judged by the quality gate: the timings are the ones the gate passed, and the text is the
  person's own correction (hypothesis about what Cyril wants: a wording he typed should never be refused).
"""

import logging
import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.cells import CellState, VideoRun
from core.jobs import job_registry
from core.output import OutputBusy, OutputError, current_path, read_track, update_in_place
from core.provenance import OV, ORIGINS, parse_name_marker
from core.subtitles import Cue

from . import library
from .events_hub import hub
from .schemas import Cue as ApiCue
from .schemas import CueList, LibraryChangedEvent

logger = logging.getLogger("SubStudio.Subtitles")

MAX_CUE_CHARS = 500          # a subtitle cue is two lines of ~42; this only stops a pasted page
WRITABLE_ORIGINS = tuple(o for o in ORIGINS if o != "kept")


class SubtitleError(Exception):
    """A request the route answers with `status` and this one sentence."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _same(a, b) -> bool:
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def _run_for(orch, path: str) -> Optional[VideoRun]:
    """The run kept for retry whose video is now at `path` (after its swap, its new name)."""
    for run in getattr(orch, "runs", {}).values():
        if _same(current_path(run), path):
            return run
    return None


def _busy(orch, path: str) -> bool:
    """A job holds this video: running (active_jobs) or registered by a batch that has not reached it yet."""
    runs = getattr(orch, "runs", {})
    for fid in [*getattr(orch, "active_jobs", ()), *job_registry.active_ids()]:
        if _same(fid, path) or (fid in runs and _same(current_path(runs[fid]), path)):
            return True
    return False


@contextmanager
def editing(orch, path: str):
    """Holds the video for one save. Enter it ON THE EVENT LOOP, before any await: the check and the claim then
    happen with no request in between. The claim is the video's path in orchestrator.active_jobs, the set every
    starter already refuses on (/api/process, /api/retry, the job runner's _busy), so no job can start on any video
    while the swap runs, and the kept run is only touched under the claim."""
    if _busy(orch, path):
        raise SubtitleError(409, "A job is running or queued on this video: edit it when the job has finished.")
    orch.active_jobs.add(path)
    try:
        yield
    finally:
        orch.active_jobs.discard(path)


def _resolve(path: str, lang: str) -> str:
    """The ISO 639-1 code of the track to read: `lang` itself, or for 'ov' the language of the video's audio."""
    if lang == OV:
        resolved = library.audio_language(path)
        if resolved is None:
            raise SubtitleError(404, "This video's audio language is not tagged, so « OV » names no track: "
                                     "open its language instead.")
        return resolved
    if len(lang) != 2 or not lang.isalpha() or not lang.islower():
        raise SubtitleError(422, f"{lang!r} is not a language code like « en » or « ov ».")
    return lang


def _read(path: str, lang: str):
    try:
        track = read_track(Path(path), lang)
    except OutputError as e:
        raise SubtitleError(500, f"The {lang} subtitle of this video cannot be read: {e}.")
    if track is None:
        raise SubtitleError(404, f"This video has no SubStudio {lang} subtitle yet: start it from the Library.")
    return track


def _matched(run: Optional[VideoRun], lang: str, cues: List[Cue]) -> List[Optional[Cue]]:
    """For each cue of the track, the run's cue at the same position with the same timing, else None."""
    cell = run.cells.get(lang) if run else None
    mem = cell.cues if cell and cell.state == CellState.PASSED and cell.cues else []
    return [mem[i] if i < len(mem) and (mem[i].start_ms, mem[i].end_ms) == (c.start_ms, c.end_ms) else None
            for i, c in enumerate(cues)]


def _translated_from(run: Optional[VideoRun], lang: str) -> Tuple[Optional[str], Dict[int, str]]:
    """(source language, {source cue id: text}) of a translated cell the run still holds, else (None, {})."""
    source = run.source_of(lang) if run else None
    if source is None:
        return None, {}
    return run.cells[lang].origin.from_language, {c.id: c.text for c in source.cues}


def get_cue_list(orch, video_id: str, path: str, lang: str) -> CueList:
    """`lang` as the screen asked ('ov' included) is echoed back; the track read is the resolved language's."""
    code = _resolve(path, lang)
    track = _read(path, code)
    run = _run_for(orch, path)
    from_language, sources = _translated_from(run, code)
    from_language = from_language or track.from_language      # the file's tag survives a restart; memory may not
    left = set(track.untranslated)
    cues = [ApiCue(id=c.id, start_ms=c.start_ms, end_ms=c.end_ms, text=c.text,
                   source_text=sources.get(m.id) if m else None,
                   untranslated_from=(from_language or "und") if c.id in left else None)
            for c, m in zip(track.cues, _matched(run, code, track.cues))]
    return CueList(video_id=video_id, language=lang, origin=track.origin if track.origin in ORIGINS else "existing",
                   from_language=from_language, reviewed=track.reviewed, cues=cues)


def _edited_texts(body: CueList, current: List[Cue]) -> Dict[int, str]:
    """{id: text} from the body, refused unless its ids and timings are exactly the track's."""
    sent = {c.id: c for c in body.cues}
    if len(sent) != len(body.cues) or set(sent) != {c.id for c in current}:
        raise SubtitleError(409, "This subtitle changed since it was opened (its cues differ): reload it.")
    texts: Dict[int, str] = {}
    for c in current:
        b = sent[c.id]
        if (b.start_ms, b.end_ms) != (c.start_ms, c.end_ms):
            raise SubtitleError(409, f"Cue {c.id}'s timing differs from the file's: timings are not edited here, "
                                     f"or the subtitle changed since it was opened; reload it.")
        text = b.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            raise SubtitleError(422, f"Cue {c.id} is empty: write its text (removing a cue is not supported).")
        if len(text) > MAX_CUE_CHARS:
            raise SubtitleError(422, f"Cue {c.id} is {len(text)} characters long; the limit is {MAX_CUE_CHARS}.")
        texts[c.id] = text
    return texts


def save_cue_list(orch, video_id: str, path: str, lang: str, body: CueList) -> CueList:
    """Call inside editing(orch, path), entered on the event loop: that claim is what keeps jobs off this video."""
    if body.video_id != video_id or body.language != lang:
        raise SubtitleError(422, "The body is for another video or language than the URL.")
    code = _resolve(path, lang)
    track = _read(path, code)
    if track.origin not in WRITABLE_ORIGINS:
        raise SubtitleError(409, f"This {code} track does not record how it was made, so it cannot be rewritten "
                                 f"with its origin: start the video again to make a new one.")
    texts = _edited_texts(body, track.cues)
    new = [replace(c, text=texts[c.id]) for c in track.cues]
    if track.reviewed and new == track.cues:
        return get_cue_list(orch, video_id, path, lang)        # nothing to write: no swap, no new mtime

    validated = parse_name_marker(Path(path).stem) or []
    if code not in validated:
        validated.append(code)
    # A line left in the source language stops being listed once its text was changed; one saved as is stays listed.
    still_left = {i for i in track.untranslated if texts[i] == track.cues[i - 1].text}
    try:
        result = update_in_place(Path(path), {code: new}, validated, origins={code: track.origin}, reviewed={code},
                                 untranslated={code: still_left},
                                 from_languages={code: track.from_language} if track.from_language else {})
    except OutputBusy as e:
        raise SubtitleError(409, f"Not saved, the video is unchanged: {e}.")
    except OutputError as e:
        raise SubtitleError(500, f"Not saved, the video is unchanged: {e}.")

    run = _run_for(orch, path)
    if run is not None:
        cell = run.cells.get(code)
        matched = _matched(run, code, track.cues)
        if cell is not None and cell.state == CellState.PASSED and cell.cues:
            cell.reviewed = True
            if all(m is not None for m in matched):
                # Same cues, new text: keeps the source ids, so source_text still resolves.
                cell.cues = [replace(m, text=texts[c.id]) for c, m in zip(track.cues, matched)] + cell.cues[len(matched):]
            else:
                logger.warning("%s %s: the kept run no longer matches the file cue by cue; it now holds the file's "
                               "cues (source text lost for this cell)", Path(path).name, code)
                cell.cues = new
        run.output_path = str(result.path)

    new_path = str(result.path)
    new_id = library.remember(new_path) if not _same(new_path, path) else video_id
    hub.publish(LibraryChangedEvent(video_ids=sorted({video_id, new_id})))
    return get_cue_list(orch, new_id, new_path, lang)
