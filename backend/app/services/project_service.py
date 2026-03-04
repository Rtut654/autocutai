"""Service for managing and processing AI-assisted video editing projects."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.project import (
    Project,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    TrackType,
    VideoTrack,
)
from ..services.ai_service import ai_service
from ..services.pipeline_service import (
    build_subtitle_cues,
    find_gaps,
    normalize_words,
    write_subtitles_srt,
    write_word_level_srt,
)
from ..services.video_processor import VideoProcessor
from ..services.whisper_service import transcribe_audio

logger = logging.getLogger(__name__)


class ProjectService:
    """Service for managing video editing projects."""

    def __init__(self, projects_dir: str = "projects", temp_dir: str = "temp"):
        self.projects_dir = Path(projects_dir)
        self.temp_dir = Path(temp_dir)
        self.projects_dir.mkdir(exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)
        self.projects: Dict[str, Project] = {}
        logger.info("ProjectService initialized")

    async def create_project(self, request: ProjectCreateRequest, user_id: Optional[str] = None) -> Project:
        project_id = str(uuid.uuid4())
        tracks: List[VideoTrack] = []
        for i, file_path in enumerate(request.track_files):
            captured = None
            if request.track_capture_times and i < len(request.track_capture_times):
                captured = request.track_capture_times[i]

            extra_metadata: Dict[str, Any] = {}
            if request.track_metadata and i < len(request.track_metadata):
                extra_metadata = request.track_metadata[i] or {}

            track = await self._create_track_from_file(file_path, i, captured, extra_metadata)
            tracks.append(track)

        project = Project(
            id=project_id,
            name=request.name,
            description=request.description,
            tracks=tracks,
            settings=request.settings,
            user_id=user_id,
        )

        project.tracks = self._sorted_tracks(project.tracks)
        await self._save_project(project)
        self.projects[project_id] = project
        return project

    async def get_project(self, project_id: str) -> Optional[Project]:
        if project_id in self.projects:
            return self.projects[project_id]

        project_file = self.projects_dir / f"{project_id}.json"
        if project_file.exists():
            project = await self._load_project(project_file)
            self.projects[project_id] = project
            return project
        return None

    async def update_project(self, project_id: str, request: ProjectUpdateRequest) -> Optional[Project]:
        project = await self.get_project(project_id)
        if not project:
            return None

        if request.name is not None:
            project.name = request.name
        if request.description is not None:
            project.description = request.description
        if request.settings is not None:
            project.settings = request.settings
        if request.tracks is not None:
            project.tracks = request.tracks

        project.updated_at = datetime.utcnow()
        await self._save_project(project)
        self.projects[project_id] = project
        return project

    async def delete_project(self, project_id: str) -> bool:
        project = await self.get_project(project_id)
        if not project:
            return False

        self.projects.pop(project_id, None)
        project_file = self.projects_dir / f"{project_id}.json"
        if project_file.exists():
            project_file.unlink()
        return True

    async def list_projects(self, user_id: Optional[str] = None) -> List[Project]:
        projects = list(self.projects.values())
        if user_id:
            projects = [p for p in projects if p.user_id == user_id]
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)

    async def process_project(self, project_id: str) -> Project:
        project = await self.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        project.status = "processing"
        project.error_message = None
        await self._save_project(project)

        try:
            project.tracks = self._sorted_tracks(project.tracks)
            await self._transcribe_tracks(project)
            await self._build_pipeline(project)
            output_path = await self._generate_final_video(project)
            project.output_path = output_path
            project.status = "completed"
        except Exception as exc:
            project.status = "error"
            project.error_message = str(exc)
            logger.exception("Project processing failed", exc_info=exc)
            raise
        finally:
            project.updated_at = datetime.utcnow()
            await self._save_project(project)

        return project

    async def _create_track_from_file(
        self,
        file_path: str,
        position: int,
        recorded_at: Optional[datetime],
        extra_metadata: Dict[str, Any],
    ) -> VideoTrack:
        file_path_obj = Path(file_path)
        suffix = file_path_obj.suffix.lower()

        if suffix in [".mp4", ".mov", ".avi", ".mkv", ".m4v"]:
            track_type = TrackType.VIDEO
        elif suffix in [".mp3", ".wav", ".m4a", ".aac", ".oga"]:
            track_type = TrackType.AUDIO
        elif suffix in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
            track_type = TrackType.IMAGE
        else:
            track_type = TrackType.VIDEO

        stat = file_path_obj.stat()
        inferred_recorded_at = recorded_at or datetime.fromtimestamp(stat.st_mtime)

        metadata: Dict[str, Any] = {
            "filename": file_path_obj.name,
            "size": stat.st_size,
            "extension": suffix,
            "uploaded_at": datetime.utcnow().isoformat(),
        }
        metadata.update(extra_metadata)

        duration = 30.0
        if track_type in {TrackType.VIDEO, TrackType.AUDIO}:
            try:
                info = await VideoProcessor().get_video_info(str(file_path_obj))
                streams = info.get("streams", [])
                fmt = info.get("format", {})
                duration = float(fmt.get("duration", duration))
                location = self._extract_location_from_streams(streams, fmt)
                if location:
                    metadata["location"] = location
            except Exception:
                pass

        return VideoTrack(
            id=str(uuid.uuid4()),
            type=track_type,
            filename=file_path_obj.name,
            file_path=str(file_path_obj),
            duration=duration,
            position=position,
            metadata=metadata,
            recorded_at=inferred_recorded_at,
        )

    @staticmethod
    def _sorted_tracks(tracks: List[VideoTrack]) -> List[VideoTrack]:
        ordered = sorted(
            tracks,
            key=lambda t: (
                t.recorded_at or datetime.min,
                t.position,
                t.filename,
            ),
        )
        for idx, track in enumerate(ordered):
            track.position = idx
        return ordered

    async def _transcribe_tracks(self, project: Project) -> None:
        for track in project.tracks:
            if track.type not in {TrackType.VIDEO, TrackType.AUDIO}:
                continue
            with open(track.file_path, "rb") as handle:
                audio_data = handle.read()

            raw_transcription = await transcribe_audio(audio_data, track.filename, "en")
            words = normalize_words(
                raw_transcription.get("words")
                or [
                    word
                    for segment in raw_transcription.get("segments", [])
                    for word in segment.get("words", [])
                ]
            )

            track.has_voice = len(words) > 0
            track.transcription = {
                "text": raw_transcription.get("text") or raw_transcription.get("transcript") or "",
                "words": [word.model_dump() for word in words],
                "language": raw_transcription.get("language", "en"),
                "segments": raw_transcription.get("segments", []),
            }
            track.local_gap_ranges = find_gaps(words, project.settings.min_gap_seconds)

    async def _build_pipeline(self, project: Project) -> None:
        combined_words = []
        timeline_offset = 0.0
        locations: List[str] = []

        for track in project.tracks:
            if loc := track.metadata.get("location"):
                locations.append(str(loc))

            track_words = normalize_words((track.transcription or {}).get("words", []))
            for word in track_words:
                combined_words.append(
                    word.model_copy(update={"start": word.start + timeline_offset, "end": word.end + timeline_offset})
                )
            timeline_offset += track.duration

        combined_words = sorted(combined_words, key=lambda w: w.start)
        transcript = " ".join(word.word for word in combined_words).strip()
        gap_ranges = find_gaps(combined_words, project.settings.min_gap_seconds)

        word_srt_path = self.temp_dir / f"{project.id}_word_level.srt"
        subtitle_srt_path = self.temp_dir / f"{project.id}_subtitles.srt"

        write_word_level_srt(combined_words, word_srt_path)

        subtitle_cues = build_subtitle_cues(combined_words)
        if locations:
            for cue in subtitle_cues:
                cue.location = locations[0]
                cue.text = f"[{locations[0]}] {cue.text}"
        write_subtitles_srt(subtitle_cues, subtitle_srt_path)

        insertions = []
        if project.settings.insert_suggestions:
            insertions = await ai_service.suggest_insertions(combined_words)

        project.pipeline.combined_transcript = transcript
        project.pipeline.combined_words = combined_words
        project.pipeline.gap_ranges = gap_ranges
        project.pipeline.word_srt_path = str(word_srt_path)
        project.pipeline.subtitle_path = str(subtitle_srt_path)
        project.pipeline.subtitle_cues = subtitle_cues
        project.pipeline.locations = sorted(set(locations))
        project.pipeline.insertion_suggestions = insertions
        project.pipeline.render_plan = {
            "ordered_track_ids": [track.id for track in project.tracks],
            "auto_cut_enabled": project.settings.smart_pause_cutter,
            "gap_ranges": [gap.model_dump() for gap in gap_ranges],
            "subtitle_path": str(subtitle_srt_path) if project.settings.generate_subtitles else None,
        }

    async def _generate_final_video(self, project: Project) -> str:
        processor = VideoProcessor()
        return await processor.process_project(project)

    @staticmethod
    def _extract_location_from_streams(streams: List[Dict[str, Any]], fmt: Dict[str, Any]) -> Optional[str]:
        def _extract_from_tags(tags: Dict[str, Any]) -> Optional[str]:
            for key, value in tags.items():
                lowered = key.lower()
                if "location" in lowered or "com.apple.quicktime.location" in lowered:
                    return str(value)
            return None

        for stream in streams:
            tags = stream.get("tags", {})
            if tags:
                location = _extract_from_tags(tags)
                if location:
                    return location

        fmt_tags = fmt.get("tags", {})
        if fmt_tags:
            return _extract_from_tags(fmt_tags)
        return None

    async def _save_project(self, project: Project) -> None:
        project_file = self.projects_dir / f"{project.id}.json"
        payload = project.model_dump(mode="json")
        project_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def _load_project(self, project_file: Path) -> Project:
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        return Project(**payload)


project_service = ProjectService()
