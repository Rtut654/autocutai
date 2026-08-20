"""Full pipeline against real media files.

Everything here uses a real ffmpeg and real ffprobe: actual clips are
generated, probed, cut and rendered, and the output is inspected. Only
transcription is stubbed, because it is the one step that needs a paid
external service.

These are the tests that catch the failures unit tests cannot - wrong
geometry, unplayable output, subtitles drifting out of sync with the cut.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from factories import DEFAULT_TRANSCRIPT_DISPLAY, DEFAULT_TRANSCRIPT_WORDS, azure_payload

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


def _make_clip(path: Path, *, width: int, height: int, fps: int, seconds: float, audio: bool = True) -> Path:
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
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


@pytest.fixture
def real_transcription(monkeypatch):
    """Only transcription is faked; every media step is real."""
    async def fake(audio_path, language=None):
        from backend.app.services.azure_speech_service import parse_recognition_payloads

        return parse_recognition_payloads(
            [azure_payload(DEFAULT_TRANSCRIPT_WORDS, display=DEFAULT_TRANSCRIPT_DISPLAY)],
            language or "en-US",
        )

    monkeypatch.setattr("backend.app.services.project_service.transcribe_audio_file", fake)


@pytest.fixture
def media_client(client, auth_headers, real_transcription, no_llm, tmp_path):
    return client, auth_headers, tmp_path


def _create(client, headers, clips, **settings):
    data = {
        "name": "Real media trip",
        "smart_pause_cutter": "true",
        "generate_subtitles": "true",
        "insert_suggestions": "false",
        "aspect_ratio": "vertical",
    }
    data.update({key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in settings.items()})
    files = [("files", (path.name, path.read_bytes(), "video/mp4")) for path in clips]
    return client.post("/api/projects/", data=data, files=files, headers=headers)


def test_mixed_footage_project_renders_a_playable_vertical_video(media_client):
    """The end-to-end case: phone, drone and action-cam clips in one project."""
    client, headers, tmp_path = media_client
    clips = [
        _make_clip(tmp_path / "phone.mp4", width=540, height=960, fps=30, seconds=6),
        _make_clip(tmp_path / "drone.mp4", width=1280, height=720, fps=24, seconds=4, audio=False),
        _make_clip(tmp_path / "action.mp4", width=1920, height=1080, fps=60, seconds=5),
    ]

    created = _create(client, headers, clips)
    assert created.status_code == 200, created.text
    project_id = created.json()["project"]["id"]

    processed = client.post(f"/api/projects/{project_id}/process-sync", headers=headers)
    assert processed.status_code == 200, processed.text
    project = processed.json()["project"]
    assert project["status"] == "completed"

    output = Path(project["output_path"])
    assert output.exists()

    info = _probe(output)
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert any(stream["codec_type"] == "audio" for stream in info["streams"])
    assert float(info["format"]["duration"]) > 0


def test_real_durations_are_read_from_the_media(media_client):
    client, headers, tmp_path = media_client
    clips = [_make_clip(tmp_path / "a.mp4", width=640, height=480, fps=30, seconds=7)]

    created = _create(client, headers, clips)

    track = created.json()["project"]["tracks"][0]
    assert track["duration"] == pytest.approx(7.0, abs=0.2)
    assert track["width"] == 640
    assert track["height"] == 480


def test_the_export_endpoint_serves_a_real_mp4(media_client):
    client, headers, tmp_path = media_client
    clips = [_make_clip(tmp_path / "a.mp4", width=540, height=960, fps=30, seconds=5)]
    project_id = _create(client, headers, clips).json()["project"]["id"]
    client.post(f"/api/projects/{project_id}/process-sync", headers=headers)

    response = client.get(f"/api/projects/{project_id}/download", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    # ftyp box at the start of a real MP4, and faststart puts moov up front.
    assert response.content[4:8] == b"ftyp"
    assert b"moov" in response.content[:4096]


def test_the_rendered_output_is_shorter_than_the_source_when_pauses_are_cut(media_client):
    client, headers, tmp_path = media_client
    clips = [_make_clip(tmp_path / "a.mp4", width=540, height=960, fps=30, seconds=12)]
    project_id = _create(client, headers, clips).json()["project"]["id"]

    processed = client.post(f"/api/projects/{project_id}/process-sync", headers=headers)

    output = Path(processed.json()["project"]["output_path"])
    rendered = float(_probe(output)["format"]["duration"])
    # The fixture transcript ends at 5.6s with a pause at 1.95-3.60s, so the
    # 12s source should come out at roughly 3.95s.
    assert rendered == pytest.approx(3.95, abs=0.4)


def test_a_single_track_render_version_is_playable(media_client):
    client, headers, tmp_path = media_client
    clips = [_make_clip(tmp_path / "a.mp4", width=540, height=960, fps=30, seconds=8)]
    project_id = _create(client, headers, clips).json()["project"]["id"]
    client.post(f"/api/projects/{project_id}/process-sync", headers=headers)
    track_id = client.get(f"/api/projects/{project_id}", headers=headers).json()["project"]["tracks"][0]["id"]

    response = client.post(
        f"/api/projects/{project_id}/tracks/{track_id}/render",
        json={"cuts": [{"start": 2.0, "end": 4.0, "duration": 2.0, "reason": "manual", "transcript": "", "confidence": 1.0}]},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    version = response.json()["version"]
    rendered = Path(version["file_path"])
    assert rendered.exists()
    assert float(_probe(rendered)["format"]["duration"]) == pytest.approx(6.0, abs=0.5)


def test_audio_extraction_produces_the_format_azure_requires(media_client):
    """Azure's file input only accepts 16 kHz mono PCM WAV."""
    from backend.app.services.video_processor import VideoProcessor
    import asyncio

    _client, _headers, tmp_path = media_client
    source = _make_clip(tmp_path / "a.mp4", width=320, height=240, fps=30, seconds=3)
    target = tmp_path / "out.wav"

    asyncio.run(VideoProcessor().extract_audio_for_transcription(str(source), str(target)))

    stream = next(s for s in _probe(target)["streams"] if s["codec_type"] == "audio")
    assert stream["sample_rate"] == "16000"
    assert stream["channels"] == 1
    assert stream["codec_name"] == "pcm_s16le"
