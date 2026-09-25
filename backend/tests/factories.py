"""Builders for test fixtures shared across the suite."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

TICKS_PER_SECOND = 10_000_000

WordSpec = Tuple[str, float, float]


def azure_payload(
    words: Sequence[WordSpec],
    *,
    display: str | None = None,
    offset_seconds: float = 0.0,
) -> Dict[str, Any]:
    """Build one Azure recognition payload from (word, start, end) tuples."""
    azure_words = [
        {
            "Word": word,
            "Offset": int(round(start * TICKS_PER_SECOND)),
            "Duration": int(round((end - start) * TICKS_PER_SECOND)),
            "Confidence": 0.9,
        }
        for word, start, end in words
    ]
    lexical = " ".join(word for word, _, _ in words)
    return {
        "RecognitionStatus": "Success",
        "Offset": int(round(offset_seconds * TICKS_PER_SECOND)),
        "Duration": int(round((words[-1][2] - offset_seconds) * TICKS_PER_SECOND)) if words else 0,
        "DisplayText": display or lexical,
        "NBest": [
            {
                "Confidence": 0.9,
                "Lexical": lexical,
                "Display": display or lexical,
                "Words": azure_words,
            }
        ],
    }


# A short travel clip with two filler words and a 1.65s pause between phrases.
DEFAULT_TRANSCRIPT_WORDS: List[WordSpec] = [
    ("So", 0.20, 0.45),
    ("um", 0.50, 0.75),
    ("this", 0.80, 1.05),
    ("is", 1.10, 1.30),
    ("Lisbon", 1.35, 1.95),
    ("and", 3.60, 3.85),
    ("the", 3.90, 4.10),
    ("view", 4.15, 4.55),
    ("is", 4.60, 4.80),
    ("incredible", 4.85, 5.60),
]

DEFAULT_TRANSCRIPT_DISPLAY = "So um this is Lisbon and the view is incredible"
