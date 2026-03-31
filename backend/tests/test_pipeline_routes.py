from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def pipeline_test_context(tmp_path, monkeypatch):
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

    orchestrator_module = types.ModuleType("backend.app.pipeline.orchestrator")

    async def fake_run_pipeline(clip_paths, job_id, status_callback, output_dir):
        return f"{output_dir}/{job_id}/output.mp4"

    orchestrator_module.run_pipeline = fake_run_pipeline
    monkeypatch.setitem(sys.modules, "backend.app.pipeline.orchestrator", orchestrator_module)
    sys.modules.pop("backend.app.api.pipeline_api", None)

    pipeline_api = importlib.import_module("backend.app.api.pipeline_api")
    pipeline_api = importlib.reload(pipeline_api)

    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "outputs"
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(pipeline_api, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(pipeline_api, "OUTPUT_DIR", str(output_dir))
    pipeline_api.job_status.clear()

    app = FastAPI()
    app.include_router(pipeline_api.router)
    client = TestClient(app)
    return pipeline_api, client, upload_dir, output_dir


def test_pipeline_router_exposes_spec_routes(pipeline_test_context):
    _pipeline_api, client, _upload_dir, _output_dir = pipeline_test_context
    route_map = {}
    for route in client.app.routes:
        methods = {method for method in getattr(route, "methods", set()) if method not in {"HEAD", "OPTIONS"}}
        if methods:
            route_map[route.path] = methods

    assert route_map["/upload"] == {"POST"}
    assert route_map["/status/{job_id}"] == {"GET"}
    assert route_map["/status/{job_id}/poll"] == {"GET"}
    assert route_map["/outputs/{job_id}/output.mp4"] == {"GET"}
    assert route_map["/retry/{job_id}"] == {"POST"}


def test_upload_stores_files_and_starts_background_job(pipeline_test_context, monkeypatch):
    pipeline_api, client, upload_dir, _output_dir = pipeline_test_context
    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr(pipeline_api.asyncio, "create_task", fake_create_task)

    response = client.post(
        "/upload",
        files=[
            ("files", ("clip1.mp4", b"alpha", "video/mp4")),
            ("files", ("clip2.mov", b"beta", "video/quicktime")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    job_id = payload["job_id"]
    job_dir = upload_dir / job_id
    assert job_dir.exists()
    assert (job_dir / "clip1.mp4").read_bytes() == b"alpha"
    assert (job_dir / "clip2.mov").read_bytes() == b"beta"
    assert len(created_tasks) == 1


def test_status_poll_returns_current_or_unknown(pipeline_test_context):
    pipeline_api, client, _upload_dir, _output_dir = pipeline_test_context
    status_model = pipeline_api.PipelineStatus(
        job_id="job-1",
        stage="analysis",
        progress=0.5,
        message="Working",
    )
    pipeline_api.job_status["job-1"] = status_model

    current = client.get("/status/job-1/poll")
    assert current.status_code == 200
    assert current.json()["stage"] == "analysis"
    assert current.json()["progress"] == 0.5

    missing = client.get("/status/missing/poll")
    assert missing.status_code == 200
    assert missing.json() == {
        "job_id": "missing",
        "stage": "unknown",
        "progress": 0,
        "message": "Job not found",
    }


def test_status_stream_emits_done_payload(pipeline_test_context):
    pipeline_api, client, _upload_dir, _output_dir = pipeline_test_context
    pipeline_api.job_status["job-stream"] = pipeline_api.PipelineStatus(
        job_id="job-stream",
        stage="done",
        progress=1.0,
        message="Done!",
        output_url="/outputs/job-stream/output.mp4",
    )

    with client.stream("GET", "/status/job-stream") as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"stage":"done"' in body
    assert '"job_id":"job-stream"' in body


def test_download_output_handles_missing_and_existing_file(pipeline_test_context):
    _pipeline_api, client, _upload_dir, output_dir = pipeline_test_context

    missing = client.get("/outputs/job-missing/output.mp4")
    assert missing.status_code == 200
    assert missing.json() == {"error": "Output not found"}

    target_dir = output_dir / "job-123"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "output.mp4"
    target_path.write_bytes(b"video-bytes")

    existing = client.get("/outputs/job-123/output.mp4")
    assert existing.status_code == 200
    assert existing.content == b"video-bytes"
    assert existing.headers["content-type"].startswith("video/mp4")


def test_retry_requires_existing_job_with_video_files(pipeline_test_context, monkeypatch):
    pipeline_api, client, upload_dir, _output_dir = pipeline_test_context
    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return object()

    monkeypatch.setattr(pipeline_api.asyncio, "create_task", fake_create_task)

    missing = client.post("/retry/nope")
    assert missing.status_code == 200
    assert missing.json() == {"error": "Job not found"}

    empty_job_dir = upload_dir / "empty-job"
    empty_job_dir.mkdir(parents=True, exist_ok=True)
    (empty_job_dir / "notes.txt").write_text("ignore me")

    no_clips = client.post("/retry/empty-job")
    assert no_clips.status_code == 200
    assert no_clips.json() == {"error": "No clips found for this job"}

    ready_job_dir = upload_dir / "job-ready"
    ready_job_dir.mkdir(parents=True, exist_ok=True)
    (ready_job_dir / "b.mov").write_bytes(b"b")
    (ready_job_dir / "a.mp4").write_bytes(b"a")
    (ready_job_dir / "ignore.txt").write_text("x")

    retried = client.post("/retry/job-ready")
    assert retried.status_code == 200
    assert retried.json() == {
        "job_id": "job-ready",
        "message": "Retrying from last checkpoint",
    }
    assert len(created_tasks) == 1
