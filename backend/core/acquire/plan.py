# plan.py — choose the subtitle-ladder rung for each wanted language from what the file already carries. Exports: plan, normalize_language.
from typing import Iterable, List, Optional

from babelfish import Language, LanguageConvertError, LanguageReverseError

from .types import ExistingSub, LanguagePlan, Rung, Step

# Downloaded to translate from when the original language is unknown: the language most subtitles exist in.
_FALLBACK_SOURCE = "en"


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


def plan(wanted: Iterable[str], existing: Iterable[ExistingSub], original_language: Optional[str] = None,
         downloads_enabled: bool = True, count_existing: bool = False) -> List[LanguagePlan]:
    """One LanguagePlan per wanted language: the ordered rungs to try, the first one being the plan.

    Raises ValueError for a wanted language that cannot be named, rather than planning a track nobody can tag.
    """
    # Subtitles already there are untrusted by default: an older SubStudio wrote bad muxed tracks (« AI FR ») and
    # plain {stem}.{lang}.srt sidecars that nothing tells apart from a release's own (Cyril, 2026-11-19). Only what
    # the current SubStudio marked (`marked`, from core/provenance.is_substudio_track), or everything once the user
    # turns on Settings.count_existing, may satisfy a language or feed its translation: a subtitle judged bad is not
    # a good translation source either.
    existing = [s for s in existing if s.marked or count_existing]
    original = normalize_language(original_language)
    plans: List[LanguagePlan] = []
    seen = set()
    for raw in wanted:
        lang = normalize_language(raw)
        if lang is None:
            raise ValueError(f"unknown target language: {raw!r}")
        if lang in seen:
            continue
        seen.add(lang)

        have = [s for s in existing if s.language == lang]
        if have:
            # A text track beats a bitmap one as the reported source: it can be edited and re-timed.
            have.sort(key=lambda s: (not s.text, s.kind != "sidecar"))
            plans.append(LanguagePlan(lang, [Step(Rung.EXISTING, lang, source=have[0])]))
            continue

        steps: List[Step] = []
        if downloads_enabled:
            steps.append(Step(Rung.DOWNLOAD, lang))

        # Rung 3: a human text track in another language, the original language first (it is what was spoken,
        # so it carries the least drift), then any named language, then an untagged one.
        sources = [s for s in existing if s.text and s.language != lang]
        sources.sort(key=lambda s: (s.language != original or original is None, s.language is None))
        if sources:
            steps.append(Step(Rung.TRANSLATE, lang, source=sources[0]))
        elif downloads_enabled:
            src = original or _FALLBACK_SOURCE
            if src != lang:
                steps.append(Step(Rung.TRANSLATE, lang, download_language=src))

        steps.append(Step(Rung.ASR, lang))
        plans.append(LanguagePlan(lang, steps))
    return plans
