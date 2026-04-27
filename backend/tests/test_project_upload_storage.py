from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient


def test_create_project_stores_uploads_in_user_project_video_dir(tmp_path, monkeypatch):
    aiofiles_module = types.ModuleType("aiofiles")

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

    def aiofiles_open(path, mode="r"):
        return AsyncFile(path, mode)

    aiofiles_module.open = aiofiles_open
    monkeypatch.setitem(sys.modules, "aiofiles", aiofiles_module)

    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    projects_dir = tmp_path / "projects"
    temp_dir = tmp_path / "temp"
    projects_dir.mkdir()
    temp_dir.mkdir()

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", projects_dir)
    monkeypatch.setattr(project_service, "temp_dir", temp_dir)
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "storage@example.com",
            "password": "password123",
            "full_name": "Storage User",
        },
    )
    assert signup_response.status_code == 200
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Storage Test"},
        files=[
            ("files", ("IMG_6157.MOV", b"video-one", "video/quicktime")),
            ("files", ("IMG_6158.MOV", b"video-two", "video/quicktime")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()["project"]
    project_id = payload["id"]
    video_dir = projects_dir / user_id / project_id / "video"
    project_file = projects_dir / user_id / project_id / "project.json"

    assert video_dir.exists()
    assert project_file.exists()
    assert (video_dir / "IMG_6157.MOV").read_bytes() == b"video-one"
    assert (video_dir / "IMG_6158.MOV").read_bytes() == b"video-two"
    assert payload["user_id"] == user_id
    assert payload["tracks"][0]["filename"] == "IMG_6157.MOV"
    assert payload["tracks"][0]["file_path"] == str(video_dir / "IMG_6157.MOV")
    assert payload["tracks"][1]["filename"] == "IMG_6158.MOV"
    assert payload["tracks"][1]["file_path"] == str(video_dir / "IMG_6158.MOV")


def test_create_project_queues_transcript_backfill(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    calls = []

    async def fake_backfill(project_id: str, user_id: str | None = None):
        calls.append((project_id, user_id))
        return None

    monkeypatch.setattr(project_service, "backfill_missing_transcripts", fake_backfill)

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "queued@example.com",
            "password": "password123",
            "full_name": "Queued User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Queued Transcript Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )

    assert response.status_code == 200
    payload = response.json()["project"]
    assert payload["tracks"][0]["metadata"]["transcript_status"] == "pending"
    assert calls == [(payload["id"], user_id)]


def test_create_project_prefers_media_creation_time_for_recorded_at(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service
    from backend.app.services.video_processor import VideoProcessor

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    async def fake_get_video_info(self, video_path: str):
        return {
            "streams": [
                {
                    "tags": {
                        "creation_time": "2026-04-24T17:03:00.000000Z",
                    }
                }
            ],
            "format": {
                "duration": "41.0",
                "tags": {},
            },
        }

    monkeypatch.setattr(VideoProcessor, "get_video_info", fake_get_video_info)

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "recorded@example.com",
            "password": "password123",
            "full_name": "Recorded User",
        },
    )
    token = signup_response.json()["access_token"]

    response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Recorded Time Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )

    assert response.status_code == 200
    payload = response.json()["project"]
    assert payload["tracks"][0]["recorded_at"] == "2026-04-24T17:03:00Z"


def test_get_project_keeps_missing_transcribable_track_pending(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "pending@example.com",
            "password": "password123",
            "full_name": "Pending User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Pending Transcript Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project_id = create_response.json()["project"]["id"]

    project_file = project_service.get_project_file(user_id, project_id, create=False)
    stored = json.loads(project_file.read_text(encoding="utf-8"))
    stored["tracks"][0]["transcription"] = None
    stored["tracks"][0]["metadata"]["transcript_status"] = "pending"
    project_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    project_service.projects.clear()

    response = client.get(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()["project"]
    assert payload["tracks"][0]["metadata"]["transcript_status"] == "pending"


def test_get_project_queues_backfill_for_pending_track(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    calls = []

    async def fake_backfill(project_id: str, user_id: str | None = None):
        calls.append((project_id, user_id))
        return None

    monkeypatch.setattr(project_service, "backfill_missing_transcripts", fake_backfill)

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "kick@example.com",
            "password": "password123",
            "full_name": "Kick User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Kick Transcript Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project_id = create_response.json()["project"]["id"]
    calls.clear()

    response = client.get(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert calls == [(project_id, user_id)]


def test_create_project_requires_authenticated_user(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    client = TestClient(app)
    response = client.post(
        "/api/projects/",
        data={"name": "Storage Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )

    assert response.status_code == 401


def test_track_media_route_serves_browser_preview(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service
    from backend.app.services.video_processor import video_processor

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "preview@example.com",
            "password": "password123",
            "full_name": "Preview User",
        },
    )
    token = signup_response.json()["access_token"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Preview Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project = create_response.json()["project"]
    track = project["tracks"][0]

    async def fake_ensure_browser_playable_video(source_path: str | Path, output_path: str | Path) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"preview-mp4")
        return str(target)

    monkeypatch.setattr(video_processor, "ensure_browser_playable_video", fake_ensure_browser_playable_video)

    media_response = client.get(
        f"/api/projects/{project['id']}/tracks/{track['id']}/media",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert media_response.status_code == 200
    assert media_response.content == b"preview-mp4"
    assert media_response.headers["content-type"].startswith("video/mp4")


def test_transcribe_missing_route_marks_legacy_tracks_pending(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    projects_dir = tmp_path / "projects"
    temp_dir = tmp_path / "temp"
    projects_dir.mkdir()
    temp_dir.mkdir()

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", projects_dir)
    monkeypatch.setattr(project_service, "temp_dir", temp_dir)
    project_service.projects.clear()

    calls = []

    async def fake_backfill(project_id: str, user_id: str | None = None):
        calls.append((project_id, user_id))
        return None

    monkeypatch.setattr(project_service, "backfill_missing_transcripts", fake_backfill)

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "legacy@example.com",
            "password": "password123",
            "full_name": "Legacy User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Legacy Transcript Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project_id = create_response.json()["project"]["id"]
    calls.clear()

    project_file = projects_dir / user_id / project_id / "project.json"
    stored = json.loads(project_file.read_text(encoding="utf-8"))
    stored["tracks"][0]["transcription"] = None
    stored["tracks"][0]["metadata"].pop("transcript_status", None)
    project_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    project_service.projects.clear()

    response = client.post(
        f"/api/projects/{project_id}/transcribe-missing",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()["project"]
    assert payload["tracks"][0]["metadata"]["transcript_status"] == "pending"
    assert calls == [(project_id, user_id)]


def test_get_project_sanitizes_existing_hallucinated_transcript(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    projects_dir = tmp_path / "projects"
    temp_dir = tmp_path / "temp"
    projects_dir.mkdir()
    temp_dir.mkdir()

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", projects_dir)
    monkeypatch.setattr(project_service, "temp_dir", temp_dir)
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "sanitize-existing@example.com",
            "password": "password123",
            "full_name": "Sanitize Existing User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Sanitize Existing Transcript"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project_id = create_response.json()["project"]["id"]

    project_file = projects_dir / user_id / project_id / "project.json"
    stored = json.loads(project_file.read_text(encoding="utf-8"))
    stored["tracks"][0]["duration"] = 10.0
    stored["tracks"][0]["transcription"] = {
        "text": "Thanks for watching!",
        "words": [],
        "language": "en",
        "segments": [
            {
                "start": 10.0,
                "end": 10.0,
                "text": "Thanks for watching!",
                "words": [],
            }
        ],
    }
    stored["tracks"][0]["has_voice"] = True
    stored["tracks"][0]["metadata"]["transcript_status"] = "completed"
    project_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    project_service.projects.clear()

    response = client.get(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()["project"]
    assert payload["tracks"][0]["transcription"] is None
    assert payload["tracks"][0]["has_voice"] is False
    assert payload["tracks"][0]["metadata"]["transcript_status"] == "pending"


def test_get_project_refreshes_existing_recorded_at_from_media_metadata_and_sorts_tracks(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service
    from backend.app.services.video_processor import VideoProcessor

    projects_dir = tmp_path / "projects"
    temp_dir = tmp_path / "temp"
    projects_dir.mkdir()
    temp_dir.mkdir()

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", projects_dir)
    monkeypatch.setattr(project_service, "temp_dir", temp_dir)
    project_service.projects.clear()

    async def fake_backfill(project_id: str, user_id: str | None = None):
        return None

    async def fake_get_video_info(self, video_path: str):
        name = Path(video_path).name
        creation_time = {
            "IMG_9585.MOV": "2026-04-24T10:03:00.000000Z",
            "IMG_9579.MOV": "2026-04-24T10:02:00.000000Z",
        }[name]
        return {
            "streams": [{"tags": {"creation_time": creation_time}}],
            "format": {"duration": "41.0", "tags": {"creation_time": creation_time}},
        }

    monkeypatch.setattr(project_service, "backfill_missing_transcripts", fake_backfill)
    monkeypatch.setattr(VideoProcessor, "get_video_info", fake_get_video_info)

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "refresh-recorded@example.com",
            "password": "password123",
            "full_name": "Refresh Recorded User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Refresh Recorded Times"},
        files=[
            ("files", ("IMG_9585.MOV", b"video-one", "video/quicktime")),
            ("files", ("IMG_9579.MOV", b"video-two", "video/quicktime")),
        ],
    )
    assert create_response.status_code == 200
    project_id = create_response.json()["project"]["id"]

    project_file = projects_dir / user_id / project_id / "project.json"
    stored = json.loads(project_file.read_text(encoding="utf-8"))
    stored["tracks"][0]["recorded_at"] = "2026-04-25T20:28:00Z"
    stored["tracks"][1]["recorded_at"] = "2026-04-25T20:29:00Z"
    project_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    project_service.projects.clear()

    response = client.get(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()["project"]
    assert [track["filename"] for track in payload["tracks"]] == ["IMG_9579.MOV", "IMG_9585.MOV"]
    assert payload["tracks"][0]["recorded_at"] == "2026-04-24T10:02:00Z"
    assert payload["tracks"][1]["recorded_at"] == "2026-04-24T10:03:00Z"


def test_add_tracks_skips_duplicate_uploads(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    projects_dir = tmp_path / "projects"
    temp_dir = tmp_path / "temp"
    projects_dir.mkdir()
    temp_dir.mkdir()

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", projects_dir)
    monkeypatch.setattr(project_service, "temp_dir", temp_dir)
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "dedupe@example.com",
            "password": "password123",
            "full_name": "Dedupe User",
        },
    )
    token = signup_response.json()["access_token"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Duplicate Upload Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project = create_response.json()["project"]

    add_response = client.post(
        f"/api/projects/{project['id']}/tracks",
        headers={"Authorization": f"Bearer {token}"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )

    assert add_response.status_code == 200
    payload = add_response.json()["project"]
    assert len(payload["tracks"]) == 1
    assert add_response.json()["message"] == "All selected clips were already uploaded"


def test_download_uses_resolved_output_path(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "download@example.com",
            "password": "password123",
            "full_name": "Download User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Download Test"},
        files=[("files", ("clip.mp4", b"video-one", "video/mp4"))],
    )
    assert create_response.status_code == 200
    project = create_response.json()["project"]
    project_id = project["id"]

    project_file = project_service.get_project_file(user_id, project_id, create=False)
    output_path = project_service.get_project_dir(user_id, project_id, create=True) / "output.mp4"
    output_path.write_bytes(b"final-video")

    project_payload = project_file.read_text(encoding="utf-8")
    project_payload = project_payload.replace('"status": "draft"', '"status": "completed"')
    project_payload = project_payload.replace('"output_path": null', f'"output_path": "{output_path}"')
    project_file.write_text(project_payload, encoding="utf-8")
    project_service.projects.clear()

    download_response = client.get(
        f"/api/projects/{project_id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert download_response.status_code == 200
    assert download_response.content == b"final-video"
    assert download_response.headers["content-type"].startswith("video/mp4")


def test_track_speech_filter_route_persists_per_track_edit_json(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.models.project import SpeechFilterArtifact, SpeechFilterCut, ZoomPreviewBeat
    from backend.app.services.ai_service import ai_service
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    async def fake_suggest_speech_filter_cuts(**kwargs):
        return SpeechFilterArtifact(
            project_id=kwargs["project_id"],
            track_id=kwargs["track_id"],
            filename=kwargs["filename"],
            status="completed",
            summary="2 suggested cuts, about 1.1s total.",
            cuts=[
                SpeechFilterCut(
                    start=0.45,
                    end=0.62,
                    duration=0.17,
                    reason="filler_word",
                    transcript="um",
                    confidence=0.93,
                ),
                SpeechFilterCut(
                    start=1.10,
                    end=2.03,
                    duration=0.93,
                    reason="long_pause",
                    transcript="",
                    confidence=0.98,
                ),
            ],
            zoom_beats=[
                ZoomPreviewBeat(
                    start=0.0,
                    end=3.4,
                    duration=3.4,
                    text="Here are the most important strategies.",
                    enabled=True,
                    scale=1.12,
                ),
                ZoomPreviewBeat(
                    start=3.4,
                    end=6.0,
                    duration=2.6,
                    text="for TOEFL listening task two conversations.",
                    enabled=False,
                    scale=1.12,
                ),
            ],
            generated_at=datetime(2026, 4, 26),
            source_word_count=8,
            model="test-model",
        )

    monkeypatch.setattr(ai_service, "suggest_speech_filter_cuts", fake_suggest_speech_filter_cuts)

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "speech-filter@example.com",
            "password": "password123",
            "full_name": "Speech Filter User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Speech Filter Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project = create_response.json()["project"]
    project_id = project["id"]
    track_id = project["tracks"][0]["id"]

    project_file = project_service.get_project_file(user_id, project_id, create=False)
    stored = json.loads(project_file.read_text(encoding="utf-8"))
    stored["tracks"][0]["duration"] = 6.0
    stored["tracks"][0]["transcription"] = {
        "text": "hello um hello there",
        "words": [
            {"word": "hello", "start": 0.0, "end": 0.4},
            {"word": "um", "start": 0.45, "end": 0.62},
            {"word": "hello", "start": 0.9, "end": 1.25},
            {"word": "there", "start": 2.03, "end": 2.45},
        ],
        "language": "en",
        "segments": [],
    }
    stored["tracks"][0]["metadata"]["transcript_status"] = "completed"
    project_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    project_service.projects.clear()

    filter_response = client.post(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert filter_response.status_code == 200
    payload = filter_response.json()
    assert payload["status"] == "completed"
    assert payload["model"] == "test-model"
    assert len(payload["cuts"]) == 2

    edit_file = project_service.get_track_edit_file(user_id, project_id, track_id)
    assert edit_file.exists()
    stored_artifact = json.loads(edit_file.read_text(encoding="utf-8"))
    assert stored_artifact["track_id"] == track_id
    assert stored_artifact["cuts"][0]["reason"] == "filler_word"
    assert stored_artifact["zoom_beats"][0]["enabled"] is True
    assert stored_artifact["zoom_beats"][0]["text"] == "Here are the most important strategies."

    refreshed_project = json.loads(project_file.read_text(encoding="utf-8"))
    metadata = refreshed_project["tracks"][0]["metadata"]
    assert metadata["speech_filter_status"] == "completed"
    assert metadata["speech_filter_cut_count"] == 2
    assert metadata["speech_filter_path"] == str(edit_file)


def test_get_track_speech_filter_returns_saved_artifact(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.models.project import SpeechFilterArtifact, SpeechFilterCut
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "speech-filter-get@example.com",
            "password": "password123",
            "full_name": "Speech Filter Get User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Speech Filter Existing Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project = create_response.json()["project"]
    project_id = project["id"]
    track_id = project["tracks"][0]["id"]

    artifact = SpeechFilterArtifact(
        project_id=project_id,
        track_id=track_id,
        filename="IMG_6157.MOV",
        status="completed",
        summary="1 suggested cut, about 0.2s total.",
        cuts=[
            SpeechFilterCut(
                start=0.4,
                end=0.6,
                duration=0.2,
                reason="filler_word",
                transcript="uh",
                confidence=0.91,
            )
        ],
        generated_at=datetime(2026, 4, 26),
        source_word_count=4,
        model="heuristic",
    )
    edit_file = project_service.get_track_edit_file(user_id, project_id, track_id)
    edit_file.parent.mkdir(parents=True, exist_ok=True)
    edit_file.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2), encoding="utf-8")

    get_response = client.get(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    payload = get_response.json()
    assert payload["track_id"] == track_id
    assert payload["cuts"][0]["transcript"] == "uh"


def test_patch_track_speech_filter_updates_saved_artifact(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.models.project import SpeechFilterArtifact, SpeechFilterCut, ZoomPreviewBeat
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "speech-filter-patch@example.com",
            "password": "password123",
            "full_name": "Speech Filter Patch User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Speech Filter Patch Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project = create_response.json()["project"]
    project_id = project["id"]
    track_id = project["tracks"][0]["id"]

    artifact = SpeechFilterArtifact(
        project_id=project_id,
        track_id=track_id,
        filename="IMG_6157.MOV",
        status="completed",
        summary="1 suggested cut, about 0.2s total.",
        cuts=[
            SpeechFilterCut(
                start=0.4,
                end=0.6,
                duration=0.2,
                reason="filler_word",
                transcript="uh",
                confidence=0.91,
            )
        ],
        zoom_beats=[
            ZoomPreviewBeat(
                start=0.0,
                end=3.0,
                duration=3.0,
                text="Original logical beat",
                enabled=True,
                scale=1.12,
            )
        ],
        generated_at=datetime(2026, 4, 26),
        source_word_count=4,
        model="heuristic",
    )
    edit_file = project_service.get_track_edit_file(user_id, project_id, track_id)
    edit_file.parent.mkdir(parents=True, exist_ok=True)
    edit_file.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2), encoding="utf-8")

    patch_response = client.patch(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "cuts": [
                {
                    "start": 1.0,
                    "end": 1.6,
                    "duration": 0.6,
                    "reason": "manual_adjustment",
                    "transcript": "retry phrase",
                    "confidence": 1.0,
                }
            ]
        },
    )

    assert patch_response.status_code == 200
    payload = patch_response.json()
    assert payload["cuts"][0]["start"] == 1.0
    assert payload["cuts"][0]["end"] == 1.6

    stored = json.loads(edit_file.read_text(encoding="utf-8"))
    assert stored["cuts"][0]["reason"] == "manual_adjustment"
    assert stored["cuts"][0]["transcript"] == "retry phrase"
    assert stored["zoom_beats"][0]["text"] == "Original logical beat"


def test_heuristic_zoom_beats_group_logical_parts_from_segments():
    from backend.app.services.ai_service import ai_service

    beats = ai_service._heuristic_zoom_beats(
        [],
        20.0,
        transcript_segments=[
            {"start": 0.0, "end": 1.2, "text": "Here are the most important strategies"},
            {"start": 1.25, "end": 2.6, "text": "for TOEFL Listening Task 2 conversations."},
            {"start": 3.4, "end": 4.6, "text": "In this task you'll hear conversations"},
            {"start": 4.65, "end": 6.7, "text": "between two speakers and then questions."},
        ],
    )

    assert len(beats) == 2
    assert beats[0].start == 0.0
    assert beats[0].end == 2.6
    assert "important strategies" in beats[0].text
    assert beats[0].enabled is True
    assert beats[1].start == 3.4
    assert beats[1].end == 6.7


def test_speech_filter_heuristic_detects_rephrased_restart_after_pause():
    from backend.app.models.transcription import WordTimestamp
    from backend.app.services.ai_service import ai_service

    words = [
        WordTimestamp(word="So", start=44.42, end=44.84),
        WordTimestamp(word="the", start=44.84, end=45.00),
        WordTimestamp(word="statement", start=45.00, end=45.44),
        WordTimestamp(word="is", start=45.44, end=45.96),
        WordTimestamp(word="usually", start=45.96, end=46.48),
        WordTimestamp(word="followed", start=46.48, end=46.96),
        WordTimestamp(word="by", start=46.96, end=47.46),
        WordTimestamp(word="a", start=47.46, end=47.72),
        WordTimestamp(word="question.", start=47.72, end=48.18),
        WordTimestamp(word="So", start=51.74, end=52.00),
        WordTimestamp(word="again", start=52.00, end=52.28),
        WordTimestamp(word="when", start=52.28, end=53.42),
        WordTimestamp(word="you", start=53.42, end=53.64),
        WordTimestamp(word="have", start=53.64, end=54.10),
        WordTimestamp(word="a", start=54.10, end=54.96),
        WordTimestamp(word="problem", start=54.96, end=55.32),
        WordTimestamp(word="it", start=55.32, end=55.66),
        WordTimestamp(word="usually", start=55.66, end=56.10),
        WordTimestamp(word="follows", start=56.10, end=56.56),
        WordTimestamp(word="with", start=56.56, end=56.88),
        WordTimestamp(word="the", start=56.88, end=57.04),
        WordTimestamp(word="solution.", start=57.04, end=57.42),
    ]

    cuts = ai_service._heuristic_speech_filter_cuts(words, 60.0, min_gap_seconds=1.0)
    reasons = {cut.reason for cut in cuts}

    assert "long_pause" in reasons
    assert "rephrased_restart" in reasons
    repeated = next(cut for cut in cuts if cut.reason == "rephrased_restart")
    assert repeated.start == 51.74
    assert repeated.end == 57.42


def test_speech_filter_heuristic_includes_leading_and_trailing_silence():
    from backend.app.models.transcription import WordTimestamp
    from backend.app.services.ai_service import ai_service

    words = [
        WordTimestamp(word="Here", start=6.664, end=7.38),
        WordTimestamp(word="are", start=7.38, end=7.56),
        WordTimestamp(word="speaker.", start=111.64, end=111.94),
    ]

    cuts = ai_service._heuristic_speech_filter_cuts(words, 117.0, min_gap_seconds=1.0)
    reasons = {cut.reason for cut in cuts}

    assert "leading_silence" in reasons
    assert "trailing_silence" in reasons
    leading = next(cut for cut in cuts if cut.reason == "leading_silence")
    trailing = next(cut for cut in cuts if cut.reason == "trailing_silence")
    assert leading.start == 0.0
    assert leading.end == 6.664
    assert trailing.start == 111.94
    assert trailing.end == 117.0


def test_find_gaps_includes_leading_and_trailing_edges():
    from backend.app.models.transcription import WordTimestamp
    from backend.app.services.pipeline_service import find_gaps

    words = [
        WordTimestamp(word="Hello", start=6.664, end=7.38),
        WordTimestamp(word="world", start=7.38, end=7.56),
        WordTimestamp(word="done", start=111.64, end=111.94),
    ]

    gaps = find_gaps(words, 1.0, clip_duration=117.0)

    assert gaps[0].start == 0.0
    assert gaps[0].end == 6.664
    assert gaps[-1].start == 111.94
    assert gaps[-1].end == 117.0


def test_align_transcription_to_detected_silence_repairs_broken_edge_timestamps():
    from backend.app.services.project_service import project_service

    sanitized = {
        "text": "Here are the most important strategies",
        "language": "en",
        "words": [
            {"word": "Here", "start": 0.0, "end": 7.38, "confidence": 0.639},
            {"word": "are", "start": 7.38, "end": 7.56, "confidence": 0.974},
            {"word": "speaker.", "start": 111.64, "end": 117.0, "confidence": 0.986},
        ],
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 13.2,
                "text": "Here are ...",
                "words": [
                    {"word": "Here", "start": 0.0, "end": 7.38, "confidence": 0.639},
                    {"word": "are", "start": 7.38, "end": 7.56, "confidence": 0.974},
                ],
            },
            {
                "id": 1,
                "start": 111.24,
                "end": 117.0,
                "text": "speaker.",
                "words": [
                    {"word": "speaker.", "start": 111.64, "end": 117.0, "confidence": 0.986},
                ],
            },
        ],
    }
    silence_ranges = [
        (0.0, 1.149),
        (1.181, 1.513),
        (1.614, 2.460),
        (2.705, 2.968),
        (3.416, 6.664),
        (111.943, 116.615),
    ]

    aligned = project_service._align_transcription_to_detected_silence(sanitized, silence_ranges, 117.0)

    assert aligned["words"][0]["start"] == 6.664
    assert aligned["segments"][0]["start"] == 6.664
    assert aligned["words"][-1]["end"] == 111.943
    assert aligned["segments"][-1]["end"] == 111.943


def test_build_transcription_windows_splits_long_speech_ranges():
    from backend.app.services.project_service import project_service

    windows = project_service._build_transcription_windows(
        [(0.0, 6.664), (13.255, 13.341), (111.943, 116.615)],
        117.0,
    )

    assert windows[:2] == [(6.664, 10.164), (10.164, 13.255)]
    assert windows[-1] == (116.615, 117.0)


def test_transcribe_audio_with_chunking_offsets_words(tmp_path, monkeypatch):
    from backend.app.services.project_service import project_service
    from backend.app.services.video_processor import VideoProcessor

    async def fake_extract_audio_segment(self, source_path, output_path, start, end):
        Path(output_path).write_bytes(f"{start:.3f}-{end:.3f}".encode("utf-8"))
        return str(output_path)

    async def fake_transcribe_audio(audio_data: bytes, filename: str, language: str = "en"):
        marker = audio_data.decode("utf-8")
        if marker.startswith("6.514-10.314"):
            return {
                "text": "Here are the most",
                "language": "en",
                "words": [
                    {"word": "Here", "start": 0.15, "end": 0.45},
                    {"word": "are", "start": 0.45, "end": 0.60},
                    {"word": "the", "start": 0.60, "end": 0.75},
                    {"word": "most", "start": 0.75, "end": 1.0},
                ],
                "segments": [],
            }
        if marker.startswith("10.014-13.405"):
            return {
                "text": "important strategies",
                "language": "en",
                "words": [
                    {"word": "important", "start": 0.15, "end": 0.65},
                    {"word": "strategies", "start": 0.65, "end": 1.10},
                ],
                "segments": [],
            }
        return {"text": "", "language": "en", "words": [], "segments": []}

    monkeypatch.setattr(VideoProcessor, "extract_audio_segment", fake_extract_audio_segment)
    monkeypatch.setattr("backend.app.services.project_service.transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(project_service, "temp_dir", tmp_path)

    processor = VideoProcessor()
    result = asyncio.run(
        project_service._transcribe_audio_with_chunking(
            "fake.wav",
            20.0,
            [(0.0, 6.664), (13.255, 13.341)],
            processor=processor,
        )
    )

    assert result is not None
    assert [word["word"] for word in result["words"]] == ["Here", "are", "the", "most", "important", "strategies"]
    assert result["words"][0]["start"] == 6.664
    assert result["words"][-1]["end"] == 11.114


def test_extract_track_geometry_from_media_info_handles_rotation():
    from backend.app.models.project import TrackOrientation
    from backend.app.services.project_service import project_service

    width, height, orientation = project_service._extract_track_geometry_from_media_info(
        [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [{"rotation": 90}],
            }
        ]
    )

    assert width == 1080
    assert height == 1920
    assert orientation == TrackOrientation.VERTICAL


def test_create_track_from_file_sets_orientation_and_dimensions(tmp_path, monkeypatch):
    from backend.app.models.project import TrackOrientation
    from backend.app.services.project_service import project_service
    from backend.app.services.video_processor import VideoProcessor

    clip_path = tmp_path / "portrait.mov"
    clip_path.write_bytes(b"video")

    async def fake_get_video_info(self, source_path: str):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "tags": {"rotate": "90"},
                }
            ],
            "format": {"duration": "12.5", "tags": {}},
        }

    monkeypatch.setattr(VideoProcessor, "get_video_info", fake_get_video_info)

    track = asyncio.run(project_service._create_track_from_file(str(clip_path), 0, None, {}))

    assert track.duration == 12.5
    assert track.width == 1080
    assert track.height == 1920
    assert track.orientation == TrackOrientation.VERTICAL


def test_project_tracks_default_to_natural_filename_order(tmp_path, monkeypatch):
    from backend.app.models.project import ProjectCreateRequest, ProjectSettings
    from backend.app.services.project_service import project_service

    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    first = tmp_path / "IMG_6158.MOV"
    second = tmp_path / "IMG_6157.MOV"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    request = ProjectCreateRequest(
        name="Natural sort",
        description=None,
        track_files=[str(first), str(second)],
        settings=ProjectSettings(),
    )

    project = asyncio.run(project_service.create_project_with_id("project-natural", request, user_id="user-1"))

    assert [track.filename for track in project.tracks] == ["IMG_6157.MOV", "IMG_6158.MOV"]
    assert [track.position for track in project.tracks] == [0, 1]


def test_reorder_project_tracks_persists_manual_order(tmp_path, monkeypatch):
    from backend.app.models.project import EditMode, ProjectCreateRequest, ProjectSettings
    from backend.app.services.project_service import project_service

    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    paths = []
    for name in ["IMG_6157.MOV", "IMG_6158.MOV", "IMG_6160.MOV"]:
        path = tmp_path / name
        path.write_bytes(name.encode("utf-8"))
        paths.append(str(path))

    request = ProjectCreateRequest(
        name="Manual reorder",
        description=None,
        track_files=paths,
        settings=ProjectSettings(),
    )
    project = asyncio.run(project_service.create_project_with_id("project-manual", request, user_id="user-1"))
    target_order = [project.tracks[2].id, project.tracks[0].id, project.tracks[1].id]

    reordered = asyncio.run(project_service.reorder_project_tracks(project.id, target_order, user_id="user-1"))

    assert reordered is not None
    assert reordered.settings.edit_mode == EditMode.MANUAL
    assert [track.id for track in reordered.tracks] == target_order
    assert [track.position for track in reordered.tracks] == [0, 1, 2]


def test_reorder_tracks_route_updates_project_positions(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "reorder@example.com",
            "password": "password123",
            "full_name": "Reorder User",
        },
    )
    token = signup_response.json()["access_token"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Reorder Test"},
        files=[
            ("files", ("IMG_6158.MOV", b"video-two", "video/quicktime")),
            ("files", ("IMG_6157.MOV", b"video-one", "video/quicktime")),
        ],
    )
    assert create_response.status_code == 200
    project = create_response.json()["project"]
    assert [track["filename"] for track in project["tracks"]] == ["IMG_6157.MOV", "IMG_6158.MOV"]

    reordered_ids = [project["tracks"][1]["id"], project["tracks"][0]["id"]]
    reorder_response = client.patch(
        f"/api/projects/{project['id']}/tracks/reorder",
        headers={"Authorization": f"Bearer {token}"},
        json={"track_ids": reordered_ids},
    )

    assert reorder_response.status_code == 200
    payload = reorder_response.json()["project"]
    assert [track["id"] for track in payload["tracks"]] == reordered_ids
    assert [track["position"] for track in payload["tracks"]] == [0, 1]


def test_track_visual_plan_route_persists_separate_worker_artifact(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.models.project import VisualAssetKind, VisualAssetStatus, VisualPlanArtifact, VisualPlanPart
    from backend.app.services.ai_service import ai_service
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service
    from backend.app.services.visual_worker_service import visual_worker_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    async def fake_suggest_visual_plan(**kwargs):
        return VisualPlanArtifact(
            project_id=kwargs["project_id"],
            track_id=kwargs["track_id"],
            filename=kwargs["filename"],
            status="completed",
            summary="2 visual parts planned: 1 animations, 1 web images.",
            parts=[
                VisualPlanPart(
                    start=0.0,
                    end=3.2,
                    duration=3.2,
                    text="Show the workflow overview",
                    visual_type=VisualAssetKind.ANIMATION,
                    prompt="Animated workflow blocks with arrows",
                    asset_status=VisualAssetStatus.PLANNED,
                ),
                VisualPlanPart(
                    start=3.2,
                    end=6.5,
                    duration=3.3,
                    text="Reference a laptop on desk",
                    visual_type=VisualAssetKind.WEB_IMAGE,
                    prompt="Editorial laptop on desk image",
                    search_query="laptop on desk editorial",
                    asset_status=VisualAssetStatus.PLANNED,
                ),
            ],
            generated_at=datetime(2026, 4, 26),
            source_word_count=12,
            model="visual-test-model",
        )

    async def fake_materialize(user_id, project_id, artifact):
        return artifact

    monkeypatch.setattr(ai_service, "suggest_visual_plan", fake_suggest_visual_plan)
    monkeypatch.setattr(visual_worker_service, "_materialize_web_images", fake_materialize)

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "visual-worker@example.com",
            "password": "password123",
            "full_name": "Visual Worker User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Visual Worker Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    assert create_response.status_code == 200
    project = create_response.json()["project"]
    project_id = project["id"]
    track_id = project["tracks"][0]["id"]

    project_file = project_service.get_project_file(user_id, project_id, create=False)
    stored = json.loads(project_file.read_text(encoding="utf-8"))
    stored["tracks"][0]["transcription"] = {
        "text": "Show the workflow overview. Reference a laptop on desk.",
        "words": [
            {"word": "Show", "start": 0.0, "end": 0.3},
            {"word": "workflow", "start": 0.3, "end": 0.7},
            {"word": "laptop", "start": 3.3, "end": 3.7},
        ],
        "language": "en",
        "segments": [],
    }
    stored["tracks"][0]["metadata"]["transcript_status"] = "completed"
    project_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    project_service.projects.clear()

    visual_response = client.post(
        f"/api/projects/{project_id}/tracks/{track_id}/visual-plan",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert visual_response.status_code == 200
    payload = visual_response.json()
    assert payload["model"] == "visual-test-model"
    assert len(payload["parts"]) == 2
    assert payload["parts"][1]["visual_type"] == "web_image"

    artifact_path = tmp_path / "projects" / user_id / project_id / "visual_worker" / f"{track_id}.json"
    assert artifact_path.exists()

    refreshed = json.loads(project_file.read_text(encoding="utf-8"))
    metadata = refreshed["tracks"][0]["metadata"]
    assert metadata["visual_worker_status"] == "completed"
    assert metadata["visual_worker_part_count"] == 2
    assert metadata["visual_worker_path"] == str(artifact_path)


def test_get_track_visual_plan_returns_saved_worker_artifact(tmp_path, monkeypatch):
    from backend.app.main import app
    from backend.app.models.project import VisualAssetKind, VisualAssetStatus, VisualPlanArtifact, VisualPlanPart
    from backend.app.services.auth_service import auth_service
    from backend.app.services.project_service import project_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(project_service, "temp_dir", tmp_path / "temp")
    project_service.projects.clear()

    client = TestClient(app)
    signup_response = client.post(
        "/api/auth/signup",
        json={
            "email": "visual-worker-get@example.com",
            "password": "password123",
            "full_name": "Visual Worker Get User",
        },
    )
    token = signup_response.json()["access_token"]
    user_id = signup_response.json()["user_id"]

    create_response = client.post(
        "/api/projects/",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": "Visual Worker Existing Test"},
        files=[("files", ("IMG_6157.MOV", b"video-one", "video/quicktime"))],
    )
    project = create_response.json()["project"]
    project_id = project["id"]
    track_id = project["tracks"][0]["id"]

    artifact = VisualPlanArtifact(
        project_id=project_id,
        track_id=track_id,
        filename="IMG_6157.MOV",
        status="completed",
        summary="1 visual parts planned: 1 animations, 0 web images.",
        parts=[
            VisualPlanPart(
                start=0.0,
                end=3.0,
                duration=3.0,
                text="Animate the main concept",
                visual_type=VisualAssetKind.ANIMATION,
                prompt="Simple explainer animation",
                asset_status=VisualAssetStatus.READY,
            )
        ],
        generated_at=datetime(2026, 4, 26),
        source_word_count=4,
        model="visual-heuristic",
    )
    artifact_path = tmp_path / "projects" / user_id / project_id / "visual_worker" / f"{track_id}.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2), encoding="utf-8")

    get_response = client.get(
        f"/api/projects/{project_id}/tracks/{track_id}/visual-plan",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_response.status_code == 200
    payload = get_response.json()
    assert payload["parts"][0]["visual_type"] == "animation"
    assert payload["parts"][0]["text"] == "Animate the main concept"


def test_visual_plan_sanitizer_preserves_structured_animation_metadata():
    from backend.app.services.ai_service import ai_service
    from backend.app.models.transcription import WordTimestamp

    words = [
        WordTimestamp(word="Listen", start=0.0, end=0.4),
        WordTimestamp(word="to", start=0.4, end=0.6),
        WordTimestamp(word="the", start=0.6, end=0.7),
        WordTimestamp(word="customer", start=0.7, end=1.2),
        WordTimestamp(word="problem", start=1.2, end=1.7),
    ]

    parts = ai_service._sanitize_visual_plan_parts(
        [
            {
                "start": 0.0,
                "end": 3.5,
                "text": "Listen to the customer problem.",
                "visual_type": "animation",
                "prompt": "Two-person conversation overlay with message flow",
                "search_query": None,
                "animation_kind": "conversation_flow",
                "title": "Listen first",
                "keywords": ["Listen", "Problem"],
                "scene_objects": ["speaker_a", "speaker_b", "message_arc"],
                "placement": "upper_left",
                "density": "light",
                "background_style": "transparent",
            }
        ],
        words,
        4.0,
    )

    assert len(parts) == 1
    part = parts[0]
    assert part.animation_kind == "conversation_flow"
    assert part.title == "Listen first"
    assert part.keywords == ["Listen", "Problem"]
    assert part.scene_objects == ["speaker_a", "speaker_b", "message_arc"]
    assert part.placement == "upper_left"
    assert part.density == "light"
    assert part.background_style == "transparent"
