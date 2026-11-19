# tvmaze.py — TVmaze lookup (show, episode, characters), cached per series in a local JSON file. Exports: TVMAZE_ATTRIBUTION, TvmazeClient, LookupFailed, SeriesFacts.
"""
TVmaze is free and keyless; its data is « licensed by CC BY-SA », credited as the source, and calls are « rate
limited to allow at least 20 calls every 10 seconds per IP address », a 429 being retried « after a small pause »
(https://www.tvmaze.com/api, read 2026-11-19). One episode costs at most 3 calls, and none once cached.

A show is accepted only when it is unambiguous: five TVmaze shows are named « Ghosts » (2021-11-19), so a name
matching several shows needs the filename's year, and anything else is reported as not found rather than guessed.
Every answer is checked for shape before use: an unexpected one is a LookupFailed, never a KeyError downstream.
The cache is an optimisation: a cache that cannot be read or written is ignored with a warning.
"""

import hashlib
import html
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger("SubStudio.Context")

API = "https://api.tvmaze.com"
TVMAZE_ATTRIBUTION = "TVmaze (https://www.tvmaze.com, CC BY-SA)"
# A series' cast changes between seasons; a month-old entry is looked up again.
CACHE_DAYS = 30


class LookupFailed(RuntimeError):
    """The source could not be reached or answered wrongly: the caller keeps what it knows, with a reason."""


@dataclass
class SeriesFacts:
    show_id: int
    name: str
    premiered_year: Optional[int]
    characters: List[str] = field(default_factory=list)   # character names, most important first
    episodes: dict = field(default_factory=dict)           # "5x1" -> {"name", "summary"}; only complete ones
    fetched: str = ""                                      # ISO date of the show and cast lookup

    @classmethod
    def from_json(cls, data) -> Optional["SeriesFacts"]:
        """None for anything that is not exactly the shape we write (an old or damaged cache file)."""
        if not isinstance(data, dict):
            return None
        try:
            facts = cls(**data)
        except TypeError:
            return None
        ok = (isinstance(facts.show_id, int) and isinstance(facts.name, str)
              and (facts.premiered_year is None or isinstance(facts.premiered_year, int))
              and isinstance(facts.characters, list) and all(isinstance(c, str) for c in facts.characters)
              and isinstance(facts.episodes, dict)
              and all(isinstance(e, dict) and isinstance(e.get("name"), str) and isinstance(e.get("summary"), str)
                      for e in facts.episodes.values())
              and isinstance(facts.fetched, str))
        return facts if ok else None


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", name.casefold()).split())


def plain_text(summary) -> str:
    """TVmaze summaries are HTML (« <p>…</p> »)."""
    if not isinstance(summary, str):
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", summary)).split())


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
        # Readable part for a human, hash of the exact key so « Élite » / « Lite » or two non-Latin titles never
        # share a file (a shared file would hand one show's cast to another).
        key = f"{_norm(title)}|{year or ''}"
        readable = re.sub(r"[^\w]+", "-", _norm(title)).strip("-")[:40] or "title"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        return self.cache_dir / "tvmaze" / f"{readable}-{year or 'any'}-{digest}.json"

    def _load(self, title: str, year: Optional[int]) -> Optional[SeriesFacts]:
        p = self._cache_path(title, year)
        try:
            facts = SeriesFacts.from_json(json.loads(p.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            logger.warning(f"Ignoring unreadable context cache {p.name}: {e}")
            return None
        if facts is None:
            logger.warning(f"Ignoring context cache {p.name}: unexpected shape")
            return None
        try:
            if date.today() - date.fromisoformat(facts.fetched) > timedelta(days=CACHE_DAYS):
                return None
        except ValueError:
            return None
        return facts

    def _save(self, title: str, year: Optional[int], facts: SeriesFacts) -> None:
        p = self._cache_path(title, year)
        tmp = None
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            # A temp file of its own per write: two jobs on the same series never share one; replace() is atomic.
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.stem + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(facts), f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except OSError as e:
            logger.warning(f"Context cache not written ({type(e).__name__}: {e}); lookups still work, uncached")
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # --- lookups ----------------------------------------------------------------------------------------------

    @staticmethod
    def _shows(results) -> List[dict]:
        if not isinstance(results, list):
            raise LookupFailed("TVmaze search answered an unexpected shape")
        shows = [r.get("show") for r in results if isinstance(r, dict)]
        return [s for s in shows if isinstance(s, dict) and isinstance(s.get("id"), int)
                and isinstance(s.get("name"), str)]

    def _find_show(self, title: str, year: Optional[int]) -> Tuple[Optional[dict], str]:
        shows = self._shows(self._get("/search/shows", {"q": title}) or [])
        same_name = [s for s in shows if _norm(s["name"]) == _norm(title)]
        if year is not None:
            same_year = [s for s in same_name if str(s.get("premiered") or "")[:4] == str(year)]
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
        if not isinstance(cast, list):
            raise LookupFailed("TVmaze cast answered an unexpected shape")
        characters: List[str] = []
        for member in cast:
            character = member.get("character") if isinstance(member, dict) else None
            name = character.get("name") if isinstance(character, dict) else None
            if isinstance(name, str) and name.strip() and name not in characters:
                characters.append(name.strip())
        premiered = str(show.get("premiered") or "")[:4]
        facts = SeriesFacts(show_id=show["id"], name=show["name"],
                            premiered_year=int(premiered) if premiered.isdigit() else None,
                            characters=characters, fetched=date.today().isoformat())
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
        if not isinstance(ep, dict):
            raise LookupFailed("TVmaze episode answered an unexpected shape")
        found = {"name": ep.get("name") if isinstance(ep.get("name"), str) else "",
                 "summary": plain_text(ep.get("summary"))}
        if found["summary"]:
            # Only a complete episode is cached: one aired yesterday gets its summary on a later run.
            facts.episodes[key] = found
            self._save(title, year, facts)
        return found, ""
