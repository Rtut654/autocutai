from __future__ import annotations

import asyncio
from pathlib import Path

from backend.app.models.project import GapRange, TrackType, VideoTrack
from backend.app.pipeline.audio_track import transcribe_clip
from backend.app.pipeline.edit_llm import generate_edit_plan
from backend.app.pipeline.visual_track import describe_uncertain_frames
from backend.app.services.pipeline_service import sanitize_transcription_payload
from backend.app.services.video_processor import VideoProcessor


def test_generate_edit_plan_falls_back_without_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    plan = asyncio.run(
        generate_edit_plan(
            transcripts=[
                {
                    "clip_id": "clip-1",
                    "source_file": "/tmp/clip-1.mov",
                    "segments": [
                        {"start": 0.0, "end": 1.2, "text": "hello"},
                        {"start": 2.0, "end": 3.5, "text": "world"},
                    ],
                    "words": [],
                }
            ],
            scene_map=[],
            gap_map=[],
            music_suggestions=[],
        )
    )

    assert plan.output_duration_estimate == "00:02.700"
    assert [clip.source_file for clip in plan.clips] == ["/tmp/clip-1.mov", "/tmp/clip-1.mov"]
    assert plan.clips[0].in_point == "00:00:00.000"
    assert plan.clips[1].out_point == "00:00:03.500"


def test_describe_uncertain_frames_falls_back_without_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    frames = [
        {
            "clip_id": "clip-1",
            "frame_idx": 0,
            "frame_path": "/tmp/frame.jpg",
            "scene_type": "market",
            "mood": "energetic",
            "scene_confidence": 0.3,
        }
    ]

    described = asyncio.run(describe_uncertain_frames(frames))

    assert described[0]["gpt_description"] == "market, energetic mood"


def test_burn_subtitles_uses_escaped_filename_filter(monkeypatch, tmp_path):
    processor = VideoProcessor()
    captured = {}

    async def fake_run(cmd):
        captured["cmd"] = cmd

    monkeypatch.setattr(processor, "_run_ffmpeg_command", fake_run)

    subtitle_path = tmp_path / "clip's,1.srt"
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")

    asyncio.run(processor._burn_subtitles("/tmp/input.mp4", subtitle_path, tmp_path))

    vf_arg = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert "subtitles=filename='" in vf_arg
    assert "\\'" in vf_arg
    assert "\\," in vf_arg


def test_transcribe_clip_uses_working_whisper_request_shape(tmp_path):
    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"fake-audio")

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "text": "hello world",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "text": "hello world",
                        "words": [
                            {"text": "hello", "start": 0.0, "end": 0.4, "confidence": 0.9},
                            {"text": "world", "start": 0.5, "end": 1.0, "confidence": 0.8},
                        ],
                    }
                ],
            }

    class DummyClient:
        def __init__(self):
            self.calls = []

        async def post(self, url, files=None, params=None, timeout=None):
            self.calls.append({"url": url, "files": files, "params": params, "timeout": timeout})
            return DummyResponse()

    client = DummyClient()
    result = asyncio.run(transcribe_clip(client, str(audio_path), "clip-1"))

    assert result["transcript"] == "hello world"
    assert [word["word"] for word in result["words"]] == ["hello", "world"]
    assert client.calls[0]["params"] == {"language": "en"}
    assert "file" in client.calls[0]["files"]


def test_process_project_skips_burn_when_subtitles_filter_is_unavailable(monkeypatch):
    processor = VideoProcessor()

    async def fake_process_track(track, project):
        return track

    async def fake_combine_tracks(tracks, apply_gap_cuts=False, output_dir=None):
        return "/tmp/final.mp4"

    async def fake_burn(video_path, subtitle_path, output_dir):
        raise RuntimeError("No such filter: 'subtitles'")

    monkeypatch.setattr(processor, "_process_track", fake_process_track)
    monkeypatch.setattr(processor, "_combine_tracks", fake_combine_tracks)
    monkeypatch.setattr(processor, "_burn_subtitles", fake_burn)

    class Settings:
        edit_mode = "chronological"
        generate_subtitles = True
        smart_pause_cutter = True

    class Pipeline:
        subtitle_path = "/tmp/subtitles.srt"

    class Track:
        position = 0
        local_gap_ranges = []

    class Project:
        settings = Settings()
        pipeline = Pipeline()
        tracks = [Track()]
        output_path = None

    monkeypatch.setattr(Path, "exists", lambda self: True)
    output = asyncio.run(processor.process_project(Project()))
    assert output == "/tmp/final.mp4"


def test_combine_tracks_uses_original_files_without_segment_artifacts(monkeypatch, tmp_path):
    processor = VideoProcessor()
    processor.temp_dir = tmp_path
    captured = {}

    async def fake_run(cmd):
        captured["cmd"] = cmd
        manifest_path = Path(cmd[cmd.index("-i") + 1])
        captured["manifest"] = manifest_path.read_text(encoding="utf-8")

    monkeypatch.setattr(processor, "_run_ffmpeg_command", fake_run)

    track = VideoTrack(
        id="track-1",
        type=TrackType.VIDEO,
        filename="clip.mov",
        file_path="/tmp/clip.mov",
        duration=12.0,
        position=0,
        local_gap_ranges=[
            GapRange(start=2.0, end=3.0, duration=1.0),
            GapRange(start=7.5, end=8.0, duration=0.5),
        ],
    )

    output = asyncio.run(processor._combine_tracks([track], apply_gap_cuts=True))

    assert output == str(tmp_path / "output.mp4")
    manifest_path = Path(captured["cmd"][captured["cmd"].index("-i") + 1])
    manifest = captured["manifest"]
    assert "file '/private/tmp/clip.mov'" in manifest
    assert "inpoint 3.000" in manifest
    assert "outpoint 7.500" in manifest
    assert "segment_" not in manifest
    assert "trimmed_" not in manifest
    assert not manifest_path.exists()


def test_sanitize_transcription_payload_drops_known_whisper_hallucination_at_clip_end():
    sanitized = sanitize_transcription_payload(
        {
            "text": "Thanks for watching!",
            "segments": [
                {
                    "start": 10.0,
                    "end": 10.0,
                    "text": "Thanks for watching!",
                    "words": [],
                }
            ],
        },
        clip_duration=10.0,
    )

    assert sanitized["text"] == ""
    assert sanitized["words"] == []
    assert sanitized["segments"] == []


def test_sanitize_transcription_payload_keeps_real_short_speech():
    sanitized = sanitize_transcription_payload(
        {
            "text": "keep going",
            "segments": [
                {
                    "start": 2.0,
                    "end": 2.8,
                    "text": "keep going",
                    "words": [
                        {"text": "keep", "start": 2.0, "end": 2.3},
                        {"text": "going", "start": 2.35, "end": 2.8},
                    ],
                }
            ],
        },
        clip_duration=10.0,
    )

    assert sanitized["text"] == "keep going"
    assert [word["word"] for word in sanitized["words"]] == ["keep", "going"]
    assert sanitized["segments"]
