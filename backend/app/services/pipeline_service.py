"""Pipeline helpers: merge words, sanitize transcripts, detect gaps, and generate SRT."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, List, Sequence

from ..models.project import GapRange, SubtitleCue
from ..models.transcription import WordTimestamp


@dataclass
class TimelineWord:
    word: str
    start: float
    end: float
    confidence: float | None = None


HALLUCINATION_PHRASES = {
    "thanks for watching",
    "thank you for watching",
    "thanks for watching!",
    "thank you for watching!",
}


def _normalize_text_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def normalize_words(raw_words: Sequence[dict]) -> List[WordTimestamp]:
    result: List[WordTimestamp] = []
    for raw in raw_words:
        text = str(raw.get("word") or raw.get("text") or "").strip()
        if not text:
            continue
        start = float(raw.get("start", 0.0))
        end = float(raw.get("end", start))
        if end - start < 0.02:
            continue
        result.append(
            WordTimestamp(word=text, start=max(0.0, start), end=max(start, end), confidence=raw.get("confidence"))
        )
    return result


def _words_within_segments(words: Sequence[WordTimestamp], segments: Sequence[dict]) -> List[WordTimestamp]:
    """Keep only the words that fall inside a surviving segment.

    Providers return both a flat word list and per-segment words. When a
    segment is dropped as a hallucination its words have to go with it,
    otherwise the flat list silently reintroduces the text we just removed.
    """
    if not segments:
        return []
    spans = [
        (float(segment.get("start", 0.0)), float(segment.get("end", 0.0)))
        for segment in segments
    ]
    kept: List[WordTimestamp] = []
    for word in words:
        midpoint = (word.start + word.end) / 2
        if any(start - 0.05 <= midpoint <= end + 0.05 for start, end in spans):
            kept.append(word)
    return kept


def sanitize_transcription_payload(raw_transcription: dict, clip_duration: float) -> dict:
    """Drop hallucinated and empty output from a raw provider transcript."""
    raw_segments = raw_transcription.get("segments") or []
    sanitized_segments: List[dict] = []

    for raw_segment in raw_segments:
        start = float(raw_segment.get("start", 0.0))
        end = float(raw_segment.get("end", start))
        text = str(raw_segment.get("text") or "").strip()
        normalized_text = _normalize_text_key(text)
        words = normalize_words(raw_segment.get("words") or [])
        duration = max(0.0, end - start)
        is_known_hallucination = normalized_text in HALLUCINATION_PHRASES
        is_tiny_tail_segment = (
            duration < 0.35
            and clip_duration > 0
            and start >= max(0.0, clip_duration - 1.5)
            and len(words) <= 4
        )

        if (not text and not words) or is_known_hallucination or (is_tiny_tail_segment and not words):
            continue

        sanitized_segment = dict(raw_segment)
        sanitized_segment["text"] = text
        sanitized_segment["words"] = [word.model_dump() for word in words]
        sanitized_segments.append(sanitized_segment)

    top_level_words = normalize_words(raw_transcription.get("words") or [])
    if raw_segments:
        # Segments are authoritative: a word only survives if its segment did.
        sanitized_words = (
            _words_within_segments(top_level_words, sanitized_segments)
            if top_level_words
            else normalize_words(
                [word for segment in sanitized_segments for word in segment.get("words", [])]
            )
        )
    else:
        sanitized_words = top_level_words

    joined_text = " ".join(
        str(segment.get("text") or "").strip()
        for segment in sanitized_segments
        if str(segment.get("text") or "").strip()
    ).strip()
    if not joined_text and not sanitized_segments:
        joined_text = str(raw_transcription.get("text") or "").strip()

    # A transcript that is nothing but a known hallucination is not a transcript.
    if len(sanitized_words) <= 4 and _normalize_text_key(joined_text) in HALLUCINATION_PHRASES:
        joined_text = ""
        sanitized_words = []
        sanitized_segments = []

    return {
        "text": joined_text,
        "words": [word.model_dump() for word in sanitized_words],
        "segments": sanitized_segments,
        "language": raw_transcription.get("language", "en"),
    }


def find_gaps(words: Sequence[WordTimestamp], min_gap: float = 1.0, clip_duration: float | None = None) -> List[GapRange]:
    gaps: List[GapRange] = []
    if not words:
        if clip_duration and clip_duration > min_gap:
            gaps.append(
                GapRange(start=0.0, end=round(clip_duration, 3), duration=round(float(clip_duration), 3))
            )
        return gaps

    if words[0].start > min_gap:
        gaps.append(
            GapRange(
                start=0.0,
                end=round(words[0].start, 3),
                duration=round(words[0].start, 3),
            )
        )

    for prev_word, next_word in zip(words, words[1:]):
        gap = next_word.start - prev_word.end
        if gap > min_gap:
            gaps.append(
                GapRange(
                    start=round(prev_word.end, 3),
                    end=round(next_word.start, 3),
                    duration=round(gap, 3),
                )
            )

    if clip_duration is not None:
        trailing_gap = float(clip_duration) - words[-1].end
        if trailing_gap > min_gap:
            gaps.append(
                GapRange(
                    start=round(words[-1].end, 3),
                    end=round(float(clip_duration), 3),
                    duration=round(trailing_gap, 3),
                )
            )
    return gaps


def to_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hrs = millis // 3_600_000
    mins = (millis % 3_600_000) // 60_000
    secs = (millis % 60_000) // 1000
    ms = millis % 1000
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def write_word_level_srt(words: Sequence[WordTimestamp], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for idx, word in enumerate(words, start=1):
        lines.extend(
            [
                str(idx),
                f"{to_timestamp(word.start)} --> {to_timestamp(word.end)}",
                word.word,
                "",
            ]
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def build_subtitle_cues(words: Sequence[WordTimestamp], words_per_cue: int = 7) -> List[SubtitleCue]:
    cues: List[SubtitleCue] = []
    if not words:
        return cues

    index = 1
    for i in range(0, len(words), words_per_cue):
        chunk = words[i : i + words_per_cue]
        text = " ".join(word.word for word in chunk)
        cues.append(
            SubtitleCue(
                index=index,
                start=chunk[0].start,
                end=chunk[-1].end,
                text=text,
            )
        )
        index += 1

    return cues


def write_subtitles_srt(cues: Iterable[SubtitleCue], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for cue in cues:
        lines.extend(
            [
                str(cue.index),
                f"{to_timestamp(cue.start)} --> {to_timestamp(cue.end)}",
                cue.text,
                "",
            ]
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")
