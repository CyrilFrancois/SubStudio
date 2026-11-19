# schemas.py — The UI contract (ux-review §3) as pydantic models; frontend types.ts is generated from these. Exports: every model below.

from datetime import datetime
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field

# --- Shared vocabulary -----------------------------------------------------------------------

# ISO 639-1 two-letter codes ("fr", "en") everywhere in the contract, plus "ov" (original version:
# the audio's language, resolved per video; D-0002). Raw container tags ("eng", "fre", "und") are
# normalised server-side; the raw tag is kept on SubtitleTrack.language_tag.
LanguageCode = str

# What a cell of the coverage matrix says about one (video, wanted language).
# Cell vocabulary shared with core/cells.py (workspace-6e): pending | running | passed | failed, plus what only
# a scan can say: "missing" (nothing usable yet), "unchecked" (a subtitle SubStudio did not make is there, but the
# quality gate has not judged it yet: D-0002, it never counts before) and "unknown" (ffprobe failed, a track may be unseen).
CoverageState = Literal["passed", "missing", "unchecked", "pending", "running", "failed", "unknown"]
# The ladder rung that produced a passed cell (core/acquire/types.Rung values).
Rung = Literal["existing", "download", "translate", "asr"]
# How a present subtitle was obtained (D-0002 §3; core/provenance.ORIGINS), or "existing" when it
# counts only because Settings.count_existing is on (not validated by SubStudio).
CoverageOrigin = Literal["kept", "downloaded", "translated", "transcribed", "existing"]

# D-0002 §5 stepper: Check -> Download -> Translate / Transcribe -> Correct -> Write.
Stage = Literal["check", "download", "transcribe", "correct", "translate", "write"]
StageState = Literal["pending", "running", "done", "skipped", "failed"]
JobState = Literal["queued", "running", "done", "failed", "cancelled"]


# --- Library ---------------------------------------------------------------------------------

class SubtitleTrack(BaseModel):
    """One subtitle found for a video, counted as coverage or not (and why)."""
    kind: Literal["embedded", "sidecar"]
    language: Optional[LanguageCode] = Field(None, description="Normalised code; null when the tag is absent or 'und'.")
    language_tag: Optional[str] = Field(None, description="The raw tag as found (ffprobe tag or filename part).")
    stream_index: Optional[int] = Field(None, description="ffprobe stream index, embedded tracks only.")
    path: Optional[str] = Field(None, description="Absolute path, sidecar files only.")
    codec: Optional[str] = None
    title: Optional[str] = None
    made_by_substudio: bool = Field(False, description="Carries the SubStudio marker (core/provenance.py).")
    origin: Optional[Literal["kept", "downloaded", "translated", "transcribed"]] = Field(
        None, description="SubStudio tracks only: the SUBSTUDIO_ORIGIN stream tag, null when not recorded.")
    counted: bool = Field(..., description="True when this track counts as coverage for its language.")
    reason: Optional[str] = Field(None, description="One sentence when counted is false, e.g. 'not made by this SubStudio (Settings)'.")


class LanguageCoverage(BaseModel):
    language: LanguageCode = Field(..., description="The wanted language as in Settings, 'ov' included.")
    resolved: Optional[LanguageCode] = Field(None, description="For 'ov': the audio's language when known ('en'); else equals language.")
    state: CoverageState
    origin: Optional[CoverageOrigin] = Field(None, description="Set when state is 'passed'; null = a SubStudio track of unrecorded origin.")
    rung: Optional[Rung] = Field(None, description="Set when known (from the job's cell verdict).")
    from_language: Optional[LanguageCode] = Field(None, description="For translated/transcribed+translated: the source language.")
    path: Optional[str] = Field(None, description="Sidecar path when the subtitle is a file.")
    job_id: Optional[str] = Field(None, description="Set when state is queued/running/failed.")
    reason: Optional[str] = Field(None, description="One sentence: why it failed, why it is unknown, or what was ignored.")


class LibraryVideo(BaseModel):
    id: str = Field(..., description="Stable id derived from the path; use it in every URL instead of the path.")
    path: str
    name: str
    folder: str = Field(..., description="Folder relative to the media root, '' for the root itself; '/' separated.")
    size_bytes: int
    mtime: datetime
    duration_s: Optional[float] = Field(None, description="Seconds; null when ffprobe could not read it.")
    audio_language: Optional[LanguageCode] = Field(None, description="Language tag of the first audio track (OV), null when untagged.")
    validated: bool = Field(..., description="Every wanted language passed as a SubStudio track: Start skips this video.")
    marker_languages: Optional[List[LanguageCode]] = Field(None, description="Languages the file name claims, '(Sub-EN,FR)' -> ['en','fr']; a claim, not a proof.")
    coverage: List[LanguageCoverage] = Field(..., description="One entry per wanted language, in Settings order.")
    tracks: List[SubtitleTrack] = Field(..., description="Every subtitle found, counted or not.")
    probe_error: Optional[str] = Field(None, description="Set when ffprobe failed: coverage is then unknown, not missing.")


class LibraryResponse(BaseModel):
    root: str
    languages: List[LanguageCode] = Field(..., description="The wanted languages the coverage was computed for.")
    scanned_at: datetime
    videos: List[LibraryVideo]
    warnings: List[str] = Field(..., description="Folders or files that could not be read; shown, never dropped silently.")


# --- Jobs ------------------------------------------------------------------------------------

class JobOptions(BaseModel):
    """The 'Advanced' overrides of one video; every field defaults to 'let the ladder decide'."""
    force_asr: bool = Field(False, description="Ignore every existing subtitle and transcribe.")
    source_track: Optional[str] = Field(None, description="Path or 'stream:N' of the subtitle to translate from.")
    sync_offset_ms: int = Field(0, description="Milliseconds added to every cue; positive = later.")


class JobCreate(BaseModel):
    video_ids: List[str] = Field(..., min_length=1)
    languages: Optional[List[LanguageCode]] = Field(
        None, description="Null = every wanted language that is missing for each video ('Fill missing')."
    )
    options: JobOptions = JobOptions()


class StageProgress(BaseModel):
    stage: Stage
    state: StageState
    progress: float = Field(0.0, ge=0.0, le=1.0, description="0..1 within this stage only; never across stages.")
    eta_s: Optional[float] = Field(None, description="Seconds left in this stage; null when unknown.")
    detail: Optional[str] = Field(None, description="e.g. which rung was used: 'downloaded from OpenSubtitles'.")


class Job(BaseModel):
    id: str
    video_id: str
    video_name: str
    languages: List[LanguageCode]
    state: JobState
    batch_id: Optional[str] = Field(None, description="Same for every job one POST /api/jobs created: the Queue's overall bar counts them together.")
    position: Optional[int] = Field(None, description="Place in the queue (0 = next); null once started.")
    stages: List[StageProgress]
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = Field(None, description="One sentence a person understands, when state is 'failed'.")


class JobList(BaseModel):
    jobs: List[Job] = Field(..., description="Running first, then queued by position, then history newest first.")


class JobCreateResponse(BaseModel):
    jobs: List[Job]
    skipped: List[str] = Field(..., description="Video ids with nothing missing in the requested languages.")


# --- Events (GET /api/events, one SSE stream for every job) ---------------------------------

class JobEvent(BaseModel):
    kind: Literal["job"] = "job"
    job: Job


class LogEvent(BaseModel):
    kind: Literal["log"] = "log"
    job_id: Optional[str] = Field(None, description="Null for system-wide lines.")
    ts: datetime = Field(..., description="When the backend emitted the line, not when the browser received it.")
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    message: str


class LibraryChangedEvent(BaseModel):
    """Coverage of these videos changed (a job wrote a subtitle): refetch the library."""
    kind: Literal["library"] = "library"
    video_ids: List[str]


EventFrame = Union[JobEvent, LogEvent, LibraryChangedEvent]


class EventFrameDoc(BaseModel):
    """Documents the SSE payload for OpenAPI: each `data:` line is one of these, discriminated by `kind`."""
    frame: EventFrame = Field(..., discriminator="kind")


# --- Subtitles (review screen) ---------------------------------------------------------------

class Cue(BaseModel):
    id: int = Field(..., description="Position in the source cue list; the translator keys on it.")
    start_ms: int
    end_ms: int
    text: str
    source_text: Optional[str] = Field(None, description="The cue it was translated from, when translated.")


class CueList(BaseModel):
    video_id: str
    language: LanguageCode
    origin: CoverageOrigin
    reviewed: bool = False
    cues: List[Cue]


# --- Settings and system ---------------------------------------------------------------------

class Settings(BaseModel):
    """D-0002: languages are the only choice on the main path; the output is always the source file,
    replaced in place after the check, renamed with '(Sub-OV,EN,FR)'."""
    languages: List[LanguageCode] = Field(..., min_length=1, description="Wanted languages, 'ov' included: the only list every picker reads.")
    count_existing: bool = Field(
        True, description="Count subtitles not made by this SubStudio (core/provenance.py) when the quality gate passes them "
        "(on by default: Cyril, 2026-11-19, D-0002). Off: listed, never counted, never checked."
    )


class ProviderStatus(BaseModel):
    name: str
    configured: bool = Field(..., description="A key is set; the value itself is never sent.")


class SettingsResponse(BaseModel):
    settings: Settings
    providers: List[ProviderStatus]


class SystemStatus(BaseModel):
    stub: bool = Field(..., description="True when the API serves stub data (SUBSTUDIO_API_STUB=1): the UI must say so.")
    media_root: str
    loaded_model: Optional[str] = None
    cpu_percent: Optional[float] = Field(None, description="Whole machine, 0..100.")
    ram_used_mb: Optional[int] = Field(None, description="This backend process, MB.")
    ram_budget_mb: int = Field(7168, description="D-0001: about half of this 15 GB machine.")
    cpu_threads_budget: int = Field(4, description="D-0001: 4 of 8 threads.")
