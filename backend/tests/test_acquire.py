# test_acquire.py — offline tests of the subtitle ladder: plan, language tags, provider choice, score floor, re-timing. Exports: none (run: python -m unittest discover -s tests).
import importlib
import os
import random
import tempfile
import unittest
from unittest import mock

import pysubs2
from subliminal.video import Episode, Movie

from core.acquire import ExistingSub, Rung, normalize_language, plan
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

    def test_unmarked_subtitles_are_not_considered_by_default(self):
        # The C:\Videos case: an older SubStudio's bad tracks and sidecars look like a release's own.
        old = [ExistingSub("fr", "embedded", "2"), ExistingSub("de", "embedded", "3"),
               ExistingSub("fr", "sidecar", "/x.fr.srt")]
        plans = plan(["fr"], old, original_language="en")
        self.assertEqual([p.language for p in plans], ["en", "fr"])  # no German source-only plan
        self.assertEqual(_rungs(plans[1]), [Rung.DOWNLOAD, Rung.TRANSLATE, Rung.ASR])
        for kind in ("embedded", "sidecar"):
            (p,) = plan(["fr"], [ExistingSub("fr", kind, "2", marked=True)])
            self.assertEqual(p.rung, Rung.EXISTING, kind)

    def test_existing_is_evaluated_then_falls_through(self):
        # Rung 1 ends at the gate: a subtitle that fails it must leave the rest of the ladder in place.
        (p,) = plan(["fr"], [ExistingSub("fr", "embedded", "2")], count_existing=True)
        self.assertEqual(_rungs(p), [Rung.EXISTING, Rung.DOWNLOAD, Rung.ASR])

    def test_text_existing_before_bitmap(self):
        (p,) = plan(["fr"], [ExistingSub("fr", "embedded", "3", text=False), ExistingSub("fr", "sidecar", "/x.fr.srt")],
                    count_existing=True)
        self.assertEqual([s.source.kind for s in p.steps[:2]], ["sidecar", "embedded"])

    def test_other_language_text_track_becomes_a_source_only_plan(self):
        plans = plan(["fr"], [ExistingSub("de", "embedded", "2"), ExistingSub("it", "embedded", "4", text=False)],
                     original_language="ja", count_existing=True)
        self.assertEqual([(p.language, p.source_only) for p in plans], [("ja", True), ("fr", False), ("de", True)])
        self.assertEqual(plans[1].steps[1].from_languages, ("ja", "de"))  # OV first; bitmap Italian never a source
        self.assertEqual(_rungs(plans[2]), [Rung.EXISTING])

    def test_no_downloads_means_no_download_rungs(self):
        (p,) = plan(["fr"], [], downloads_enabled=False)
        self.assertEqual(_rungs(p), [Rung.ASR])

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


class _FakePool:
    """Stands in for subliminal's ProviderPool: no network, scripted outcome."""
    def __init__(self, initialized, failed, found=(), best=()):
        self.initialized_providers = {n: object() for n in initialized}
        self.failed_providers = set(failed)
        self._found, self._best = list(found), list(best)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def list_subtitles(self, video, langs):
        return self._found

    def download_best_subtitles(self, found, video, langs, min_score):
        return self._best


class DownloadOutcomes(unittest.TestCase):
    def _run(self, pool):
        from core.acquire import download as dl
        video = Episode("Show.S01E02.mkv", "Show", 1, [2])
        with mock.patch.object(dl, "scan_video", return_value=video), mock.patch.object(dl, "refine"), \
                mock.patch.object(dl, "ProviderPool", return_value=pool):
            return dl.download_subtitles("Show.S01E02.mkv", ["fr"], tempfile.mkdtemp(prefix="substudio-dl-"), env={})

    def test_every_asked_provider_failing_is_not_reported_as_nothing_found(self):
        from core.acquire import ProvidersUnavailable
        with self.assertRaises(ProvidersUnavailable):
            self._run(_FakePool(initialized=["gestdown"], failed=["gestdown", "podnapisi"]))

    def test_partial_outage_is_reported_with_the_empty_result(self):
        r = self._run(_FakePool(initialized=["gestdown", "podnapisi"], failed=["podnapisi"]))
        self.assertEqual(r.subtitles, {})
        self.assertEqual(r.failed_providers, frozenset({"podnapisi"}))

    def test_no_provider_applies_is_a_clean_empty_result(self):
        r = self._run(_FakePool(initialized=[], failed=[]))
        self.assertEqual((r.subtitles, r.failed_providers), ({}, frozenset()))


class FetchBranches(unittest.TestCase):
    def _report(self, hash_match):
        from core.acquire.download import Downloaded, DownloadReport
        return DownloadReport({"fr": Downloaded("fr", "/w/downloaded/x.fr.srt", "gestdown", hash_match)},
                              frozenset({"podnapisi"}))

    def test_hash_match_skips_alignment(self):
        f = importlib.import_module("core.acquire.fetch")
        with mock.patch.object(f, "download_subtitles", return_value=self._report(True)), \
                mock.patch.object(f, "align") as al:
            r = f.fetch("v.mkv", ["fr"], "/w")
        al.assert_not_called()
        self.assertTrue(r.subtitles["fr"].synced)
        self.assertEqual(r.failed_providers, frozenset({"podnapisi"}))

    def test_align_failure_keeps_the_downloaded_subtitle(self):
        from core.acquire import AlignError
        f = importlib.import_module("core.acquire.fetch")
        with mock.patch.object(f, "download_subtitles", return_value=self._report(False)), \
                mock.patch.object(f, "align", side_effect=AlignError("boom")):
            r = f.fetch("v.mkv", ["fr"], "/w")
        a = r.subtitles["fr"]
        self.assertEqual((a.path, a.synced), ("/w/downloaded/x.fr.srt", False))


if __name__ == "__main__":
    unittest.main()
