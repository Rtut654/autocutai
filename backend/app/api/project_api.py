"""API endpoints for video editing project management."""

from __future__ import annotations

import json
import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .auth_dependencies import get_current_user
from ..models.project import (
    AspectRatio,
    BackgroundVideoPlanArtifact,
    EditMode,
    HybridProjectAnalyzeRequest,
    ProcessingStatus,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectSettings,
    TrackReorderRequest,
    ProjectUpdateRequest,
    SpeechFilterArtifact,
    SpeechFilterUpdateRequest,
    TrackBackgroundVideoUpdateRequest,
    TrackBackgroundMusicUpdateRequest,
    TrackRenderRequest,
    TrackRenderResponse,
    VisualPlanArtifact,
)
from ..services.project_service import project_service
from ..services.background_video_worker_service import background_video_worker_service
from ..services.video_processor import video_processor
from ..services.visual_worker_service import visual_worker_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


def _is_duplicate_track_upload(project, filename: str, size: int) -> bool:
    normalized_name = Path(filename).name.lower()
    for track in project.tracks:
        existing_name = str(track.filename or "").strip().lower()
        existing_size = track.metadata.get("size")
        if existing_name == normalized_name and existing_size == size:
            return True
    return False


def _resolve_existing_project_path(path_value: str | None, project_id: str, current_user_id: str) -> Path:
    if not path_value:
        raise HTTPException(status_code=404, detail="Media file not found")

    candidates = [
        Path(path_value),
        Path.cwd() / path_value,
        Path.cwd().parent / path_value,
        project_service.get_project_dir(current_user_id, project_id, create=True) / Path(path_value).name,
        project_service.get_project_video_dir(current_user_id, project_id) / Path(path_value).name,
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise HTTPException(status_code=404, detail="Media file not found")


@router.post("/hybrid-analyze", response_model=ProjectResponse)
async def hybrid_analyze_project(
    request: HybridProjectAnalyzeRequest,
    current_user=Depends(get_current_user),
):
    try:
        project = await project_service.analyze_hybrid_project(request, user_id=current_user.id)
        return ProjectResponse(project=project, message="Hybrid analysis completed")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to analyze hybrid project: {exc}") from exc


@router.post("/", response_model=ProjectResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
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
        project_id = str(uuid4())

        saved_files: List[str] = []
        for upload in files:
            if not upload.filename:
                raise HTTPException(status_code=400, detail="Each file needs a filename")
            destination = project_service.reserve_project_video_path(current_user.id, project_id, upload.filename)
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

        project = await project_service.create_project_with_id(project_id, request, user_id=current_user.id)
        if project_service.project_has_missing_transcripts(project):
            background_tasks.add_task(project_service.backfill_missing_transcripts, project.id, current_user.id)
        return ProjectResponse(project=project, message="Project created successfully")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create project: {exc}") from exc


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(get_current_user),
):
    projects = await project_service.list_projects(current_user.id)
    total = len(projects)
    return ProjectListResponse(projects=projects[offset : offset + limit], total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project_service.project_has_missing_transcripts(project):
        background_tasks.add_task(project_service.backfill_missing_transcripts, project.id, current_user.id)
    return ProjectResponse(project=project, message="Project retrieved successfully")


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, request: ProjectUpdateRequest, current_user=Depends(get_current_user)):
    project = await project_service.update_project(project_id, request, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(project=project, message="Project updated successfully")


@router.delete("/{project_id}")
async def delete_project(project_id: str, current_user=Depends(get_current_user)):
    success = await project_service.delete_project(project_id, user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"message": "Project deleted successfully"}


@router.post("/{project_id}/process", response_model=ProjectResponse)
async def process_project(project_id: str, background_tasks: BackgroundTasks, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    background_tasks.add_task(project_service.process_project, project_id, current_user.id)
    project.status = "processing"
    await project_service.update_project(project_id, ProjectUpdateRequest(), user_id=current_user.id)
    return ProjectResponse(project=project, message="Project processing started")


@router.post("/{project_id}/process-sync", response_model=ProjectResponse)
async def process_project_sync(project_id: str, current_user=Depends(get_current_user)):
    try:
        project = await project_service.process_project(project_id, user_id=current_user.id)
        return ProjectResponse(project=project, message="Project processed successfully")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to process project: {exc}") from exc


@router.get("/{project_id}/status", response_model=ProcessingStatus)
async def get_processing_status(project_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
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
async def get_timeline(project_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return {
        "project_id": project.id,
        "tracks": [track.model_dump(mode="json") for track in project.tracks],
        "pipeline": project.pipeline.model_dump(mode="json"),
    }


@router.get("/{project_id}/render-manifest")
async def get_render_manifest(project_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return {
        "project_id": project.id,
        "render_plan": project.pipeline.render_plan,
        "subtitle_cues": [cue.model_dump() for cue in project.pipeline.subtitle_cues],
        "gap_ranges": [gap.model_dump() for gap in project.pipeline.gap_ranges],
        "insertions": [item.model_dump() for item in project.pipeline.insertion_suggestions],
    }


@router.get("/{project_id}/gaps")
async def get_gaps(project_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "gaps": [gap.model_dump() for gap in project.pipeline.gap_ranges]}


@router.get("/{project_id}/insertions")
async def get_insertions(project_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "project_id": project_id,
        "insertions": [item.model_dump() for item in project.pipeline.insertion_suggestions],
    }


@router.get("/{project_id}/background-video-plan", response_model=BackgroundVideoPlanArtifact)
async def get_background_video_plan(project_id: str, current_user=Depends(get_current_user)):
    artifact = await background_video_worker_service.get_project_background_video_plan(
        project_id,
        user_id=current_user.id,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Background video plan not found")
    return artifact


@router.get("/{project_id}/download")
async def download_project_output(project_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != "completed" or not project.output_path:
        raise HTTPException(status_code=400, detail="Project not completed or no output available")
    output_path = _resolve_existing_project_path(project.output_path, project_id, current_user.id)
    return FileResponse(output_path, media_type="video/mp4", filename=f"{project.name}_output.mp4")


@router.patch("/{project_id}/tracks/{track_id}/exclude")
async def exclude_track(project_id: str, track_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    track = next((item for item in project.tracks if item.id == track_id), None)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    track.excluded = not track.excluded
    track.status = "hidden" if track.excluded else "visible"
    project.updated_at = datetime.now()
    await project_service._save_project(project)
    return {"track_id": track_id, "excluded": track.excluded, "status": track.status}


@router.post("/{project_id}/tracks", response_model=ProjectResponse)
async def add_tracks_to_project(
    project_id: str,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
    files: List[UploadFile] = File(...),
    capture_times_json: Optional[str] = Form(default=None),
    metadata_json: Optional[str] = Form(default=None),
):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    saved_files: List[str] = []
    skipped_duplicates = 0
    for upload in files:
        if not upload.filename:
            raise HTTPException(status_code=400, detail="Each file needs a filename")
        payload = await upload.read()
        if _is_duplicate_track_upload(project, upload.filename, len(payload)):
            skipped_duplicates += 1
            continue
        destination = project_service.reserve_project_video_path(current_user.id, project_id, upload.filename)
        destination.write_bytes(payload)
        saved_files.append(str(destination))

    capture_times = None
    if capture_times_json:
        raw = json.loads(capture_times_json)
        capture_times = [datetime.fromisoformat(v) if v else None for v in raw]

    track_metadata = None
    if metadata_json:
        track_metadata = json.loads(metadata_json)

    start_position = len(project.tracks)
    for i, file_path in enumerate(saved_files):
        captured = None
        if capture_times and i < len(capture_times):
            captured = capture_times[i]
        extra_meta: Dict[str, Any] = {}
        if track_metadata and i < len(track_metadata):
            extra_meta = track_metadata[i] or {}
        track = await project_service._create_track_from_file(
            file_path, start_position + i, captured, extra_meta
        )
        project.tracks.append(track)

    project.tracks = project_service._ordered_tracks(project.tracks, project.settings.edit_mode)
    project_service.mark_missing_transcripts_pending(project)
    project.updated_at = datetime.now()
    await project_service._save_project(project)
    if project_service.project_has_missing_transcripts(project):
        background_tasks.add_task(project_service.backfill_missing_transcripts, project.id, current_user.id)
    if not saved_files and skipped_duplicates:
        return ProjectResponse(project=project, message="All selected clips were already uploaded")
    if skipped_duplicates:
        return ProjectResponse(project=project, message=f"Tracks added successfully ({skipped_duplicates} duplicates skipped)")
    return ProjectResponse(project=project, message="Tracks added successfully")


@router.patch("/{project_id}/tracks/reorder", response_model=ProjectResponse)
async def reorder_project_tracks(
    project_id: str,
    request: TrackReorderRequest,
    current_user=Depends(get_current_user),
):
    try:
        project = await project_service.reorder_project_tracks(
            project_id,
            request.track_ids,
            user_id=current_user.id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Project not found":
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(project=project, message="Track order updated")


@router.patch("/{project_id}/tracks/{track_id}/background-music", response_model=ProjectResponse)
async def update_track_background_music(
    project_id: str,
    track_id: str,
    request: TrackBackgroundMusicUpdateRequest,
    current_user=Depends(get_current_user),
):
    try:
        project = await project_service.update_track_background_music(
            project_id,
            track_id,
            request.model_dump(),
            user_id=current_user.id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail in {"Project not found", "Track not found"}:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return ProjectResponse(project=project, message="Background music updated")


@router.patch("/{project_id}/tracks/{track_id}/background-video", response_model=ProjectResponse)
async def update_track_background_video(
    project_id: str,
    track_id: str,
    request: TrackBackgroundVideoUpdateRequest,
    current_user=Depends(get_current_user),
):
    try:
        project = await project_service.update_track_background_video(
            project_id,
            track_id,
            request.model_dump(),
            user_id=current_user.id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail in {"Project not found", "Track not found"}:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    return ProjectResponse(project=project, message="Background clip updated")


@router.post("/{project_id}/background-video-plan", response_model=BackgroundVideoPlanArtifact)
async def generate_background_video_plan(project_id: str, current_user=Depends(get_current_user)):
    try:
        return await background_video_worker_service.generate_project_background_video_plan(
            project_id,
            user_id=current_user.id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Project not found":
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate background video plan: {exc}") from exc


@router.post("/{project_id}/transcribe-missing", response_model=ProjectResponse)
async def transcribe_missing_project_tracks(
    project_id: str,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_service.mark_missing_transcripts_pending(project)
    project.updated_at = datetime.utcnow()
    await project_service._save_project(project)

    if project_service.project_has_missing_transcripts(project):
        background_tasks.add_task(project_service.backfill_missing_transcripts, project.id, current_user.id)
        return ProjectResponse(project=project, message="Transcript processing started")

    return ProjectResponse(project=project, message="All transcripts already available")


@router.get("/{project_id}/tracks/{track_id}/speech-filter", response_model=SpeechFilterArtifact)
async def get_track_speech_filter(
    project_id: str,
    track_id: str,
    current_user=Depends(get_current_user),
):
    artifact = await project_service.get_track_speech_filter(project_id, track_id, user_id=current_user.id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Speech filter suggestions not found")
    return artifact


@router.post("/{project_id}/tracks/{track_id}/speech-filter", response_model=SpeechFilterArtifact)
async def generate_track_speech_filter(
    project_id: str,
    track_id: str,
    current_user=Depends(get_current_user),
):
    try:
        return await project_service.generate_track_speech_filter(project_id, track_id, user_id=current_user.id)
    except ValueError as exc:
        detail = str(exc)
        if detail in {"Project not found", "Track not found"}:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate speech filter suggestions: {exc}") from exc


@router.patch("/{project_id}/tracks/{track_id}/speech-filter", response_model=SpeechFilterArtifact)
async def update_track_speech_filter(
    project_id: str,
    track_id: str,
    request: SpeechFilterUpdateRequest,
    current_user=Depends(get_current_user),
):
    try:
        return await project_service.update_track_speech_filter(
            project_id,
            track_id,
            [cut.model_dump() for cut in request.cuts],
            user_id=current_user.id,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail in {"Project not found", "Track not found"}:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update speech filter suggestions: {exc}") from exc


@router.get("/{project_id}/tracks/{track_id}/visual-plan", response_model=VisualPlanArtifact)
async def get_track_visual_plan(
    project_id: str,
    track_id: str,
    current_user=Depends(get_current_user),
):
    artifact = await visual_worker_service.get_track_visual_plan(project_id, track_id, user_id=current_user.id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Visual plan not found")
    return artifact


@router.post("/{project_id}/tracks/{track_id}/visual-plan", response_model=VisualPlanArtifact)
async def generate_track_visual_plan(
    project_id: str,
    track_id: str,
    current_user=Depends(get_current_user),
):
    try:
        return await visual_worker_service.generate_track_visual_plan(project_id, track_id, user_id=current_user.id)
    except ValueError as exc:
        detail = str(exc)
        if detail in {"Project not found", "Track not found"}:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate visual plan: {exc}") from exc


@router.get("/{project_id}/tracks/{track_id}/visual-assets/{part_index}")
async def get_track_visual_asset(
    project_id: str,
    track_id: str,
    part_index: int,
    current_user=Depends(get_current_user),
):
    artifact = await visual_worker_service.get_track_visual_plan(project_id, track_id, user_id=current_user.id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Visual plan not found")
    if part_index < 0 or part_index >= len(artifact.parts):
        raise HTTPException(status_code=404, detail="Visual asset not found")

    part = artifact.parts[part_index]
    if not part.local_path:
        raise HTTPException(status_code=404, detail="Visual asset not ready")

    asset_path = Path(part.local_path)
    if not asset_path.exists():
        raise HTTPException(status_code=404, detail="Visual asset not found")

    media_type, _ = mimetypes.guess_type(asset_path.name)
    return FileResponse(
        asset_path,
        media_type=media_type or "application/octet-stream",
        filename=asset_path.name,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/{project_id}/tracks/{track_id}/media")
async def download_project_track_media(project_id: str, track_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    track = next((item for item in project.tracks if item.id == track_id), None)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    media_path = _resolve_existing_project_path(track.file_path, project_id, current_user.id)
    preview_dir = project_service.get_project_dir(current_user.id, project_id, create=True) / "preview"
    preview_path = preview_dir / f"{track.id}.mp4"
    served_path = media_path
    served_filename = track.filename
    media_type, _ = mimetypes.guess_type(track.filename)

    if track.type.value == "video":
        try:
            preview_candidate = await video_processor.ensure_browser_playable_video(
                media_path,
                preview_path,
            )
            served_path = Path(preview_candidate)
            if served_path.suffix.lower() == ".mp4":
                served_filename = f"{Path(track.filename).stem}.mp4"
                media_type = "video/mp4"
        except RuntimeError as exc:
            logger.warning("Falling back to original track media for %s: %s", track.id, exc)

    return FileResponse(
        served_path,
        media_type=media_type or "application/octet-stream",
        filename=served_filename,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.post("/{project_id}/tracks/{track_id}/render", response_model=TrackRenderResponse)
async def render_project_track_version(
    project_id: str,
    track_id: str,
    request: TrackRenderRequest,
    current_user=Depends(get_current_user),
):
    try:
        version = await project_service.render_track_version(
            project_id,
            track_id,
            cuts=[cut.model_dump() for cut in request.cuts],
            user_id=current_user.id,
        )
        return TrackRenderResponse(version=version, message="Track version rendered successfully")
    except ValueError as exc:
        detail = str(exc)
        if detail in {"Project not found", "Track not found"}:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to render track version: {exc}") from exc


@router.get("/{project_id}/tracks/{track_id}/renders/{version_id}")
async def download_project_track_render_version(
    project_id: str,
    track_id: str,
    version_id: str,
    current_user=Depends(get_current_user),
):
    resolved = await project_service.get_track_render_version(
        project_id,
        track_id,
        version_id,
        user_id=current_user.id,
    )
    if not resolved:
        raise HTTPException(status_code=404, detail="Rendered version not found")

    _, _, version = resolved
    render_path = _resolve_existing_project_path(version.file_path, project_id, current_user.id)
    media_type, _ = mimetypes.guess_type(version.filename)
    return FileResponse(
        render_path,
        media_type=media_type or "video/mp4",
        filename=version.filename,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/{project_id}/subtitles")
async def get_project_subtitles(project_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    path = project.pipeline.subtitle_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Subtitle file not found")

    return FileResponse(path, media_type="text/plain", filename=f"{project.name}_subtitles.srt")


@router.get("/{project_id}/word-srt")
async def get_project_word_srt(project_id: str, current_user=Depends(get_current_user)):
    project = await project_service.get_project(project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    path = project.pipeline.word_srt_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Word-level SRT not found")

    return FileResponse(path, media_type="text/plain", filename=f"{project.name}_word_level.srt")
