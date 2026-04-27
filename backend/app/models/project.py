"""Data models for video editing projects and AI pipeline artifacts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .transcription import WordTimestamp


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


class TrackOrientation(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    SQUARE = "square"
    UNKNOWN = "unknown"


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
    has_voice: bool = Field(default=False, description="Whether narration/voice exists in track")
    recorded_at: Optional[datetime] = Field(None, description="Capture time used for chronological ordering")
    orientation: TrackOrientation = Field(default=TrackOrientation.UNKNOWN, description="Normalized track orientation")
    width: Optional[int] = Field(default=None, description="Normalized display width in pixels")
    height: Optional[int] = Field(default=None, description="Normalized display height in pixels")
    local_gap_ranges: List["GapRange"] = Field(
        default_factory=list, description="Track-local pause ranges to remove"
    )
    status: Literal["visible", "hidden"] = Field(default="visible", description="Visibility within the project")
    excluded: bool = Field(default=False, description="Soft-deleted from project (file kept on disk)")


class ProjectSettings(BaseModel):
    """Model for project settings."""
    
    aspect_ratio: AspectRatio = Field(default=AspectRatio.HORIZONTAL, description="Video aspect ratio")
    edit_mode: EditMode = Field(default=EditMode.CHRONOLOGICAL, description="Editing mode")
    remove_duplicates: bool = Field(default=False, description="Remove duplicate speech")
    smart_pause_cutter: bool = Field(default=False, description="AI smart pause cutter (pro feature)")
    generate_subtitles: bool = Field(default=False, description="Generate subtitles (pro feature)")
    insert_suggestions: bool = Field(default=False, description="Insert media suggestions (pro feature)")
    min_gap_seconds: float = Field(default=1.0, description="Minimum silence gap to auto-cut")


class GapRange(BaseModel):
    """A pause/silence interval that can be removed."""

    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    duration: float = Field(..., description="Gap duration in seconds")
    reason: str = Field(default="silence_gap", description="Reason this interval is removable")


class SpeechFilterCut(BaseModel):
    """A suggested transcript-driven cut range within one track."""

    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    duration: float = Field(..., description="Cut duration in seconds")
    reason: str = Field(..., description="Why this region is suggested for removal")
    transcript: str = Field(default="", description="Words covered by this cut")
    confidence: float = Field(default=0.5, description="Confidence score from 0 to 1")


class ZoomPreviewBeat(BaseModel):
    """A logical transcript beat used for punch-in preview timing."""

    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    duration: float = Field(..., description="Beat duration in seconds")
    text: str = Field(default="", description="Transcript text associated with the beat")
    enabled: bool = Field(default=False, description="Whether the zoom effect should apply on this beat")
    scale: float = Field(default=1.12, description="Target scale for gradual center zoom preview")


class SpeechFilterArtifact(BaseModel):
    """Persisted speech-filter suggestions for a single track."""

    project_id: str = Field(..., description="Project ID")
    track_id: str = Field(..., description="Track ID")
    filename: str = Field(..., description="Original filename")
    status: Literal["completed", "error"] = Field(..., description="Generation result")
    summary: str = Field(default="", description="Human-readable summary of the suggested cuts")
    cuts: List[SpeechFilterCut] = Field(default_factory=list, description="Suggested removable ranges")
    zoom_beats: List[ZoomPreviewBeat] = Field(
        default_factory=list,
        description="Logical transcript beats used for preview zoom timing",
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow, description="Artifact creation timestamp")
    source_word_count: int = Field(default=0, description="Number of words evaluated")
    model: str = Field(default="heuristic", description="Model/provider used to generate the suggestions")
    error_message: Optional[str] = Field(default=None, description="Error detail if generation failed")


class SpeechFilterUpdateRequest(BaseModel):
    """Manual edits to persisted speech-filter suggestions."""

    cuts: List[SpeechFilterCut] = Field(default_factory=list, description="Edited removable ranges")


class VisualAssetKind(str, Enum):
    ANIMATION = "animation"
    WEB_IMAGE = "web_image"


class VisualAssetStatus(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    ERROR = "error"


class VisualPlanPart(BaseModel):
    """A timed visual enhancement aligned to narration."""

    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    duration: float = Field(..., description="Duration in seconds")
    text: str = Field(default="", description="Narration text this visual supports")
    visual_type: VisualAssetKind = Field(default=VisualAssetKind.ANIMATION, description="Animation or image")
    prompt: str = Field(default="", description="Creative brief for the worker")
    search_query: Optional[str] = Field(default=None, description="Optional search query for external image sourcing")
    animation_kind: Optional[str] = Field(default=None, description="Explicit animation motif/category for overlay rendering")
    title: Optional[str] = Field(default=None, description="Short visual title, usually 1 to 4 words")
    keywords: List[str] = Field(default_factory=list, description="Short on-screen keywords or tags")
    scene_objects: List[str] = Field(default_factory=list, description="Named visual objects to render in the scene")
    placement: Optional[str] = Field(default=None, description="Preferred placement in frame, such as top_left or lower_right")
    density: Optional[str] = Field(default=None, description="Visual density, usually light or medium")
    palette: Optional[str] = Field(default=None, description="Color system for the composition, such as cool, mint, sunset, mono")
    variant: Optional[str] = Field(default=None, description="Variant key within the animation family")
    motion_profile: Optional[str] = Field(default=None, description="Motion treatment, such as calm, punchy, drift")
    background_style: Optional[str] = Field(default="transparent", description="Background treatment, default transparent")
    asset_status: VisualAssetStatus = Field(default=VisualAssetStatus.PLANNED, description="Worker result status")
    asset_url: Optional[str] = Field(default=None, description="Source URL for downloaded image if used")
    local_path: Optional[str] = Field(default=None, description="Local cached asset path if available")
    transition_in: Optional[str] = Field(default=None, description="Reserved for future transition style")
    transition_out: Optional[str] = Field(default=None, description="Reserved for future transition style")
    sfx: Optional[str] = Field(default=None, description="Reserved for future sound effect cue")


class VisualPlanArtifact(BaseModel):
    """Separate visual worker artifact for script-following animations/images."""

    project_id: str = Field(..., description="Project ID")
    track_id: str = Field(..., description="Track ID")
    filename: str = Field(..., description="Original filename")
    status: Literal["completed", "error"] = Field(..., description="Generation result")
    summary: str = Field(default="", description="Human-readable summary")
    parts: List[VisualPlanPart] = Field(default_factory=list, description="Timed visual enhancement plan")
    generated_at: datetime = Field(default_factory=datetime.utcnow, description="Artifact creation timestamp")
    source_word_count: int = Field(default=0, description="Number of words evaluated")
    model: str = Field(default="heuristic", description="Planner model/provider")
    worker: str = Field(default="visual_enhancement_worker", description="Worker identity")
    error_message: Optional[str] = Field(default=None, description="Error detail if generation failed")


class InsertionSuggestion(BaseModel):
    """Recommended insertions to support narration moments."""

    time: float = Field(..., description="Project timeline timestamp in seconds")
    suggestion: str = Field(..., description="What to insert")
    media_type: Literal["picture", "meme_video", "broll"] = Field(
        default="picture", description="Suggested media type"
    )


class SubtitleCue(BaseModel):
    """Subtitle cue with optional location context."""

    index: int = Field(..., description="Cue index")
    start: float = Field(..., description="Cue start in seconds")
    end: float = Field(..., description="Cue end in seconds")
    text: str = Field(..., description="Subtitle text")
    location: Optional[str] = Field(default=None, description="Location label if available")


class ProjectPipeline(BaseModel):
    """Pipeline outputs used by mobile/web editors."""

    combined_transcript: str = Field(default="", description="Combined narration transcript")
    combined_words: List[WordTimestamp] = Field(default_factory=list, description="Full word timeline")
    word_srt_path: Optional[str] = Field(default=None, description="Path to word-level SRT")
    subtitle_path: Optional[str] = Field(default=None, description="Path to subtitle file")
    subtitle_cues: List[SubtitleCue] = Field(default_factory=list, description="Subtitle cue list")
    gap_ranges: List[GapRange] = Field(default_factory=list, description="Project-level gaps")
    insertion_suggestions: List[InsertionSuggestion] = Field(
        default_factory=list, description="AI insertion suggestions"
    )
    locations: List[str] = Field(default_factory=list, description="Detected location tags")
    render_plan: Dict[str, Any] = Field(default_factory=dict, description="Computed render instructions")


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
    pipeline: ProjectPipeline = Field(default_factory=ProjectPipeline, description="AI pipeline outputs")
    error_message: Optional[str] = Field(default=None, description="Error details if processing failed")


class ProjectCreateRequest(BaseModel):
    """Model for project creation request."""
    
    name: str = Field(..., description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    track_files: List[str] = Field(..., description="List of track file paths")
    track_capture_times: Optional[List[Optional[datetime]]] = Field(
        default=None, description="Capture datetimes for uploaded tracks"
    )
    track_metadata: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Per-track metadata from client"
    )
    settings: ProjectSettings = Field(default_factory=ProjectSettings, description="Project settings")


class HybridTrackInput(BaseModel):
    """Client-prepared analysis input for hybrid mobile/cloud workflow."""

    filename: str = Field(..., description="Original local filename")
    duration: Optional[float] = Field(default=None, description="Clip duration in seconds if known on-device")
    recorded_at: Optional[datetime] = Field(default=None, description="Client capture timestamp")
    source_reference: Optional[str] = Field(
        default=None,
        description="Opaque client-side reference to the original source media for local render",
    )
    proxy_reference: Optional[str] = Field(
        default=None,
        description="Optional client-side reference to low-res proxy media",
    )
    thumbnail_reference: Optional[str] = Field(
        default=None,
        description="Optional client-side reference to a thumbnail image",
    )
    shot_boundaries: List[float] = Field(default_factory=list, description="Client-detected shot boundary timestamps")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional client metadata")
    transcription: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Client-generated transcript payload with words/segments/text",
    )


class HybridProjectAnalyzeRequest(BaseModel):
    """Request for hybrid analysis where raw source video stays on the device."""

    name: str = Field(..., description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    tracks: List[HybridTrackInput] = Field(..., description="Per-track client analysis payload")
    settings: ProjectSettings = Field(default_factory=ProjectSettings, description="Project settings")
    render_strategy: Literal["on_device", "selected_ranges_upload"] = Field(
        default="on_device",
        description="How final export is expected to happen after backend analysis",
    )


class ProjectUpdateRequest(BaseModel):
    """Model for project update request."""
    
    name: Optional[str] = Field(None, description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    settings: Optional[ProjectSettings] = Field(None, description="Project settings")
    tracks: Optional[List[VideoTrack]] = Field(None, description="Project tracks")


class TrackReorderRequest(BaseModel):
    """Persisted manual clip order."""

    track_ids: List[str] = Field(..., description="Track IDs in the desired order")


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
