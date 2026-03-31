"""MoviePy 2.0.1 utilities for covers, title cards, transitions, and overlays."""

import os
from pathlib import Path

try:
    from moviepy import (
        VideoFileClip, ImageClip, TextClip, CompositeVideoClip,
        concatenate_videoclips, AudioFileClip, ColorClip,
        vfx, afx
    )
    HAS_MOVIEPY = True
except ImportError:
    HAS_MOVIEPY = False


def make_title_card(
    text: str,
    duration: float = 3.0,
    size: tuple = (1920, 1080),
    bg_color: tuple = (0, 0, 0),
    font_size: int = 80,
    output_path: str = None,
) -> str:
    """
    Generate a title card clip (black background, centered white text).
    Used for location titles, date overlays, chapter markers.
    Returns path to rendered MP4.
    """
    if not HAS_MOVIEPY:
        raise RuntimeError("MoviePy is not installed")

    bg = ColorClip(size=size, color=bg_color, duration=duration)
    txt = TextClip(
        text=text,
        font_size=font_size,
        color="white",
        font="Arial",
        method="label",
    ).with_position("center").with_duration(duration)

    txt = txt.with_effects([vfx.FadeIn(0.4), vfx.FadeOut(0.4)])

    clip = CompositeVideoClip([bg, txt])
    clip.write_videofile(output_path, fps=30, codec="libx264",
                         audio=False, logger=None)
    clip.close()
    return output_path


def make_image_cover(
    image_path: str,
    duration: float = 4.0,
    size: tuple = (1920, 1080),
    caption: str = None,
    output_path: str = None,
) -> str:
    """
    Turn a still image into a video cover slide.
    Optionally adds a caption at the bottom.
    Returns path to rendered MP4.
    """
    if not HAS_MOVIEPY:
        raise RuntimeError("MoviePy is not installed")

    img = ImageClip(image_path).resized(size).with_duration(duration)
    img = img.with_effects([vfx.FadeIn(0.5), vfx.FadeOut(0.5)])

    layers = [img]

    if caption:
        cap = TextClip(
            text=caption,
            font_size=48,
            color="white",
            font="Arial",
            method="label",
        ).with_position(("center", 0.85), relative=True).with_duration(duration)
        layers.append(cap)

    clip = CompositeVideoClip(layers, size=size)
    clip.write_videofile(output_path, fps=30, codec="libx264",
                         audio=False, logger=None)
    clip.close()
    return output_path


def apply_crossfade(
    clip_a_path: str,
    clip_b_path: str,
    overlap_seconds: float = 0.5,
    output_path: str = None,
) -> str:
    """
    Render a cross-dissolve between the end of clip_a and start of clip_b.
    Returns path to the joined clip with transition baked in.
    """
    if not HAS_MOVIEPY:
        raise RuntimeError("MoviePy is not installed")

    a = VideoFileClip(clip_a_path)
    b = VideoFileClip(clip_b_path).with_effects(
        [vfx.CrossFadeIn(overlap_seconds)]
    )

    result = concatenate_videoclips([a, b], method="compose")
    result.write_videofile(output_path, fps=30, codec="libx264",
                           audio_codec="aac", logger=None)
    a.close()
    b.close()
    result.close()
    return output_path


def apply_fade_to_black(
    clip_path: str,
    fade_out_duration: float = 0.4,
    output_path: str = None,
) -> str:
    """Apply a fade-to-black at the end of a clip."""
    if not HAS_MOVIEPY:
        raise RuntimeError("MoviePy is not installed")

    clip = VideoFileClip(clip_path)
    clip = clip.with_effects([vfx.FadeOut(fade_out_duration)])
    clip.write_videofile(output_path, fps=30, codec="libx264",
                         audio_codec="aac", logger=None)
    clip.close()
    return output_path


def add_lower_third(
    clip_path: str,
    text: str,
    appear_at: float = 1.0,
    duration: float = 3.0,
    output_path: str = None,
) -> str:
    """
    Overlay a lower-third text label on an existing clip.
    Used for location names, dates, speaker IDs.
    """
    if not HAS_MOVIEPY:
        raise RuntimeError("MoviePy is not installed")

    clip = VideoFileClip(clip_path)
    w, h = clip.size

    txt = TextClip(
        text=text,
        font_size=42,
        color="white",
        font="Arial",
        method="label",
        stroke_color="black",
        stroke_width=2,
    ).with_position((60, h - 120)).with_start(appear_at).with_duration(duration)
    txt = txt.with_effects([vfx.FadeIn(0.3), vfx.FadeOut(0.3)])

    result = CompositeVideoClip([clip, txt])
    result.write_videofile(output_path, fps=clip.fps, codec="libx264",
                           audio_codec="aac", logger=None)
    clip.close()
    result.close()
    return output_path
