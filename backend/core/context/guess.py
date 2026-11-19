# guess.py — what the filename says: series or film, title, year, season, episode (guessit). Exports: MediaGuess, guess_media.

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class MediaGuess:
    kind: str                      # "episode", "movie" or "unknown"
    title: str                     # series or film title, as guessit reads it ("" when none)
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None

    def label(self) -> str:
        """e.g. « Ghosts (2021) S05E01 »: facts from the filename only."""
        name = f"{self.title} ({self.year})" if self.year else self.title
        if self.kind == "episode" and self.season is not None and self.episode is not None:
            return f"{name} S{self.season:02}E{self.episode:02}"
        return name


def _single(value) -> Optional[int]:
    # guessit gives a list for a double episode (S01E01E02): the first one names the file.
    if isinstance(value, list):
        value = value[0] if value else None
    return int(value) if isinstance(value, int) else None


def guess_media(video_path: str) -> MediaGuess:
    """Reads the FILE NAME only: a folder such as « Saison 5 » or « Films » must not become a title."""
    from guessit import guessit
    g = guessit(Path(video_path).name)
    kind = g.get("type") if g.get("type") in ("episode", "movie") else "unknown"
    title = str(g.get("title") or "").strip()
    return MediaGuess(kind=kind, title=title, year=_single(g.get("year")),
                      season=_single(g.get("season")), episode=_single(g.get("episode")))
