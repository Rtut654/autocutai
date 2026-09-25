"""Caption layout and timing."""

from __future__ import annotations

import pytest

from backend.app.models.transcription import WordTimestamp
from backend.app.services.captions import (
    CAPTION_STYLE_KEYS,
    FONTS_DIR,
    STYLES,
    ass_colour,
    build_ass,
    group_into_lines,
    resolve_style,
)


def words(*spec):
    return [WordTimestamp(word=w, start=s, end=e) for w, s, e in spec]


SENTENCE = words(
    ("So,", 0.20, 0.45),
    ("this", 0.50, 0.80),
    ("is", 0.85, 1.00),
    ("Lisbon.", 1.05, 1.70),
    ("The", 2.00, 2.20),
    ("view", 2.25, 2.60),
    ("is", 2.65, 2.80),
    ("incredible!", 2.85, 3.60),
)


def dialogue_lines(document: str):
    return [line for line in document.splitlines() if line.startswith("Dialogue:")]


def event_text(line: str) -> str:
    return line.split(",", 9)[9]


def event_times(line: str):
    fields = line.split(",")
    def seconds(value):
        hours, minutes, secs = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(secs)
    return seconds(fields[1]), seconds(fields[2])


# --------------------------------------------------------------------------
# Styles
# --------------------------------------------------------------------------


def test_bold_word_highlight_is_the_default():
    assert resolve_style(None).key == "bold"
    assert resolve_style("unknown").key == "bold"


def test_none_switches_captions_off():
    assert resolve_style("none") is None


def test_every_advertised_style_resolves():
    for key in CAPTION_STYLE_KEYS:
        if key != "none":
            assert resolve_style(key).key == key


def test_the_fonts_the_styles_use_are_bundled():
    bundled = {path.name for path in FONTS_DIR.glob("*.ttf")}
    assert "Montserrat_800ExtraBold.ttf" in bundled
    assert "Montserrat_600SemiBold.ttf" in bundled
    # Only fonts in the directory libass scans, so it logs no load errors.
    assert {path.suffix for path in FONTS_DIR.iterdir()} == {".ttf"}


def test_ass_colours_are_blue_green_red():
    assert ass_colour("#FFE500") == "&H0000E5FF"
    assert ass_colour("#000000", 0x40) == "&H40000000"


# --------------------------------------------------------------------------
# Line grouping
# --------------------------------------------------------------------------


def test_bold_lines_hold_at_most_three_words():
    lines = group_into_lines(SENTENCE, STYLES["bold"])

    assert all(len(line) <= 3 for line in lines)


def test_a_line_never_straddles_two_sentences():
    lines = group_into_lines(SENTENCE, STYLES["clean"])

    assert [word.word for word in lines[0]] == ["So,", "this", "is", "Lisbon."]
    assert [word.word for word in lines[1]] == ["The", "view", "is", "incredible!"]


def test_a_pause_in_speech_starts_a_new_line():
    spoken = words(("Look", 0.0, 0.3), ("at", 0.35, 0.5), ("that", 1.6, 1.9))

    lines = group_into_lines(spoken, STYLES["clean"])

    assert [[w.word for w in line] for line in lines] == [["Look", "at"], ["that"]]


def test_long_words_wrap_to_a_new_line_by_character_budget():
    spoken = words(
        ("Extraordinarily", 0.0, 0.5),
        ("breathtaking", 0.6, 1.0),
        ("mountains", 1.1, 1.5),
    )

    lines = group_into_lines(spoken, STYLES["bold"])

    assert len(lines) >= 2


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------


def test_one_event_per_spoken_word_with_the_active_word_highlighted():
    document = build_ass(SENTENCE[:3], width=1080, height=1920, style=STYLES["bold"])
    events = dialogue_lines(document)

    assert len(events) == 3
    assert "\\c&H0000E5FF" in event_text(events[0]) and event_text(events[0]).count("\\c&H") == 1
    assert event_text(events[1]).index("\\c&H") > event_text(events[1]).index("SO")


def test_highlight_follows_the_voice_without_gaps():
    document = build_ass(SENTENCE[:3], width=1080, height=1920, style=STYLES["bold"])
    times = [event_times(line) for line in dialogue_lines(document)]

    # Each word's event runs until the next word starts, so nothing flickers.
    for (start, end), (next_start, _) in zip(times, times[1:]):
        assert end == pytest.approx(next_start, abs=0.01)
    assert times[0][0] == pytest.approx(0.20, abs=0.01)


def test_bold_is_uppercase_without_trailing_punctuation():
    document = build_ass(SENTENCE[:4], width=1080, height=1920, style=STYLES["bold"])

    text = " ".join(event_text(line) for line in dialogue_lines(document))
    assert "LISBON" in text
    assert "Lisbon" not in text
    assert "SO," not in text


def test_clean_keeps_sentence_case_and_punctuation_in_one_event_per_line():
    document = build_ass(SENTENCE, width=1080, height=1920, style=STYLES["clean"])
    events = dialogue_lines(document)

    assert len(events) == 2
    assert event_text(events[0]) == "So, this is Lisbon."


def test_captions_never_run_past_the_video():
    document = build_ass(SENTENCE, width=1080, height=1920, style=STYLES["bold"], duration=3.4)

    assert max(end for _, end in map(event_times, dialogue_lines(document))) <= 3.4


def test_the_last_line_lingers_briefly_when_nothing_follows():
    document = build_ass(SENTENCE[:4], width=1080, height=1920, style=STYLES["clean"])

    _, end = event_times(dialogue_lines(document)[-1])
    assert end == pytest.approx(1.70 + 0.4, abs=0.01)


def test_vertical_captions_sit_above_the_platform_ui():
    document = build_ass(SENTENCE, width=1080, height=1920, style=STYLES["bold"])

    style_line = next(line for line in document.splitlines() if line.startswith("Style: Caption"))
    margin_v = int(style_line.split(",")[21])
    assert margin_v == round(1920 * 0.22)


def test_font_size_scales_with_the_canvas():
    small = build_ass(SENTENCE, width=540, height=960, style=STYLES["bold"])
    large = build_ass(SENTENCE, width=1080, height=1920, style=STYLES["bold"])

    size = lambda doc: int(next(l for l in doc.splitlines() if l.startswith("Style:")).split(",")[2])
    assert size(large) == 2 * size(small)


def test_override_characters_in_speech_cannot_break_the_document():
    spoken = words(("{\\an8}hack", 0.0, 0.5), ("ok", 0.6, 0.9))

    document = build_ass(spoken, width=1080, height=1920, style=STYLES["clean"])

    assert "\\an8" not in document
    assert "hack ok" in document.lower()


def test_no_words_means_no_events():
    assert dialogue_lines(build_ass([], width=1080, height=1920, style=STYLES["bold"])) == []
