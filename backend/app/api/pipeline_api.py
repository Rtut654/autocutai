"""Pipeline API endpoints: upload, status (SSE), download, retry."""

import asyncio
import os
import uuid

import aiofiles
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import StreamingResponse

from ..pipeline.orchestrator import run_pipeline
from ..models.schemas import PipelineStatus

router = APIRouter(tags=["pipeline"])

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/video_uploads")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/tmp/video_outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# In-memory status store (use Redis in production)
job_status: dict[str, PipelineStatus] = {}


async def _update_status(job_id: str, status: PipelineStatus):
    job_status[job_id] = status


async def _run_pipeline_job(job_id: str, clip_paths: list[str]) -> None:
    try:
        await run_pipeline(
            clip_paths,
            job_id,
            status_callback=lambda s: _update_status(job_id, s),
            output_dir=OUTPUT_DIR,
        )
    except Exception as exc:
        if isinstance(exc, FileNotFoundError):
            message = "ffmpeg is required but was not found in PATH"
        else:
            message = str(exc) or "Pipeline failed"
        await _update_status(
            job_id,
            PipelineStatus(
                job_id=job_id,
                stage="error",
                progress=0.0,
                message=message,
            ),
        )


@router.post("/upload")
async def upload_clips(files: list[UploadFile] = File(...)):
    """Accept multiple video clips, return a job_id."""
    job_id = str(uuid.uuid4())
    job_dir = f"{UPLOAD_DIR}/{job_id}"
    os.makedirs(job_dir)

    clip_paths = []
    for file in files:
        dest = f"{job_dir}/{file.filename}"
        async with aiofiles.open(dest, "wb") as f:
            await f.write(await file.read())
        clip_paths.append(dest)

    await _update_status(
        job_id,
        PipelineStatus(
            job_id=job_id,
            stage="queued",
            progress=0.0,
            message="Upload complete. Waiting to start pipeline...",
        ),
    )

    # Kick off pipeline as background task
    asyncio.create_task(_run_pipeline_job(job_id, clip_paths))

    return {"job_id": job_id}


@router.get("/status/{job_id}")
async def get_status(job_id: str):
    """SSE endpoint - streams progress updates to client."""
    async def event_stream():
        last_stage = None
        while True:
            status = job_status.get(job_id)
            if status and status.stage != last_stage:
                last_stage = status.stage
                yield f"data: {status.model_dump_json()}\n\n"
                if status.stage in {"done", "error"}:
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/status/{job_id}/poll")
async def poll_status(job_id: str):
    """Simple polling endpoint - returns current status as JSON."""
    status = job_status.get(job_id)
    if status:
        return status.model_dump()
    return {"job_id": job_id, "stage": "unknown", "progress": 0, "message": "Job not found"}


@router.get("/outputs/{job_id}/output.mp4")
async def download_output(job_id: str):
    """Serve the rendered video file."""
    path = f"{OUTPUT_DIR}/{job_id}/output.mp4"

    if not os.path.exists(path):
        return {"error": "Output not found"}

    async def file_stream():
        async with aiofiles.open(path, "rb") as f:
            while chunk := await f.read(1024 * 64):
                yield chunk

    return StreamingResponse(file_stream(), media_type="video/mp4")


@router.post("/retry/{job_id}")
async def retry_job(job_id: str):
    """
    Retry a failed job. The orchestrator reloads StepCache and skips
    every step that already has a completed entry.
    """
    job_dir = f"{UPLOAD_DIR}/{job_id}"
    if not os.path.exists(job_dir):
        return {"error": "Job not found"}

    clip_paths = [
        f"{job_dir}/{f}" for f in sorted(os.listdir(job_dir))
        if f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm'))
    ]

    if not clip_paths:
        return {"error": "No clips found for this job"}

    await _update_status(
        job_id,
        PipelineStatus(
            job_id=job_id,
            stage="queued",
            progress=0.0,
            message="Retry queued. Waiting to restart pipeline...",
        ),
    )
    asyncio.create_task(_run_pipeline_job(job_id, clip_paths))

    return {"job_id": job_id, "message": "Retrying from last checkpoint"}
