from __future__ import annotations

import sys
import types
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
