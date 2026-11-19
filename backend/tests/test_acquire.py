# test_acquire.py — offline tests of the subtitle ladder: plan, language tags, provider choice, score floor, re-timing. Exports: none (run: python -m unittest discover -s tests).
import importlib
import os
import random
import tempfile
import unittest
from unittest import mock

import pysubs2
from subliminal.video import Episode, Movie

from core.acquire import ExistingSub, Rung, Step, normalize_language, plan
from core.acquire.align import align
from core.acquire.download import min_score, providers


class NormalizeLanguage(unittest.TestCase):
    def test_tags_files_and_users_give(self):
        for tag, want in [("fr", "fr"), ("fre", "fr"), ("fra", "fr"), ("FR", "fr"), ("eng", "en"),
                          ("ger", "de"), ("pt-BR", "pt"), ("French", "fr")]:
            self.assertEqual(normalize_language(tag), want, tag)

    def test_unknown_is_none_not_a_guess(self):
        for tag in (None, "", "und", "auto", "xx", "klingonish"):
            self.assertIsNone(normalize_language(tag), tag)

    def test_real_language_without_two_letter_code_is_none_not_a_crash(self):
        for tag in ("fil", "yue", "haw", "ast"):
            self.assertIsNone(normalize_language(tag), tag)
        with self.assertRaises(ValueError):
            plan(["yue"], [])


def _rungs(p):
    return [s.rung for s in p.steps]


class Plan(unittest.TestCase):
    def test_default_ov_en_fr_on_an_english_episode(self):
        # Cyril's default « OV, English, French » on Ghosts (English audio): OV and EN are one track.
        plans = plan(["OV", "English", "French"], [], original_language="eng")
        self.assertEqual([(p.language, p.is_ov, p.source_only) for p in plans], [("en", True, False), ("fr", False, False)])
        en, fr = plans
        self.assertEqual(_rungs(en), [Rung.DOWNLOAD, Rung.TRANSLATE, Rung.ASR])
        self.assertEqual(_rungs(fr), [Rung.DOWNLOAD, Rung.TRANSLATE, Rung.ASR])
        self.assertEqual(fr.steps[1].from_languages, ("en",))  # OV first

    def test_ov_without_a_detected_language_raises_instead_of_guessing(self):
        with self.assertRaises(ValueError):
            plan(["OV", "fr"], [])

    def test_ov_feeds_translation_even_when_not_asked(self):
        plans = plan(["fr"], [], original_language="ja")
        self.assertEqual([(p.language, p.source_only) for p in plans], [("ja", True), ("fr", False)])
        self.assertEqual(_rungs(plans[0]), [Rung.DOWNLOAD])  # never translated or transcribed for itself
        self.assertEqual(plans[1].steps[1].from_languages, ("ja",))

    def test_unmarked_existing_subtitles_are_checked_not_ignored(self):
        # The C:\Videos case (Cyril, 2026-11-19): « English (SDH) » tracks of unknown origin are evaluated at rung 1;
        # the gate, not the planner, keeps a bad one out — so the rest of the ladder stays behind it.
        sdh = ExistingSub("en", "embedded", "2")
        en, fr = plan(["OV", "en", "fr"], [sdh], original_language="eng")
        self.assertEqual(en.steps[0], Step(Rung.EXISTING, "en", source=sdh))
        self.assertEqual(_rungs(en), [Rung.EXISTING, Rung.DOWNLOAD, Rung.TRANSLATE, Rung.ASR])
        self.assertEqual(fr.steps[1].from_languages, ("en",))  # a passed SDH track can feed French

    def test_ours_first_then_text_before_bitmap_and_untagged_left_out(self):
        subs = [ExistingSub("fr", "embedded", "3", text=False), ExistingSub("fr", "embedded", "2"),
                ExistingSub("fr", "sidecar", "/x.fr.srt"), ExistingSub("fr", "embedded", "5", marked=True),
                ExistingSub(None, "embedded", "6")]
        (p,) = plan(["fr"], subs)
        self.assertEqual([s.source.ref for s in p.steps if s.rung == Rung.EXISTING], ["5", "/x.fr.srt", "2", "3"])

    def test_existing_is_evaluated_then_falls_through(self):
        # Rung 1 ends at the gate: a subtitle that fails it must leave the rest of the ladder in place.
        (p,) = plan(["fr"], [ExistingSub("fr", "embedded", "2")])
        self.assertEqual(_rungs(p), [Rung.EXISTING, Rung.DOWNLOAD, Rung.TRANSLATE, Rung.ASR])

    def test_other_language_text_track_becomes_a_source_only_plan(self):
        plans = plan(["fr"], [ExistingSub("de", "embedded", "2"), ExistingSub("it", "embedded", "4", text=False)],
                     original_language="ja")
        self.assertEqual([(p.language, p.source_only) for p in plans], [("ja", True), ("fr", False), ("de", True)])
        self.assertEqual(plans[1].steps[1].from_languages, ("ja", "de"))  # OV first; bitmap Italian never a source
        self.assertEqual(_rungs(plans[2]), [Rung.EXISTING])

    def test_no_downloads_means_no_download_rungs(self):
        (p,) = plan(["fr"], [], downloads_enabled=False)
        self.assertEqual(_rungs(p), [Rung.TRANSLATE, Rung.ASR])

    def test_duplicates_collapse_and_unknown_raises(self):
        self.assertEqual(len(plan(["fr", "fre", "fra"], [])), 1)
        with self.assertRaises(ValueError):
            plan(["zz"], [])


class Providers(unittest.TestCase):
    def test_opensubtitles_only_with_both_credentials(self):
        names, cfg = providers({})
        self.assertNotIn("opensubtitlescom", names)
        self.assertEqual(cfg, {})
        names, cfg = providers({"OPENSUBTITLES_USERNAME": "u", "OPENSUBTITLES_PASSWORD": "p\r\n"})
        self.assertEqual(names[0], "opensubtitlescom")
        self.assertEqual(cfg["opensubtitlescom"], {"username": "u", "password": "p"})
        names, _ = providers({"OPENSUBTITLES_USERNAME": "u"})
        self.assertNotIn("opensubtitlescom", names)


class MinScore(unittest.TestCase):
    def test_episode_needs_series_season_episode(self):
        ep = Episode("Show.S01E02.mkv", "Show", 1, [2])
        self.assertEqual(min_score(ep), 486 + 54 + 54)

    def test_movie_needs_year_when_the_file_names_one(self):
        self.assertEqual(min_score(Movie("Film.mkv", "Film")), 162)
        self.assertEqual(min_score(Movie("Film.1999.mkv", "Film", year=1999)), 162 + 54)


class Align(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="substudio-align-")
        rnd = random.Random(7)
        self.ref = pysubs2.SSAFile()
        t = 2000
        for i in range(120):
            d = rnd.randint(800, 3500)
            self.ref.append(pysubs2.SSAEvent(start=t, end=t + d, text=f"line {i}"))
            t += d + rnd.randint(300, 4000)
        self.ref_path = os.path.join(self.dir, "ref.srt")
        self.ref.save(self.ref_path)

    def test_recovers_a_known_offset(self):
        shifted = os.path.join(self.dir, "shifted.srt")
        s = self.ref.copy() if hasattr(self.ref, "copy") else pysubs2.load(self.ref_path)
        s.shift(ms=7500)
        s.save(shifted)
        r = align(self.ref_path, shifted, os.path.join(self.dir, "out.srt"))
        self.assertTrue(r.synced)
        self.assertAlmostEqual(r.offset_seconds, -7.5, places=1)
        self.assertEqual(pysubs2.load(r.output_path)[0].start, self.ref[0].start)

    def test_empty_input_raises_instead_of_reporting_success(self):
        empty = os.path.join(self.dir, "empty.srt")
        open(empty, "w").close()
        from core.acquire import AlignError
        with self.assertRaises(AlignError):
            align(self.ref_path, empty, os.path.join(self.dir, "out.srt"))


SRT = "1\n00:00:01,000 --> 00:00:02,500\nBonjour à tous.\n\n2\n00:00:03,000 --> 00:00:04,000\nÇa va ?\n"


class _Sub:
    """A listed subtitle as subliminal hands it over: language, provider, score, text once downloaded."""
    def __init__(self, provider, score, ok=True, lang="fra", hash_match=False, body=None):
        from babelfish import Language
        self.language, self.provider_name, self.score, self.ok = Language(lang), provider, score, ok
        self.text, self._hash = None, hash_match
        # Each listed subtitle is a different file unless a test gives two the same body.
        self.body = body or SRT + f"\n3\n00:00:05,000 --> 00:00:06,000\nNuméro {score}.\n"

    def get_matches(self, video):
        return {"hash"} if self._hash else {"series"}


class _FakePool:
    """Stands in for subliminal's ProviderPool: no network, scripted outcome."""
    def __init__(self, initialized, failed, found=()):
        self.initialized_providers = {n: object() for n in initialized}
        self.failed_providers = set(failed)
        self._found = list(found)
        self.downloaded = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def list_subtitles(self, video, langs):
        return self._found

    def download_subtitle(self, sub):
        self.downloaded.append(sub.score)
        if sub.ok:
            sub.text = sub.body
        return sub.ok


def _patched(pool):
    from core.acquire import download as dl
    video = Episode("Show.S01E02.mkv", "Show", 1, [2])
    return [mock.patch.object(dl, "scan_video", return_value=video), mock.patch.object(dl, "refine"),
            mock.patch.object(dl, "ProviderPool", return_value=pool),
            mock.patch.object(dl, "compute_score", side_effect=lambda s, v: s.score)]


def _download(pool, try_one=None, per_language=1):
    from contextlib import ExitStack
    from core.acquire import download as dl
    with ExitStack() as stack:
        for p in _patched(pool):
            stack.enter_context(p)
        return dl.download_subtitles("Show.S01E02.mkv", ["fr"], tempfile.mkdtemp(prefix="substudio-dl-"), env={},
                                     try_one=try_one, per_language=per_language)


class DownloadOutcomes(unittest.TestCase):
    def test_every_asked_provider_failing_is_not_reported_as_nothing_found(self):
        from core.acquire import ProvidersUnavailable
        with self.assertRaises(ProvidersUnavailable):
            _download(_FakePool(initialized=["gestdown"], failed=["gestdown", "podnapisi"]))

    def test_partial_outage_is_reported_with_the_empty_result(self):
        r = _download(_FakePool(initialized=["gestdown", "podnapisi"], failed=["podnapisi"]))
        self.assertEqual(r.subtitles, {})
        self.assertEqual(r.failed_providers, frozenset({"podnapisi"}))

    def test_no_provider_applies_is_a_clean_empty_result(self):
        r = _download(_FakePool(initialized=[], failed=[]))
        self.assertEqual((r.subtitles, r.failed_providers), ({}, frozenset()))


class Candidates(unittest.TestCase):
    """S05E12 (2026-11-19): the best EN drifted 5.7 s and the best FR landed 2.4 s off, while 6 EN were listed."""

    def _pool(self, *subs):
        return _FakePool(initialized=["gestdown"], failed=[], found=list(subs))

    def test_a_turned_down_candidate_makes_room_for_the_next_best(self):
        seen = []

        def try_one(lang, d):
            seen.append(os.path.basename(d.path))
            return "timing moves -5.7 s between the first and second half" if len(seen) == 1 else None
        pool = self._pool(_Sub("gestdown", 650), _Sub("gestdown", 700), _Sub("podnapisi", 600))
        r = _download(pool, try_one, per_language=3)
        self.assertEqual(pool.downloaded, [700, 650])                       # best first, then the next
        self.assertEqual(seen, ["Show.S01E02.fr.1.srt", "Show.S01E02.fr.2.srt"])
        self.assertEqual(r.rejected["fr"], ["gestdown #1: timing moves -5.7 s between the first and second half"])
        self.assertTrue(r.subtitles["fr"].path.endswith("Show.S01E02.fr.2.srt"))
        with open(r.subtitles["fr"].path, encoding="utf-8") as f:
            self.assertIn("Bonjour à tous.", f.read())                       # saved as UTF-8 SRT

    def test_the_cap_stops_the_search_and_every_try_is_listed(self):
        pool = self._pool(*[_Sub("gestdown", 700 - i) for i in range(6)])
        r = _download(pool, lambda lang, d: "out of sync: shifted +2.4 s from the speech", per_language=3)
        self.assertEqual(pool.downloaded, [700, 699, 698])
        self.assertEqual(len(r.rejected["fr"]), 3)
        self.assertNotIn("fr", r.subtitles)

    def test_a_candidate_for_another_episode_is_never_tried(self):
        pool = self._pool(_Sub("gestdown", 500))                            # under series+season+episode = 594
        r = _download(pool, lambda lang, d: None, per_language=3)
        self.assertEqual((pool.downloaded, r.subtitles, r.rejected), ([], {}, {}))

    def test_a_copy_of_a_judged_candidate_is_skipped_without_using_a_try(self):
        # S05E12 (2026-11-19): gestdown #1 and #3 were the same file; #3 cost a download and a re-timing for nothing.
        crlf_copy = SRT.replace("\n", "\r\n").replace("Bonjour à tous.", "Bonjour  à\ntous.")
        pool = self._pool(_Sub("gestdown", 700, body=SRT), _Sub("gestdown", 690, body=crlf_copy),
                          _Sub("gestdown", 680), _Sub("gestdown", 670), _Sub("gestdown", 660))
        seen = []
        r = _download(pool, lambda lang, d: seen.append(os.path.basename(d.path)) or "out of sync", per_language=3)
        self.assertEqual(seen, ["Show.S01E02.fr.1.srt", "Show.S01E02.fr.3.srt", "Show.S01E02.fr.4.srt"])
        self.assertEqual(pool.downloaded, [700, 690, 680, 670])               # 3 tries, the copy not counted
        self.assertEqual(len(r.rejected["fr"]), 3)

    def test_copies_cannot_spend_more_than_twice_the_tries_in_downloads(self):
        pool = self._pool(*[_Sub("gestdown", 700 - i, body=SRT) for i in range(9)])
        r = _download(pool, lambda lang, d: "out of sync", per_language=3)
        self.assertEqual(len(pool.downloaded), 6)
        self.assertEqual(r.rejected["fr"], ["gestdown #1: out of sync"])

    def test_a_download_that_fails_is_noted_and_the_next_is_tried(self):
        pool = self._pool(_Sub("opensubtitlescom", 700, ok=False), _Sub("gestdown", 650))
        r = _download(pool, lambda lang, d: None, per_language=3)
        self.assertEqual(r.rejected["fr"], ["opensubtitlescom #1: could not be downloaded"])
        self.assertEqual(r.subtitles["fr"].provider, "gestdown")


class FetchBranches(unittest.TestCase):
    """fetch() re-times each candidate and hands it to the gate; a turned-down one makes room for the next."""

    def _fetch(self, subs, align=None, accept=None):
        from contextlib import ExitStack
        f = importlib.import_module("core.acquire.fetch")
        pool = _FakePool(initialized=["gestdown"], failed=[], found=list(subs))
        with ExitStack() as stack:
            for p in _patched(pool):
                stack.enter_context(p)
            al = stack.enter_context(mock.patch.object(f, "align", side_effect=align))
            r = f.fetch("Show.S01E02.mkv", ["fr"], tempfile.mkdtemp(prefix="substudio-fetch-"), env={}, accept=accept)
        return r, al

    def test_hash_match_skips_alignment(self):
        r, al = self._fetch([_Sub("opensubtitlescom", 971, hash_match=True)])
        al.assert_not_called()
        self.assertEqual((r.subtitles["fr"].synced, r.subtitles["fr"].offset_seconds), (True, 0.0))

    def test_a_candidate_that_cannot_be_retimed_is_turned_down_and_the_next_tried(self):
        from core.acquire import AlignError
        from core.acquire.align import AlignResult
        calls = []

        def align(video, sub, out):
            calls.append(sub)
            if len(calls) == 1:
                raise AlignError("boom")
            if len(calls) == 2:
                return AlignResult(out, False, None)                     # untrustworthy alignment
            return AlignResult(sub, True, -1.5)
        r, _ = self._fetch([_Sub("gestdown", 700), _Sub("gestdown", 690), _Sub("gestdown", 680)], align=align)
        self.assertEqual(r.rejected["fr"], ["gestdown #1: could not be re-timed: boom",
                                            "gestdown #2: could not be matched to the audio"])
        self.assertEqual(r.subtitles["fr"].offset_seconds, -1.5)

    def test_the_gate_turning_one_down_makes_room_for_the_next(self):
        from core.acquire.align import AlignResult
        verdicts = iter(["out of sync: shifted +2.4 s from the speech", None])
        r, _ = self._fetch([_Sub("gestdown", 700), _Sub("podnapisi", 650)],
                           align=lambda v, s, o: AlignResult(s, True, -10.09), accept=lambda lang, p: next(verdicts))
        self.assertEqual(r.rejected["fr"], ["gestdown #1: out of sync: shifted +2.4 s from the speech"])
        self.assertEqual(r.subtitles["fr"].provider, "podnapisi")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not on PATH")
class AlignToTheAudio(unittest.TestCase):
    """A video that already carries a (wrong) subtitle track: the download must be re-timed to the SPEECH."""

    def test_reference_is_the_audio_not_the_embedded_track(self):
        import subprocess
        import wave
        import numpy as np
        d = tempfile.mkdtemp(prefix="substudio-vad-")
        rate, rnd = 16000, random.Random(3)
        spans, t = [], 1.5
        for _ in range(40):
            dur = rnd.uniform(0.8, 2.2)
            spans.append((t, t + dur))
            t += dur + rnd.uniform(0.6, 2.5)
        total = t + 2
        x = np.arange(int(total * rate)) / rate
        voice = sum(np.sin(2 * np.pi * 140 * k * x) / k for k in range(1, 12)) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * x))
        gate = np.zeros_like(x)
        for a, b in spans:
            gate[int(a * rate):int(b * rate)] = 1.0
        pcm = (voice * gate / np.abs(voice).max() * 0.6 * 32767).astype("<i2")
        wav = os.path.join(d, "speech.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate), w.writeframes(pcm.tobytes())

        def srt(path, shift_ms):
            s = pysubs2.SSAFile()
            for i, (a, b) in enumerate(spans):
                s.append(pysubs2.SSAEvent(start=int(a * 1000) + shift_ms, end=int(b * 1000) + shift_ms, text=f"l{i}"))
            s.save(path)
        srt(os.path.join(d, "embedded.srt"), 2000)       # the file's own track: 2 s late
        srt(os.path.join(d, "download.srt"), 3000)       # the download: 3 s late
        video = os.path.join(d, "clip.mkv")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=160x120:d={total}",
                        "-i", wav, "-i", os.path.join(d, "embedded.srt"), "-map", "0:v", "-map", "1:a", "-map", "2:s",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt", "-shortest", video],
                       check=True)
        r = align(video, os.path.join(d, "download.srt"), os.path.join(d, "out.srt"))
        # Speech-based: −3 s. Matched to the embedded track instead, it would read −1 s.
        self.assertTrue(r.synced)
        self.assertAlmostEqual(r.offset_seconds, -3.0, delta=0.3)
