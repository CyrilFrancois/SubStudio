# download.py — rung 2 of the ladder: find and download a human subtitle for one video through subliminal's free providers. Exports: download_subtitles, providers, min_score, Downloaded, DownloadReport, ProvidersUnavailable.
import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional

from babelfish import Language
import pysubs2
from subliminal import ProviderPool, region, refine, scan_video
from subliminal.score import compute_score, episode_scores, movie_scores
from subliminal.video import Episode

logger = logging.getLogger("SubStudio.Acquire.Download")

# Free providers that need no account (subliminal 2.8.0). Left out: `opensubtitles` (the retired .org XML-RPC
# API), `addic7ed` (scrapes the site; `gestdown` serves the same subtitles through an API), `bsplayer` (not in
# subliminal's default set).
_KEYLESS = ("gestdown", "podnapisi", "tvsubtitles", "subtitulamos", "subtis", "napiprojekt")
# OpenSubtitles.com: the largest catalogue and the only exact-file (moviehash) match. subliminal searches it with its
# own registered app key, but its download step logs in (measured 2026-11-19: « Cannot authenticate with username
# and password » with none set), so it joins the pool only with a free account's credentials.
_OPENSUBTITLES = "opensubtitlescom"


class _TerseErrors(logging.Filter):
    # subliminal logs every provider failure with a full traceback, and the log bridge mirrors the root logger into
    # the dashboard: keep the one-line cause, drop the stack.
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            record.msg = f"{record.getMessage()}: {type(record.exc_info[1]).__name__}: {record.exc_info[1]}"[:300]
            record.args, record.exc_info, record.exc_text = (), None, None
        return True


for _name in ("subliminal.utils", "subliminal.core"):
    logging.getLogger(_name).addFilter(_TerseErrors())


class ProvidersUnavailable(RuntimeError):
    """Every provider that could serve this video failed: « not searched », never « nothing exists »."""


@dataclass(frozen=True)
class Downloaded:
    language: str      # ISO 639-1
    path: str          # UTF-8 SRT written in `out_dir`
    provider: str
    hash_match: bool   # cut for this exact file: timings should already fit


@dataclass(frozen=True)
class DownloadReport:
    subtitles: Dict[str, Downloaded]
    # Providers that errored (outage, quota, block). A language missing from `subtitles` while this is non-empty
    # means « not found where we could look », not « no human subtitle exists »: a later run may find it.
    failed_providers: FrozenSet[str] = frozenset()
    # Per language, one sentence per candidate tried and turned down, in order (« gestdown #1: <reason> »).
    rejected: Dict[str, List[str]] = field(default_factory=dict)


# Candidates tried per language when the first is turned down (re-timed and gated each): a release cut differently
# is common (Ghosts S05E12: the best EN drifted 5.7 s, 6 EN were listed), but each try costs a download (quota) and a
# re-timing (~30 s), so the ladder falls to translation after this many.
MAX_CANDIDATES = 3


def _configure_cache() -> None:
    # subliminal refuses to search until its dogpile region is configured; memory is enough because
    # the work folder (not this cache) is what makes a re-run cheap.
    if not region.is_configured:
        region.configure("dogpile.cache.memory")


def providers(env: Optional[dict] = None) -> "tuple[List[str], dict]":
    """The provider list and configs for this run: OpenSubtitles.com first when `.env` holds its account."""
    env = os.environ if env is None else env
    user = env.get("OPENSUBTITLES_USERNAME", "").strip()
    pwd = env.get("OPENSUBTITLES_PASSWORD", "").strip()
    if user and pwd:
        return [_OPENSUBTITLES, *_KEYLESS], {_OPENSUBTITLES: {"username": user, "password": pwd}}
    if user or pwd:
        logger.warning("OpenSubtitles.com skipped: set both OPENSUBTITLES_USERNAME and OPENSUBTITLES_PASSWORD")
    return list(_KEYLESS), {}


def min_score(video) -> int:
    """The lowest score that still names the right video: never accept a subtitle for another episode or film."""
    if isinstance(video, Episode):
        return episode_scores["series"] + episode_scores["season"] + episode_scores["episode"]
    floor = movie_scores["title"]
    if getattr(video, "year", None):
        floor += movie_scores["year"]  # a remake shares the title, not the year
    return floor


def download_subtitles(video_path: str, languages: Iterable[str], out_dir: str, env: Optional[dict] = None,
                       try_one: Optional[Callable[[str, Downloaded], Optional[str]]] = None,
                       per_language: int = 1) -> DownloadReport:
    """Human subtitles per language, best match first, saved as UTF-8 SRT in `out_dir`. With `try_one`, each
    downloaded candidate is offered to it (re-time, gate…): None keeps it, a sentence turns it down and the next
    candidate is tried, up to `per_language`. A language with nothing kept is absent from `subtitles`; a search no
    provider could run raises ProvidersUnavailable."""
    langs = {Language.fromalpha2(l) for l in languages}
    if not langs:
        return DownloadReport({})
    _configure_cache()
    video = scan_video(video_path)
    if isinstance(video, Episode) and video.series and video.year:
        # Shows sharing a name are listed as « Ghosts (2021) »; subliminal searches « Ghosts » and gestdown rejects
        # every suggestion (measured 2026-11-19 on Ghosts.2021.S05E03). The year is in the filename: offer that name.
        disambiguated = f"{video.series} ({video.year})"
        if disambiguated not in video.alternative_series:
            video.alternative_series.append(disambiguated)
    # Only the file hash: the omdb/tmdb/tvdb refiners call third-party APIs that need their own keys.
    names, configs = providers(env)
    refine(video, refiners=["hash"], providers=names, languages=langs)
    floor = min_score(video)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    out: Dict[str, Downloaded] = {}
    rejected: Dict[str, List[str]] = {}

    with ProviderPool(providers=names, provider_configs=configs) as pool:
        found = pool.list_subtitles(video, langs)
        failed = set(pool.failed_providers)
        # A provider that does not serve this video type or language is skipped without being initialised;
        # only the ones actually asked count, so « none of them answered » is told apart from « none applies ».
        asked = set(pool.initialized_providers) | failed
        if failed:
            logger.warning(f"Subtitle providers failed: {', '.join(sorted(failed))}")
        if asked and asked <= failed:
            raise ProvidersUnavailable(f"no subtitle provider answered ({', '.join(sorted(failed))})")
        for lang in sorted(langs, key=str):
            code = lang.alpha2
            ranked = sorted((s for s in found if s.language == lang and compute_score(s, video) >= floor),
                            key=lambda s: compute_score(s, video), reverse=True)
            for n, sub in enumerate(ranked[:max(1, per_language)], start=1):
                label = f"{sub.provider_name} #{n}"
                # A quota or a ban discards the provider inside the pool: its download returns False at once.
                if not pool.download_subtitle(sub):
                    rejected.setdefault(code, []).append(f"{label}: could not be downloaded")
                    continue
                path = os.path.join(out_dir, f"{stem}.{code}.{n}.srt")
                try:
                    # sub.text is decoded by subliminal from the provider's encoding; written as UTF-8 SRT whatever the
                    # provider served (cp1252, ASS…), so no later read can lose the accents.
                    pysubs2.SSAFile.from_string(sub.text or "").save(path, encoding="utf-8", format_="srt")
                except Exception as e:  # unparseable file: the next candidate may be fine
                    rejected.setdefault(code, []).append(f"{label}: unreadable ({str(e)[:80]})")
                    continue
                d = Downloaded(code, path, sub.provider_name, "hash" in sub.get_matches(video))
                why = try_one(code, d) if try_one else None
                if why:
                    rejected.setdefault(code, []).append(f"{label}: {why}")
                    logger.info(f"{code}: candidate {label} turned down: {why}")
                    continue
                out[code] = d
                logger.info(f"Downloaded {code} subtitle from {sub.provider_name} (candidate {n} of {len(ranked)}, "
                            f"score {compute_score(sub, video)}, {floor} needed)")
                break
        failed = frozenset(pool.failed_providers)  # now including download-time failures (quota)
    return DownloadReport(out, failed, rejected)
