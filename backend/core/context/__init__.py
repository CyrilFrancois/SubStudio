# context — looked-up translation context (series, episode, summary, characters, names), never invented. Exports: get_context, state_dir.
"""
doc/user-process.md §2 rung 3: translate « knowing the context of the video »: title, and for an episode the series,
season, episode and an episode summary, looked up, never invented by the model. A small local model asked to
« identify the show from its filename » invents a cast; here every fact comes from the filename (guessit), from
TVmaze, or from the cues themselves. When a lookup fails, the context holds what is known plus one sentence saying
what is missing: never a crash, never a made-up value.
"""

import logging
from pathlib import Path
from typing import List, Optional

from ..subtitles import Cue, TranslationContext
from .guess import guess_media
from .names import proper_nouns
from .tvmaze import TVMAZE_ATTRIBUTION, LookupFailed, TvmazeClient

logger = logging.getLogger("SubStudio.Context")


def state_dir() -> Path:
    """The context cache's folder, inside SubStudio's one state dir (api/config.STATE_DIR: SUBSTUDIO_STATE_DIR,
    default backend/state, gitignored, never inside the library)."""
    from api.config import STATE_DIR
    return Path(STATE_DIR) / "context"


# A title is translated (« Captain » → « Capitaine »), so it never joins the names to keep.
_TITLES = {"captain", "capt", "doctor", "dr", "mr", "mrs", "ms", "miss", "sir", "lady", "lord", "madame",
           "mister", "father", "sister", "brother", "uncle", "aunt", "officer", "detective", "agent", "general",
           "colonel", "major", "sergeant", "lieutenant", "professor", "judge", "king", "queen", "prince", "princess"}


def _name_parts(characters: List[str]) -> List[str]:
    """« Captain Isaac Higgintoot » → « Isaac Higgintoot », « Isaac », « Higgintoot »; « Henrietta "Hetty"
    Woodstone » also gives « Hetty »: dialogue uses any of them."""
    out: List[str] = []
    for full in characters:
        words = [w for w in full.split() if w.strip('"“”«»()\'.,').casefold() not in _TITLES]
        nickname = [w for w in words if w[:1] in '"“«(']  # « "Hetty" » is said alone, never inside the full name
        parts = [w.strip('"“”«»()\'.,') for w in words]
        proper = " ".join(w.strip(".,") for w in words if w not in nickname)
        for term in [proper] + [p for p in parts if len(p) >= 3 and p[0].isupper()]:
            if term and term not in out:
                out.append(term)
    return out


def _notes(series: str, season, episode, episode_title: str, summary: str, characters: List[str]) -> str:
    lines = []
    if series:
        lines.append(f"Series: {series}.")
    if season is not None and episode is not None:
        lines.append(f"Season {season}, episode {episode}" + (f": « {episode_title} »." if episode_title else "."))
    if summary:
        lines.append(f"Episode summary: {summary}")
    if characters:
        lines.append("Characters: " + ", ".join(characters) + ".")
    return "\n".join(lines)


def get_context(video_path: str, cues: Optional[List[Cue]] = None,
                client: Optional[TvmazeClient] = None) -> TranslationContext:
    """Every fact a translator may use for this video, each one looked up or computed, with what is missing."""
    media = guess_media(video_path)
    glossary_from_cues = proper_nouns(cues or [])
    series = media.label() if media.kind != "episode" else (f"{media.title} ({media.year})" if media.year
                                                           else media.title)
    episode_title = summary = attribution = ""
    characters: List[str] = []
    missing: List[str] = []

    if media.kind == "episode" and media.title:
        client = client or TvmazeClient(state_dir())
        try:
            facts, why = client.series(media.title, media.year)
            if facts is None:
                missing.append(why)
            else:
                attribution = TVMAZE_ATTRIBUTION
                series = f"{facts.name} ({facts.premiered_year})" if facts.premiered_year else facts.name
                characters = facts.characters
                if media.season is None or media.episode is None:
                    missing.append("the filename gives no season and episode number")
                else:
                    ep, why = client.episode(media.title, media.year, facts, media.season, media.episode)
                    if ep is None:
                        missing.append(why)
                    else:
                        episode_title, summary = ep["name"], ep["summary"]
                        if not summary:
                            missing.append("TVmaze has no summary for this episode")
        except LookupFailed as e:
            missing.append(f"{e}: only the filename's facts are known")
            logger.warning(f"Context lookup failed for {Path(video_path).name}: {e}")
    elif media.kind == "movie":
        missing.append("films are not looked up (no free keyless source): title and year from the filename only")
    else:
        missing.append("the filename names no series or film")

    glossary: List[str] = []
    for term in _name_parts(characters) + glossary_from_cues:
        if term not in glossary:
            glossary.append(term)

    # The looked-up name has the right casing (a file named « ghosts.2021.s05e01 » gives « ghosts »).
    title = media.label() or Path(video_path).stem
    if attribution and media.season is not None and media.episode is not None:
        title = f"{series} S{media.season:02}E{media.episode:02}"
    return TranslationContext(
        title=title,
        notes=_notes(series, media.season, media.episode, episode_title, summary, characters),
        glossary=tuple(glossary),
        series=series if media.kind == "episode" else "",
        season=media.season, episode=media.episode, episode_title=episode_title, summary=summary,
        missing="; ".join(missing), attribution=attribution,
    )
