"""Building the final multi-clip render.

Travel footage is the hard case for assembly: one project mixes phone,
action-camera and drone clips with different resolutions, frame rates,
rotations and pixel formats, and some clips have no audio at all. The ffmpeg
concat *demuxer* requires every input to match exactly, so it either fails or
produces garbled output on exactly this input.

This module builds a concat *filter* graph instead. Every segment is
normalised to one canvas, one frame rate, one pixel format and one audio
layout before concatenation, and the whole thing - trim, scale, concat and
subtitle burn-in - runs as a single encode rather than one pass per stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

Segment = Tuple[float, float]

# Output canvases. Vertical is the default for social travel content.
CANVAS = {
    "vertical": (1080, 1920),
    "horizontal": (1920, 1080),
}
DEFAULT_FPS = 30
DEFAULT_SAMPLE_RATE = 48000


@dataclass
class RenderClip:
    """One source file and the ranges of it that survive into the output."""

    source_path: str
    segments: List[Segment] = field(default_factory=list)
    has_audio: bool = True

    def usable_segments(self, min_duration: float = 0.04) -> List[Segment]:
        return [(start, end) for start, end in self.segments if end - start >= min_duration]


@dataclass
class RenderPlan:
    """Everything the renderer needs, with no reference back to the project."""

    clips: List[RenderClip]
    width: int
    height: int
    fps: int = DEFAULT_FPS
    subtitle_path: Optional[str] = None

    def total_duration(self) -> float:
        return sum(end - start for clip in self.clips for start, end in clip.usable_segments())


def canvas_for_aspect_ratio(aspect_ratio: str) -> Tuple[int, int]:
    return CANVAS.get(str(aspect_ratio), CANVAS["vertical"])


def escape_filter_path(value: str) -> str:
    """Escape a path for use inside a filter argument."""
    escaped = value.replace("\\", "\\\\")
    for char in (":", "'", ",", "[", "]"):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def build_filter_graph(plan: RenderPlan, *, include_subtitles: bool = True) -> Tuple[str, List[str], str, str]:
    """Return (filter_graph, extra_inputs, video_label, audio_label).

    ``extra_inputs`` are ffmpeg arguments that must be appended to the input
    list - currently a silent source for clips that carry no audio track.
    """
    width, height, fps = plan.width, plan.height, plan.fps
    chains: List[str] = []
    concat_labels: List[str] = []
    extra_inputs: List[str] = []

    # One silent source covers every audio-less clip.
    silent_input_index: Optional[int] = None
    needs_silence = any(not clip.has_audio for clip in plan.clips if clip.usable_segments())
    if needs_silence:
        silent_input_index = len(plan.clips)
        extra_inputs.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=channel_layout=stereo:sample_rate={DEFAULT_SAMPLE_RATE}",
            ]
        )

    part = 0
    for index, clip in enumerate(plan.clips):
        segments = clip.usable_segments()
        if not segments:
            continue

        # A filter input can only be consumed once, so fan it out first.
        video_pads = [f"v{index}s{n}" for n in range(len(segments))]
        chains.append(f"[{index}:v]split={len(segments)}[{']['.join(video_pads)}]"
                      if len(segments) > 1 else f"[{index}:v]null[{video_pads[0]}]")

        audio_pads: List[str] = []
        if clip.has_audio:
            audio_pads = [f"a{index}s{n}" for n in range(len(segments))]
            chains.append(f"[{index}:a]asplit={len(segments)}[{']['.join(audio_pads)}]"
                          if len(segments) > 1 else f"[{index}:a]anull[{audio_pads[0]}]")

        for n, (start, end) in enumerate(segments):
            duration = end - start
            video_label = f"v{part}"
            audio_label = f"a{part}"

            # Normalise onto the shared canvas: letterbox rather than crop, so
            # a vertical phone clip and a horizontal drone clip can sit in the
            # same timeline without losing content.
            chains.append(
                f"[{video_pads[n]}]"
                f"trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={fps},format=yuv420p"
                f"[{video_label}]"
            )

            if clip.has_audio:
                chains.append(
                    f"[{audio_pads[n]}]"
                    f"atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                    f"aresample={DEFAULT_SAMPLE_RATE},"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                    f"[{audio_label}]"
                )
            else:
                chains.append(
                    f"[{silent_input_index}:a]"
                    f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo"
                    f"[{audio_label}]"
                )

            concat_labels.extend([f"[{video_label}]", f"[{audio_label}]"])
            part += 1

    if not concat_labels:
        raise ValueError("No usable video ranges were available to render")

    segment_count = part
    chains.append(f"{''.join(concat_labels)}concat=n={segment_count}:v=1:a=1[cv][ca]")

    video_out = "cv"
    if include_subtitles and plan.subtitle_path:
        chains.append(f"[cv]subtitles=filename='{escape_filter_path(plan.subtitle_path)}'[sv]")
        video_out = "sv"

    return ";".join(chains), extra_inputs, video_out, "ca"


def remap_time_to_output(time_value: float, segments: Sequence[Segment]) -> Optional[float]:
    """Map a source timestamp onto the rendered timeline.

    Returns None when the timestamp falls inside a removed range.
    """
    elapsed = 0.0
    for start, end in segments:
        if time_value < start:
            return None
        if time_value <= end:
            return round(elapsed + (time_value - start), 3)
        elapsed += end - start
    return None


def clamp_time_to_output(time_value: float, segments: Sequence[Segment]) -> float:
    """Like ``remap_time_to_output`` but snaps removed times to the cut point."""
    elapsed = 0.0
    for start, end in segments:
        if time_value < start:
            return round(elapsed, 3)
        if time_value <= end:
            return round(elapsed + (time_value - start), 3)
        elapsed += end - start
    return round(elapsed, 3)
