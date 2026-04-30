"""Project-level worker for matching labeled background clips to narration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models.project import BackgroundVideoPlanArtifact, TrackRole
from ..models.transcription import WordTimestamp
from ..services.ai_service import ai_service
from ..services.pipeline_service import normalize_words
from ..services.project_service import project_service


class BackgroundVideoWorker:
    worker_name = "background_video_worker"

    async def get_project_background_video_plan(
        self,
        project_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[BackgroundVideoPlanArtifact]:
        project = await project_service.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            return None
        artifact_path = self._artifact_path(project.user_id, project.id)
        if not artifact_path.exists():
            return None
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return BackgroundVideoPlanArtifact(**payload)

    async def generate_project_background_video_plan(
        self,
        project_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> BackgroundVideoPlanArtifact:
        project = await project_service.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            raise ValueError("Project not found")

        background_tracks = [
            track for track in project.tracks
            if track.type.value in {"video", "image"}
            and track.role == TrackRole.BACKGROUND
            and not track.excluded
        ]
        if not background_tracks:
            raise ValueError("No background clips are labeled yet")
        if any(not str(track.background_description or "").strip() for track in background_tracks):
            raise ValueError("Every background clip needs a short description before planning placements")

        narration_words = self._collect_project_narration_words(project)
        if not narration_words:
            raise ValueError("Narration transcript is required before planning background clips")

        assets = [
            {
                "track_id": track.id,
                "filename": track.filename,
                "description": str(track.background_description or "").strip(),
                "duration": float(track.duration or (4.0 if track.type.value == "image" else 0.0)),
                "media_type": track.type.value,
            }
            for track in background_tracks
        ]
        artifact = await ai_service.suggest_background_video_plan(
            project_id=project.id,
            narration_words=narration_words,
            background_assets=assets,
        )
        return await self._persist(project, artifact)

    def _collect_project_narration_words(self, project) -> list[WordTimestamp]:
        combined = list(project.pipeline.combined_words or [])
        if combined:
            return combined

        words: list[WordTimestamp] = []
        offset = 0.0
        primary_tracks = [
            track for track in sorted(project.tracks, key=lambda item: item.position)
            if track.role != TrackRole.BACKGROUND and not track.excluded
        ]
        for track in primary_tracks:
            track_words = normalize_words((track.transcription or {}).get("words", []))
            if track_words:
                for word in track_words:
                    words.append(
                        WordTimestamp(
                            word=str(word.word),
                            start=round(offset + float(word.start), 3),
                            end=round(offset + float(word.end), 3),
                            confidence=word.confidence,
                        )
                    )
            offset += float(track.duration or 0.0)
        return words

    async def _persist(self, project, artifact: BackgroundVideoPlanArtifact) -> BackgroundVideoPlanArtifact:
        artifact_path = self._artifact_path(project.user_id, project.id)
        artifact_path.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2), encoding="utf-8")
        project.pipeline.background_video_suggestions = artifact.placements
        project.pipeline.render_plan["background_video_plan_path"] = str(artifact_path)
        project.pipeline.render_plan["background_video_track_ids"] = [item.track_id for item in artifact.placements]
        project.updated_at = datetime.utcnow()
        await project_service._save_project(project)
        project_service.projects[project.id] = project
        return artifact

    def _artifact_path(self, user_id: str, project_id: str) -> Path:
        directory = project_service.get_project_dir(user_id, project_id, create=True) / "background_video_worker"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "plan.json"


background_video_worker_service = BackgroundVideoWorker()
