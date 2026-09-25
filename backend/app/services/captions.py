"""Burned-in captions in the style short-form viewers expect.

Most short-form video is watched muted, so captions carry the narration.
The modern look is a few words at a time with the spoken word highlighted
as it is said, which keeps the eye moving with the voice. That is what the
"bold" style does and why it is the default.

Captions are written as ASS subtitles and burned in by libass. Highlighting
uses one event per spoken word: each event shows the whole line, with the
active word styled differently. That is simpler and more reliable across
libass versions than karaoke tags.

Timings must already be on the rendered timeline (after cuts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from ..models.transcription import WordTimestamp

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

# Gap in speech that starts a new caption line even if it is not full.
PHRASE_BREAK_SECONDS = 0.6
# How long a line lingers after its last word, when nothing follows it.
LINGER_SECONDS = 0.4


def ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB plus alpha (0 opaque .. 255 clear) to ASS &HAABBGGRR."""
    value = hex_rgb.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


@dataclass(frozen=True)
class CaptionStyle:
    key: str
    label: str
    font: str
    # Font size as a fraction of the canvas's shorter side.
    size: float
    text_colour: str
    highlight_colour: Optional[str]
    outline_colour: str = "#000000"
    outline: float = 0.0
    shadow: float = 0.0
    # 1 = outline and shadow, 3 = opaque box behind the text.
    border_style: int = 1
    box_colour: str = "#000000"
    box_alpha: int = 0x60
    uppercase: bool = False
    strip_punctuation: bool = False
    max_words: int = 4
    max_chars: int = 28
    highlight_scale: int = 100


STYLES = {
    "bold": CaptionStyle(
        key="bold",
        label="Bold",
        font="Montserrat ExtraBold",
        size=0.085,
        text_colour="#FFFFFF",
        highlight_colour="#FFE500",
        outline=6,
        shadow=2,
        uppercase=True,
        strip_punctuation=True,
        max_words=3,
        max_chars=22,
        highlight_scale=112,
    ),
    "boxed": CaptionStyle(
        key="boxed",
        label="Boxed",
        font="Montserrat ExtraBold",
        size=0.066,
        text_colour="#FFFFFF",
        highlight_colour="#FFE500",
        border_style=3,
        outline=14,
        box_alpha=0x40,
        strip_punctuation=True,
        max_words=4,
        max_chars=26,
    ),
    "clean": CaptionStyle(
        key="clean",
        label="Clean",
        font="Montserrat SemiBold",
        size=0.056,
        text_colour="#FFFFFF",
        highlight_colour=None,
        outline=3,
        shadow=1,
        max_words=8,
        max_chars=42,
    ),
}

DEFAULT_STYLE = "bold"
CAPTION_STYLE_KEYS = ("bold", "boxed", "clean", "none")


def resolve_style(key: Optional[str]) -> Optional[CaptionStyle]:
    """The style for a key, or None when captions are switched off."""
    if key == "none":
        return None
    return STYLES.get(key or DEFAULT_STYLE, STYLES[DEFAULT_STYLE])


_ASS_UNSAFE = re.compile(r"[{}\\]")
_TRAILING_PUNCTUATION = re.compile(r"[.,;:!?…]+$")


def _display_word(word: str, style: CaptionStyle) -> str:
    text = _ASS_UNSAFE.sub("", word).strip()
    if style.strip_punctuation:
        # Social captions drop trailing punctuation: each line is only a
        # few words, and a lone comma or full stop reads as noise.
        text = _TRAILING_PUNCTUATION.sub("", text)
    if style.uppercase:
        text = text.upper()
    return text


def group_into_lines(words: Sequence[WordTimestamp], style: CaptionStyle) -> List[List[WordTimestamp]]:
    """Split words into short caption lines.

    A line ends when it is full, when the speaker pauses, or at the end of a
    sentence - so a line never straddles two thoughts.
    """
    lines: List[List[WordTimestamp]] = []
    current: List[WordTimestamp] = []
    chars = 0

    for word in words:
        text = word.word.strip()
        if not text:
            continue
        paused = bool(current) and (word.start - current[-1].end) > PHRASE_BREAK_SECONDS
        too_long = bool(current) and (
            len(current) >= style.max_words or chars + 1 + len(text) > style.max_chars
        )
        if paused or too_long:
            lines.append(current)
            current, chars = [], 0
        current.append(word)
        chars += (1 if chars else 0) + len(text)
        if re.search(r"[.!?…]$", text):
            lines.append(current)
            current, chars = [], 0

    if current:
        lines.append(current)
    return lines


def _timestamp(seconds: float) -> str:
    centis = max(0, int(round(seconds * 100)))
    hours, rem = divmod(centis, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _line_text(words: Sequence[WordTimestamp], style: CaptionStyle, active: Optional[int]) -> str:
    parts = []
    for index, word in enumerate(words):
        text = _display_word(word.word, style)
        if not text:
            continue
        if active is not None and index == active and style.highlight_colour:
            scale = style.highlight_scale
            parts.append(
                f"{{\\c{ass_colour(style.highlight_colour)}\\fscx{scale}\\fscy{scale}}}{text}{{\\r}}"
            )
        else:
            parts.append(text)
    return " ".join(parts)


def build_ass(
    words: Sequence[WordTimestamp],
    *,
    width: int,
    height: int,
    style: CaptionStyle,
    duration: Optional[float] = None,
) -> str:
    """ASS subtitle document for a rendered video.

    ``duration`` is the rendered video's length; no caption runs past it.
    """
    font_size = max(18, int(round(min(width, height) * style.size)))
    vertical = height > width
    # Keep clear of the like/comment/share column and caption tray that
    # TikTok, Reels and Shorts draw over the bottom of a vertical video.
    margin_v = int(round(height * (0.22 if vertical else 0.08)))
    margin_h = int(round(width * 0.08))

    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            (
                f"Style: Caption,{style.font},{font_size},"
                f"{ass_colour(style.text_colour)},{ass_colour(style.text_colour)},"
                f"{ass_colour(style.outline_colour)},"
                f"{ass_colour(style.box_colour, style.box_alpha if style.border_style == 3 else 0x80)},"
                f"0,0,0,0,100,100,0,0,{style.border_style},{style.outline},{style.shadow},"
                f"2,{margin_h},{margin_h},{margin_v},1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )

    events: List[str] = []
    lines = group_into_lines(words, style)
    for line_index, line in enumerate(lines):
        next_line_start = lines[line_index + 1][0].start if line_index + 1 < len(lines) else None
        line_end = line[-1].end + LINGER_SECONDS
        if next_line_start is not None:
            line_end = min(line_end, next_line_start)
        line_end = max(line_end, line[-1].end)
        if duration is not None:
            line_end = min(line_end, duration)

        if style.highlight_colour:
            for word_index, word in enumerate(line):
                start = line[0].start if word_index == 0 else word.start
                end = line[word_index + 1].start if word_index + 1 < len(line) else line_end
                if duration is not None:
                    end = min(end, duration)
                if end - start < 0.02:
                    continue
                events.append(
                    f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},Caption,,0,0,0,,"
                    f"{_line_text(line, style, word_index)}"
                )
        else:
            events.append(
                f"Dialogue: 0,{_timestamp(line[0].start)},{_timestamp(line_end)},Caption,,0,0,0,,"
                f"{_line_text(line, style, None)}"
            )

    return header + "\n" + "\n".join(events) + "\n"


def write_ass(
    words: Iterable[WordTimestamp],
    path: Path,
    *,
    width: int,
    height: int,
    style: CaptionStyle,
    duration: Optional[float] = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = build_ass(list(words), width=width, height=height, style=style, duration=duration)
    path.write_text(document, encoding="utf-8")
    return path
