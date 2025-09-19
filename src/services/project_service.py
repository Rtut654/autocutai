"""
Service for managing video editing projects.
"""

import os
import uuid
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

from ..models.project import (
    Project, VideoTrack, ProjectSettings, ProjectCreateRequest,
    ProjectUpdateRequest, TrackType, AspectRatio, EditMode
)
from ..services.whisper_service import transcribe_audio
from ..services.video_processor import VideoProcessor

logger = logging.getLogger(__name__)


class ProjectService:
    """Service for managing video editing projects."""
    
    def __init__(self, projects_dir: str = "projects", temp_dir: str = "temp"):
        """
        Initialize the project service.
        
        Args:
            projects_dir: Directory to store project files
            temp_dir: Directory for temporary files
        """
        self.projects_dir = Path(projects_dir)
        self.temp_dir = Path(temp_dir)
        self.projects_dir.mkdir(exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)
        
        # In-memory storage for demo (use database in production)
        self.projects: Dict[str, Project] = {}
        
        logger.info(f"ProjectService initialized with projects_dir: {self.projects_dir}")
    
    async def create_project(self, request: ProjectCreateRequest, user_id: Optional[str] = None) -> Project:
        """
        Create a new video editing project.
        
        Args:
            request: Project creation request
            user_id: User ID (optional)
        
        Returns:
            Created project
        """
        project_id = str(uuid.uuid4())
        
        # Create tracks from uploaded files
        tracks = []
        for i, file_path in enumerate(request.track_files):
            track = await self._create_track_from_file(file_path, i)
            tracks.append(track)
        
        # Create project
        project = Project(
            id=project_id,
            name=request.name,
            description=request.description,
            tracks=tracks,
            settings=request.settings,
            user_id=user_id
        )
        
        # Save project
        await self._save_project(project)
        self.projects[project_id] = project
        
        logger.info(f"Created project: {project_id} with {len(tracks)} tracks")
        return project
    
    async def get_project(self, project_id: str) -> Optional[Project]:
        """
        Get a project by ID.
        
        Args:
            project_id: Project ID
        
        Returns:
            Project if found, None otherwise
        """
        if project_id in self.projects:
            return self.projects[project_id]
        
        # Try to load from file
        project_file = self.projects_dir / f"{project_id}.json"
        if project_file.exists():
            project = await self._load_project(project_file)
            self.projects[project_id] = project
            return project
        
        return None
    
    async def update_project(self, project_id: str, request: ProjectUpdateRequest) -> Optional[Project]:
        """
        Update a project.
        
        Args:
            project_id: Project ID
            request: Update request
        
        Returns:
            Updated project if found, None otherwise
        """
        project = await self.get_project(project_id)
        if not project:
            return None
        
        # Update fields
        if request.name is not None:
            project.name = request.name
        if request.description is not None:
            project.description = request.description
        if request.settings is not None:
            project.settings = request.settings
        if request.tracks is not None:
            project.tracks = request.tracks
        
        project.updated_at = datetime.now()
        
        # Save updated project
        await self._save_project(project)
        self.projects[project_id] = project
        
        logger.info(f"Updated project: {project_id}")
        return project
    
    async def delete_project(self, project_id: str) -> bool:
        """
        Delete a project.
        
        Args:
            project_id: Project ID
        
        Returns:
            True if deleted, False if not found
        """
        project = await self.get_project(project_id)
        if not project:
            return False
        
        # Remove from memory
        if project_id in self.projects:
            del self.projects[project_id]
        
        # Remove project file
        project_file = self.projects_dir / f"{project_id}.json"
        if project_file.exists():
            project_file.unlink()
        
        logger.info(f"Deleted project: {project_id}")
        return True
    
    async def list_projects(self, user_id: Optional[str] = None) -> List[Project]:
        """
        List all projects.
        
        Args:
            user_id: User ID to filter projects (optional)
        
        Returns:
            List of projects
        """
        projects = list(self.projects.values())
        
        if user_id:
            projects = [p for p in projects if p.user_id == user_id]
        
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)
    
    async def process_project(self, project_id: str) -> Project:
        """
        Process a project (transcribe, analyze, and generate output).
        
        Args:
            project_id: Project ID
        
        Returns:
            Updated project with processing results
        """
        project = await self.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        
        project.status = "processing"
        await self._save_project(project)
        
        try:
            # Step 1: Transcribe all audio tracks
            await self._transcribe_tracks(project)
            
            # Step 2: Process based on settings
            if project.settings.remove_duplicates:
                await self._remove_duplicates(project)
            
            if project.settings.smart_pause_cutter:
                await self._smart_pause_cutter(project)
            
            if project.settings.generate_subtitles:
                await self._generate_subtitles(project)
            
            if project.settings.insert_suggestions:
                await self._insert_media_suggestions(project)
            
            # Step 3: Generate final video
            output_path = await self._generate_final_video(project)
            project.output_path = output_path
            project.status = "completed"
            
        except Exception as e:
            logger.error(f"Error processing project {project_id}: {str(e)}")
            project.status = "error"
            raise
        
        finally:
            project.updated_at = datetime.now()
            await self._save_project(project)
        
        return project
    
    async def _create_track_from_file(self, file_path: str, position: int) -> VideoTrack:
        """
        Create a track from a file.
        
        Args:
            file_path: Path to the file
            position: Position in timeline
        
        Returns:
            Created track
        """
        file_path_obj = Path(file_path)
        
        # Determine track type
        if file_path_obj.suffix.lower() in ['.mp4', '.mov', '.avi', '.mkv']:
            track_type = TrackType.VIDEO
        elif file_path_obj.suffix.lower() in ['.mp3', '.wav', '.m4a', '.aac']:
            track_type = TrackType.AUDIO
        elif file_path_obj.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif']:
            track_type = TrackType.IMAGE
        else:
            track_type = TrackType.VIDEO  # Default
        
        # Get file metadata (simplified)
        metadata = {
            "filename": file_path_obj.name,
            "size": file_path_obj.stat().st_size if file_path_obj.exists() else 0,
            "extension": file_path_obj.suffix.lower()
        }
        
        # For demo, assume duration (in production, use ffprobe)
        duration = 30.0  # Default duration
        
        track = VideoTrack(
            id=str(uuid.uuid4()),
            type=track_type,
            filename=file_path_obj.name,
            file_path=str(file_path_obj),
            duration=duration,
            position=position,
            metadata=metadata
        )
        
        return track
    
    async def _transcribe_tracks(self, project: Project):
        """Transcribe all audio tracks in the project."""
        for track in project.tracks:
            if track.type in [TrackType.VIDEO, TrackType.AUDIO]:
                try:
                    # Read audio data
                    with open(track.file_path, 'rb') as f:
                        audio_data = f.read()
                    
                    # Transcribe using Whisper
                    transcription = await transcribe_audio(
                        audio_data, 
                        track.filename, 
                        "en"
                    )
                    
                    track.transcription = transcription
                    logger.info(f"Transcribed track: {track.filename}")
                    
                except Exception as e:
                    logger.error(f"Error transcribing track {track.filename}: {str(e)}")
    
    async def _remove_duplicates(self, project: Project):
        """Remove duplicate speech from tracks."""
        # Implementation for duplicate removal
        logger.info("Removing duplicate speech...")
        # This would analyze transcriptions and remove repeated phrases
        await asyncio.sleep(1)  # Simulate processing
    
    async def _smart_pause_cutter(self, project: Project):
        """Use AI to intelligently cut pauses."""
        logger.info("Applying smart pause cutter...")
        # This would analyze audio and cut natural pause points
        await asyncio.sleep(2)  # Simulate processing
    
    async def _generate_subtitles(self, project: Project):
        """Generate subtitles from transcriptions."""
        logger.info("Generating subtitles...")
        # This would create subtitle files from transcriptions
        await asyncio.sleep(1)  # Simulate processing
    
    async def _insert_media_suggestions(self, project: Project):
        """Insert suggested media based on speech content."""
        logger.info("Inserting media suggestions...")
        # This would analyze speech and suggest relevant images/videos
        await asyncio.sleep(2)  # Simulate processing
    
    async def _generate_final_video(self, project: Project) -> str:
        """Generate the final output video."""
        logger.info("Generating final video...")
        
        # Use VideoProcessor to combine tracks
        processor = VideoProcessor()
        output_path = await processor.process_project(project)
        
        return output_path
    
    async def _save_project(self, project: Project):
        """Save project to file."""
        project_file = self.projects_dir / f"{project.id}.json"
        
        # Convert to dict and save
        project_dict = project.dict()
        project_dict["created_at"] = project.created_at.isoformat()
        project_dict["updated_at"] = project.updated_at.isoformat()
        
        with open(project_file, 'w') as f:
            json.dump(project_dict, f, indent=2)
    
    async def _load_project(self, project_file: Path) -> Project:
        """Load project from file."""
        with open(project_file, 'r') as f:
            project_dict = json.load(f)
        
        # Convert datetime strings back to datetime objects
        project_dict["created_at"] = datetime.fromisoformat(project_dict["created_at"])
        project_dict["updated_at"] = datetime.fromisoformat(project_dict["updated_at"])
        
        return Project(**project_dict)


# Global service instance
project_service = ProjectService()
