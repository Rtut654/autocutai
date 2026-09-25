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
    assert (video, audio) == ("cv", "cleanaudio")


def test_every_segment_is_normalised_onto_the_canvas():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]))

    graph, _, _, _ = build_filter_graph(plan)

    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in graph
    assert "setsar=1" in graph
    assert "fps=30" in graph
    assert "format=yuv420p" in graph


def test_mismatched_clips_get_a_blurred_fill_by_default():
    """No black bars: TikTok down-ranks letterboxed video."""
    plan = _plan(RenderClip("drone.mp4", [(0.0, 5.0)], width=1920, height=1080))

    graph, _, _, _ = build_filter_graph(plan)

    assert "split=2" in graph
    assert "boxblur" in graph
    assert "overlay=(W-w)/2:(H-h)/2" in graph
    assert "color=black" not in graph


def test_the_blur_is_computed_at_quarter_resolution():
    plan = _plan(RenderClip("drone.mp4", [(0.0, 5.0)], width=1920, height=1080))

    graph, _, _, _ = build_filter_graph(plan)

    assert "scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur" in graph


def test_a_clip_that_already_matches_the_canvas_is_just_scaled():
    plan = _plan(RenderClip("phone.mp4", [(0.0, 5.0)], width=1080, height=1920))

    graph, _, _, _ = build_filter_graph(plan)

    assert "boxblur" not in graph
    assert "force_original_aspect_ratio=increase,crop=1080:1920" in graph


def test_near_matching_shapes_count_as_matching():
    # 2160x3840 and 1080x1920 are the same shape; 1080x1918 is within tolerance.
    assert RenderClip("a", [], width=2160, height=3840).matches_canvas(1080, 1920)
    assert RenderClip("a", [], width=1080, height=1918).matches_canvas(1080, 1920)
    assert not RenderClip("a", [], width=1920, height=1080).matches_canvas(1080, 1920)
    assert not RenderClip("a", [], width=None, height=None).matches_canvas(1080, 1920)


def test_black_bars_remain_available():
    plan = _plan(RenderClip("drone.mp4", [(0.0, 5.0)], width=1920, height=1080), fill_mode="black")

    graph, _, _, _ = build_filter_graph(plan)

    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black" in graph
    assert "boxblur" not in graph


def test_crop_fill_fills_the_frame():
    plan = _plan(RenderClip("drone.mp4", [(0.0, 5.0)], width=1920, height=1080), fill_mode="crop")

    graph, _, _, _ = build_filter_graph(plan)

    assert "force_original_aspect_ratio=increase,crop=1080:1920" in graph
    assert "boxblur" not in graph


def test_audio_is_cleaned_and_normalised_to_platform_loudness():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]))

    graph, _, _, audio = build_filter_graph(plan)

    assert "highpass=f=90" in graph
    assert "afftdn" in graph
    assert "loudnorm=I=-14" in graph
    assert audio == "cleanaudio"


def test_audio_cleanup_can_be_switched_off():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]), audio_cleanup=False)

    graph, _, _, audio = build_filter_graph(plan)

    assert "loudnorm" not in graph
    assert audio == "ca"


def test_an_edit_with_no_audio_at_all_skips_normalisation():
    """loudnorm cannot normalise pure silence; it computes an infinite gain."""
    plan = _plan(RenderClip("drone.mp4", [(0.0, 5.0)], has_audio=False))

    graph, _, _, audio = build_filter_graph(plan)

    assert "loudnorm" not in graph
    assert audio == "ca"


def test_captions_are_burned_with_the_bundled_fonts():
    plan = _plan(RenderClip("a.mp4", [(0.0, 5.0)]), subtitle_path="/tmp/c.ass", fonts_dir="/app/fonts")

    graph, _, video, _ = build_filter_graph(plan)

    assert "subtitles=filename='/tmp/c.ass':fontsdir='/app/fonts'" in graph
    assert video == "sv"


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


def _mean_volume_in_box(path: Path, *, x: int, y: int, w: int, h: int, at: float) -> float:
    """Average luma of a region of one frame."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-ss", str(at), "-i", str(path),
            "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y},format=gray,signalstats,metadata=print",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in result.stderr.splitlines():
        if "lavfi.signalstats.YAVG=" in line:
            return float(line.split("=")[-1])
    raise AssertionError("no signalstats in ffmpeg output")


def _integrated_loudness(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    summary = result.stderr[result.stderr.rfind("Summary:"):]
    for line in summary.splitlines():
        if line.strip().startswith("I:"):
            return float(line.split()[1])
    raise AssertionError("no integrated loudness in ffmpeg output")


@requires_ffmpeg
def test_blurred_fill_puts_picture_where_black_bars_used_to_be(tmp_path, processor):
    """The band above a landscape clip in a vertical frame is no longer black."""
    drone = _synth_clip(tmp_path / "drone.mp4", width=1280, height=720, fps=30, seconds=2, audio=True)
    clip = RenderClip(str(drone), [(0.0, 2.0)], has_audio=True, width=1280, height=720)

    blurred = tmp_path / "blur.mp4"
    black = tmp_path / "black.mp4"
    asyncio.run(processor.render_final_video(RenderPlan([clip], 540, 960, fill_mode="blur"), blurred))
    asyncio.run(processor.render_final_video(RenderPlan([clip], 540, 960, fill_mode="black"), black))

    top_band = dict(x=0, y=20, w=540, h=120, at=1.0)
    assert _mean_volume_in_box(black, **top_band) < 20
    assert _mean_volume_in_box(blurred, **top_band) > 40


@requires_ffmpeg
def test_output_loudness_lands_near_the_platform_target(tmp_path, processor):
    """A quiet recording comes out at about -14 LUFS, where TikTok/Reels/YouTube normalise."""
    quiet = tmp_path / "quiet.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=300:duration=6,volume=0.03",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(quiet),
        ],
        check=True,
        capture_output=True,
    )
    before = _integrated_loudness(quiet)
    output = tmp_path / "out.mp4"

    asyncio.run(
        processor.render_final_video(
            RenderPlan([RenderClip(str(quiet), [(0.0, 6.0)], has_audio=True)], 320, 240), output
        )
    )

    after = _integrated_loudness(output)
    assert before < -30
    assert after == pytest.approx(-14.0, abs=2.0)


@requires_ffmpeg
def test_a_muted_recording_still_renders(tmp_path, processor):
    """An audio track of pure silence defeats loudnorm; the render must still succeed."""
    muted = tmp_path / "muted.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "2", "-y", str(muted),
        ],
        check=True,
        capture_output=True,
    )
    output = tmp_path / "out.mp4"

    asyncio.run(
        processor.render_final_video(
            RenderPlan([RenderClip(str(muted), [(0.0, 2.0)], has_audio=True)], 320, 240), output
        )
    )

    assert any(stream["codec_type"] == "audio" for stream in _probe(output)["streams"])


@requires_ffmpeg
def test_styled_captions_render_with_the_bundled_font(tmp_path, processor):
    """Word-by-word captions actually appear, in the caption band, using our font."""
    from backend.app.models.transcription import WordTimestamp
    from backend.app.services.captions import FONTS_DIR, STYLES, write_ass

    clip = _synth_clip(tmp_path / "a.mp4", width=540, height=960, fps=30, seconds=3, audio=True)
    black = tmp_path / "black.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:size=540x960:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(black),
        ],
        check=True,
        capture_output=True,
    )
    words = [
        WordTimestamp(word="Hello", start=0.2, end=0.8),
        WordTimestamp(word="Lisbon", start=0.9, end=1.6),
    ]
    captions = write_ass(words, tmp_path / "c.ass", width=540, height=960, style=STYLES["bold"], duration=3.0)

    with_captions = tmp_path / "captioned.mp4"
    asyncio.run(
        processor.render_final_video(
            RenderPlan(
                [RenderClip(str(black), [(0.0, 3.0)], has_audio=True, width=540, height=960)],
                540,
                960,
                subtitle_path=str(captions),
                fonts_dir=str(FONTS_DIR),
            ),
            with_captions,
        )
    )

    # The line sits with its baseline 22% up from the bottom (y ~749 of 960),
    # clear of the like/comment column and caption tray platforms draw.
    text_band = dict(x=0, y=690, w=540, h=70)
    showing = _mean_volume_in_box(with_captions, **text_band, at=1.2)
    finished = _mean_volume_in_box(with_captions, **text_band, at=2.8)
    bottom_ui_zone = _mean_volume_in_box(with_captions, x=0, y=780, w=540, h=180, at=1.2)

    assert showing > 15            # white text on a black frame
    assert finished < 2            # gone once the line is over
    assert bottom_ui_zone < 2      # nothing drawn under the platform UI
    assert clip.exists()
