"""Rules that turn detected cuts into a watchable edit.

Two complaints dominate reviews of auto-editors: words get clipped at the
edges of a cut, and the result is choppy because every tiny pause became a
jump cut. Both come from cutting exactly on detected timestamps. These rules
soften that:

- Pause and silence cuts keep a little air around the speech either side, so
  a word's tail and the next word's attack survive.
- A cut that would remove almost nothing is dropped. A jump cut to save a
  quarter of a second costs more in smoothness than it saves in time.
- Cuts the user placed by hand are applied exactly as given.

Separately, long clips with no narration are paced down to a short b-roll
beat, which is what makes a travel edit feel edited rather than dumped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

Segment = Tuple[float, float]

# Keep this much before a pause cut (protects the end of the previous word)...
PAD_BEFORE_CUT = 0.10
# ...and this much after it (protects the attack of the next word).
PAD_AFTER_CUT = 0.06
# Below this, a pause cut is not worth the jump cut.
MIN_PAUSE_CUT = 0.30
# Silent clips longer than this are paced down (seconds); 0 disables.
DEFAULT_BROLL_MAX_SECONDS = 6.0
# Skip the start of a silent clip: the first moments of a handheld or drone
# shot are usually the camera settling.
BROLL_LEAD_IN = 0.5

# Reasons that describe dead air rather than words.
_PAUSE_MARKERS = ("pause", "silence", "gap")


@dataclass(frozen=True)
class Cut:
    start: float
    end: float
    reason: str = ""


def is_pause_cut(reason: str) -> bool:
    lowered = (reason or "").lower()
    return any(marker in lowered for marker in _PAUSE_MARKERS)


def is_manual_cut(reason: str) -> bool:
    return "manual" in (reason or "").lower()


def refine_cuts(cuts: Iterable[Any], duration: float) -> List[Cut]:
    """Pad pause cuts, drop ones too small to be worth it, merge overlaps.

    Word-level cuts (filler words, false starts) are left exact: padding them
    would leave half an "um" in the edit.
    """
    safe_duration = max(0.0, float(duration or 0.0))
    refined: List[Cut] = []

    for raw in cuts:
        start = max(0.0, float(getattr(raw, "start", 0.0)))
        end = min(safe_duration, float(getattr(raw, "end", 0.0)))
        reason = str(getattr(raw, "reason", "") or "")
        if end <= start:
            continue

        if is_pause_cut(reason) and not is_manual_cut(reason):
            # Leading silence has no word before it, trailing none after.
            if start > 0.0:
                start += PAD_BEFORE_CUT
            if end < safe_duration:
                end -= PAD_AFTER_CUT
            if end - start < MIN_PAUSE_CUT:
                continue

        refined.append(Cut(round(start, 3), round(end, 3), reason))

    refined.sort(key=lambda cut: (cut.start, cut.end))
    merged: List[Cut] = []
    for cut in refined:
        if merged and cut.start <= merged[-1].end + 0.001:
            previous = merged[-1]
            merged[-1] = Cut(previous.start, max(previous.end, cut.end), previous.reason)
        else:
            merged.append(cut)
    return merged


def keep_segments(duration: float, cuts: Sequence[Cut], min_segment: float = 0.05) -> List[Segment]:
    """The complement of the cuts within [0, duration]."""
    safe_duration = max(0.0, float(duration or 0.0))
    if safe_duration <= 0:
        return []
    segments: List[Segment] = []
    cursor = 0.0
    for cut in sorted(cuts, key=lambda item: item.start):
        if cut.start > cursor + 0.001:
            segments.append((round(cursor, 3), round(cut.start, 3)))
        cursor = max(cursor, cut.end)
    if cursor < safe_duration - 0.001:
        segments.append((round(cursor, 3), round(safe_duration, 3)))
    return [(start, end) for start, end in segments if end - start >= min_segment]


def broll_window(
    duration: float,
    max_seconds: Optional[float],
    *,
    trim_start: float = 0.0,
    trim_end: Optional[float] = None,
) -> List[Segment]:
    """The part of a silent clip to keep.

    A trim the user set by hand always wins. Otherwise a clip longer than
    max_seconds keeps a window from its middle, after the lead-in, which on
    travel footage is usually where the shot has settled and is moving.
    """
    safe_duration = max(0.0, float(duration or 0.0))
    if safe_duration <= 0:
        return []

    user_start = max(0.0, float(trim_start or 0.0))
    user_end = min(safe_duration, float(trim_end)) if trim_end is not None else safe_duration
    # The stored trim defaults to the rounded clip length; only a trim that
    # differs meaningfully from the whole clip counts as the user's choice.
    if user_start > 0.01 or user_end < safe_duration - 0.01:
        if user_end - user_start <= 0.05:
            return []
        return [(round(user_start, 3), round(user_end, 3))]

    if not max_seconds or max_seconds <= 0 or safe_duration <= max_seconds:
        return [(0.0, round(safe_duration, 3))]

    usable_start = min(BROLL_LEAD_IN, max(0.0, safe_duration - max_seconds))
    usable = safe_duration - usable_start
    start = usable_start + (usable - max_seconds) / 2
    return [(round(start, 3), round(start + max_seconds, 3))]
