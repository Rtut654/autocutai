from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def hybrid_client(tmp_path, monkeypatch):
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

    project_dir = tmp_path / "projects"
    temp_dir = tmp_path / "temp"
    project_dir.mkdir()
    temp_dir.mkdir()

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()
    monkeypatch.setattr(project_service, "projects_dir", project_dir)
    monkeypatch.setattr(project_service, "temp_dir", temp_dir)
    project_service.projects.clear()

    client = TestClient(app)
    signup = client.post(
        "/api/auth/signup",
        json={
            "email": "hybrid@example.com",
            "password": "password123",
            "full_name": "Hybrid User",
        },
    )
    token = signup.json()["access_token"]
    user_id = signup.json()["user_id"]
    return client, project_dir, temp_dir, token, user_id


def test_hybrid_analyze_creates_project_and_manifest(hybrid_client):
    client, project_dir, _temp_dir, token, user_id = hybrid_client

    response = client.post(
        "/api/projects/hybrid-analyze",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Tokyo Hybrid",
            "render_strategy": "on_device",
            "settings": {
                "smart_pause_cutter": True,
                "generate_subtitles": True,
                "insert_suggestions": True,
                "min_gap_seconds": 1.0,
            },
            "tracks": [
                {
                    "filename": "clip-a.mov",
                    "recorded_at": "2026-03-31T12:00:00Z",
                    "source_reference": "file:///clips/clip-a.mov",
                    "thumbnail_reference": "file:///thumbs/clip-a.jpg",
                    "shot_boundaries": [0.0, 1.25, 2.8],
                    "metadata": {"location": "Tokyo"},
                    "transcription": {
                        "text": "hello from tokyo",
                        "language": "en",
                        "words": [
                            {"word": "hello", "start": 0.0, "end": 0.5},
                            {"word": "from", "start": 0.6, "end": 0.9},
                            {"word": "tokyo", "start": 2.5, "end": 2.9},
                        ],
                        "segments": [
                            {
                                "start": 0.0,
                                "end": 2.9,
                                "text": "hello from tokyo",
                                "words": [
                                    {"word": "hello", "start": 0.0, "end": 0.5},
                                    {"word": "from", "start": 0.6, "end": 0.9},
                                    {"word": "tokyo", "start": 2.5, "end": 2.9},
                                ],
                            }
                        ],
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()["project"]
    project_id = payload["id"]

    assert payload["user_id"] == user_id
    assert payload["status"] == "completed"
    assert payload["output_path"] is None
    assert payload["pipeline"]["combined_transcript"] == "hello from tokyo"
    assert payload["pipeline"]["gap_ranges"][0]["start"] == 0.9
    assert payload["pipeline"]["render_plan"]["render_strategy"] == "on_device"
    assert payload["pipeline"]["render_plan"]["requires_source_upload_for_server_render"] is False
    assert payload["pipeline"]["render_plan"]["track_decisions"][0]["source_reference"] == "file:///clips/clip-a.mov"
    assert payload["pipeline"]["render_plan"]["track_decisions"][0]["keep_ranges"] == [
        {"start": 0.0, "end": 0.9},
        {"start": 2.5, "end": 2.9},
    ]

    project_root = project_dir / user_id / project_id
    assert (project_root / "project.json").exists()
    assert (project_root / "transcript" / f"{project_id}_word_level.srt").exists()
    assert (project_root / "transcript" / f"{project_id}_subtitles.srt").exists()

    manifest = client.get(
        f"/api/projects/{project_id}/render-manifest",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert manifest.status_code == 200
    manifest_payload = manifest.json()
    assert manifest_payload["project_id"] == project_id
    assert manifest_payload["render_plan"]["track_decisions"][0]["thumbnail_reference"] == "file:///thumbs/clip-a.jpg"
    assert manifest_payload["subtitle_cues"]


def test_render_manifest_requires_auth_and_hides_missing_project(hybrid_client):
    _client, _project_dir, _temp_dir, _token, _user_id = hybrid_client

    from backend.app.main import app

    unauthorized = TestClient(app).get("/api/projects/missing/render-manifest")
    assert unauthorized.status_code == 401
