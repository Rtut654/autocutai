"""
API endpoints for video editing project management.
"""

import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse

from ..models.project import (
    Project, ProjectCreateRequest, ProjectUpdateRequest, ProjectResponse,
    ProjectListResponse, ProcessingStatus, AspectRatio, EditMode
)
from ..services.project_service import project_service

logger = logging.getLogger(__name__)

# Create router for project endpoints
router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("/", response_model=ProjectResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    name: str = Form(..., description="Project name"),
    description: Optional[str] = Form(None, description="Project description"),
    aspect_ratio: AspectRatio = Form(default=AspectRatio.HORIZONTAL, description="Video aspect ratio"),
    edit_mode: EditMode = Form(default=EditMode.CHRONOLOGICAL, description="Editing mode"),
    remove_duplicates: bool = Form(default=False, description="Remove duplicate speech"),
    smart_pause_cutter: bool = Form(default=False, description="AI smart pause cutter (pro feature)"),
    generate_subtitles: bool = Form(default=False, description="Generate subtitles (pro feature)"),
    insert_suggestions: bool = Form(default=False, description="Insert media suggestions (pro feature)"),
    files: List[UploadFile] = File(..., description="Video/audio files for the project")
):
    """
    Create a new video editing project.
    
    Args:
        name: Project name
        description: Project description
        aspect_ratio: Video aspect ratio
        edit_mode: Editing mode
        remove_duplicates: Remove duplicate speech
        smart_pause_cutter: AI smart pause cutter (pro feature)
        generate_subtitles: Generate subtitles (pro feature)
        insert_suggestions: Insert media suggestions (pro feature)
        files: Uploaded files
    
    Returns:
        Created project
    """
    try:
        logger.info(f"Creating project: {name} with {len(files)} files")
        
        # Save uploaded files
        saved_files = []
        for file in files:
            if not file.filename:
                raise HTTPException(status_code=400, detail="File must have a filename")
            
            # Save file to temp directory
            file_path = f"temp/{file.filename}"
            with open(file_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)
            
            saved_files.append(file_path)
            logger.info(f"Saved file: {file_path}")
        
        # Create project request
        from ..models.project import ProjectSettings
        settings = ProjectSettings(
            aspect_ratio=aspect_ratio,
            edit_mode=edit_mode,
            remove_duplicates=remove_duplicates,
            smart_pause_cutter=smart_pause_cutter,
            generate_subtitles=generate_subtitles,
            insert_suggestions=insert_suggestions
        )
        
        request = ProjectCreateRequest(
            name=name,
            description=description,
            track_files=saved_files,
            settings=settings
        )
        
        # Create project
        project = await project_service.create_project(request)
        
        logger.info(f"Project created successfully: {project.id}")
        
        return ProjectResponse(
            project=project,
            message="Project created successfully"
        )
        
    except Exception as e:
        logger.error(f"Error creating project: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    user_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """
    List all projects.
    
    Args:
        user_id: User ID to filter projects (optional)
        limit: Maximum number of projects to return
        offset: Number of projects to skip
    
    Returns:
        List of projects
    """
    try:
        projects = await project_service.list_projects(user_id)
        
        # Apply pagination
        total = len(projects)
        projects = projects[offset:offset + limit]
        
        return ProjectListResponse(
            projects=projects,
            total=total
        )
        
    except Exception as e:
        logger.error(f"Error listing projects: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list projects: {str(e)}")


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    """
    Get a project by ID.
    
    Args:
        project_id: Project ID
    
    Returns:
        Project data
    """
    try:
        project = await project_service.get_project(project_id)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        return ProjectResponse(
            project=project,
            message="Project retrieved successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting project {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get project: {str(e)}")


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    request: ProjectUpdateRequest
):
    """
    Update a project.
    
    Args:
        project_id: Project ID
        request: Update request
    
    Returns:
        Updated project
    """
    try:
        project = await project_service.update_project(project_id, request)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        return ProjectResponse(
            project=project,
            message="Project updated successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating project {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update project: {str(e)}")


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """
    Delete a project.
    
    Args:
        project_id: Project ID
    
    Returns:
        Success message
    """
    try:
        success = await project_service.delete_project(project_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Project not found")
        
        return {"message": "Project deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting project {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to delete project: {str(e)}")


@router.post("/{project_id}/process", response_model=ProjectResponse)
async def process_project(
    project_id: str,
    background_tasks: BackgroundTasks
):
    """
    Process a project (transcribe, analyze, and generate output).
    
    Args:
        project_id: Project ID
        background_tasks: Background tasks for async processing
    
    Returns:
        Project with processing status
    """
    try:
        # Start processing in background
        background_tasks.add_task(project_service.process_project, project_id)
        
        # Get current project status
        project = await project_service.get_project(project_id)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Update status to processing
        project.status = "processing"
        await project_service.update_project(project_id, ProjectUpdateRequest())
        
        return ProjectResponse(
            project=project,
            message="Project processing started"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting project processing {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start project processing: {str(e)}")


@router.get("/{project_id}/status", response_model=ProcessingStatus)
async def get_processing_status(project_id: str):
    """
    Get project processing status.
    
    Args:
        project_id: Project ID
    
    Returns:
        Processing status
    """
    try:
        project = await project_service.get_project(project_id)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Determine current step based on status
        current_step = "idle"
        progress = 0.0
        
        if project.status == "processing":
            current_step = "transcribing"
            progress = 25.0
        elif project.status == "completed":
            current_step = "completed"
            progress = 100.0
        elif project.status == "error":
            current_step = "error"
            progress = 0.0
        
        return ProcessingStatus(
            project_id=project_id,
            status=project.status,
            progress=progress,
            current_step=current_step,
            estimated_time_remaining=None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting processing status {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get processing status: {str(e)}")


@router.get("/{project_id}/download")
async def download_project_output(project_id: str):
    """
    Download the final output video.
    
    Args:
        project_id: Project ID
    
    Returns:
        Video file
    """
    try:
        project = await project_service.get_project(project_id)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.status != "completed" or not project.output_path:
            raise HTTPException(status_code=400, detail="Project not completed or no output available")
        
        from fastapi.responses import FileResponse
        return FileResponse(
            project.output_path,
            media_type="video/mp4",
            filename=f"{project.name}_output.mp4"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading project output {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to download output: {str(e)}")


@router.get("/{project_id}/subtitles")
async def get_project_subtitles(project_id: str):
    """
    Get generated subtitles for the project.
    
    Args:
        project_id: Project ID
    
    Returns:
        Subtitle file
    """
    try:
        project = await project_service.get_project(project_id)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if not project.settings.generate_subtitles:
            raise HTTPException(status_code=400, detail="Subtitles not enabled for this project")
        
        # Generate subtitle file path
        subtitle_path = f"temp/{project_id}_subtitles.srt"
        
        if not os.path.exists(subtitle_path):
            raise HTTPException(status_code=404, detail="Subtitle file not found")
        
        from fastapi.responses import FileResponse
        return FileResponse(
            subtitle_path,
            media_type="text/plain",
            filename=f"{project.name}_subtitles.srt"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting subtitles {project_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get subtitles: {str(e)}")
