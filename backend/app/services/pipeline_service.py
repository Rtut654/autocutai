"""Pipeline helpers: merge words, detect gaps, generate subtitles and SRT."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from ..models.project import GapRange, SubtitleCue
from ..models.transcription import WordTimestamp


@dataclass
class TimelineWord:
    word: str
    start: float
    end: float
    confidence: float | None = None


def normalize_words(raw_words: Sequence[dict]) -> List[WordTimestamp]:
    result: List[WordTimestamp] = []
    for raw in raw_words:
        text = str(raw.get("word") or raw.get("text") or "").strip()
        if not text:
            continue
        start = float(raw.get("start", 0.0))
        end = float(raw.get("end", start))
        result.append(
            WordTimestamp(word=text, start=max(0.0, start), end=max(start, end), confidence=raw.get("confidence"))
        )
    return result


def find_gaps(words: Sequence[WordTimestamp], min_gap: float = 1.0) -> List[GapRange]:
    if len(words) < 2:
        return []

    gaps: List[GapRange] = []
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
