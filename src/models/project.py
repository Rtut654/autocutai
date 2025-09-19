"""
Data models for video editing projects.
"""

from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class AspectRatio(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class EditMode(str, Enum):
    CHRONOLOGICAL = "chronological"
    MANUAL = "manual"


class ProFeature(str, Enum):
    DUPLICATE_REMOVAL = "duplicate_removal"
    SMART_PAUSE_CUTTER = "smart_pause_cutter"
    SUBTITLE_GENERATION = "subtitle_generation"
    MEDIA_SUGGESTIONS = "media_suggestions"


class TrackType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"


class VideoTrack(BaseModel):
    """Model for video track data."""
    
    id: str = Field(..., description="Unique track ID")
    type: TrackType = Field(..., description="Type of track")
    filename: str = Field(..., description="Original filename")
    file_path: str = Field(..., description="Path to the file")
    duration: float = Field(..., description="Duration in seconds")
    start_time: Optional[float] = Field(None, description="Start time in project timeline")
    end_time: Optional[float] = Field(None, description="End time in project timeline")
    position: int = Field(..., description="Position in timeline")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="File metadata")
    transcription: Optional[Dict[str, Any]] = Field(None, description="Transcription data if available")


class ProjectSettings(BaseModel):
    """Model for project settings."""
    
    aspect_ratio: AspectRatio = Field(default=AspectRatio.HORIZONTAL, description="Video aspect ratio")
    edit_mode: EditMode = Field(default=EditMode.CHRONOLOGICAL, description="Editing mode")
    remove_duplicates: bool = Field(default=False, description="Remove duplicate speech")
    smart_pause_cutter: bool = Field(default=False, description="AI smart pause cutter (pro feature)")
    generate_subtitles: bool = Field(default=False, description="Generate subtitles (pro feature)")
    insert_suggestions: bool = Field(default=False, description="Insert media suggestions (pro feature)")


class Project(BaseModel):
    """Model for video editing project."""
    
    id: str = Field(..., description="Unique project ID")
    name: str = Field(..., description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    tracks: List[VideoTrack] = Field(default_factory=list, description="Project tracks")
    settings: ProjectSettings = Field(default_factory=ProjectSettings, description="Project settings")
    status: Literal["draft", "processing", "completed", "error"] = Field(default="draft", description="Project status")
    output_path: Optional[str] = Field(None, description="Path to final output video")
    user_id: Optional[str] = Field(None, description="User ID (for multi-user support)")


class ProjectCreateRequest(BaseModel):
    """Model for project creation request."""
    
    name: str = Field(..., description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    track_files: List[str] = Field(..., description="List of track file paths")
    settings: ProjectSettings = Field(default_factory=ProjectSettings, description="Project settings")


class ProjectUpdateRequest(BaseModel):
    """Model for project update request."""
    
    name: Optional[str] = Field(None, description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    settings: Optional[ProjectSettings] = Field(None, description="Project settings")
    tracks: Optional[List[VideoTrack]] = Field(None, description="Project tracks")


class ProjectResponse(BaseModel):
    """Model for project response."""
    
    project: Project = Field(..., description="Project data")
    message: str = Field(..., description="Response message")


class ProjectListResponse(BaseModel):
    """Model for project list response."""
    
    projects: List[Project] = Field(..., description="List of projects")
    total: int = Field(..., description="Total number of projects")


class ProcessingStatus(BaseModel):
    """Model for processing status."""
    
    project_id: str = Field(..., description="Project ID")
    status: str = Field(..., description="Processing status")
    progress: float = Field(..., description="Progress percentage (0-100)")
    current_step: str = Field(..., description="Current processing step")
    estimated_time_remaining: Optional[int] = Field(None, description="Estimated time remaining in seconds")
    error_message: Optional[str] = Field(None, description="Error message if any")
