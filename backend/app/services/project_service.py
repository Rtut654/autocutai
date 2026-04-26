"""Service for managing and processing AI-assisted video editing projects."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.project import (
    HybridProjectAnalyzeRequest,
    HybridTrackInput,
    Project,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    SpeechFilterArtifact,
    TrackType,
    VideoTrack,
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

logger = logging.getLogger(__name__)


class ProjectService:
    """Service for managing video editing projects."""

    def __init__(self, projects_dir: str = "projects", temp_dir: str = "temp"):
        self.projects_dir = Path(projects_dir)
        self.temp_dir = Path(temp_dir)
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

        project.tracks = self._sorted_tracks(project.tracks)
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
            tracks=self._sorted_tracks(tracks),
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

    async def get_project(self, project_id: str, user_id: Optional[str] = None) -> Optional[Project]:
        cached = self.projects.get(project_id)
        if cached and (not user_id or cached.user_id == user_id):
            await self._sanitize_existing_project_state(cached)
            return cached

        candidate_files: List[Path] = []
        if user_id:
            candidate_files.append(self.get_project_file(user_id, project_id, create=False))
        else:
            candidate_files.extend(self.projects_dir.glob(f"*/{project_id}/project.json"))

        for project_file in candidate_files:
            if project_file.exists():
                project = await self._load_project(project_file)
                await self._sanitize_existing_project_state(project)
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

        project.updated_at = datetime.utcnow()
        await self._save_project(project)
        self.projects[project_id] = project
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
            await self._sanitize_existing_project_state(project)
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

            transcript_path = track.metadata.get("transcript_path")
            if transcript_path and isinstance(transcript_path, str):
                candidate = Path(transcript_path)
                if candidate.exists():
                    desired_text = (track.transcription or {}).get("text", "")
                    existing_text = candidate.read_text(encoding="utf-8")
                    if existing_text != desired_text:
                        candidate.write_text(desired_text, encoding="utf-8")
                        changed = True

        sorted_tracks = self._sorted_tracks(project.tracks)
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
            except Exception:
                pass

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
        )

    @staticmethod
    def _sorted_tracks(tracks: List[VideoTrack]) -> List[VideoTrack]:
        def _sort_timestamp(value: Optional[datetime]) -> float:
            if value is None:
                return float("-inf")
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc).timestamp()
            return value.timestamp()

        ordered = sorted(
            tracks,
            key=lambda t: (
                _sort_timestamp(t.recorded_at),
                t.position,
                t.filename,
            ),
        )
        for idx, track in enumerate(ordered):
            track.position = idx
        return ordered

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

        with open(audio_path, "rb") as handle:
            audio_data = handle.read()
        transcription_filename = Path(audio_path).name
        raw_transcription = await transcribe_audio(audio_data, transcription_filename, "en")
        sanitized = sanitize_transcription_payload(raw_transcription, track.duration)
        try:
            silence_ranges = await processor.detect_silence_ranges(audio_path)
            sanitized = self._align_transcription_to_detected_silence(sanitized, silence_ranges, track.duration)
        except Exception:
            pass
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
        for track in project.tracks:
            track_decisions.append(
                {
                    "track_id": track.id,
                    "filename": track.filename,
                    "duration": track.duration,
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
        project_file = self.get_project_file(project.user_id, project.id)
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
