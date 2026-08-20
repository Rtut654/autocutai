"""Tests for the final multi-clip render.

The graph-building tests are pure and always run. The tests at the bottom
shell out to a real ffmpeg and are skipped when it is not installed - they
are the ones that would have caught the concat-demuxer failure on mixed
footage, so they matter most in CI where ffmpeg is present.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.services.final_render import (
    CANVAS,
    RenderClip,
    RenderPlan,
    build_filter_graph,
    canvas_for_aspect_ratio,
    clamp_time_to_output,
    escape_filter_path,
    remap_time_to_output,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


# --------------------------------------------------------------------------
# Canvas selection
# --------------------------------------------------------------------------


def test_vertical_is_the_default_canvas():
    assert canvas_for_aspect_ratio("vertical") == (1080, 1920)
    assert canvas_for_aspect_ratio("nonsense") == (1080, 1920)


def test_horizontal_canvas_is_landscape():
    assert canvas_for_aspect_ratio("horizontal") == (1920, 1080)


# --------------------------------------------------------------------------
# Graph building
# --------------------------------------------------------------------------


def _plan(*clips, **kwargs):
    return RenderPlan(clips=list(clips), width=1080, height=1920, **kwargs)


def test_single_clip_single_segment_builds_a_concat_of_one():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]))

    graph, extra_inputs, video, audio = build_filter_graph(plan)

    assert "concat=n=1:v=1:a=1" in graph
    assert "trim=start=0.000:end=5.000" in graph
    assert extra_inputs == []
    assert (video, audio) == ("cv", "ca")


def test_every_segment_is_normalised_onto_the_canvas():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]))

    graph, _, _, _ = build_filter_graph(plan)

    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in graph
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black" in graph
    assert "setsar=1" in graph
    assert "fps=30" in graph
    assert "format=yuv420p" in graph


def test_audio_is_normalised_to_one_rate_and_layout():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]))

    graph, _, _, _ = build_filter_graph(plan)

    assert "aresample=48000" in graph
    assert "aformat=sample_fmts=fltp:channel_layouts=stereo" in graph


def test_multiple_segments_from_one_clip_are_split_before_trimming():
    # Referencing an input pad twice is an ffmpeg error; it has to be split.
    plan = _plan(RenderClip("a.mp4", [(0.0, 2.0), (3.0, 5.0), (6.0, 8.0)]))

    graph, _, _, _ = build_filter_graph(plan)

    assert "split=3" in graph
    assert "asplit=3" in graph
    assert "concat=n=3:v=1:a=1" in graph


def test_multiple_clips_concatenate_in_order():
    plan = _plan(
        RenderClip("a.mp4", [(0.0, 2.0)]),
        RenderClip("b.mp4", [(0.0, 3.0)]),
    )

    graph, _, _, _ = build_filter_graph(plan)

    assert "[0:v]" in graph
    assert "[1:v]" in graph
    assert "concat=n=2:v=1:a=1" in graph


def test_silent_clip_gets_a_generated_audio_track():
    # Drone and timelapse footage has no audio stream, and concat requires
    # every input to carry the same number of streams.
    plan = _plan(
        RenderClip("a.mp4", [(0.0, 2.0)], has_audio=True),
        RenderClip("drone.mp4", [(0.0, 4.0)], has_audio=False),
    )

    graph, extra_inputs, _, _ = build_filter_graph(plan)

    assert "anullsrc" in " ".join(extra_inputs)
    assert "[2:a]atrim=duration=4.000" in graph
    assert "concat=n=2:v=1:a=1" in graph


def test_no_silent_source_is_added_when_every_clip_has_audio():
    plan = _plan(RenderClip("a.mp4", [(0.0, 2.0)], has_audio=True))

    _, extra_inputs, _, _ = build_filter_graph(plan)

    assert extra_inputs == []


def test_clips_with_no_usable_segments_are_left_out():
    plan = _plan(
        RenderClip("a.mp4", [(0.0, 2.0)]),
        RenderClip("empty.mp4", []),
    )

    graph, _, _, _ = build_filter_graph(plan)

    assert "concat=n=1:v=1:a=1" in graph


def test_segments_shorter_than_a_frame_are_dropped():
    plan = _plan(RenderClip("a.mp4", [(0.0, 2.0), (2.0, 2.01)]))

    graph, _, _, _ = build_filter_graph(plan)

    assert "concat=n=1:v=1:a=1" in graph


def test_an_empty_plan_is_an_error_not_a_silent_empty_video():
    with pytest.raises(ValueError, match="No usable video ranges"):
        build_filter_graph(_plan(RenderClip("a.mp4", [])))


def test_subtitles_are_burned_into_the_concatenated_stream():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]), subtitle_path="/tmp/subs.srt")

    graph, _, video_label, _ = build_filter_graph(plan)

    assert "subtitles=filename=" in graph
    assert video_label == "sv"


def test_subtitles_can_be_skipped_for_the_fallback_pass():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]), subtitle_path="/tmp/subs.srt")

    graph, _, video_label, _ = build_filter_graph(plan, include_subtitles=False)

    assert "subtitles=" not in graph
    assert video_label == "cv"


def test_subtitle_paths_with_special_characters_are_escaped():
    escaped = escape_filter_path("/tmp/my project [1]/subs.srt")

    assert "\\[" in escaped
    assert "\\]" in escaped


def test_total_duration_sums_usable_segments():
    plan = _plan(
        RenderClip("a.mp4", [(0.0, 2.0), (3.0, 4.0)]),
        RenderClip("b.mp4", [(1.0, 6.0)]),
    )

    assert plan.total_duration() == pytest.approx(8.0)


# --------------------------------------------------------------------------
# Timeline remapping
# --------------------------------------------------------------------------


def test_time_before_any_cut_maps_to_itself():
    assert remap_time_to_output(1.0, [(0.0, 5.0)]) == 1.0


def test_time_after_a_cut_shifts_back_by_the_removed_amount():
    segments = [(0.0, 2.0), (4.0, 8.0)]

    assert remap_time_to_output(5.0, segments) == 3.0


def test_time_inside_a_removed_range_has_no_output_position():
    assert remap_time_to_output(3.0, [(0.0, 2.0), (4.0, 8.0)]) is None


def test_clamping_snaps_a_removed_time_to_the_cut_point():
    assert clamp_time_to_output(3.0, [(0.0, 2.0), (4.0, 8.0)]) == 2.0


def test_time_past_the_last_segment_clamps_to_the_end():
    assert clamp_time_to_output(99.0, [(0.0, 2.0), (4.0, 8.0)]) == 6.0


def test_remapping_across_several_cuts_accumulates():
    segments = [(0.0, 1.0), (2.0, 3.0), (4.0, 5.0)]

    assert remap_time_to_output(4.5, segments) == 2.5


# --------------------------------------------------------------------------
# Real ffmpeg
# --------------------------------------------------------------------------


def _synth_clip(path: Path, *, width: int, height: int, fps: int, seconds: float, audio: bool) -> Path:
    """Generate a test clip with given geometry, the way a device would differ."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += ["-y", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.fixture
def processor():
    from backend.app.services.video_processor import VideoProcessor

    return VideoProcessor()


@requires_ffmpeg
def test_renders_mixed_resolutions_and_frame_rates(tmp_path, processor):
    """The case the concat demuxer could not handle.

    A vertical phone clip at 30fps and a horizontal drone clip at 24fps in
    one project is the normal shape of travel footage.
    """
    phone = _synth_clip(tmp_path / "phone.mp4", width=540, height=960, fps=30, seconds=2, audio=True)
    drone = _synth_clip(tmp_path / "drone.mp4", width=1280, height=720, fps=24, seconds=2, audio=False)

    plan = RenderPlan(
        clips=[
            RenderClip(str(phone), [(0.0, 2.0)], has_audio=True),
            RenderClip(str(drone), [(0.0, 2.0)], has_audio=False),
        ],
        width=1080,
        height=1920,
    )
    output = tmp_path / "out.mp4"

    asyncio.run(processor.render_final_video(plan, output))

    assert output.exists()
    info = _probe(output)
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(stream["codec_type"] == "audio" for stream in info["streams"])
    assert float(info["format"]["duration"]) == pytest.approx(4.0, abs=0.35)


@requires_ffmpeg
def test_cut_segments_are_actually_removed_from_the_output(tmp_path, processor):
    clip = _synth_clip(tmp_path / "a.mp4", width=640, height=480, fps=30, seconds=6, audio=True)
    plan = RenderPlan(
        clips=[RenderClip(str(clip), [(0.0, 2.0), (4.0, 6.0)], has_audio=True)],
        width=640,
        height=480,
    )
    output = tmp_path / "out.mp4"

    asyncio.run(processor.render_final_video(plan, output))

    duration = float(_probe(output)["format"]["duration"])
    assert duration == pytest.approx(4.0, abs=0.35)


@requires_ffmpeg
def test_a_silent_clip_still_produces_an_audio_track(tmp_path, processor):
    silent = _synth_clip(tmp_path / "silent.mp4", width=640, height=480, fps=30, seconds=2, audio=False)
    plan = RenderPlan(
        clips=[RenderClip(str(silent), [(0.0, 2.0)], has_audio=False)],
        width=640,
        height=480,
    )
    output = tmp_path / "out.mp4"

    asyncio.run(processor.render_final_video(plan, output))

    assert any(stream["codec_type"] == "audio" for stream in _probe(output)["streams"])


@requires_ffmpeg
def test_probe_has_audio_distinguishes_silent_clips(tmp_path, processor):
    with_audio = _synth_clip(tmp_path / "a.mp4", width=320, height=240, fps=30, seconds=1, audio=True)
    without_audio = _synth_clip(tmp_path / "b.mp4", width=320, height=240, fps=30, seconds=1, audio=False)

    assert asyncio.run(processor.probe_has_audio(with_audio)) is True
    assert asyncio.run(processor.probe_has_audio(without_audio)) is False


@requires_ffmpeg
def test_many_segments_render_without_blowing_the_command_line(tmp_path, processor):
    """A heavily pause-cut clip produces a very large filter graph."""
    clip = _synth_clip(tmp_path / "a.mp4", width=320, height=240, fps=30, seconds=10, audio=True)
    segments = [(index * 0.5, index * 0.5 + 0.3) for index in range(20)]
    plan = RenderPlan(clips=[RenderClip(str(clip), segments, has_audio=True)], width=320, height=240)
    output = tmp_path / "out.mp4"

    asyncio.run(processor.render_final_video(plan, output))

    duration = float(_probe(output)["format"]["duration"])
    assert duration == pytest.approx(6.0, abs=0.5)


@requires_ffmpeg
def test_subtitles_are_burned_in(tmp_path, processor):
    clip = _synth_clip(tmp_path / "a.mp4", width=640, height=480, fps=30, seconds=3, audio=True)
    subtitles = tmp_path / "subs.srt"
    subtitles.write_text(
        "1\n00:00:00,200 --> 00:00:02,000\nHello Lisbon\n\n",
        encoding="utf-8",
    )
    plan = RenderPlan(
        clips=[RenderClip(str(clip), [(0.0, 3.0)], has_audio=True)],
        width=640,
        height=480,
        subtitle_path=str(subtitles),
    )
    output = tmp_path / "out.mp4"

    asyncio.run(processor.render_final_video(plan, output))

    assert output.exists()
    assert output.stat().st_size > 0
