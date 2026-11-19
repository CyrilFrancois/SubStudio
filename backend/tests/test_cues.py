# test_cues.py — Cue list: charset detection, formats, SRT round trip, shift, clip, wrap. Exports: none (pytest).

import pytest

from core.subtitles import SubtitleReadError, drop_after, load_file, parse, shift, to_srt, wrap_lines

FR_SRT = "1\n00:00:01,000 --> 00:00:02,500\nÇa va, Hélène ? Très bien.\n\n2\n00:00:03,000 --> 00:00:04,000\nÀ demain, garçon.\n"


def test_cp1252_sidecar_keeps_its_accents(tmp_path):
    # The old reader used utf-8 errors="ignore": every accent of a cp1252 French file vanished silently.
    p = tmp_path / "film.fr.srt"
    p.write_bytes(FR_SRT.encode("cp1252"))
    cues = load_file(p)
    assert [c.text for c in cues] == ["Ça va, Hélène ? Très bien.", "À demain, garçon."]


@pytest.mark.parametrize("text,encoding", [
    ("Grüße aus München, das Mädchen spielt Fußball. Schön!", "cp1252"),
    ("Příliš žluťoučký kůň úpěl ďábelské ódy. Děkuji. Dobrý den, jak se máte?", "cp1250"),
    ("Zażółć gęślą jaźń. Dzień dobry, jak się masz? Dziękuję bardzo.", "cp1250"),
    ("Привет, как дела? Спасибо, всё хорошо. До завтра.", "cp1251"),
])
def test_legacy_code_page_follows_the_language(tmp_path, text, encoding):
    p = tmp_path / "x.srt"
    p.write_bytes(f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n".encode(encoding))
    assert load_file(p)[0].text == text


def test_utf8_bom_and_crlf(tmp_path):
    p = tmp_path / "film.srt"
    p.write_bytes(b"\xef\xbb\xbf" + FR_SRT.replace("\n", "\r\n").encode("utf-8"))
    cues = load_file(p)
    assert cues[0].text.startswith("Ça va") and cues[0].start_ms == 1000 and cues[0].end_ms == 2500


def test_srt_round_trip_keeps_every_timing():
    cues = parse(FR_SRT)
    assert [(c.id, c.start_ms, c.end_ms) for c in cues] == [(1, 1000, 2500), (2, 3000, 4000)]
    assert parse(to_srt(cues)) == cues


def test_ids_are_renumbered_from_one_whatever_the_source_says():
    cues = parse("7\n00:00:01,000 --> 00:00:02,000\nA\n\n9\n00:00:03,000 --> 00:00:04,000\nB\n")
    assert [c.id for c in cues] == [1, 2]


def test_vtt_and_ass_tags_become_plain_text():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>Hello</i> there\n"
    assert parse(vtt)[0].text == "Hello there"
    ass = ("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname\nStyle: Default,Arial\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
           "MarginV, Effect, Text\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\i1}Hi{\\i0}\\Nyou\n")
    assert parse(ass)[0].text == "Hi\nyou"


def test_a_file_without_cues_fails_loudly(tmp_path):
    p = tmp_path / "empty.srt"
    p.write_bytes(b"")
    with pytest.raises(SubtitleReadError):
        load_file(p)
    p.write_bytes(b"this is not a subtitle")
    with pytest.raises(SubtitleReadError):
        load_file(p)


def test_shift_clamps_at_zero_and_drops_cues_pushed_before_it():
    cues = parse(FR_SRT)
    assert [(c.start_ms, c.end_ms) for c in shift(cues, 1.5)] == [(2500, 4000), (4500, 5500)]
    moved = shift(cues, -2.6)
    assert [(c.id, c.start_ms, c.end_ms) for c in moved] == [(2, 400, 1400)]
    assert shift(cues, -1.2)[0].start_ms == 0


def test_drop_after_removes_cues_starting_past_the_end():
    cues = parse(FR_SRT)
    assert [c.id for c in drop_after(cues, 3.0)] == [1]
    assert [c.id for c in drop_after(cues, 3.001)] == [1, 2]


def test_wrap_splits_long_lines_at_the_middle_space():
    cues = parse("1\n00:00:01,000 --> 00:00:02,000\n" + "word " * 14 + "end\n")
    lines = wrap_lines(cues, 50)[0].text.split("\n")
    assert len(lines) == 2 and all(len(l) <= 50 for l in lines)
