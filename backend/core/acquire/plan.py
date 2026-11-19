# plan.py — Cyril's subtitle ladder per language (OV first): which rungs to try, in order, for one video. Exports: plan, normalize_language, OV_TAGS.
from typing import Iterable, List, Optional

from babelfish import Language, LanguageConvertError, LanguageReverseError

from .types import ExistingSub, LanguagePlan, Rung, Step

# What a user (or the remembered setting) writes for « Original Version »: the audio's own language.
OV_TAGS = {"ov", "vo", "original", "original version", "version originale"}


def normalize_language(code: Optional[str]) -> Optional[str]:
    """Any tag a file or a user gives (`en`, `eng`, `fre`, `fra`, `pt-BR`, `French`) → ISO 639-1, or None.

    A real language with no two-letter code (`fil`, `yue`, `haw`) is None too: providers and tags here speak 639-1.
    """
    if not code:
        return None
    c = code.strip().replace("_", "-")
    if c.lower() in ("und", "unk", "auto", "mis", "zxx", "mul"):
        return None
    base = c.split("-")[0].lower()
    attempts = [
        lambda: Language.fromalpha2(base),
        lambda: Language.fromalpha3b(base),  # ffprobe/MKV tags are ISO 639-2/B: `fre`, `ger`
        lambda: Language(base),              # ISO 639-3: `fra`, `deu`
        lambda: Language.fromname(c.title()),
    ]
    for attempt in attempts:
        try:
            lang = attempt()
            return lang.alpha2
        except (ValueError, KeyError, LanguageReverseError, LanguageConvertError):
            continue
    return None


def _resolve_wanted(wanted: Iterable[str], original: Optional[str]) -> List[str]:
    langs: List[str] = []
    for raw in wanted:
        if raw and raw.strip().lower() in OV_TAGS:
            if original is None:
                # Never guess the audio language: the caller detects it (stream tag, else ASR on a sample) first.
                raise ValueError("OV asked but the audio language is unknown: detect it before planning")
            lang = original
        else:
            lang = normalize_language(raw)
            if lang is None:
                raise ValueError(f"unknown target language: {raw!r}")
        if lang not in langs:  # « OV can already be in English »: OV and EN are then one track
            langs.append(lang)
    return langs


def plan(wanted: Iterable[str], existing: Iterable[ExistingSub], original_language: Optional[str] = None,
         downloads_enabled: bool = True, count_existing: bool = False) -> List[LanguagePlan]:
    """Per language, the ordered steps of Cyril's ladder (doc/user-process.md §2); each result is gated, and a
    failure falls to the next step. `wanted` may hold « OV » (the audio language, `original_language`).

    Plans come OV first, then the wanted languages in the user's order, then source-only ones (not asked for, run
    only to give TRANSLATE a human source): the executor runs rungs 1–2 for every language before any rung 3.
    Raises ValueError for a language that cannot be named, rather than planning a track nobody can tag.
    """
    # Subtitles already there are not considered by default: an older SubStudio wrote bad muxed tracks (« AI FR ») and
    # plain {stem}.{lang}.srt sidecars that nothing tells apart from a release's own (Cyril, 2026-11-19). Only what
    # the current SubStudio marked (core/provenance.is_substudio_track), or everything once Settings.count_existing
    # is on, is evaluated. user-process.md §3: count_existing stays off until the gate is shown to reject the old ones.
    existing = [s for s in existing if (s.marked or count_existing) and s.language]
    original = normalize_language(original_language)
    wanted_langs = _resolve_wanted(wanted, original)

    extra: List[str] = []
    if original and original not in wanted_langs:
        extra.append(original)  # a human OV subtitle is the best translation source even when OV is not wanted
    extra += [s.language for s in existing if s.text and s.language not in wanted_langs and s.language not in extra]
    ordered = [l for l in [original] if l] + [l for l in wanted_langs + extra if l != original]

    plans: List[LanguagePlan] = []
    for lang in ordered:
        source_only = lang not in wanted_langs
        steps: List[Step] = []
        # A text subtitle before a bitmap one: it can be re-timed, checked and translated.
        for s in sorted((s for s in existing if s.language == lang), key=lambda s: (not s.text, s.kind != "sidecar")):
            if s.text or not source_only:
                steps.append(Step(Rung.EXISTING, lang, source=s))
        if downloads_enabled and (not source_only or lang == original):
            steps.append(Step(Rung.DOWNLOAD, lang))
        if not source_only:
            froms = tuple(l for l in ordered if l != lang)
            if froms:
                steps.append(Step(Rung.TRANSLATE, lang, from_languages=froms))
            steps.append(Step(Rung.ASR, lang))
        if steps:
            plans.append(LanguagePlan(lang, steps, is_ov=lang == original, source_only=source_only))
    return plans
