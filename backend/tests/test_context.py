# test_context.py — looked-up context: filename facts, TVmaze (recorded fixtures, offline), cache, names, failures. Exports: none (pytest).

import json
from pathlib import Path

import httpx
import pytest

pytest.importorskip("guessit")

from core.context import get_context  # noqa: E402
from core.context.guess import guess_media  # noqa: E402
from core.context.names import proper_nouns  # noqa: E402
from core.context.tvmaze import TvmazeClient  # noqa: E402
from core.subtitles import Cue  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "tvmaze"
EPISODE = "C:/Videos/Ghosts/Saison 5/Ghosts.2021.S05E01.720p.HDTV.x264-SYNCOPY[EZTVx.to].mkv"


def recorded(calls, fail=None):
    """An httpx transport answering from the recorded TVmaze fixtures; `fail` maps a path to a status or an error."""
    def handler(request: httpx.Request):
        path = request.url.path
        calls.append(str(request.url))
        if fail and path in fail:
            outcome = fail[path]
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, list):
                return httpx.Response(outcome.pop(0) if len(outcome) > 1 else outcome[0],
                                      json=json.loads((FIX / "search_ghosts.json").read_text(encoding="utf-8")))
            return httpx.Response(outcome)
        if path == "/search/shows":
            return httpx.Response(200, content=(FIX / "search_ghosts.json").read_bytes())
        if path == "/shows/46573/cast":
            return httpx.Response(200, content=(FIX / "cast_46573.json").read_bytes())
        if path == "/shows/46573/episodebynumber" and request.url.params.get("season") == "5" \
                and request.url.params.get("number") == "1":
            return httpx.Response(200, content=(FIX / "episode_46573_5x1.json").read_bytes())
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def client(tmp_path, calls, fail=None):
    http = httpx.Client(base_url="https://api.tvmaze.com", transport=recorded(calls, fail))
    return TvmazeClient(tmp_path, http=http, pause_s=0)


def cue(i, text):
    return Cue(id=i, start_ms=i * 1000, end_ms=i * 1000 + 900, text=text)


def test_the_filename_gives_series_year_season_episode_not_the_folder():
    g = guess_media(EPISODE)
    assert (g.kind, g.title, g.year, g.season, g.episode) == ("episode", "Ghosts", 2021, 5, 1)
    assert g.label() == "Ghosts (2021) S05E01"
    film = guess_media("C:/Videos/Films/Saison 5/The.Matrix.1999.1080p.BluRay.mkv")
    assert (film.kind, film.title, film.year) == ("movie", "The Matrix", 1999)


def test_an_episode_gets_its_looked_up_title_summary_and_characters(tmp_path):
    calls = []
    ctx = get_context(EPISODE, client=client(tmp_path, calls))
    assert ctx.series == "Ghosts (2021)" and (ctx.season, ctx.episode) == (5, 1)
    assert ctx.episode_title == "Soul Custody"
    assert ctx.summary.startswith("Sam, Jay and the ghosts attempt to extricate Jay") and "<p>" not in ctx.summary
    assert ctx.missing == "" and ctx.attribution.startswith("TVmaze")
    assert "Isaac Higgintoot" in ctx.glossary and "Hetty" in ctx.glossary and "Sasappis" in ctx.glossary
    assert "Captain" not in ctx.glossary            # a title is translated, never kept
    assert "Henrietta Woodstone" in ctx.glossary and not any('"' in t for t in ctx.glossary)
    assert ctx.title == "Ghosts (2021) S05E01"
    lower = get_context(EPISODE.replace("Ghosts.2021.S05E01", "ghosts.2021.s05e01"), client=client(tmp_path, []))
    assert lower.title == "Ghosts (2021) S05E01"   # the looked-up name's casing, not the file's
    assert "Season 5, episode 1: « Soul Custody »." in ctx.notes and "Characters: Captain Isaac Higgintoot" in ctx.notes


def test_the_us_show_is_chosen_by_year_among_five_shows_named_ghosts(tmp_path):
    calls = []
    ctx = get_context(EPISODE.replace(".2021.", "."), client=client(tmp_path, calls))
    assert ctx.series == "Ghosts" and ctx.summary == "" and ctx.glossary == ()
    assert "5 shows are named « Ghosts » on TVmaze and the filename gives no year" in ctx.missing


def test_the_cache_answers_the_second_time_without_a_call(tmp_path):
    calls = []
    get_context(EPISODE, client=client(tmp_path, calls))
    assert len(calls) == 3
    (cache_file,) = (tmp_path / "tvmaze").glob("*.json")
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached["show_id"] == 46573 and cached["episodes"]["5x1"]["name"] == "Soul Custody"
    again = []
    assert get_context(EPISODE, client=client(tmp_path, again)).episode_title == "Soul Custody"
    assert again == []


def test_a_network_failure_keeps_the_filename_facts_and_says_why(tmp_path):
    calls = []
    fail = {"/search/shows": httpx.ConnectError("offline")}
    ctx = get_context(EPISODE, client=client(tmp_path, calls, fail))
    assert ctx.title == "Ghosts (2021) S05E01" and (ctx.season, ctx.episode) == (5, 1)
    assert ctx.summary == "" and ctx.episode_title == "" and ctx.attribution == ""
    assert ctx.missing == "TVmaze unreachable (ConnectError): only the filename's facts are known"


def test_a_server_error_and_a_lasting_rate_limit_are_reasons_not_crashes(tmp_path):
    assert "HTTP 500" in get_context(EPISODE, client=client(tmp_path, [], {"/shows/46573/cast": 500})).missing
    assert "429" in get_context(EPISODE, client=client(tmp_path / "b", [], {"/search/shows": 429})).missing


def test_one_429_is_retried_after_a_pause(tmp_path):
    calls = []
    ctx = get_context(EPISODE, client=client(tmp_path, calls, {"/search/shows": [429, 200]}))
    assert ctx.episode_title == "Soul Custody" and sum("search" in c for c in calls) == 2


def test_an_unknown_episode_keeps_the_series_facts(tmp_path):
    ctx = get_context(EPISODE.replace("S05E01", "S05E77"), client=client(tmp_path, []))
    assert ctx.series == "Ghosts (2021)" and ctx.summary == "" and "Isaac" in ctx.glossary
    assert ctx.missing == "TVmaze has no episode S05E77 of « Ghosts »"


def test_a_corrupt_cache_file_is_ignored_and_rebuilt(tmp_path):
    c = client(tmp_path, [])
    path = c._cache_path("Ghosts", 2021)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert get_context(EPISODE, client=c).episode_title == "Soul Custody"
    path.write_text(json.dumps({"show_id": 1, "name": "X", "premiered_year": None, "characters": [],
                                "episodes": [], "fetched": "2026-11-19"}), encoding="utf-8")  # wrong shape
    assert get_context(EPISODE, client=c).episode_title == "Soul Custody"


def test_a_film_and_an_unreadable_name_say_what_is_missing(tmp_path):
    film = get_context("C:/Videos/The.Matrix.1999.1080p.mkv", client=client(tmp_path, []))
    assert film.title == "The Matrix (1999)" and film.summary == "" and "films are not looked up" in film.missing
    junk = get_context("C:/Videos/1234.mkv", client=client(tmp_path, []))
    assert junk.missing and junk.summary == ""


def test_proper_nouns_are_counted_mid_sentence_and_never_common_words():
    cues = [cue(1, "Hey, where is Sam?"), cue(2, "I told\nSam's brother."), cue(3, "Look at Thorfinn go."),
            cue(4, "Thorfinn! Hey, Thorfinn."), cue(5, "Hey there."), cue(6, "hey you, Look at that."),
            cue(7, "[SAM] Where is Jay?"), cue(8, "Ask Jay. Look out!"), cue(9, "Paris is nice, said Pete.")]
    names = proper_nouns(cues)
    assert set(names) == {"Sam", "Thorfinn", "Jay"}
    assert "Hey" not in names and "Look" not in names and "SAM" not in names and "Paris" not in names


def test_names_from_the_cues_join_the_glossary(tmp_path):
    cues = [cue(1, "Where is Patience?"), cue(2, "Ask Patience.")]
    ctx = get_context(EPISODE, cues=cues, client=client(tmp_path, []))
    assert "Patience" in ctx.glossary


def test_the_cache_lives_in_substudios_one_state_dir(monkeypatch, tmp_path):
    import api.config
    from core.context import state_dir
    monkeypatch.setattr(api.config, "STATE_DIR", tmp_path / "state")
    assert state_dir() == tmp_path / "state" / "context"


# --- review findings (2026-11-19) ---------------------------------------------------------------------------------

def test_unexpected_answers_and_disk_errors_are_reasons_never_crashes(tmp_path):
    def answers(shape):
        def handler(request):
            return httpx.Response(200, json=shape)
        return TvmazeClient(tmp_path, http=httpx.Client(base_url="https://api.tvmaze.com",
                                                         transport=httpx.MockTransport(handler)), pause_s=0)
    for shape in ({"show": 1}, [1, "x", {"show": {"name": "Ghosts"}}], None, "text"):
        ctx = get_context(EPISODE, client=answers(shape))
        assert ctx.summary == "" and ctx.title == "Ghosts (2021) S05E01" and ctx.missing, shape
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "tvmaze").write_text("a file where the cache folder should be", encoding="utf-8")
    ctx = get_context(EPISODE, client=client(blocked, []))
    assert ctx.episode_title == "Soul Custody" and ctx.missing == ""   # uncached, still complete

    class Broken:
        def series(self, *a):
            raise RuntimeError("bug")
    ctx = get_context(EPISODE, client=Broken())
    assert ctx.missing == "the context could not be built (RuntimeError): none is used" and ctx.summary == ""


def test_two_different_titles_never_share_a_cache_file(tmp_path):
    c = client(tmp_path, [])
    paths = {c._cache_path(t, y) for t, y in [("Élite", None), ("Lite", None), ("東京", None), ("大阪", None),
                                               ("Ghosts", 2021), ("Ghosts", 2019), ("Ghosts", None)]}
    assert len(paths) == 7


def test_titles_and_articles_are_never_names_to_keep():
    from core.context import _name_parts
    assert _name_parts(["The Doctor", "Captain Isaac Higgintoot"]) == ["Isaac Higgintoot", "Isaac", "Higgintoot"]
    cues = [cue(1, "Thank you, Captain."), cue(2, "Yes, Captain."), cue(3, "> Previously on Ghosts"),
            cue(4, "> Previously, Sam said.")]
    assert proper_nouns(cues) == []


def test_a_film_is_called_a_film():
    ctx = get_context("C:/Videos/Inception.2010.1080p.mkv")
    assert ctx.notes == "Film: Inception (2010)." and ctx.series == ""


def test_an_episode_without_a_summary_yet_is_asked_again_next_time(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/shows/46573/episodebynumber":
            return httpx.Response(200, json={"name": "Soul Custody", "summary": None})
        return recorded([], None).handle_request(request)
    c = TvmazeClient(tmp_path, http=httpx.Client(base_url="https://api.tvmaze.com",
                                                 transport=httpx.MockTransport(handler)), pause_s=0)
    first = get_context(EPISODE, client=c)
    assert first.episode_title == "Soul Custody" and "no summary for this episode yet" in first.missing
    calls.clear()
    get_context(EPISODE, client=c)
    assert calls == ["/shows/46573/episodebynumber"]          # series cached, the episode asked again


def test_a_month_old_cache_is_looked_up_again(tmp_path):
    calls = []
    c = client(tmp_path, calls)
    get_context(EPISODE, client=c)
    path = c._cache_path("Ghosts", 2021)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["fetched"] = "2026-09-01"
    path.write_text(json.dumps(data), encoding="utf-8")
    calls.clear()
    get_context(EPISODE, client=c)
    assert any("/search/shows" in u for u in calls)


def test_a_file_without_a_title_says_so():
    ctx = get_context("C:/Videos/S05E01.mkv")
    assert ctx.missing == "the filename gives no title to look up" and (ctx.season, ctx.episode) == (5, 1)
