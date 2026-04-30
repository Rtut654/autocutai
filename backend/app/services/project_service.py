"""Service for managing and processing AI-assisted video editing projects."""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.project import (
    BackgroundMusicSettings,
    TrackRole,
    EditMode,
    HybridProjectAnalyzeRequest,
    HybridTrackInput,
    Project,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    SpeechFilterArtifact,
    VisualPlanPart,
    VisualPlanArtifact,
    TrackRenderVersion,
    TrackOrientation,
    TrackType,
    VideoTrack,
    ZoomPreviewBeat,
)
from ..services.ai_service import ai_service
from ..services.pipeline_service import (
    build_subtitle_cues,
    find_gaps,
    normalize_words,
    sanitize_transcription_payload,
    write_subtitles_srt,
    write_word_level_srt,
)
from ..services.video_processor import VideoProcessor
from ..services.whisper_service import transcribe_audio
from ..models.transcription import WordTimestamp

logger = logging.getLogger(__name__)

VISUAL_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "how",
    "i", "if", "in", "into", "is", "it", "its", "not", "of", "on", "or", "so", "that", "the",
    "their", "then", "there", "they", "this", "to", "up", "was", "we", "what", "when", "which",
    "with", "you", "your", "once", "usually", "again",
}


class ProjectService:
    """Service for managing video editing projects."""

    def __init__(self, projects_dir: str = "projects", temp_dir: str = "temp"):
        repo_root = Path(__file__).resolve().parents[3]
        self.projects_dir = (repo_root / projects_dir).resolve() if not Path(projects_dir).is_absolute() else Path(projects_dir)
        self.temp_dir = (repo_root / temp_dir).resolve() if not Path(temp_dir).is_absolute() else Path(temp_dir)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(exist_ok=True)
        self.projects: Dict[str, Project] = {}
        self._active_transcription_jobs: set[str] = set()
        logger.info("ProjectService initialized")

    async def create_project(self, request: ProjectCreateRequest, user_id: Optional[str] = None) -> Project:
        project_id = str(uuid.uuid4())
        return await self.create_project_with_id(project_id, request, user_id=user_id)

    async def create_project_with_id(
        self,
        project_id: str,
        request: ProjectCreateRequest,
        user_id: Optional[str] = None,
    ) -> Project:
        if not user_id:
            raise ValueError("Authenticated user is required")
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

        project.tracks = self._ordered_tracks(project.tracks, project.settings.edit_mode)
        await self._save_project(project)
        self.projects[project_id] = project
        return project

    async def analyze_hybrid_project(
        self,
        request: HybridProjectAnalyzeRequest,
        user_id: Optional[str] = None,
    ) -> Project:
        if not user_id:
            raise ValueError("Authenticated user is required")
        project_id = str(uuid.uuid4())
        tracks = [self._create_track_from_hybrid_input(track, idx) for idx, track in enumerate(request.tracks)]

        project = Project(
            id=project_id,
            name=request.name,
            description=request.description,
            tracks=self._ordered_tracks(tracks, request.settings.edit_mode),
            settings=request.settings,
            user_id=user_id,
            status="processing",
        )

        await self._build_pipeline(project, render_strategy=request.render_strategy)
        project.status = "completed"
        project.output_path = None
        project.updated_at = datetime.utcnow()

        await self._save_project(project)
        self.projects[project_id] = project
        return project

    def get_user_projects_dir(self, user_id: str, *, create: bool = True) -> Path:
        root = self.projects_dir / user_id
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    def get_project_dir(self, user_id: str, project_id: str, *, create: bool = True) -> Path:
        directory = self.get_user_projects_dir(user_id, create=create) / project_id
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get_project_file(self, user_id: str, project_id: str, *, create: bool = False) -> Path:
        return self.get_project_dir(user_id, project_id, create=create) / "project.json"

    def get_project_video_dir(self, user_id: str, project_id: str) -> Path:
        directory = self.get_project_dir(user_id, project_id, create=True) / "video"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get_project_audio_dir(self, user_id: str, project_id: str) -> Path:
        directory = self.get_project_dir(user_id, project_id, create=True) / "audio"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get_project_transcript_dir(self, user_id: str, project_id: str) -> Path:
        directory = self.get_project_dir(user_id, project_id, create=True) / "transcript"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get_project_edits_dir(self, user_id: str, project_id: str) -> Path:
        directory = self.get_project_dir(user_id, project_id, create=True) / "edits"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get_track_edit_file(self, user_id: str, project_id: str, track_id: str) -> Path:
        return self.get_project_edits_dir(user_id, project_id) / f"{track_id}.json"

    def get_project_renders_dir(self, user_id: str, project_id: str) -> Path:
        directory = self.get_project_dir(user_id, project_id, create=True) / "renders"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get_track_renders_dir(self, user_id: str, project_id: str, track_id: str) -> Path:
        directory = self.get_project_renders_dir(user_id, project_id) / track_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def get_project(self, project_id: str, user_id: Optional[str] = None) -> Optional[Project]:
        cached = self.projects.get(project_id)
        if cached and (not user_id or cached.user_id == user_id):
            if self._sync_background_track_defaults(cached):
                await self._save_project(cached)
            return cached

        candidate_files: List[Path] = []
        if user_id:
            candidate_files.append(self.get_project_file(user_id, project_id, create=False))
        else:
            candidate_files.extend(self.projects_dir.glob(f"*/{project_id}/project.json"))

        for project_file in candidate_files:
            if project_file.exists():
                project = await self._load_project(project_file)
                if self._sync_background_track_defaults(project):
                    await self._save_project(project)
                self.projects[project_id] = project
                if not user_id or project.user_id == user_id:
                    return project
        return None

    async def update_project(
        self,
        project_id: str,
        request: ProjectUpdateRequest,
        user_id: Optional[str] = None,
    ) -> Optional[Project]:
        project = await self.get_project(project_id, user_id=user_id)
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

        project.tracks = self._ordered_tracks(project.tracks, project.settings.edit_mode)
        project.updated_at = datetime.utcnow()
        await self._save_project(project)
        self.projects[project_id] = project
        return project

    async def reorder_project_tracks(
        self,
        project_id: str,
        track_ids: List[str],
        *,
        user_id: Optional[str] = None,
    ) -> Optional[Project]:
        project = await self.get_project(project_id, user_id=user_id)
        if not project:
            return None

        current_by_id = {track.id: track for track in project.tracks}
        if len(track_ids) != len(project.tracks) or set(track_ids) != set(current_by_id.keys()):
            raise ValueError("Track reorder payload must include every project track exactly once")

        project.settings.edit_mode = EditMode.MANUAL
        project.tracks = self._normalize_track_positions([current_by_id[track_id] for track_id in track_ids])
        project.updated_at = datetime.utcnow()
        await self._save_project(project)
        self.projects[project.id] = project
        return project

    async def delete_project(self, project_id: str, user_id: Optional[str] = None) -> bool:
        project = await self.get_project(project_id, user_id=user_id)
        if not project:
            return False

        self.projects.pop(project_id, None)
        if not project.user_id:
            return False
        project_dir = self.get_project_dir(project.user_id, project.id, create=False)
        if project_dir.exists():
            shutil.rmtree(project_dir)
        return True

    def reserve_project_video_path(self, user_id: str, project_id: str, filename: str) -> Path:
        base_name = Path(filename).name
        media_dir = self.get_project_video_dir(user_id, project_id)
        candidate = media_dir / base_name
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        counter = 2
        while True:
            next_candidate = media_dir / f"{stem}-{counter}{suffix}"
            if not next_candidate.exists():
                return next_candidate
            counter += 1

    async def list_projects(self, user_id: Optional[str] = None) -> List[Project]:
        projects: List[Project] = []
        if user_id:
            root = self.get_user_projects_dir(user_id, create=False)
            candidate_files = sorted(root.glob("*/project.json"))
        else:
            candidate_files = sorted(self.projects_dir.glob("*/*/project.json"))

        for project_file in candidate_files:
            project = await self._load_project(project_file)
            if self._sync_background_track_defaults(project):
                await self._save_project(project)
            self.projects[project.id] = project
            projects.append(project)
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)

    async def _sanitize_existing_project_state(self, project: Project) -> bool:
        changed = False
        processor = VideoProcessor()
        for track in project.tracks:
            refreshed_recorded_at = await self._refresh_track_recorded_at_from_media_info(
                project,
                track,
                processor=processor,
            )
            if refreshed_recorded_at:
                changed = True

            if not self._track_is_transcribable(track):
                continue

            sanitized = sanitize_transcription_payload(track.transcription or {}, track.duration)
            silence_source = str(track.metadata.get("audio_path") or track.file_path or "")
            if silence_source:
                try:
                    silence_ranges = await processor.detect_silence_ranges(silence_source)
                    sanitized = self._align_transcription_to_detected_silence(
                        sanitized,
                        silence_ranges,
                        track.duration,
                    )
                except Exception:
                    pass
            words = normalize_words(sanitized.get("words") or [])
            sanitized_transcription = (
                {
                    "text": sanitized.get("text", ""),
                    "words": [word.model_dump() for word in words],
                    "language": sanitized.get("language", "en"),
                    "segments": sanitized.get("segments", []),
                }
                if (sanitized.get("text") or words)
                else None
            )
            next_has_voice = bool(words)
            next_gap_ranges = find_gaps(words, project.settings.min_gap_seconds, clip_duration=track.duration)
            existing_status = self._get_track_transcript_status(track)
            if sanitized_transcription:
                next_status = "completed"
            elif existing_status in {"pending", "processing", "error"}:
                next_status = existing_status
            else:
                next_status = "pending"

            if track.transcription != sanitized_transcription:
                track.transcription = sanitized_transcription
                changed = True
            if track.has_voice != next_has_voice:
                track.has_voice = next_has_voice
                changed = True
            if track.local_gap_ranges != next_gap_ranges:
                track.local_gap_ranges = next_gap_ranges
                changed = True
            if track.metadata.get("transcript_status") != next_status:
                self._set_track_transcript_status(track, next_status)
                changed = True

            refreshed_geometry = await self._refresh_track_geometry_from_media_info(
                project,
                track,
                processor=processor,
            )
            if refreshed_geometry:
                changed = True

            transcript_path = track.metadata.get("transcript_path")
            if transcript_path and isinstance(transcript_path, str):
                candidate = Path(transcript_path)
                if candidate.exists():
                    desired_text = (track.transcription or {}).get("text", "")
                    existing_text = candidate.read_text(encoding="utf-8")
                    if existing_text != desired_text:
                        candidate.write_text(desired_text, encoding="utf-8")
                        changed = True

        sorted_tracks = self._ordered_tracks(project.tracks, project.settings.edit_mode)
        if project.tracks != sorted_tracks:
            project.tracks = sorted_tracks
            changed = True

        if changed:
            project.updated_at = datetime.utcnow()
            await self._save_project(project)
        return changed

    async def _refresh_track_recorded_at_from_media_info(
        self,
        project: Project,
        track: VideoTrack,
        *,
        processor: VideoProcessor,
    ) -> bool:
        if not project.user_id:
            return False
        if str(track.file_path).startswith("client://"):
            return False

        media_path = self._resolve_project_media_path(project, track.file_path)
        if media_path is None:
            return False

        try:
            info = await processor.get_video_info(str(media_path))
        except Exception:
            return False

        streams = info.get("streams", [])
        fmt = info.get("format", {})
        extracted = self._extract_recorded_at_from_media_info(streams, fmt)
        if extracted is None:
            return False

        current = track.recorded_at
        if current is not None:
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            else:
                current = current.astimezone(timezone.utc)

        if current == extracted:
            return False

        track.recorded_at = extracted
        return True

    async def _refresh_track_geometry_from_media_info(
        self,
        project: Project,
        track: VideoTrack,
        *,
        processor: VideoProcessor,
    ) -> bool:
        if str(track.file_path).startswith("client://"):
            return False

        media_path = self._resolve_project_media_path(project, track.file_path)
        if media_path is None:
            return False

        try:
            info = await processor.get_video_info(str(media_path))
        except Exception:
            return False

        width, height, orientation = self._extract_track_geometry_from_media_info(info.get("streams", []))
        if width is None or height is None:
            return False

        changed = False
        if track.width != width:
            track.width = width
            changed = True
        if track.height != height:
            track.height = height
            changed = True
        if track.orientation != orientation:
            track.orientation = orientation
            changed = True
        return changed

    def _resolve_project_media_path(self, project: Project, path_value: str | None) -> Optional[Path]:
        if not path_value or not project.user_id:
            return None

        candidates = [
            Path(path_value),
            Path.cwd() / path_value,
            Path.cwd().parent / path_value,
            self.get_project_dir(project.user_id, project.id, create=True) / Path(path_value).name,
            self.get_project_video_dir(project.user_id, project.id) / Path(path_value).name,
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @classmethod
    def _align_transcription_to_detected_silence(
        cls,
        sanitized: Dict[str, Any],
        silence_ranges: List[tuple[float, float]],
        clip_duration: float,
    ) -> Dict[str, Any]:
        words = [dict(word) for word in (sanitized.get("words") or [])]
        segments = [dict(segment) for segment in (sanitized.get("segments") or [])]
        if not words:
            return sanitized

        next_anchor = float(words[1].get("start", words[0].get("end", 0.0))) if len(words) > 1 else clip_duration
        leading_onset = cls._infer_leading_speech_onset(silence_ranges, next_anchor)
        first_start = float(words[0].get("start", 0.0))
        first_end = float(words[0].get("end", 0.0))
        first_duration = max(0.0, first_end - first_start)
        should_fix_leading = (
            first_start <= 0.05
            or (
                leading_onset is not None
                and leading_onset > first_start + 0.25
                and first_duration > 2.0
            )
        )
        if leading_onset is not None and should_fix_leading:
            if leading_onset < float(words[0].get("end", 0.0)) - 0.05:
                words[0]["start"] = round(leading_onset, 3)
                if segments:
                    segments[0]["start"] = round(max(float(segments[0].get("start", 0.0)), leading_onset), 3)
                    if segments[0].get("words"):
                        segment_words = [dict(word) for word in segments[0]["words"]]
                        segment_words[0]["start"] = round(leading_onset, 3)
                        segments[0]["words"] = segment_words

        previous_anchor = float(words[-2].get("end", words[-1].get("start", 0.0))) if len(words) > 1 else 0.0
        trailing_cutoff = cls._infer_trailing_speech_end(silence_ranges, previous_anchor, clip_duration)
        if trailing_cutoff is not None and float(words[-1].get("end", 0.0)) > trailing_cutoff + 0.05:
            if trailing_cutoff > float(words[-1].get("start", 0.0)) + 0.05:
                words[-1]["end"] = round(trailing_cutoff, 3)
                if segments:
                    segments[-1]["end"] = round(min(float(segments[-1].get("end", clip_duration)), trailing_cutoff), 3)
                    if segments[-1].get("words"):
                        segment_words = [dict(word) for word in segments[-1]["words"]]
                        segment_words[-1]["end"] = round(trailing_cutoff, 3)
                        segments[-1]["words"] = segment_words

        sanitized["words"] = words
        sanitized["segments"] = segments
        return sanitized

    @staticmethod
    def _infer_leading_speech_onset(silence_ranges: List[tuple[float, float]], next_anchor: float) -> Optional[float]:
        if next_anchor <= 0.5:
            return None
        candidates = [end for _, end in silence_ranges if end < next_anchor - 0.05]
        return max(candidates) if candidates else None

    @staticmethod
    def _infer_trailing_speech_end(
        silence_ranges: List[tuple[float, float]],
        previous_anchor: float,
        clip_duration: float,
    ) -> Optional[float]:
        candidates = [
            start
            for start, end in silence_ranges
            if start > previous_anchor + 0.05 and end <= clip_duration + 0.25
        ]
        return min(candidates) if candidates else None

    async def process_project(self, project_id: str, user_id: Optional[str] = None) -> Project:
        project = await self.get_project(project_id, user_id=user_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        project.status = "processing"
        project.error_message = None
        await self._save_project(project)

        try:
            project.tracks = self._ordered_tracks(project.tracks, project.settings.edit_mode)
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

    async def get_track_speech_filter(
        self,
        project_id: str,
        track_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[SpeechFilterArtifact]:
        project = await self.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            return None

        track = next((item for item in project.tracks if item.id == track_id), None)
        if not track:
            return None

        artifact_path = self.get_track_edit_file(project.user_id, project.id, track_id)
        if not artifact_path.exists():
            return None
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return SpeechFilterArtifact(**payload)

    async def generate_track_speech_filter(
        self,
        project_id: str,
        track_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> SpeechFilterArtifact:
        project = await self.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            raise ValueError("Project not found")

        track = next((item for item in project.tracks if item.id == track_id), None)
        if not track:
            raise ValueError("Track not found")

        words = normalize_words((track.transcription or {}).get("words", []))
        if not words:
            raise ValueError("Transcript is required before speech filtering")

        track.metadata["speech_filter_status"] = "processing"
        project.updated_at = datetime.utcnow()
        await self._save_project(project)

        try:
            artifact = await ai_service.suggest_speech_filter_cuts(
                project_id=project.id,
                track_id=track.id,
                filename=track.filename,
                words=words,
                duration=float(track.duration or 0.0),
                transcript_segments=(track.transcription or {}).get("segments", []),
                min_gap_seconds=float(project.settings.min_gap_seconds or 1.0),
            )
        except Exception as exc:
            track.metadata["speech_filter_status"] = "error"
            track.metadata["speech_filter_error"] = str(exc)
            project.updated_at = datetime.utcnow()
            await self._save_project(project)
            raise

        return await self._persist_track_speech_filter(project, track, artifact)

    async def update_track_speech_filter(
        self,
        project_id: str,
        track_id: str,
        cuts: List[Dict[str, Any]] | List[Any],
        *,
        user_id: Optional[str] = None,
    ) -> SpeechFilterArtifact:
        project = await self.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            raise ValueError("Project not found")

        track = next((item for item in project.tracks if item.id == track_id), None)
        if not track:
            raise ValueError("Track not found")

        existing = await self.get_track_speech_filter(project_id, track_id, user_id=user_id)
        model_name = existing.model if existing else str(track.metadata.get("speech_filter_model") or "manual")
        normalized_cuts = self._normalize_manual_speech_filter_cuts(cuts, track.duration)
        artifact = SpeechFilterArtifact(
            project_id=project.id,
            track_id=track.id,
            filename=track.filename,
            status="completed",
            summary=self._summarize_speech_filter_cuts(normalized_cuts),
            cuts=normalized_cuts,
            zoom_beats=existing.zoom_beats if existing else [],
            source_word_count=len(normalize_words((track.transcription or {}).get("words", []))),
            model=model_name,
        )
        return await self._persist_track_speech_filter(project, track, artifact)

    async def _persist_track_speech_filter(
        self,
        project: Project,
        track: VideoTrack,
        artifact: SpeechFilterArtifact,
    ) -> SpeechFilterArtifact:
        if not project.user_id:
            raise ValueError("Project owner missing")

        artifact_path = self.get_track_edit_file(project.user_id, project.id, track.id)
        artifact_path.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2), encoding="utf-8")

        track.metadata["speech_filter_status"] = artifact.status
        track.metadata["speech_filter_path"] = str(artifact_path)
        track.metadata["speech_filter_cut_count"] = len(artifact.cuts)
        track.metadata["speech_filter_model"] = artifact.model
        track.metadata["speech_filter_generated_at"] = artifact.generated_at.isoformat()
        track.metadata.pop("speech_filter_error", None)
        project.updated_at = datetime.utcnow()
        await self._save_project(project)
        self.projects[project.id] = project
        return artifact

    @staticmethod
    def _summarize_speech_filter_cuts(cuts: List[Any]) -> str:
        if not cuts:
            return "No suggested cuts."
        total = sum(max(0.0, float(cut.duration)) for cut in cuts)
        return f"{len(cuts)} suggested cut{'s' if len(cuts) != 1 else ''}, about {total:.1f}s total."

    @staticmethod
    def _normalize_manual_speech_filter_cuts(cuts: List[Dict[str, Any]] | List[Any], duration: float) -> List[Any]:
        normalized = []
        for raw in cuts:
            start = max(0.0, float(getattr(raw, "start", None) if hasattr(raw, "start") else raw.get("start", 0.0)))
            end = min(float(duration), float(getattr(raw, "end", None) if hasattr(raw, "end") else raw.get("end", 0.0)))
            if end <= start:
                continue
            reason = getattr(raw, "reason", None) if hasattr(raw, "reason") else raw.get("reason", "manual_adjustment")
            transcript = getattr(raw, "transcript", None) if hasattr(raw, "transcript") else raw.get("transcript", "")
            confidence = getattr(raw, "confidence", None) if hasattr(raw, "confidence") else raw.get("confidence", 1.0)
            normalized.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(end - start, 3),
                    "reason": str(reason or "manual_adjustment"),
                    "transcript": str(transcript or "").strip(),
                    "confidence": max(0.0, min(1.0, float(confidence or 1.0))),
                }
            )
        normalized.sort(key=lambda item: (item["start"], item["end"]))
        merged: List[Dict[str, Any]] = []
        for item in normalized:
            if merged and item["start"] < merged[-1]["end"] - 0.01:
                merged[-1]["end"] = max(merged[-1]["end"], item["end"])
                merged[-1]["duration"] = round(merged[-1]["end"] - merged[-1]["start"], 3)
                if item["confidence"] > merged[-1]["confidence"]:
                    merged[-1]["confidence"] = item["confidence"]
                if item["reason"] not in merged[-1]["reason"]:
                    merged[-1]["reason"] = f"{merged[-1]['reason']}+{item['reason']}"
                if item["transcript"]:
                    merged[-1]["transcript"] = " ".join(
                        part for part in [merged[-1]["transcript"], item["transcript"]] if part
                    ).strip()
                continue
            merged.append(item)
        from ..models.project import SpeechFilterCut
        return [SpeechFilterCut(**item) for item in merged]

    async def render_track_version(
        self,
        project_id: str,
        track_id: str,
        *,
        cuts: List[Dict[str, Any]] | List[Any] | None = None,
        user_id: Optional[str] = None,
    ) -> TrackRenderVersion:
        project = await self.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            raise ValueError("Project not found")

        track = next((item for item in project.tracks if item.id == track_id), None)
        if not track:
            raise ValueError("Track not found")
        if track.type != TrackType.VIDEO:
            raise ValueError("Only video tracks can be rendered")

        normalized_cuts = self._normalize_manual_speech_filter_cuts(cuts or [], track.duration)
        existing_speech_filter = await self.get_track_speech_filter(project_id, track_id, user_id=user_id)
        if normalized_cuts:
            artifact = SpeechFilterArtifact(
                project_id=project.id,
                track_id=track.id,
                filename=track.filename,
                status="completed",
                summary=self._summarize_speech_filter_cuts(normalized_cuts),
                cuts=normalized_cuts,
                zoom_beats=existing_speech_filter.zoom_beats if existing_speech_filter else [],
                source_word_count=len(normalize_words((track.transcription or {}).get("words", []))),
                model=existing_speech_filter.model if existing_speech_filter else "manual",
            )
            await self._persist_track_speech_filter(project, track, artifact)
            existing_speech_filter = artifact

        source_path = self._resolve_project_media_path(project, track.file_path)
        if source_path is None or not source_path.exists():
            raise ValueError("Track media not found")

        processor = VideoProcessor()
        keep_segments = self._segments_from_cut_ranges(track.duration, normalized_cuts)
        render_dir = self.get_track_renders_dir(project.user_id, project.id, track.id)
        version_id = uuid.uuid4().hex
        timestamp_label = datetime.now().strftime("%d.%m.%Y.%H%M")
        output_path = self._reserve_named_output(render_dir, timestamp_label, ".mp4")

        visual_plan = await self.get_track_visual_plan(project.id, track.id, user_id=project.user_id)
        zoom_beats = existing_speech_filter.zoom_beats if existing_speech_filter else []
        visual_parts = visual_plan.parts if visual_plan else []
        aligned_visual_parts = self._align_visual_parts_to_preview_timing(visual_parts, track)
        remapped_zoom_beats = self._remap_zoom_beats_to_render_timeline(zoom_beats, keep_segments)
        remapped_visual_parts = self._remap_visual_parts_to_render_timeline(aligned_visual_parts, keep_segments)

        cut_source_path = render_dir / f"{Path(output_path).stem}__cut.mp4"
        staged_source = Path(
            await processor.render_source_segments(
                source_path=source_path,
                segments=keep_segments,
                output_path=cut_source_path,
            )
        )

        if remapped_zoom_beats or remapped_visual_parts:
            styled_path = render_dir / f"{Path(output_path).stem}__styled.mp4"
            staged_source = Path(
                await processor.render_styled_track(
                    source_path=staged_source,
                    output_path=styled_path,
                    width=int(track.width or 720),
                    height=int(track.height or 1280),
                    zoom_beats=remapped_zoom_beats,
                    visual_parts=remapped_visual_parts,
                )
            )
            cut_source_path.unlink(missing_ok=True)

        if remapped_visual_parts:
            sfx_path = render_dir / f"{Path(output_path).stem}__sfx.mp4"
            previous_staged_source = staged_source
            staged_source = Path(
                await processor.mix_visual_sfx(
                    video_input=staged_source,
                    output_path=sfx_path,
                    cue_times=[float(part.start) for part in remapped_visual_parts],
                )
            )
            if staged_source != previous_staged_source and previous_staged_source != source_path:
                previous_staged_source.unlink(missing_ok=True)

        dry_output_path = output_path
        if track.background_music.enabled:
            dry_output_path = render_dir / f"{Path(output_path).stem}__dry.mp4"
            shutil.copyfile(staged_source, dry_output_path)
            rendered_path = str(dry_output_path)
        else:
            rendered_path = str(staged_source)
        if Path(rendered_path) != staged_source and staged_source != source_path:
            Path(staged_source).unlink(missing_ok=True)

        if track.background_music.enabled:
            bgm_path = render_dir / f"{Path(output_path).stem}__bgm.wav"
            await processor.generate_background_music_track(
                output_path=bgm_path,
                duration=max(0.1, round(sum(max(0.0, end - start) for start, end in keep_segments), 3)),
                preset=str(track.background_music.preset.value if hasattr(track.background_music.preset, "value") else track.background_music.preset),
            )
            rendered_path = await processor.mix_background_music(
                video_input=rendered_path,
                music_input=bgm_path,
                output_path=output_path,
                music_volume=float(track.background_music.volume),
                ducking=float(track.background_music.ducking),
            )
            Path(dry_output_path).unlink(missing_ok=True)
            bgm_path.unlink(missing_ok=True)

        if project.settings.generate_subtitles:
            remapped_words = self._remap_track_words_to_render_timeline(track, keep_segments)
            subtitle_cues = build_subtitle_cues(remapped_words)
            if subtitle_cues:
                subtitle_srt_path = render_dir / f"{Path(output_path).stem}__subtitles.srt"
                subtitled_path = render_dir / f"{Path(output_path).stem}__subtitled.mp4"
                write_subtitles_srt(subtitle_cues, subtitle_srt_path)
                previous_rendered_path = Path(rendered_path)
                try:
                    rendered_path = await processor.burn_subtitles(
                        video_input=previous_rendered_path,
                        subtitle_path=subtitle_srt_path,
                        output_path=subtitled_path,
                    )
                    if previous_rendered_path != source_path and previous_rendered_path.exists():
                        previous_rendered_path.unlink(missing_ok=True)
                except RuntimeError as exc:
                    if processor._can_skip_subtitle_burn(exc):
                        logger.warning("Falling back to drawtext subtitle burn-in: %s", exc)
                        try:
                            rendered_path = await processor.burn_subtitles_from_cues(
                                video_input=previous_rendered_path,
                                cues=subtitle_cues,
                                output_path=subtitled_path,
                            )
                        except RuntimeError as drawtext_exc:
                            logger.warning("Falling back to embedded subtitle track: %s", drawtext_exc)
                            rendered_path = await processor.attach_subtitle_track(
                                video_input=previous_rendered_path,
                                subtitle_path=subtitle_srt_path,
                                output_path=subtitled_path,
                            )
                        if previous_rendered_path != source_path and previous_rendered_path.exists():
                            previous_rendered_path.unlink(missing_ok=True)
                    else:
                        raise
                finally:
                    subtitle_srt_path.unlink(missing_ok=True)

        duration_after = round(sum(max(0.0, end - start) for start, end in keep_segments), 3)
        version = TrackRenderVersion(
            id=version_id,
            label=Path(rendered_path).stem,
            filename=Path(rendered_path).name,
            file_path=str(rendered_path),
            cut_count=len(normalized_cuts),
            duration_before=round(float(track.duration or 0.0), 3),
            duration_after=duration_after,
        )
        track.render_versions.append(version)
        track.metadata["latest_render_version_id"] = version.id
        track.metadata["render_version_count"] = len(track.render_versions)
        project.updated_at = datetime.utcnow()
        await self._save_project(project)
        self.projects[project.id] = project
        return version

    async def update_track_background_music(
        self,
        project_id: str,
        track_id: str,
        settings: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
    ) -> Project:
        project = await self.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            raise ValueError("Project not found")

        track = next((item for item in project.tracks if item.id == track_id), None)
        if not track:
            raise ValueError("Track not found")

        track.background_music = BackgroundMusicSettings(**settings)
        project.updated_at = datetime.utcnow()
        await self._save_project(project)
        self.projects[project.id] = project
        return project

    async def update_track_background_video(
        self,
        project_id: str,
        track_id: str,
        settings: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
    ) -> Project:
        project = await self.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            raise ValueError("Project not found")

        track = next((item for item in project.tracks if item.id == track_id), None)
        if not track:
            raise ValueError("Track not found")
        if track.type not in {TrackType.VIDEO, TrackType.IMAGE}:
            raise ValueError("Only video or image tracks can be labeled as background")

        raw_role = settings.get("role")
        role = track.role if raw_role is None else raw_role
        if not isinstance(role, TrackRole):
            role = TrackRole(str(role))

        track.role = role
        if "background_description" in settings:
            track.background_description = str(settings.get("background_description") or "").strip() or None
        if "background_trim_start" in settings:
            track.background_trim_start = float(settings.get("background_trim_start") or 0.0)
        if "background_trim_end" in settings:
            raw_end = settings.get("background_trim_end")
            track.background_trim_end = None if raw_end is None else float(raw_end)
        if "background_playback_rate" in settings:
            track.background_playback_rate = float(settings.get("background_playback_rate") or 1.0)
        self._normalize_background_track_edit_state(track)
        project.updated_at = datetime.utcnow()
        await self._save_project(project)
        self.projects[project.id] = project
        return project

    async def get_track_visual_plan(
        self,
        project_id: str,
        track_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[VisualPlanArtifact]:
        project = await self.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            return None
        artifact_path = self.get_project_dir(project.user_id, project.id, create=True) / "visual_worker" / f"{track_id}.json"
        if not artifact_path.exists():
            return None
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return VisualPlanArtifact(**payload)

    async def get_track_render_version(
        self,
        project_id: str,
        track_id: str,
        version_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> tuple[Project, VideoTrack, TrackRenderVersion] | None:
        project = await self.get_project(project_id, user_id=user_id)
        if not project:
            return None
        track = next((item for item in project.tracks if item.id == track_id), None)
        if not track:
            return None
        version = next((item for item in track.render_versions if item.id == version_id), None)
        if not version:
            return None
        return project, track, version

    @staticmethod
    def _segments_from_cut_ranges(duration: float, cuts: List[Any]) -> List[tuple[float, float]]:
        safe_duration = max(0.0, float(duration or 0.0))
        if safe_duration <= 0:
            return []
        if not cuts:
            return [(0.0, safe_duration)]

        ordered = sorted(cuts, key=lambda cut: (float(cut.start), float(cut.end)))
        segments: List[tuple[float, float]] = []
        cursor = 0.0
        for cut in ordered:
            start = max(0.0, min(safe_duration, float(cut.start)))
            end = max(start, min(safe_duration, float(cut.end)))
            if start > cursor + 0.001:
                segments.append((round(cursor, 3), round(start, 3)))
            cursor = max(cursor, end)
        if cursor < safe_duration - 0.001:
            segments.append((round(cursor, 3), round(safe_duration, 3)))
        return [(start, end) for start, end in segments if end - start >= 0.05]

    @staticmethod
    def _map_time_to_render_timeline(time_value: float, keep_segments: List[tuple[float, float]]) -> Optional[float]:
        elapsed = 0.0
        for start, end in keep_segments:
            if time_value < start:
                return elapsed
            if start <= time_value <= end:
                return round(elapsed + (time_value - start), 3)
            elapsed += max(0.0, end - start)
        return None

    @classmethod
    def _remap_zoom_beats_to_render_timeline(
        cls,
        zoom_beats: List[ZoomPreviewBeat],
        keep_segments: List[tuple[float, float]],
    ) -> List[ZoomPreviewBeat]:
        remapped: List[ZoomPreviewBeat] = []
        for beat in zoom_beats:
            start = cls._map_time_to_render_timeline(float(beat.start), keep_segments)
            end = cls._map_time_to_render_timeline(float(beat.end), keep_segments)
            if start is None or end is None or end - start < 0.08:
                continue
            remapped.append(
                ZoomPreviewBeat(
                    start=start,
                    end=end,
                    duration=round(end - start, 3),
                    text=beat.text,
                    enabled=beat.enabled,
                    scale=beat.scale,
                )
            )
        return remapped

    @classmethod
    def _remap_visual_parts_to_render_timeline(
        cls,
        visual_parts: List[VisualPlanPart],
        keep_segments: List[tuple[float, float]],
    ) -> List[VisualPlanPart]:
        remapped: List[VisualPlanPart] = []
        for part in visual_parts:
            start = cls._map_time_to_render_timeline(float(part.start), keep_segments)
            end = cls._map_time_to_render_timeline(float(part.end), keep_segments)
            if start is None or end is None or end - start < 0.12:
                continue
            remapped.append(
                part.model_copy(
                    update={
                        "start": start,
                        "end": end,
                        "duration": round(end - start, 3),
                    }
                )
            )
        return remapped

    @staticmethod
    def _normalize_spoken_token(value: str) -> str:
        return re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", value.lower()).strip()

    @classmethod
    def _get_normalized_transcript_words(cls, track: VideoTrack) -> List[Dict[str, Any]]:
        words = normalize_words((track.transcription or {}).get("words", []))
        normalized: List[Dict[str, Any]] = []
        for index, word in enumerate(words):
            token = cls._normalize_spoken_token(str(word.word or ""))
            start = max(0.0, float(word.start or 0.0))
            end = max(start, float(word.end or 0.0))
            if not token:
                continue
            normalized.append(
                {
                    "index": index,
                    "word": str(word.word or "").strip(),
                    "token": token,
                    "start": start,
                    "end": end,
                }
            )
        return normalized

    @classmethod
    def _build_visual_search_tokens(cls, part: VisualPlanPart) -> List[str]:
        base_tokens = [
            token
            for token in (
                cls._normalize_spoken_token(segment)
                for segment in " ".join(
                    [
                        str(part.text or ""),
                        str(part.title or ""),
                        *[str(item) for item in (part.keywords or [])],
                    ]
                ).split()
            )
            if token
        ]
        filtered = [
            token
            for token in base_tokens
            if len(token) > 2 and token not in VISUAL_STOP_WORDS
        ]
        return filtered if len(filtered) >= 2 else base_tokens

    @staticmethod
    def _find_ordered_phrase_match(
        transcript_tokens: List[str],
        phrase_tokens: List[str],
    ) -> Optional[tuple[int, int]]:
        max_window = min(6, len(phrase_tokens))
        for window_size in range(max_window, 1, -1):
            for phrase_start in range(0, len(phrase_tokens) - window_size + 1):
                phrase_slice = phrase_tokens[phrase_start : phrase_start + window_size]
                for transcript_start in range(0, len(transcript_tokens) - window_size + 1):
                    if transcript_tokens[transcript_start : transcript_start + window_size] == phrase_slice:
                        return transcript_start, transcript_start + window_size - 1
        return None

    @classmethod
    def _resolve_visual_part_timing(
        cls,
        part: VisualPlanPart,
        transcript_words: List[Dict[str, Any]],
    ) -> tuple[float, float, float]:
        fallback_start = max(0.0, float(part.start or 0.0))
        fallback_end = max(fallback_start + 0.18, float(part.end or fallback_start + 0.18))
        if not transcript_words:
            return fallback_start, fallback_end, fallback_end - fallback_start

        phrase_tokens = cls._build_visual_search_tokens(part)
        if not phrase_tokens:
            return fallback_start, fallback_end, fallback_end - fallback_start

        window_start = max(0.0, fallback_start - 0.45)
        window_end = fallback_end + 0.45
        candidate_words = [
            word
            for word in transcript_words
            if float(word["end"]) >= window_start and float(word["start"]) <= window_end
        ]
        if not candidate_words:
            return fallback_start, fallback_end, fallback_end - fallback_start

        exact_match = cls._find_ordered_phrase_match(
            [str(word["token"]) for word in candidate_words],
            phrase_tokens,
        )
        if exact_match is not None:
            first = candidate_words[exact_match[0]]
            last = candidate_words[exact_match[1]]
            aligned_start = max(fallback_start, float(first["start"]) - 0.08)
            aligned_end = min(fallback_end, max(float(last["end"]) + 0.55, aligned_start + 0.75))
            return aligned_start, aligned_end, aligned_end - aligned_start

        fallback_token = next(
            (token for token in phrase_tokens if len(token) > 3 and token not in VISUAL_STOP_WORDS),
            phrase_tokens[0],
        )
        anchor = next((word for word in candidate_words if str(word["token"]) == fallback_token), None)
        if anchor is not None:
            aligned_start = max(fallback_start, float(anchor["start"]) - 0.08)
            min_hold = min(1.4, max(0.75, float(part.duration or 0.0) * 0.45))
            aligned_end = min(fallback_end, max(float(anchor["end"]) + 0.9, aligned_start + min_hold))
            return aligned_start, aligned_end, aligned_end - aligned_start

        return fallback_start, fallback_end, fallback_end - fallback_start

    @classmethod
    def _enforce_minimum_visual_duration(
        cls,
        parts: List[Dict[str, Any]],
        max_end: float,
    ) -> List[Dict[str, Any]]:
        min_visible_seconds = 3.0
        bounded_max_end = max(min_visible_seconds, max_end or 0.0)
        resolved: List[Dict[str, Any]] = []
        for item in parts:
            previous = resolved[-1] if resolved else None
            min_start = float(previous["start"]) + min_visible_seconds if previous else 0.0
            start = max(float(item["start"]), min_start)
            end = min(bounded_max_end, max(float(item["end"]), start + min_visible_seconds))
            duration = max(0.001, end - start)
            if duration >= min_visible_seconds - 0.001:
                resolved.append(
                    {
                        **item,
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "duration": round(duration, 3),
                    }
                )
        return resolved

    @classmethod
    def _align_visual_parts_to_preview_timing(
        cls,
        visual_parts: List[VisualPlanPart],
        track: VideoTrack,
    ) -> List[VisualPlanPart]:
        if not visual_parts:
            return []
        transcript_words = cls._get_normalized_transcript_words(track)
        resolved_items: List[Dict[str, Any]] = []
        for index, part in enumerate(visual_parts):
            start, end, duration = cls._resolve_visual_part_timing(part, transcript_words)
            if duration <= 0.12:
                continue
            resolved_items.append(
                {
                    "part": part,
                    "index": index,
                    "start": start,
                    "end": end,
                    "duration": duration,
                }
            )
        enforced = cls._enforce_minimum_visual_duration(resolved_items, float(track.duration or 0.0))
        return [
            item["part"].model_copy(
                update={
                    "start": item["start"],
                    "end": item["end"],
                    "duration": item["duration"],
                }
            )
            for item in enforced
        ]

    @classmethod
    def _remap_track_words_to_render_timeline(
        cls,
        track: VideoTrack,
        keep_segments: List[tuple[float, float]],
    ) -> List[WordTimestamp]:
        remapped: List[WordTimestamp] = []
        for word in normalize_words((track.transcription or {}).get("words", [])):
            start = cls._map_time_to_render_timeline(float(word.start), keep_segments)
            end = cls._map_time_to_render_timeline(float(word.end), keep_segments)
            if start is None or end is None or end - start < 0.02:
                continue
            remapped.append(
                WordTimestamp(
                    word=str(word.word),
                    start=start,
                    end=end,
                    confidence=word.confidence,
                )
            )
        return remapped

    @staticmethod
    def _track_is_transcribable(track: VideoTrack) -> bool:
        return track.type in {TrackType.VIDEO, TrackType.AUDIO}

    @staticmethod
    def _track_has_transcript(track: VideoTrack) -> bool:
        transcription = track.transcription or {}
        text = transcription.get("text") or transcription.get("transcript") or ""
        words = transcription.get("words") or []
        return bool(str(text).strip()) or bool(words)

    @classmethod
    def _get_track_transcript_status(cls, track: VideoTrack) -> Optional[str]:
        if cls._track_has_transcript(track):
            return "completed"
        status = track.metadata.get("transcript_status")
        return status if isinstance(status, str) else None

    @staticmethod
    def _set_track_transcript_status(track: VideoTrack, status: str, error: Optional[str] = None) -> None:
        track.metadata["transcript_status"] = status
        if error:
            track.metadata["transcript_error"] = error
        else:
            track.metadata.pop("transcript_error", None)

    @classmethod
    def _track_needs_transcription(cls, track: VideoTrack) -> bool:
        if not cls._track_is_transcribable(track):
            return False
        if cls._track_has_transcript(track):
            return False
        return cls._get_track_transcript_status(track) != "not_applicable"

    def project_has_missing_transcripts(self, project: Project) -> bool:
        return any(self._track_needs_transcription(track) for track in project.tracks)

    @classmethod
    def _sync_background_track_defaults(cls, project: Project) -> bool:
        changed = False
        for track in project.tracks:
            if track.excluded:
                continue
            if track.type == TrackType.VIDEO:
                transcript_status = cls._get_track_transcript_status(track)
                has_transcript = cls._track_has_transcript(track)
                should_be_background = transcript_status in {"completed", "not_applicable", "error"} and not has_transcript
                if should_be_background and track.role != TrackRole.BACKGROUND:
                    track.role = TrackRole.BACKGROUND
                    changed = True
            if cls._normalize_background_track_edit_state(track):
                changed = True
        return changed

    @staticmethod
    def _normalize_background_track_edit_state(track: VideoTrack) -> bool:
        changed = False
        if track.type != TrackType.VIDEO:
            if track.background_trim_start != 0:
                track.background_trim_start = 0.0
                changed = True
            if track.background_trim_end is not None:
                track.background_trim_end = None
                changed = True
            if abs(float(track.background_playback_rate or 1.0) - 1.0) > 1e-6:
                track.background_playback_rate = 1.0
                changed = True
            return changed

        safe_duration = max(0.1, float(track.duration or 0.1))
        trim_start = max(0.0, min(safe_duration - 0.05, float(track.background_trim_start or 0.0)))
        trim_end_raw = safe_duration if track.background_trim_end is None else float(track.background_trim_end)
        trim_end = max(trim_start + 0.05, min(safe_duration, trim_end_raw))
        playback_rate = max(0.5, min(10.0, float(track.background_playback_rate or 1.0)))

        if abs(trim_start - float(track.background_trim_start or 0.0)) > 1e-6:
            track.background_trim_start = round(trim_start, 3)
            changed = True
        if track.background_trim_end is None or abs(trim_end - float(track.background_trim_end or 0.0)) > 1e-6:
            track.background_trim_end = round(trim_end, 3)
            changed = True
        if abs(playback_rate - float(track.background_playback_rate or 1.0)) > 1e-6:
            track.background_playback_rate = round(playback_rate, 3)
            changed = True
        return changed

    @classmethod
    def get_background_track_trim_bounds(cls, track: VideoTrack) -> tuple[float, float]:
        if track.type != TrackType.VIDEO:
            return (0.0, 0.0)
        safe_duration = max(0.1, float(track.duration or 0.1))
        trim_start = max(0.0, min(safe_duration - 0.05, float(track.background_trim_start or 0.0)))
        trim_end = safe_duration if track.background_trim_end is None else float(track.background_trim_end)
        trim_end = max(trim_start + 0.05, min(safe_duration, trim_end))
        return round(trim_start, 3), round(trim_end, 3)

    @classmethod
    def get_background_track_effective_duration(cls, track: VideoTrack) -> float:
        if track.type == TrackType.IMAGE:
            return 4.0
        if track.type != TrackType.VIDEO:
            return max(0.1, float(track.duration or 0.1))
        trim_start, trim_end = cls.get_background_track_trim_bounds(track)
        playback_rate = max(0.5, min(10.0, float(track.background_playback_rate or 1.0)))
        return round(max(0.05, trim_end - trim_start) / playback_rate, 3)

    def mark_missing_transcripts_pending(self, project: Project) -> bool:
        changed = False
        for track in project.tracks:
            if not self._track_is_transcribable(track):
                continue
            if self._track_has_transcript(track):
                if self._get_track_transcript_status(track) != "completed":
                    self._set_track_transcript_status(track, "completed")
                    changed = True
                continue
            status = self._get_track_transcript_status(track)
            if status not in {"pending", "processing"}:
                self._set_track_transcript_status(track, "pending")
                changed = True
        return changed

    async def backfill_missing_transcripts(self, project_id: str, user_id: Optional[str] = None) -> Optional[Project]:
        if project_id in self._active_transcription_jobs:
            return await self.get_project(project_id, user_id=user_id)

        self._active_transcription_jobs.add(project_id)
        try:
            project = await self.get_project(project_id, user_id=user_id)
            if not project:
                return None
            if not self.project_has_missing_transcripts(project):
                return project

            self.mark_missing_transcripts_pending(project)
            project.updated_at = datetime.utcnow()
            await self._save_project(project)

            await self._transcribe_project_tracks(project, persist=True, continue_on_error=True)
            project.updated_at = datetime.utcnow()
            await self._save_project(project)
            self.projects[project.id] = project
            return project
        finally:
            self._active_transcription_jobs.discard(project_id)

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
        inferred_recorded_at = recorded_at

        metadata: Dict[str, Any] = {
            "filename": file_path_obj.name,
            "size": stat.st_size,
            "extension": suffix,
            "uploaded_at": datetime.utcnow().isoformat(),
        }
        metadata.update(extra_metadata)
        if track_type in {TrackType.VIDEO, TrackType.AUDIO}:
            metadata.setdefault("transcript_status", "pending")
        raw_role = extra_metadata.get("role", TrackRole.PRIMARY)
        try:
            role = raw_role if isinstance(raw_role, TrackRole) else TrackRole(str(raw_role))
        except Exception:
            role = TrackRole.PRIMARY
        if track_type not in {TrackType.VIDEO, TrackType.IMAGE}:
            role = TrackRole.PRIMARY
        background_description = str(extra_metadata.get("background_description") or "").strip() or None

        duration = 30.0
        if track_type in {TrackType.VIDEO, TrackType.AUDIO}:
            try:
                info = await VideoProcessor().get_video_info(str(file_path_obj))
                streams = info.get("streams", [])
                fmt = info.get("format", {})
                duration = float(fmt.get("duration", duration))
                if inferred_recorded_at is None:
                    inferred_recorded_at = self._extract_recorded_at_from_media_info(streams, fmt)
                location = self._extract_location_from_streams(streams, fmt)
                if location:
                    metadata["location"] = location
                inferred_width, inferred_height, inferred_orientation = self._extract_track_geometry_from_media_info(streams)
            except Exception:
                inferred_width, inferred_height, inferred_orientation = (None, None, TrackOrientation.UNKNOWN)
        else:
            inferred_width, inferred_height, inferred_orientation = (None, None, TrackOrientation.UNKNOWN)

        if inferred_recorded_at is None:
            inferred_recorded_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

        return VideoTrack(
            id=str(uuid.uuid4()),
            type=track_type,
            filename=file_path_obj.name,
            file_path=str(file_path_obj),
            duration=duration,
            position=position,
            metadata=metadata,
            recorded_at=inferred_recorded_at,
            orientation=inferred_orientation,
            width=inferred_width,
            height=inferred_height,
            role=role,
            background_description=background_description,
        )

    @staticmethod
    def _filename_natural_key(value: str) -> tuple[Any, ...]:
        parts = re.split(r"(\d+)", value.lower())
        key: List[Any] = []
        for part in parts:
            if not part:
                continue
            key.append(int(part) if part.isdigit() else part)
        return tuple(key)

    @classmethod
    def _normalize_track_positions(cls, tracks: List[VideoTrack]) -> List[VideoTrack]:
        for idx, track in enumerate(tracks):
            track.position = idx
        return tracks

    @classmethod
    def _filename_sorted_tracks(cls, tracks: List[VideoTrack]) -> List[VideoTrack]:
        ordered = sorted(
            tracks,
            key=lambda t: (
                cls._filename_natural_key(t.filename or ""),
                t.position,
            ),
        )
        return cls._normalize_track_positions(ordered)

    @classmethod
    def _position_sorted_tracks(cls, tracks: List[VideoTrack]) -> List[VideoTrack]:
        ordered = sorted(
            tracks,
            key=lambda t: (
                t.position,
                cls._filename_natural_key(t.filename or ""),
            ),
        )
        return cls._normalize_track_positions(ordered)

    @classmethod
    def _ordered_tracks(cls, tracks: List[VideoTrack], edit_mode: EditMode | str) -> List[VideoTrack]:
        mode = edit_mode.value if isinstance(edit_mode, EditMode) else str(edit_mode)
        if mode == EditMode.MANUAL.value:
            return cls._position_sorted_tracks(tracks)
        return cls._filename_sorted_tracks(tracks)

    async def _transcribe_tracks(self, project: Project) -> None:
        await self._transcribe_project_tracks(project, persist=False, continue_on_error=False)

    async def _transcribe_project_tracks(
        self,
        project: Project,
        *,
        persist: bool = False,
        continue_on_error: bool = False,
    ) -> None:
        processor = VideoProcessor()
        if not project.user_id:
            raise ValueError("Project owner missing")
        audio_dir = self.get_project_audio_dir(project.user_id, project.id)
        transcript_dir = self.get_project_transcript_dir(project.user_id, project.id)
        for track in project.tracks:
            if not self._track_is_transcribable(track):
                continue
            if self._track_has_transcript(track):
                self._set_track_transcript_status(track, "completed")
                continue

            self._set_track_transcript_status(track, "processing")
            if persist:
                project.updated_at = datetime.utcnow()
                await self._save_project(project)

            try:
                await self._transcribe_track(
                    project,
                    track,
                    processor=processor,
                    audio_dir=audio_dir,
                    transcript_dir=transcript_dir,
                )
                self._set_track_transcript_status(track, "completed")
            except Exception as exc:
                self._set_track_transcript_status(track, "error", str(exc))
                if not continue_on_error:
                    raise
            finally:
                if persist:
                    project.updated_at = datetime.utcnow()
                    await self._save_project(project)

    async def _transcribe_track(
        self,
        project: Project,
        track: VideoTrack,
        *,
        processor: VideoProcessor,
        audio_dir: Path,
        transcript_dir: Path,
    ) -> None:
        audio_path = track.file_path
        if track.type == TrackType.VIDEO:
            audio_output_path = self._reserve_named_output(audio_dir, Path(track.filename).stem, ".wav")
            extracted_audio_path = await processor.extract_audio_for_transcription(track.file_path, str(audio_output_path))
            audio_path = extracted_audio_path
            track.metadata["audio_path"] = extracted_audio_path

        try:
            silence_ranges = await processor.detect_silence_ranges(audio_path, noise_db=-40.0, min_silence_duration=0.08)
        except Exception:
            silence_ranges = []

        chunked_transcription = await self._transcribe_audio_with_chunking(
            audio_path,
            track.duration,
            silence_ranges,
            processor=processor,
        )
        if chunked_transcription is not None:
            sanitized = sanitize_transcription_payload(chunked_transcription, track.duration)
        else:
            with open(audio_path, "rb") as handle:
                audio_data = handle.read()
            transcription_filename = Path(audio_path).name
            raw_transcription = await transcribe_audio(audio_data, transcription_filename, "en")
            sanitized = sanitize_transcription_payload(raw_transcription, track.duration)

        if silence_ranges:
            sanitized = self._align_transcription_to_detected_silence(sanitized, silence_ranges, track.duration)
        words = normalize_words(sanitized.get("words", []))

        track.has_voice = len(words) > 0
        track.transcription = (
            {
                "text": sanitized.get("text", ""),
                "words": [word.model_dump() for word in words],
                "language": sanitized.get("language", "en"),
                "segments": sanitized.get("segments", []),
            }
            if words or sanitized.get("text")
            else None
        )
        track.local_gap_ranges = find_gaps(words, project.settings.min_gap_seconds, clip_duration=track.duration)
        transcript_text_path = self._reserve_named_output(transcript_dir, Path(track.filename).stem, ".txt")
        transcript_text_path.write_text((track.transcription or {}).get("text", ""), encoding="utf-8")
        track.metadata["transcript_path"] = str(transcript_text_path)

    async def _transcribe_audio_with_chunking(
        self,
        audio_path: str,
        duration: float,
        silence_ranges: List[tuple[float, float]],
        *,
        processor: VideoProcessor,
    ) -> Optional[Dict[str, Any]]:
        windows = self._build_transcription_windows(silence_ranges, duration)
        if len(windows) <= 1:
            return None

        chunk_dir = self.temp_dir / "transcription_chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        merged_words: List[Dict[str, Any]] = []
        merged_segments: List[Dict[str, Any]] = []
        segment_id = 0

        for idx, (nominal_start, nominal_end) in enumerate(windows):
            padded_start = max(0.0, nominal_start - 0.15)
            padded_end = min(float(duration), nominal_end + 0.15)
            if padded_end - padded_start < 0.2:
                continue

            chunk_path = chunk_dir / f"{Path(audio_path).stem}-{idx + 1}.wav"
            await processor.extract_audio_segment(audio_path, chunk_path, padded_start, padded_end)
            try:
                chunk_bytes = chunk_path.read_bytes()
                raw_chunk = await transcribe_audio(chunk_bytes, chunk_path.name, "en")
            finally:
                chunk_path.unlink(missing_ok=True)

            sanitized_chunk = sanitize_transcription_payload(raw_chunk, padded_end - padded_start)
            chunk_words = normalize_words(sanitized_chunk.get("words", []))
            kept_words: List[Dict[str, Any]] = []
            for word in chunk_words:
                absolute_start = padded_start + float(word.start)
                absolute_end = padded_start + float(word.end)
                midpoint = (absolute_start + absolute_end) / 2
                if midpoint < nominal_start - 0.03 or midpoint > nominal_end + 0.03:
                    continue
                kept_words.append(
                    {
                        "word": word.word,
                        "start": round(max(0.0, absolute_start), 3),
                        "end": round(max(absolute_start, absolute_end), 3),
                        "confidence": word.confidence,
                    }
                )

            if not kept_words:
                continue

            merged_words.extend(kept_words)
            merged_segments.append(
                {
                    "id": segment_id,
                    "start": kept_words[0]["start"],
                    "end": kept_words[-1]["end"],
                    "text": " ".join(word["word"] for word in kept_words).strip(),
                    "words": kept_words,
                }
            )
            segment_id += 1

        if not merged_words:
            return None

        return {
            "text": " ".join(word["word"] for word in merged_words).strip(),
            "words": merged_words,
            "segments": merged_segments,
            "language": "en",
        }

    @staticmethod
    def _build_transcription_windows(
        silence_ranges: List[tuple[float, float]],
        duration: float,
        *,
        min_window_duration: float = 0.3,
        max_window_duration: float = 3.5,
    ) -> List[tuple[float, float]]:
        if duration <= 0:
            return []

        sorted_silences = sorted((max(0.0, start), max(0.0, end)) for start, end in silence_ranges if end > start)
        speech_ranges: List[tuple[float, float]] = []
        cursor = 0.0
        for silence_start, silence_end in sorted_silences:
            if silence_start - cursor >= min_window_duration:
                speech_ranges.append((cursor, silence_start))
            cursor = max(cursor, silence_end)
        if duration - cursor >= min_window_duration:
            speech_ranges.append((cursor, duration))

        windows: List[tuple[float, float]] = []
        for speech_start, speech_end in speech_ranges:
            current = speech_start
            while current < speech_end - min_window_duration:
                next_end = min(speech_end, current + max_window_duration)
                windows.append((round(current, 3), round(next_end, 3)))
                current = next_end
        return windows

    def _create_track_from_hybrid_input(self, track: HybridTrackInput, position: int) -> VideoTrack:
        transcription = sanitize_transcription_payload(track.transcription or {}, float(track.duration or 0.0))
        words = normalize_words(transcription.get("words") or [])

        duration = float(track.duration or 0.0)
        if duration <= 0 and words:
            duration = float(max(word.end for word in words))

        metadata: Dict[str, Any] = {
            "analysis_origin": "hybrid_client",
            "thumbnail_reference": track.thumbnail_reference,
            "proxy_reference": track.proxy_reference,
            "source_reference": track.source_reference,
            "shot_boundaries": track.shot_boundaries,
            "transcript_status": "completed" if (transcription.get("text") or words) else "not_applicable",
        }
        metadata.update(track.metadata)

        return VideoTrack(
            id=str(uuid.uuid4()),
            type=TrackType.VIDEO,
            filename=track.filename,
            file_path=track.source_reference or f"client://{track.filename}",
            duration=duration,
            position=position,
            metadata=metadata,
            transcription=(
                {
                    "text": transcription.get("text") or "",
                    "words": [word.model_dump() for word in words],
                    "language": transcription.get("language", "en"),
                    "segments": transcription.get("segments", []),
                }
                if (transcription.get("text") or words)
                else None
            ),
            has_voice=bool(words),
            recorded_at=track.recorded_at or datetime.utcnow(),
            orientation=self._orientation_from_metadata(track.metadata),
            width=self._int_or_none(track.metadata.get("width")),
            height=self._int_or_none(track.metadata.get("height")),
            local_gap_ranges=[],
        )

    async def _build_pipeline(self, project: Project, render_strategy: str = "server_render") -> None:
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
            track.local_gap_ranges = find_gaps(track_words, project.settings.min_gap_seconds, clip_duration=track.duration)
            timeline_offset += track.duration

        combined_words = sorted(combined_words, key=lambda w: w.start)
        transcript = " ".join(word.word for word in combined_words).strip()
        gap_ranges = find_gaps(combined_words, project.settings.min_gap_seconds, clip_duration=timeline_offset)

        if not project.user_id:
            raise ValueError("Project owner missing")
        transcript_dir = self.get_project_transcript_dir(project.user_id, project.id)
        word_srt_path = transcript_dir / f"{project.id}_word_level.srt"
        subtitle_srt_path = transcript_dir / f"{project.id}_subtitles.srt"
        combined_transcript_path = transcript_dir / f"{project.id}_combined.txt"

        write_word_level_srt(combined_words, word_srt_path)
        combined_transcript_path.write_text(transcript, encoding="utf-8")

        subtitle_cues = build_subtitle_cues(combined_words)
        if locations:
            for cue in subtitle_cues:
                cue.location = locations[0]
                cue.text = f"[{locations[0]}] {cue.text}"
        write_subtitles_srt(subtitle_cues, subtitle_srt_path)

        insertions = []
        if project.settings.insert_suggestions:
            insertions = await ai_service.suggest_insertions(combined_words)

        track_decisions = []
        orientation_streams: Dict[str, List[str]] = {}
        for track in project.tracks:
            orientation_key = track.orientation.value if isinstance(track.orientation, TrackOrientation) else str(track.orientation)
            orientation_streams.setdefault(orientation_key, []).append(track.id)
            track_decisions.append(
                {
                    "track_id": track.id,
                    "filename": track.filename,
                    "duration": track.duration,
                    "orientation": orientation_key,
                    "width": track.width,
                    "height": track.height,
                    "recorded_at": track.recorded_at.isoformat() if track.recorded_at else None,
                    "source_reference": track.metadata.get("source_reference"),
                    "proxy_reference": track.metadata.get("proxy_reference"),
                    "thumbnail_reference": track.metadata.get("thumbnail_reference"),
                    "shot_boundaries": track.metadata.get("shot_boundaries", []),
                    "keep_ranges": self._build_keep_ranges(
                        track.duration,
                        track.local_gap_ranges,
                        project.settings.smart_pause_cutter,
                    ),
                    "remove_ranges": [gap.model_dump() for gap in track.local_gap_ranges],
                }
            )

        project.pipeline.combined_transcript = transcript
        project.pipeline.combined_words = combined_words
        project.pipeline.gap_ranges = gap_ranges
        project.pipeline.word_srt_path = str(word_srt_path)
        project.pipeline.subtitle_path = str(subtitle_srt_path)
        project.pipeline.subtitle_cues = subtitle_cues
        project.pipeline.locations = sorted(set(locations))
        project.pipeline.insertion_suggestions = insertions
        project.pipeline.render_plan = {
            "render_strategy": render_strategy,
            "ordered_track_ids": [track.id for track in project.tracks],
            "auto_cut_enabled": project.settings.smart_pause_cutter,
            "gap_ranges": [gap.model_dump() for gap in gap_ranges],
            "orientation_streams": orientation_streams,
            "track_decisions": track_decisions,
            "subtitle_path": str(subtitle_srt_path) if project.settings.generate_subtitles else None,
            "requires_source_upload_for_server_render": render_strategy != "on_device",
        }

    async def _generate_final_video(self, project: Project) -> str:
        processor = VideoProcessor()
        return await processor.process_project(project)

    @staticmethod
    def _build_keep_ranges(duration: float, gaps: List[Any], auto_cut_enabled: bool) -> List[Dict[str, float]]:
        if duration <= 0:
            return []
        if not auto_cut_enabled or not gaps:
            return [{"start": 0.0, "end": round(duration, 3)}]

        ranges: List[Dict[str, float]] = []
        cursor = 0.0
        for gap in sorted(gaps, key=lambda item: item.start):
            if gap.start > cursor:
                ranges.append({"start": round(cursor, 3), "end": round(gap.start, 3)})
            cursor = max(cursor, gap.end)

        if cursor < duration:
            ranges.append({"start": round(cursor, 3), "end": round(duration, 3)})
        return ranges

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

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            parsed = int(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _orientation_from_dimensions(cls, width: Optional[int], height: Optional[int]) -> TrackOrientation:
        if not width or not height:
            return TrackOrientation.UNKNOWN
        if width == height:
            return TrackOrientation.SQUARE
        return TrackOrientation.VERTICAL if height > width else TrackOrientation.HORIZONTAL

    @classmethod
    def _orientation_from_metadata(cls, metadata: Dict[str, Any]) -> TrackOrientation:
        raw = metadata.get("orientation")
        if isinstance(raw, str):
            try:
                return TrackOrientation(raw)
            except ValueError:
                pass
        return cls._orientation_from_dimensions(
            cls._int_or_none(metadata.get("width")),
            cls._int_or_none(metadata.get("height")),
        )

    @classmethod
    def _extract_track_geometry_from_media_info(
        cls,
        streams: List[Dict[str, Any]],
    ) -> tuple[Optional[int], Optional[int], TrackOrientation]:
        for stream in streams:
            if str(stream.get("codec_type")) != "video":
                continue
            width = cls._int_or_none(stream.get("width"))
            height = cls._int_or_none(stream.get("height"))
            if not width or not height:
                continue

            rotation = 0
            tags = stream.get("tags") or {}
            rotate_tag = tags.get("rotate")
            if rotate_tag is not None:
                try:
                    rotation = int(float(rotate_tag))
                except (TypeError, ValueError):
                    rotation = 0
            for side_data in stream.get("side_data_list") or []:
                if not isinstance(side_data, dict):
                    continue
                value = side_data.get("rotation")
                if value is None:
                    continue
                try:
                    rotation = int(float(value))
                except (TypeError, ValueError):
                    continue

            normalized_rotation = rotation % 360
            if normalized_rotation in {90, 270}:
                width, height = height, width

            orientation = cls._orientation_from_dimensions(width, height)
            return width, height, orientation

        return None, None, TrackOrientation.UNKNOWN

    @staticmethod
    def _extract_recorded_at_from_media_info(
        streams: List[Dict[str, Any]],
        fmt: Dict[str, Any],
    ) -> Optional[datetime]:
        def _parse_datetime(value: Any) -> Optional[datetime]:
            if not value:
                return None
            text = str(value).strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        def _extract_from_tags(tags: Dict[str, Any]) -> Optional[datetime]:
            for key in (
                "creation_time",
                "com.apple.quicktime.creationdate",
                "com.apple.quicktime.creation_time",
            ):
                parsed = _parse_datetime(tags.get(key))
                if parsed is not None:
                    return parsed
            return None

        for stream in streams:
            tags = stream.get("tags", {})
            if tags:
                parsed = _extract_from_tags(tags)
                if parsed is not None:
                    return parsed

        fmt_tags = fmt.get("tags", {})
        if fmt_tags:
            return _extract_from_tags(fmt_tags)
        return None

    async def _save_project(self, project: Project) -> None:
        if not project.user_id:
            raise ValueError("Project owner missing")
        project_file = self.get_project_file(project.user_id, project.id, create=True)
        payload = project.model_dump(mode="json")
        project_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def _load_project(self, project_file: Path) -> Project:
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        return Project(**payload)

    @staticmethod
    def _reserve_named_output(directory: Path, stem: str, suffix: str) -> Path:
        candidate = directory / f"{stem}{suffix}"
        if not candidate.exists():
            return candidate
        counter = 2
        while True:
            next_candidate = directory / f"{stem}-{counter}{suffix}"
            if not next_candidate.exists():
                return next_candidate
            counter += 1


project_service = ProjectService()
