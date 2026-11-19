# translate.py — Translator interface + translate_cues: batches cues, validates every answer by id, re-attaches timings. Exports: Translator, TranslationContext, TranslationFailed, BatchMismatch, translate_cues.
"""
The contract every translation backend implements (Gemini today, a local llama.cpp model next):

    translate(cues: list[CueText], target: str, context: TranslationContext) -> list[CueText]

It receives ONE batch of {id, text} — never a timing — and returns one {id, text} per id it was given.
It may raise any exception for a failed call. Everything else is done here, in Python, so no backend
can shift a timing: the batch size, retries, validation of the returned id set, splitting a batch
the model keeps getting wrong, re-attaching timings by id, progress and abort.

A language whose translation cannot be completed raises TranslationFailed. The source text is never
shipped as if it were the translation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Protocol, Sequence

from ..jobs import JobAborted
from .cues import Cue, CueText

logger = logging.getLogger("SubStudio.Translate")


@dataclass(frozen=True)
class TranslationContext:
    """What a backend may use besides the texts. Only facts; a backend ignores what it cannot use."""
    title: str = ""                                      # e.g. from the filename
    notes: str = ""                                      # free text (today: the Gemini story bible)
    glossary: Sequence[str] = ()                         # names and terms to keep untranslated
    source_lang: Optional[str] = None                    # ISO 639-1 when known
    source_is_asr: bool = False                          # machine-transcribed: may hold phonetic errors
    previous: Sequence[str] = field(default_factory=tuple)  # source texts just before this batch, for continuity


class Translator(Protocol):
    name: str          # shown in logs, e.g. "gemini:gemini-3.8-flash"
    batch_size: int    # cues per call that this backend handles well

    def translate(self, cues: List[CueText], target: str, context: TranslationContext) -> List[CueText]: ...

    # Called by the orchestrator after each video's languages, success or not: a local backend frees its model
    # so it never shares RAM with the next transcription (D-0001). A backend holding nothing does nothing.
    def unload(self) -> None: ...


class TranslationFailed(RuntimeError):
    """A language could not be translated completely. Its message names the language and the cause."""


class BatchMismatch(ValueError):
    """The backend answered, but not with exactly one non-empty text per id it was sent."""


# A line of three words or more that comes back identical was not translated. Shorter lines (« OK », « John! ») may
# legitimately stay the same, so they never count.
_ECHO_MIN_WORDS = 3
# Measured 2026-11-19 on 3,323 English cues of 6 Ghosts S05 episodes: 89 % have 3+ words, 0.2 % carry ♪.
# Hypothesis, to set once the bench has human FR/EN pairs: a real translation leaves far under 1 in 10 unchanged.
MAX_ECHO_SHARE = 0.10
MIN_ECHO_SENTENCES = 10


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _is_sentence(text: str) -> bool:
    # Song lyrics (♪) are often left untranslated by human translators too: they never count.
    return len(text.split()) >= _ECHO_MIN_WORDS and "♪" not in text


def _is_echo(source: str, answer: str) -> bool:
    return _is_sentence(source) and _norm(source) == _norm(answer)


def _check(sent: List[CueText], got: object, check_echo: bool = True) -> Dict[int, str]:
    if not isinstance(got, list):
        raise BatchMismatch(f"expected a list, got {type(got).__name__}")
    want = [c["id"] for c in sent]
    out: Dict[int, str] = {}
    for item in got:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int) or not isinstance(item.get("text"), str):
            raise BatchMismatch(f"malformed item {item!r:.80}")
        if item["id"] in out:
            raise BatchMismatch(f"id {item['id']} returned twice")
        out[item["id"]] = item["text"].strip()
    missing, extra = set(want) - set(out), set(out) - set(want)
    if missing or extra:
        raise BatchMismatch(f"missing ids {sorted(missing)[:5]}, unexpected ids {sorted(extra)[:5]}")
    empty = [i for i in want if not out[i]]
    if empty:
        raise BatchMismatch(f"empty text for ids {empty[:5]}")
    if check_echo:
        long_lines = [c for c in sent if _is_sentence(c["text"])]
        echoed = [c["id"] for c in long_lines if _is_echo(c["text"], out[c["id"]])]
        # Two or more, and at least half the batch's real sentences: the model sent the source back.
        if len(echoed) >= 2 and len(echoed) * 2 >= len(long_lines):
            raise BatchMismatch(f"{len(echoed)} lines returned untranslated (ids {echoed[:5]})")
    return out


def translate_cues(
    cues: List[Cue],
    target: str,
    context: TranslationContext,
    translator: Translator,
    on_batch: Optional[Callable[[int, int], None]] = None,
    check_abort: Optional[Callable[[], None]] = None,
    call_retries: int = 2,
    retry_delay_s: float = 2.0,
) -> List[Cue]:
    """
    Translates every cue into `target`; returns new cues with the SAME ids and timings.
    on_batch(done_cues, total_cues) reports progress; check_abort() raises to stop (JobAborted).
    A call that raises is retried `call_retries` times, then the language fails.
    An answer whose id set is wrong is retried once, then the batch is split in halves down to one cue;
    a single cue still answered wrongly fails the language.
    """
    if not cues:
        raise TranslationFailed(f"{target}: nothing to translate (0 cues)")
    size = max(1, int(translator.batch_size))
    by_id = {c.id: c for c in cues}
    if len(by_id) != len(cues):
        raise TranslationFailed(f"{target}: duplicate cue ids in the source")
    texts: Dict[int, str] = {}
    done = 0

    position = {c.id: i for i, c in enumerate(cues)}
    # Translating into the source language legitimately returns the same text.
    check_echo = not (context.source_lang and context.source_lang.lower() == target.lower())

    def call(batch: List[Cue]) -> Dict[int, str]:
        sent: List[CueText] = [{"id": c.id, "text": c.text} for c in batch]
        first = position[batch[0].id]
        ctx = replace(context, previous=tuple(c.text for c in cues[max(0, first - 3):first]))
        failures = mismatches = 0
        while True:
            if check_abort:
                check_abort()
            try:
                # A backend may raise BatchMismatch itself (e.g. unparseable JSON): same budget as a wrong answer.
                return _check(sent, translator.translate(sent, target, ctx), check_echo)
            except BatchMismatch as e:
                mismatches += 1
                logger.warning(f"{translator.name} answered cues {batch[0].id}-{batch[-1].id} wrongly: {e}")
                if mismatches >= 2:
                    raise
            except (TranslationFailed, JobAborted):
                raise
            except Exception as e:
                failures += 1
                if failures > call_retries:
                    raise TranslationFailed(
                        f"{target}: {translator.name} failed {failures} times on cues "
                        f"{batch[0].id}-{batch[-1].id}: {e}") from e
                logger.warning(f"{translator.name} call failed (attempt {failures}/{call_retries + 1}): {e}")
                time.sleep(retry_delay_s)

    def run(batch: List[Cue]) -> None:
        nonlocal done
        try:
            texts.update(call(batch))
        except BatchMismatch as e:
            if len(batch) == 1:
                raise TranslationFailed(f"{target}: {translator.name} cannot translate cue {batch[0].id}: {e}") from e
            half = len(batch) // 2
            logger.warning(f"Splitting cues {batch[0].id}-{batch[-1].id} into two batches")
            run(batch[:half])
            run(batch[half:])
            return
        done += len(batch)
        if on_batch:
            on_batch(done, len(cues))

    for i in range(0, len(cues), size):
        run(cues[i:i + size])

    if check_echo:
        long_lines = [c for c in cues if _is_sentence(c.text)]
        echoed = [c.id for c in long_lines if _is_echo(c.text, texts[c.id])]
        # Under MIN_ECHO_SENTENCES there is too little text to tell an echo from lines that stay the same.
        if len(long_lines) >= MIN_ECHO_SENTENCES and len(echoed) > MAX_ECHO_SHARE * len(long_lines):
            raise TranslationFailed(f"{target}: {len(echoed)} of {len(long_lines)} sentences came back in the source "
                                    f"language (first ids {echoed[:5]})")

    return [replace(c, text=texts[c.id]) for c in cues]
