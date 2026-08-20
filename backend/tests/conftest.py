"""Shared fixtures.

The pipeline tests run without ffmpeg and without an Azure subscription:
``fake_media_tools`` replaces every subprocess call in ``VideoProcessor`` with a
deterministic stand-in, and ``fake_transcription`` replaces the Azure call.
That keeps the suite fast and hermetic while still exercising the real
orchestration, ordering, cut maths and persistence code.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from factories import DEFAULT_TRANSCRIPT_DISPLAY, DEFAULT_TRANSCRIPT_WORDS, azure_payload


# --------------------------------------------------------------------------
# Storage + auth isolation
# --------------------------------------------------------------------------


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """Point every stateful service at a temp directory."""
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    projects_dir = tmp_path / "projects"
    temp_dir = tmp_path / "temp"
    projects_dir.mkdir()
    temp_dir.mkdir()

    monkeypatch.setattr(project_service, "projects_dir", projects_dir)
    monkeypatch.setattr(project_service, "temp_dir", temp_dir)
    project_service.projects.clear()

    # Never touch the checked-in auth state file.
    auth_service.configure(tmp_path / "auth_state.json")
    auth_service.reset_for_tests()

    yield types.SimpleNamespace(root=tmp_path, projects_dir=projects_dir, temp_dir=temp_dir)

    project_service.projects.clear()


@pytest.fixture
def client(isolated_storage):
    from backend.app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_token(client):
    response = client.post(
        "/api/auth/signup",
        json={"email": "pipeline@example.com", "password": "password123", "full_name": "Pipeline User"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


# --------------------------------------------------------------------------
# Media tool doubles
# --------------------------------------------------------------------------


class FakeMediaCalls:
    """Records what the pipeline asked ffmpeg to do."""

    def __init__(self) -> None:
        self.audio_extractions: List[str] = []
        self.segment_renders: List[Dict[str, Any]] = []
        self.styled_renders: List[Dict[str, Any]] = []
        self.subtitle_burns: List[Dict[str, Any]] = []
        self.music_mixes: List[Dict[str, Any]] = []
        self.sfx_mixes: List[Dict[str, Any]] = []
        self.combines: List[Dict[str, Any]] = []


@pytest.fixture
def media_calls() -> FakeMediaCalls:
    return FakeMediaCalls()


@pytest.fixture
def fake_media_tools(monkeypatch, media_calls):
    """Replace every ffmpeg/ffprobe call with an in-process stand-in.

    Each stand-in writes a small placeholder file so downstream ``exists()``
    checks and FileResponse handling behave the same as in production.
    """
    from backend.app.services.video_processor import VideoProcessor

    def _touch(path) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x00fake-media\x00")
        return str(target)

    async def extract_audio_for_transcription(self, source_path, output_path):
        media_calls.audio_extractions.append(str(source_path))
        return _touch(output_path)

    async def extract_audio_segment(self, source_path, output_path, start, end):
        return _touch(output_path)

    async def detect_silence_ranges(self, *args, **kwargs):
        return []

    async def render_source_segments(self, *, source_path, segments, output_path):
        media_calls.segment_renders.append({"source": str(source_path), "segments": list(segments)})
        return _touch(output_path)

    async def render_styled_track(self, *, source_path, output_path, width, height, zoom_beats, visual_parts):
        media_calls.styled_renders.append(
            {
                "source": str(source_path),
                "width": width,
                "height": height,
                "zoom_beats": len(zoom_beats),
                "visual_parts": len(visual_parts),
            }
        )
        return _touch(output_path)

    async def mix_visual_sfx(self, *, video_input, output_path, cue_times):
        media_calls.sfx_mixes.append({"cues": list(cue_times)})
        return _touch(output_path)

    async def generate_background_music_track(self, *, output_path, duration, preset):
        media_calls.music_mixes.append({"duration": duration, "preset": preset})
        return _touch(output_path)

    async def mix_background_music(self, *, video_input, music_input, output_path, music_volume, ducking):
        media_calls.music_mixes.append({"volume": music_volume, "ducking": ducking})
        return _touch(output_path)

    async def burn_subtitles(self, *, video_input, subtitle_path, output_path):
        media_calls.subtitle_burns.append({"subtitles": str(subtitle_path)})
        return _touch(output_path)

    async def ensure_browser_playable_video(self, source_path, output_path):
        return _touch(output_path)

    async def get_video_info(self, video_path):
        return {
            "format": {"duration": "12.0", "tags": {"creation_time": "2026-04-24T10:00:00.000000Z"}},
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1080,
                    "height": 1920,
                    "duration": "12.0",
                    "tags": {"rotate": "0"},
                }
            ],
        }

    async def probe_has_audio(self, source_path):
        return True

    async def render_final_video(self, plan, output_path):
        # Exercise the real graph builder so a broken filter chain fails here
        # rather than silently at render time on a real device.
        from backend.app.services.final_render import build_filter_graph

        graph, extra_inputs, video_label, audio_label = build_filter_graph(plan)
        media_calls.combines.append(
            {
                "clips": [clip.source_path for clip in plan.clips],
                "segments": [clip.usable_segments() for clip in plan.clips],
                "width": plan.width,
                "height": plan.height,
                "subtitle_path": plan.subtitle_path,
                "graph": graph,
                "extra_inputs": extra_inputs,
                "video_label": video_label,
                "audio_label": audio_label,
            }
        )
        return _touch(output_path)

    for name, impl in [
        ("extract_audio_for_transcription", extract_audio_for_transcription),
        ("extract_audio_segment", extract_audio_segment),
        ("detect_silence_ranges", detect_silence_ranges),
        ("render_source_segments", render_source_segments),
        ("render_styled_track", render_styled_track),
        ("mix_visual_sfx", mix_visual_sfx),
        ("generate_background_music_track", generate_background_music_track),
        ("mix_background_music", mix_background_music),
        ("burn_subtitles", burn_subtitles),
        ("ensure_browser_playable_video", ensure_browser_playable_video),
        ("get_video_info", get_video_info),
        ("probe_has_audio", probe_has_audio),
        ("render_final_video", render_final_video),
    ]:
        monkeypatch.setattr(VideoProcessor, name, impl)

    return media_calls


# --------------------------------------------------------------------------
# Transcription doubles
# --------------------------------------------------------------------------


@pytest.fixture
def fake_transcription(monkeypatch):
    """Stub the Azure call with a fixed, realistic transcript."""
    calls: List[Dict[str, Any]] = []

    async def fake_transcribe_file(audio_path, language=None):
        calls.append({"audio_path": str(audio_path), "language": language})
        from backend.app.services.azure_speech_service import parse_recognition_payloads

        return parse_recognition_payloads(
            [azure_payload(DEFAULT_TRANSCRIPT_WORDS, display=DEFAULT_TRANSCRIPT_DISPLAY)],
            language or "en-US",
        )

    monkeypatch.setattr(
        "backend.app.services.project_service.transcribe_audio_file",
        fake_transcribe_file,
    )
    return calls


@pytest.fixture
def no_llm(monkeypatch):
    """Force every AI helper down its deterministic heuristic path."""
    from backend.app.services.ai_service import ai_service

    monkeypatch.setattr(ai_service, "api_key", "")
    return ai_service


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


@pytest.fixture
def make_clip(tmp_path):
    """Create a placeholder media file on disk."""
    created: List[Path] = []

    def _make(name: str = "clip.mp4", size: int = 2048) -> Path:
        path = tmp_path / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * size)
        created.append(path)
        return path

    return _make


@pytest.fixture
def aiofiles_stub(monkeypatch):
    """Minimal aiofiles so tests do not need the real package."""
    if "aiofiles" in sys.modules:
        return sys.modules["aiofiles"]

    module = types.ModuleType("aiofiles")

    class AsyncFile:
        def __init__(self, path, mode):
            self._file = open(path, mode)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self._file.close()

        async def write(self, data):
            return self._file.write(data)

        async def read(self, size=-1):
            return self._file.read(size)

    module.open = lambda path, mode="r": AsyncFile(path, mode)
    monkeypatch.setitem(sys.modules, "aiofiles", module)
    return module


def run_async(coro):
    """Run a coroutine from a sync test."""
    return asyncio.run(coro)
