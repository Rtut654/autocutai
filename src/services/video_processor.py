"""
Service for video processing and manipulation.
"""

import os
import asyncio
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
import subprocess
import json

from ..models.project import Project, VideoTrack, AspectRatio, EditMode

logger = logging.getLogger(__name__)


class VideoProcessor:
    """Service for video processing operations."""
    
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        """
        Initialize the video processor.
        
        Args:
            ffmpeg_path: Path to ffmpeg executable
            ffprobe_path: Path to ffprobe executable
        """
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.temp_dir = Path("temp")
        self.temp_dir.mkdir(exist_ok=True)
        
        logger.info("VideoProcessor initialized")
    
    async def process_project(self, project: Project) -> str:
        """
        Process a complete project and generate final video.
        
        Args:
            project: Project to process
        
        Returns:
            Path to the generated video
        """
        logger.info(f"Processing project: {project.name}")
        
        # Step 1: Prepare tracks based on edit mode
        if project.settings.edit_mode == EditMode.CHRONOLOGICAL:
            tracks = await self._arrange_tracks_chronologically(project.tracks)
        else:
            tracks = project.tracks  # Manual arrangement
        
        # Step 2: Process individual tracks
        processed_tracks = []
        for track in tracks:
            processed_track = await self._process_track(track, project.settings)
            processed_tracks.append(processed_track)
        
        # Step 3: Combine tracks into final video
        output_path = await self._combine_tracks(processed_tracks, project.settings)
        
        logger.info(f"Project processed successfully: {output_path}")
        return output_path
    
    async def _arrange_tracks_chronologically(self, tracks: List[VideoTrack]) -> List[VideoTrack]:
        """
        Arrange tracks chronologically based on metadata.
        
        Args:
            tracks: List of tracks to arrange
        
        Returns:
            Chronologically arranged tracks
        """
        # Sort tracks by creation time or start time
        sorted_tracks = sorted(tracks, key=lambda t: t.metadata.get('creation_time', 0))
        
        # Update positions
        for i, track in enumerate(sorted_tracks):
            track.position = i
        
        return sorted_tracks
    
    async def _process_track(self, track: VideoTrack, settings) -> VideoTrack:
        """
        Process an individual track.
        
        Args:
            track: Track to process
            settings: Project settings
        
        Returns:
            Processed track
        """
        logger.info(f"Processing track: {track.filename}")
        
        # Apply aspect ratio adjustments
        if track.type == "video":
            track = await self._adjust_aspect_ratio(track, settings.aspect_ratio)
        
        # Apply any other processing based on settings
        if settings.remove_duplicates and track.transcription:
            track = await self._remove_duplicate_segments(track)
        
        if settings.smart_pause_cutter and track.transcription:
            track = await self._apply_smart_pause_cutter(track)
        
        return track
    
    async def _adjust_aspect_ratio(self, track: VideoTrack, aspect_ratio: AspectRatio) -> VideoTrack:
        """
        Adjust video aspect ratio.
        
        Args:
            track: Video track
            aspect_ratio: Target aspect ratio
        
        Returns:
            Track with adjusted aspect ratio
        """
        if aspect_ratio == AspectRatio.VERTICAL:
            # 9:16 aspect ratio for vertical videos
            scale_filter = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2"
        else:
            # 16:9 aspect ratio for horizontal videos
            scale_filter = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
        
        # Create processed file path
        processed_path = self.temp_dir / f"processed_{track.id}.mp4"
        
        # Apply aspect ratio using ffmpeg
        cmd = [
            self.ffmpeg_path,
            "-i", track.file_path,
            "-vf", scale_filter,
            "-c:a", "copy",
            "-y",  # Overwrite output file
            str(processed_path)
        ]
        
        await self._run_ffmpeg_command(cmd)
        
        # Update track with processed file
        track.file_path = str(processed_path)
        return track
    
    async def _remove_duplicate_segments(self, track: VideoTrack) -> VideoTrack:
        """
        Remove duplicate speech segments from track.
        
        Args:
            track: Track with transcription
        
        Returns:
            Track with duplicates removed
        """
        if not track.transcription:
            return track
        
        # Analyze transcription for duplicates
        # This is a simplified implementation
        # In production, you'd use more sophisticated NLP techniques
        
        logger.info("Removing duplicate segments...")
        
        # For now, just return the original track
        # In production, you'd:
        # 1. Analyze transcription text for repeated phrases
        # 2. Identify time segments to remove
        # 3. Use ffmpeg to cut out those segments
        
        return track
    
    async def _apply_smart_pause_cutter(self, track: VideoTrack) -> VideoTrack:
        """
        Apply smart pause cutting to track.
        
        Args:
            track: Track with transcription
        
        Returns:
            Track with smart pause cutting applied
        """
        if not track.transcription:
            return track
        
        logger.info("Applying smart pause cutter...")
        
        # Analyze transcription for natural pause points
        # This is a simplified implementation
        # In production, you'd:
        # 1. Analyze audio for silence detection
        # 2. Identify natural speech boundaries
        # 3. Cut at appropriate pause points
        # 4. Maintain natural flow
        
        return track
    
    async def _combine_tracks(self, tracks: List[VideoTrack], settings) -> str:
        """
        Combine all tracks into final video.
        
        Args:
            tracks: List of processed tracks
            settings: Project settings
        
        Returns:
            Path to final video
        """
        logger.info("Combining tracks into final video...")
        
        output_path = self.temp_dir / f"final_output_{settings.aspect_ratio}.mp4"
        
        if len(tracks) == 1:
            # Single track, just copy
            cmd = [
                self.ffmpeg_path,
                "-i", tracks[0].file_path,
                "-c", "copy",
                "-y",
                str(output_path)
            ]
        else:
            # Multiple tracks, need to concatenate
            # Create file list for ffmpeg
            file_list_path = self.temp_dir / "file_list.txt"
            with open(file_list_path, 'w') as f:
                for track in tracks:
                    f.write(f"file '{track.file_path}'\n")
            
            cmd = [
                self.ffmpeg_path,
                "-f", "concat",
                "-safe", "0",
                "-i", str(file_list_path),
                "-c", "copy",
                "-y",
                str(output_path)
            ]
        
        await self._run_ffmpeg_command(cmd)
        
        return str(output_path)
    
    async def extract_audio(self, video_path: str) -> str:
        """
        Extract audio from video file.
        
        Args:
            video_path: Path to video file
        
        Returns:
            Path to extracted audio file
        """
        audio_path = self.temp_dir / f"extracted_audio_{Path(video_path).stem}.wav"
        
        cmd = [
            self.ffmpeg_path,
            "-i", video_path,
            "-vn",  # No video
            "-acodec", "pcm_s16le",  # Audio codec
            "-ar", "16000",  # Sample rate
            "-ac", "1",  # Mono
            "-y",
            str(audio_path)
        ]
        
        await self._run_ffmpeg_command(cmd)
        return str(audio_path)
    
    async def get_video_info(self, video_path: str) -> Dict[str, Any]:
        """
        Get video file information using ffprobe.
        
        Args:
            video_path: Path to video file
        
        Returns:
            Video information dictionary
        """
        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path
        ]
        
        result = await self._run_command(cmd)
        return json.loads(result)
    
    async def _run_ffmpeg_command(self, cmd: List[str]):
        """
        Run ffmpeg command asynchronously.
        
        Args:
            cmd: FFmpeg command as list
        """
        logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"FFmpeg command failed: {error_msg}")
        
        logger.debug("FFmpeg command completed successfully")
    
    async def _run_command(self, cmd: List[str]) -> str:
        """
        Run command and return output.
        
        Args:
            cmd: Command as list
        
        Returns:
            Command output
        """
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"Command failed: {error_msg}")
        
        return stdout.decode()


# Global service instance
video_processor = VideoProcessor()
