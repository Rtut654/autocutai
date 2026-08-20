"""Unit tests for the per-project input limits."""

from __future__ import annotations

import pytest

from backend.app.services.project_limits import (
    ProjectLimitError,
    check_clip_count,
    check_total_duration,
    max_clips,
    max_total_seconds,
)


def test_defaults_match_the_product_spec():
    assert max_clips() == 10
    assert max_total_seconds() == 15 * 60


def test_clip_count_within_the_limit_passes():
    check_clip_count(10)


def test_clip_count_over_the_limit_is_rejected():
    with pytest.raises(ProjectLimitError, match="at most 10 clips"):
        check_clip_count(11)


def test_empty_selection_is_rejected():
    with pytest.raises(ProjectLimitError, match="at least one clip"):
        check_clip_count(0)


def test_existing_clips_count_toward_the_limit():
    check_clip_count(2, existing_clips=8)
    with pytest.raises(ProjectLimitError, match="already has 8"):
        check_clip_count(3, existing_clips=8)


def test_total_duration_within_the_limit_passes():
    check_total_duration([300.0, 300.0, 299.0])


def test_total_duration_over_the_limit_is_rejected():
    with pytest.raises(ProjectLimitError, match="15 minutes"):
        check_total_duration([500.0, 500.0])


def test_duration_message_reports_the_actual_total():
    with pytest.raises(ProjectLimitError, match="20.0 minutes"):
        check_total_duration([600.0, 600.0])


def test_unknown_durations_are_skipped_rather_than_assumed():
    # The hybrid path may not know a clip's length yet; that must not block it.
    check_total_duration([None, None, 100.0])


def test_zero_and_negative_durations_are_ignored():
    check_total_duration([0.0, -5.0, 100.0])


def test_existing_runtime_counts_toward_the_limit():
    check_total_duration([100.0], existing_seconds=100.0)
    with pytest.raises(ProjectLimitError):
        check_total_duration([100.0], existing_seconds=890.0)


def test_limits_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("AUTOCUT_MAX_CLIPS", "4")
    monkeypatch.setenv("AUTOCUT_MAX_TOTAL_SECONDS", "60")

    assert max_clips() == 4
    assert max_total_seconds() == 60
    with pytest.raises(ProjectLimitError):
        check_clip_count(5)
    with pytest.raises(ProjectLimitError):
        check_total_duration([61.0])


def test_malformed_environment_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("AUTOCUT_MAX_CLIPS", "not-a-number")

    assert max_clips() == 10
