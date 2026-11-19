# tvmaze.py — TVmaze lookup (show, episode, characters), cached per series in a local JSON file. Exports: TVMAZE_ATTRIBUTION, TvmazeClient, LookupFailed, SeriesFacts.
"""
TVmaze is free and keyless; its data is « licensed by CC BY-SA », credited as the source, and calls are « rate
limited to allow at least 20 calls every 10 seconds per IP address », a 429 being retried « after a small pause »
(https://www.tvmaze.com/api, read 2026-11-19). One episode costs at most 3 calls, and none once cached.

A show is accepted only when it is unambiguous: two series share « Ghosts » (UK 2019, US 2021), so a name matching
several shows needs the filename's year, and anything else is reported as not found rather than guessed.
"""

import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger("SubStudio.Context")

API = "https://api.tvmaze.com"
TVMAZE_ATTRIBUTION = "TVmaze (https://www.tvmaze.com, CC BY-SA)"


class LookupFailed(RuntimeError):
    """The source could not be reached or answered with an error: the caller keeps what it knows, with a reason."""


@dataclass
class SeriesFacts:
    show_id: int
    name: str
    premiered_year: Optional[int]
    characters: List[str] = field(default_factory=list)   # character names, most important first
    episodes: dict = field(default_factory=dict)           # "5x1" -> {"name", "summary"}


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", name.casefold()).strip()


def plain_text(summary: Optional[str]) -> str:
    """TVmaze summaries are HTML (« <p>…</p> »)."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", summary or "")).split())


class TvmazeClient:
    def __init__(self, cache_dir: Path, http: Optional[httpx.Client] = None, pause_s: float = 2.0):
        self.cache_dir = Path(cache_dir)
        self.http = http or httpx.Client(base_url=API, timeout=10,
                                         headers={"User-Agent": "SubStudio (local subtitle tool)"})
        self.pause_s = pause_s

    # --- HTTP -------------------------------------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None):
        """JSON, or None for a 404. A 429 is retried once after a pause, as TVmaze asks; anything else raises."""
        for attempt in (1, 2):
            try:
                r = self.http.get(path, params=params)
            except httpx.HTTPError as e:
                raise LookupFailed(f"TVmaze unreachable ({type(e).__name__})") from e
            if r.status_code == 429 and attempt == 1:
                time.sleep(self.pause_s)
                continue
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise LookupFailed(f"TVmaze answered HTTP {r.status_code}")
            try:
                return r.json()
            except ValueError as e:
                raise LookupFailed("TVmaze answered something that is not JSON") from e
        raise LookupFailed("TVmaze kept answering 429 (rate limit)")

    # --- cache ------------------------------------------------------------------------------------------------

    def _cache_path(self, title: str, year: Optional[int]) -> Path:
        slug = re.sub(r"[^0-9a-z]+", "-", title.casefold()).strip("-") or "untitled"
        return self.cache_dir / "tvmaze" / f"{slug}{'-' + str(year) if year else ''}.json"

    def _load(self, title: str, year: Optional[int]) -> Optional[SeriesFacts]:
        p = self._cache_path(title, year)
        try:
            return SeriesFacts(**json.loads(p.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (ValueError, TypeError) as e:
            logger.warning(f"Ignoring unreadable context cache {p.name}: {e}")
            return None

    def _save(self, title: str, year: Optional[int], facts: SeriesFacts) -> None:
        p = self._cache_path(title, year)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(facts.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)  # atomic: a crash mid-write never leaves half a cache file

    # --- lookups ----------------------------------------------------------------------------------------------

    def _find_show(self, title: str, year: Optional[int]) -> Tuple[Optional[dict], str]:
        results = self._get("/search/shows", {"q": title}) or []
        same_name = [r["show"] for r in results if _norm(r.get("show", {}).get("name", "")) == _norm(title)]
        if year is not None:
            same_year = [s for s in same_name if (s.get("premiered") or "")[:4] == str(year)]
            if len(same_year) == 1:
                return same_year[0], ""
            return None, f"TVmaze has no single show named « {title} » that premiered in {year}"
        if len(same_name) == 1:
            return same_name[0], ""
        if not same_name:
            return None, f"TVmaze has no show named « {title} »"
        return None, (f"{len(same_name)} shows are named « {title} » on TVmaze and the filename gives no year "
                      "to tell them apart")

    def series(self, title: str, year: Optional[int]) -> Tuple[Optional[SeriesFacts], str]:
        """The series' facts (cached), or None and one sentence saying why. Raises LookupFailed on a network error."""
        cached = self._load(title, year)
        if cached:
            return cached, ""
        show, why = self._find_show(title, year)
        if show is None:
            return None, why
        cast = self._get(f"/shows/{show['id']}/cast") or []
        characters = []
        for member in cast:
            name = (member.get("character") or {}).get("name")
            if name and name not in characters:
                characters.append(name)
        premiered = (show.get("premiered") or "")[:4]
        facts = SeriesFacts(show_id=show["id"], name=show.get("name") or title,
                            premiered_year=int(premiered) if premiered.isdigit() else None, characters=characters)
        self._save(title, year, facts)
        return facts, ""

    def episode(self, title: str, year: Optional[int], facts: SeriesFacts, season: int,
                number: int) -> Tuple[Optional[dict], str]:
        key = f"{season}x{number}"
        if key in facts.episodes:
            return facts.episodes[key], ""
        ep = self._get(f"/shows/{facts.show_id}/episodebynumber", {"season": season, "number": number})
        if ep is None:
            return None, f"TVmaze has no episode S{season:02}E{number:02} of « {facts.name} »"
        facts.episodes[key] = {"name": ep.get("name") or "", "summary": plain_text(ep.get("summary"))}
        self._save(title, year, facts)
        return facts.episodes[key], ""
