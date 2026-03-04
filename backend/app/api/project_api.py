"""API endpoints for video editing project management."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..models.project import (
    AspectRatio,
    EditMode,
    ProcessingStatus,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectSettings,
    ProjectUpdateRequest,
)
from ..services.project_service import project_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("/", response_model=ProjectResponse)
async def create_project(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    aspect_ratio: AspectRatio = Form(default=AspectRatio.HORIZONTAL),
    edit_mode: EditMode = Form(default=EditMode.CHRONOLOGICAL),
    remove_duplicates: bool = Form(default=False),
    smart_pause_cutter: bool = Form(default=True),
    generate_subtitles: bool = Form(default=True),
    insert_suggestions: bool = Form(default=True),
    min_gap_seconds: float = Form(default=1.0),
    capture_times_json: Optional[str] = Form(default=None),
    metadata_json: Optional[str] = Form(default=None),
    files: List[UploadFile] = File(...),
):
    try:
        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)

        saved_files: List[str] = []
        for upload in files:
            if not upload.filename:
                raise HTTPException(status_code=400, detail="Each file needs a filename")
            destination = temp_dir / f"{uuid4().hex}_{upload.filename}"
            destination.write_bytes(await upload.read())
            saved_files.append(str(destination))

        capture_times: Optional[List[Optional[datetime]]] = None
        if capture_times_json:
            raw = json.loads(capture_times_json)
            capture_times = [datetime.fromisoformat(v) if v else None for v in raw]

        track_metadata: Optional[List[Dict[str, Any]]] = None
        if metadata_json:
            track_metadata = json.loads(metadata_json)

        settings = ProjectSettings(
            aspect_ratio=aspect_ratio,
            edit_mode=edit_mode,
            remove_duplicates=remove_duplicates,
            smart_pause_cutter=smart_pause_cutter,
            generate_subtitles=generate_subtitles,
            insert_suggestions=insert_suggestions,
            min_gap_seconds=min_gap_seconds,
        )

        request = ProjectCreateRequest(
            name=name,
            description=description,
            track_files=saved_files,
            track_capture_times=capture_times,
            track_metadata=track_metadata,
            settings=settings,
        )

        project = await project_service.create_project(request)
        return ProjectResponse(project=project, message="Project created successfully")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create project: {exc}") from exc


@router.get("/", response_model=ProjectListResponse)
async def list_projects(user_id: Optional[str] = None, limit: int = 50, offset: int = 0):
    projects = await project_service.list_projects(user_id)
    total = len(projects)
    return ProjectListResponse(projects=projects[offset : offset + limit], total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(project=project, message="Project retrieved successfully")


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, request: ProjectUpdateRequest):
    project = await project_service.update_project(project_id, request)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(project=project, message="Project updated successfully")


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    success = await project_service.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"message": "Project deleted successfully"}


@router.post("/{project_id}/process", response_model=ProjectResponse)
async def process_project(project_id: str, background_tasks: BackgroundTasks):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    background_tasks.add_task(project_service.process_project, project_id)
    project.status = "processing"
    await project_service.update_project(project_id, ProjectUpdateRequest())
    return ProjectResponse(project=project, message="Project processing started")


@router.post("/{project_id}/process-sync", response_model=ProjectResponse)
async def process_project_sync(project_id: str):
    try:
        project = await project_service.process_project(project_id)
        return ProjectResponse(project=project, message="Project processed successfully")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/status", response_model=ProcessingStatus)
async def get_processing_status(project_id: str):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    current_step = "idle"
    progress = 0.0
    if project.status == "processing":
        current_step = "analyzing"
        progress = 45.0
    elif project.status == "completed":
        current_step = "completed"
        progress = 100.0
    elif project.status == "error":
        current_step = "error"

    return ProcessingStatus(
        project_id=project_id,
        status=project.status,
        progress=progress,
        current_step=current_step,
        estimated_time_remaining=None,
        error_message=project.error_message,
    )


@router.get("/{project_id}/timeline")
async def get_timeline(project_id: str):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return {
        "project_id": project.id,
        "tracks": [track.model_dump(mode="json") for track in project.tracks],
        "pipeline": project.pipeline.model_dump(mode="json"),
    }


@router.get("/{project_id}/gaps")
async def get_gaps(project_id: str):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "gaps": [gap.model_dump() for gap in project.pipeline.gap_ranges]}


@router.get("/{project_id}/insertions")
async def get_insertions(project_id: str):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "project_id": project_id,
        "insertions": [item.model_dump() for item in project.pipeline.insertion_suggestions],
    }


@router.get("/{project_id}/download")
async def download_project_output(project_id: str):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != "completed" or not project.output_path:
        raise HTTPException(status_code=400, detail="Project not completed or no output available")

    return FileResponse(project.output_path, media_type="video/mp4", filename=f"{project.name}_output.mp4")


@router.get("/{project_id}/subtitles")
async def get_project_subtitles(project_id: str):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    path = project.pipeline.subtitle_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Subtitle file not found")

    return FileResponse(path, media_type="text/plain", filename=f"{project.name}_subtitles.srt")


@router.get("/{project_id}/word-srt")
async def get_project_word_srt(project_id: str):
    project = await project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    path = project.pipeline.word_srt_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Word-level SRT not found")

    return FileResponse(path, media_type="text/plain", filename=f"{project.name}_word_level.srt")
