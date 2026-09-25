"""Input limits for a single project.

These bound what one edit job can cost us: transcription is billed per audio
second and rendering is CPU-bound, so an unbounded upload is an unbounded bill.
The mobile client mirrors these values in src/api/hybrid.ts so users get an
immediate answer instead of a rejected upload.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def max_clips() -> int:
    return _int_env("AUTOCUT_MAX_CLIPS", 10)


def max_total_seconds() -> int:
    return _int_env("AUTOCUT_MAX_TOTAL_SECONDS", 15 * 60)


def max_upload_bytes() -> int:
    return _int_env("AUTOCUT_MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024)


class ProjectLimitError(ValueError):
    """Raised when a selection exceeds what one project may contain."""


def check_clip_count(new_clips: int, existing_clips: int = 0) -> None:
    total = new_clips + existing_clips
    limit = max_clips()
    if new_clips <= 0:
        raise ProjectLimitError("Add at least one clip.")
    if total > limit:
        if existing_clips:
            raise ProjectLimitError(
                f"A project holds at most {limit} clips. "
                f"This one already has {existing_clips} and you are adding {new_clips}."
            )
        raise ProjectLimitError(f"Select at most {limit} clips. You selected {new_clips}.")


def check_total_duration(durations: Iterable[Optional[float]], existing_seconds: float = 0.0) -> None:
    """Reject a selection whose total runtime exceeds the cap.

    Unknown durations are skipped rather than assumed: on the hybrid path the
    client may not know a clip's length yet, and rejecting on missing data
    would block legitimate uploads. The per-file byte cap is the backstop.
    """
    known = [float(value) for value in durations if value is not None and float(value) > 0]
    total = sum(known) + max(0.0, existing_seconds)
    limit = max_total_seconds()
    if total > limit:
        raise ProjectLimitError(
            f"Your clips add up to about {total / 60:.1f} minutes. "
            f"The limit is {limit // 60} minutes per project."
        )
