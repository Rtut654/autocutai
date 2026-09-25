"""The rules that turn detected cuts into a watchable edit."""

from __future__ import annotations

import pytest

from backend.app.services.cut_policy import (
    Cut,
    MIN_PAUSE_CUT,
    PAD_AFTER_CUT,
    PAD_BEFORE_CUT,
    broll_window,
    keep_segments,
    refine_cuts,
)


def cut(start, end, reason):
    return Cut(start, end, reason)


# --------------------------------------------------------------------------
# Padding: no clipped words
# --------------------------------------------------------------------------


def test_pause_cuts_leave_air_around_the_speech():
    [refined] = refine_cuts([cut(2.0, 4.0, "long_pause")], 10.0)

    assert refined.start == pytest.approx(2.0 + PAD_BEFORE_CUT)
    assert refined.end == pytest.approx(4.0 - PAD_AFTER_CUT)


def test_gap_and_silence_reasons_are_padded_too():
    for reason in ("silence_gap", "trailing_silence", "long_pause+filler_word"):
        [refined] = refine_cuts([cut(2.0, 4.0, reason)], 10.0)
        assert refined.start > 2.0


def test_word_level_cuts_are_exact():
    """Padding a filler-word cut would leave half an "um" in the edit."""
    [refined] = refine_cuts([cut(0.5, 0.75, "filler_word")], 10.0)

    assert (refined.start, refined.end) == (0.5, 0.75)


def test_cuts_the_user_placed_are_exact():
    [refined] = refine_cuts([cut(2.0, 4.0, "manual_pause")], 10.0)

    assert (refined.start, refined.end) == (2.0, 4.0)


def test_leading_silence_is_cut_right_up_to_the_first_word():
    [refined] = refine_cuts([cut(0.0, 1.0, "silence_gap")], 10.0)

    assert refined.start == 0.0
    assert refined.end == pytest.approx(1.0 - PAD_AFTER_CUT)


def test_trailing_silence_is_cut_right_to_the_end():
    [refined] = refine_cuts([cut(8.0, 10.0, "trailing_silence")], 10.0)

    assert refined.end == 10.0


# --------------------------------------------------------------------------
# Minimum cut: no choppy micro jump cuts
# --------------------------------------------------------------------------


def test_a_pause_too_short_to_be_worth_a_jump_cut_is_kept():
    tiny = cut(2.0, 2.0 + PAD_BEFORE_CUT + PAD_AFTER_CUT + MIN_PAUSE_CUT - 0.05, "long_pause")

    assert refine_cuts([tiny], 10.0) == []


def test_a_pause_just_long_enough_is_cut():
    enough = cut(2.0, 2.0 + PAD_BEFORE_CUT + PAD_AFTER_CUT + MIN_PAUSE_CUT + 0.05, "long_pause")

    assert len(refine_cuts([enough], 10.0)) == 1


def test_overlapping_cuts_merge():
    merged = refine_cuts([cut(1.0, 2.0, "filler_word"), cut(1.5, 3.0, "false_start")], 10.0)

    assert [(c.start, c.end) for c in merged] == [(1.0, 3.0)]


def test_cuts_are_clamped_to_the_clip():
    [refined] = refine_cuts([cut(-1.0, 99.0, "manual")], 10.0)

    assert (refined.start, refined.end) == (0.0, 10.0)


def test_empty_and_inverted_cuts_are_ignored():
    assert refine_cuts([cut(3.0, 3.0, "x"), cut(5.0, 4.0, "x")], 10.0) == []


# --------------------------------------------------------------------------
# Keep segments
# --------------------------------------------------------------------------


def test_keep_segments_are_the_complement_of_the_cuts():
    segments = keep_segments(10.0, [Cut(2.0, 3.0), Cut(6.0, 7.0)])

    assert segments == [(0.0, 2.0), (3.0, 6.0), (7.0, 10.0)]


def test_no_cuts_keeps_everything():
    assert keep_segments(10.0, []) == [(0.0, 10.0)]


def test_zero_duration_keeps_nothing():
    assert keep_segments(0.0, []) == []


# --------------------------------------------------------------------------
# B-roll pacing
# --------------------------------------------------------------------------


def test_short_broll_is_kept_whole():
    assert broll_window(4.0, 6.0) == [(0.0, 4.0)]


def test_long_broll_keeps_a_window_from_the_middle():
    [(start, end)] = broll_window(30.0, 6.0)

    assert end - start == pytest.approx(6.0)
    assert start == pytest.approx(0.5 + (29.5 - 6.0) / 2)


def test_the_window_skips_the_camera_settling_at_the_start():
    [(start, _)] = broll_window(7.0, 6.0)

    assert start >= 0.5


def test_pacing_can_be_disabled():
    assert broll_window(90.0, 0) == [(0.0, 90.0)]
    assert broll_window(90.0, None) == [(0.0, 90.0)]


def test_a_trim_the_user_set_wins():
    assert broll_window(30.0, 6.0, trim_start=10.0, trim_end=22.0) == [(10.0, 22.0)]


def test_the_default_stored_trim_is_not_mistaken_for_a_user_trim():
    """The model auto-fills trim_end with the rounded clip length."""
    [(start, end)] = broll_window(30.0004, 6.0, trim_start=0.0, trim_end=30.0)

    assert end - start == pytest.approx(6.0)


def test_an_empty_user_trim_keeps_nothing():
    assert broll_window(30.0, 6.0, trim_start=10.0, trim_end=10.01) == []
