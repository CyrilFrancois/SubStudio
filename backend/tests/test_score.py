# test_score.py — Checks the bench scorer: chrF bounds, robustness to re-segmentation, timing drift, coverage and charset reading. Exports: tests only.
import json

import pytest

from bench.score import main, score
from bench.subs import Cue, load_cues

REF = [Cue(1_000, 3_000, "Il fait beau aujourd'hui."), Cue(4_000, 6_000, "Nous allons à la plage."),
       Cue(40_000, 42_000, "Le soleil se couche déjà."), Cue(43_000, 45_000, "Rentrons à la maison.")]


def test_identical_scores_100_on_every_alignment():
    r = score(REF, REF)
    assert (r["chrf_window"], r["chrf_doc"], r["chrf_cue"]) == (100.0, 100.0, 100.0)
    assert r["ref_windows_covered"] == 1.0


def test_empty_candidate_scores_zero_and_covers_nothing():
    r = score([], REF)
    assert r["chrf_window"] == 0.0 and r["chrf_doc"] == 0.0
    assert r["chrf_cue"] is None  # cue counts differ: no cue alignment
    assert r["ref_windows_covered"] == 0.0


def test_resegmented_text_keeps_a_full_window_score():
    # ASR splits sentences differently from a human; the same words in the same windows must not be penalised.
    merged = [Cue(1_000, 6_000, "Il fait beau aujourd'hui. Nous allons à la plage."),
              Cue(40_000, 45_000, "Le soleil se couche déjà. Rentrons à la maison.")]
    r = score(merged, REF)
    assert r["chrf_window"] == 100.0
    assert r["chrf_cue"] is None


def test_drift_beyond_a_window_costs_points_but_not_doc_score():
    shifted = [Cue(c.start_ms + 30_000, c.end_ms + 30_000, c.text) for c in REF]
    r = score(shifted, REF)
    assert r["chrf_doc"] == 100.0
    assert r["chrf_window"] < 60.0


def test_wrong_language_scores_low():
    english = [Cue(c.start_ms, c.end_ms, t) for c, t in zip(REF, [
        "The weather is nice today.", "We are going to the beach.", "The sun is already setting.", "Let's go home."])]
    assert score(english, REF)["chrf_window"] < 30.0


def test_empty_reference_is_an_error_not_a_score():
    with pytest.raises(ValueError):
        score(REF, [])


def test_utf8_bom_and_tags_are_read_clean(tmp_path):
    p = tmp_path / "bom.srt"
    p.write_bytes("﻿1\n00:00:01,000 --> 00:00:02,000\n<i>Déjà</i>\nvu\n\n".encode("utf-8"))
    cues, enc = load_cues(p)
    assert enc == "utf-8"
    assert cues == [Cue(1000, 2000, "Déjà vu")]


@pytest.mark.parametrize("lang,text", [
    ("fr", "Déjà vu, à bientôt"),                # too short for statistics: detection said cp1250
    ("pl", "Zażółć gęślą jaźń"),
    ("ru", "Привет, как дела?"),
    ("ja", "こんにちは、元気ですか"),
])
def test_legacy_file_is_decoded_by_its_language(tmp_path, lang, text):
    from bench.subs import legacy_codepage
    p = tmp_path / f"ref.{lang}.srt"
    p.write_bytes(f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n".encode(legacy_codepage(lang)))
    cues, enc = load_cues(p)
    assert enc == legacy_codepage(lang)
    assert cues[0].text == text


def test_legacy_file_without_a_language_is_refused_not_guessed(tmp_path):
    p = tmp_path / "human.srt"
    p.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nUne œuvre à Noël\n".encode("cp1252"))
    with pytest.raises(ValueError, match="no language"):
        load_cues(p)
    assert load_cues(p, "fr")[0][0].text == "Une œuvre à Noël"


def test_legacy_file_with_an_unknown_language_is_refused(tmp_path):
    p = tmp_path / "ref.xx.srt"
    p.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nDéjà\n".encode("cp1252"))
    with pytest.raises(ValueError, match="no legacy code page"):
        load_cues(p)


def test_cli_appends_one_row_with_measure(tmp_path):
    ref = tmp_path / "ref.srt"
    ref.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour à tous\n", encoding="utf-8")
    measure = tmp_path / "run.json"
    measure.write_text(json.dumps({"minutes": 1.5, "peak_rss_gb": 2.1, "peak_cpu_threads": 3.9,
                                   "mean_cpu_threads": 3.2, "within_budget": True, "exit_code": 0}))
    out = tmp_path / "results.jsonl"
    args = ["--hyp", str(ref), "--ref", str(ref), "--label", "t", "--measure", str(measure), "--out", str(out)]
    main(args)
    main(args)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["chrf_window"] == 100.0 and rows[0]["minutes"] == 1.5 and rows[0]["within_budget"] is True
