# ladder.py — runs Cyril's subtitle ladder (D-0002) for one video: rungs 1–2 for every language first, then 3–4 per cell. Exports: Ladder, LadderDeps, work_dir_for.
"""
Order (doc/user-process.md §2): for every plan, OV first and source-only ones included, try the subtitles already
there (rung 1), then ONE download call for every language still missing (rung 2). Only then does each wanted cell
that is still open translate from a human subtitle that passed (rung 3), or, when no good subtitle exists anywhere,
transcribe once and translate the transcript (rung 4). A machine translation is never the source of another one.

The quality gate (task 7) plugs into `_existing` and `_download`: until it exists in code, a subtitle the current
SubStudio did not make is « ignored » unless `count_existing` is on (CLAUDE.md « Phase »), and a download counts
only when ffsubsync could match it to the audio.
"""
import asyncio
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from .acquire import FetchResult, ProvidersUnavailable, fetch
from .acquire.types import ExistingSub, Rung
from .cells import CellState, Origin, Source, VideoRun, produce_cell
from .jobs import JobAborted
from .subtitles import Cue, Translator, detect_language, load_file

logger = logging.getLogger("SubStudio.Ladder")

_UNLABELLED = "und"  # run.human key of a human source whose language is unknown


def work_dir_for(video_path: str) -> str:
    """The per-video work folder: outside the library, so nothing is ever written beside the source."""
    root = os.getenv("SUBSTUDIO_WORK_DIR") or os.path.join(tempfile.gettempdir(), "SubStudio", "work")
    return os.path.join(root, hashlib.sha1(os.path.realpath(video_path).encode("utf-8")).hexdigest()[:16])


@dataclass
class LadderDeps:
    translator: Translator
    transcribe: Callable[[], Tuple[List[Cue], Optional[str]]]  # blocking: ASR → (cues, language Whisper is sure of)
    load_embedded: Callable[[int], List[Cue]]         # blocking: one embedded text track (index among subtitles)
    fetch: Callable[..., FetchResult] = fetch         # blocking: (video_path, languages, work_dir)
    downloads_enabled: bool = True
    count_existing: bool = False
    preloaded: Dict[str, List[Cue]] = field(default_factory=dict)  # ExistingSub.ref → cues (the user's own pick)
    # The user's pick when its language cannot be detected (too little text): no rung 1 for it, but still a human
    # source to translate from, as the user asked.
    unlabelled: Optional[List[Cue]] = None
    check_abort: Callable[[], None] = lambda: None
    progress: Callable[[str, int, str], None] = lambda status, percent, message: None


class Ladder:
    def __init__(self, run: VideoRun, deps: LadderDeps):
        self.run, self.deps = run, deps

    # --- bookkeeping -------------------------------------------------------------------------------------------
    def _note(self, lang: str, text: str) -> None:
        cell = self.run.cells.get(lang)
        if cell is not None:
            cell.attempts.append(text)
        logger.info(f"{lang.upper()}: {text}")

    def _accept(self, lang: str, cues: List[Cue], rung: Rung) -> None:
        self.run.human[lang] = Source(cues=cues, rung=rung, language=lang,
                                      context=replace(self.run.context, source_lang=lang, source_is_asr=False))
        cell = self.run.cells.get(lang)
        if cell is not None:
            cell.cues, cell.origin, cell.state, cell.reason = cues, Origin(rung), CellState.PASSED, ""
        logger.info(f"{lang.upper()}: {Origin(rung).label()}" + ("" if cell else " (as a translation source)"))

    # --- rungs 1–2 ---------------------------------------------------------------------------------------------
    async def human(self) -> None:
        """Rungs 1–2 for every plan; fills run.human and passes the wanted cells they satisfy."""
        to_download: List[str] = []
        for lang, plan in self.run.plans.items():
            self.deps.check_abort()
            for step in (s for s in plan.steps if s.rung == Rung.EXISTING):
                cues, why = await self._existing(step.source)
                if cues:
                    self._accept(lang, cues, Rung.EXISTING)
                    break
                self._note(lang, f"existing: {why}")
            if lang not in self.run.human and any(s.rung == Rung.DOWNLOAD for s in plan.steps):
                to_download.append(lang)
        if to_download and self.deps.downloads_enabled:
            await self._download(to_download)
        if self.deps.unlabelled:
            self.run.human[_UNLABELLED] = Source(cues=self.deps.unlabelled, rung=Rung.EXISTING, language=None,
                                                 context=replace(self.run.context, source_is_asr=False))

    async def _existing(self, sub: ExistingSub) -> Tuple[Optional[List[Cue]], str]:
        if not sub.marked and not self.deps.count_existing:
            return None, f"ignored: {sub.language} {sub.kind}"
        if not sub.text:
            return None, f"{sub.language} {sub.kind} track is image-based and cannot be checked"
        try:
            if sub.ref in self.deps.preloaded:
                cues = self.deps.preloaded[sub.ref]
            elif sub.kind == "sidecar":
                cues = await asyncio.to_thread(load_file, sub.ref)
            else:
                cues = await asyncio.to_thread(self.deps.load_embedded, int(sub.ref))
        except JobAborted:
            raise
        except Exception as e:  # an unreadable subtitle falls through to the next rung, with its reason
            return None, f"unreadable {sub.language} {sub.kind}: {str(e)[:120]}"
        return (cues, "") if cues else (None, f"{sub.language} {sub.kind} has no lines")

    async def _download(self, langs: List[str]) -> None:
        self.deps.progress("processing", 12, f"Searching online for {', '.join(l.upper() for l in langs)} subtitles...")
        try:
            result = await asyncio.to_thread(self.deps.fetch, self.run.path, langs, work_dir_for(self.run.path))
        except JobAborted:
            raise
        except ProvidersUnavailable:
            for lang in langs:
                self._note(lang, "download: no subtitle site answered")
            return
        except Exception as e:  # a crashed search is reported per language, never read as « none online »
            logger.error(f"Subtitle download failed: {e}")
            for lang in langs:
                self._note(lang, f"download failed: {str(e)[:120]}")
            return
        down = f" ({', '.join(sorted(result.failed_providers))} unreachable)" if result.failed_providers else ""
        for lang in langs:
            got = result.subtitles.get(lang)
            if got is None:
                self._note(lang, f"download: none found online{down}")
            elif not got.synced:
                self._note(lang, "download: found, but it could not be matched to the audio")
            else:
                try:
                    self._accept(lang, await asyncio.to_thread(load_file, got.path), Rung.DOWNLOAD)
                except Exception as e:
                    self._note(lang, f"download: unreadable file: {str(e)[:120]}")

    # --- rungs 3–4 ---------------------------------------------------------------------------------------------
    async def fill(self, lang: str, on_batch: Optional[Callable[[int, int], None]] = None) -> None:
        """Rung 3, else 4, for one wanted cell not passed at rungs 1–2. Ends PASSED or FAILED with one sentence."""
        cell, plan = self.run.cells[lang], self.run.plans[lang]
        for step in plan.steps:
            if step.rung == Rung.TRANSLATE:
                # The plan's order first (OV first); then any other human source, e.g. the user's unlabelled pick.
                sources = [l for l in step.from_languages if l in self.run.human]
                sources += [l for l in self.run.human if l not in sources and l != lang]
                if not sources:
                    self._note(lang, "translate: no good subtitle in another language")
                    continue
                for src in sources:
                    await asyncio.to_thread(produce_cell, cell, self.run.human[src], self.deps.translator,
                                            on_batch=on_batch, check_abort=self.deps.check_abort)
                    if cell.state == CellState.PASSED:
                        return
                    if src != sources[-1]:
                        self._note(lang, f"translate from {src}: {cell.reason}")
                # A good human subtitle existed, so rung 4 (« no good subtitle anywhere ») does not apply.
                return
            if step.rung == Rung.ASR:
                try:
                    source = await self.asr()
                except JobAborted:
                    raise
                except Exception as e:
                    cell.state, cell.reason = CellState.FAILED, f"transcription failed: {str(e)[:160]}"
                    return
                await asyncio.to_thread(produce_cell, cell, source, self.deps.translator,
                                        on_batch=on_batch, check_abort=self.deps.check_abort)
                return
        cell.state = CellState.FAILED
        cell.reason = cell.attempts[-1] if cell.attempts else "no rung could produce this language"

    async def asr(self) -> Source:
        if self.run.asr is None:
            self.deps.progress("processing", 15, "Transcribing with Whisper...")
            cues, spoken = await asyncio.to_thread(self.deps.transcribe)
            if not cues:
                raise RuntimeError("no speech detected")
            lang = spoken or detect_language(cues)
            self.run.asr = Source(cues=cues, rung=Rung.ASR, language=lang,
                                  context=replace(self.run.context, source_lang=lang, source_is_asr=True))
        return self.run.asr
