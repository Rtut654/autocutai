"""Separate worker for narration-following visual enhancements."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from ..models.project import VisualAssetStatus, VisualPlanArtifact
from ..services.ai_service import ai_service
from ..services.pipeline_service import normalize_words
from ..services.project_service import project_service


class VisualEnhancementWorker:
    """Generates and persists visual plans independent from speech-cut logic."""

    worker_name = "visual_enhancement_worker"

    async def get_track_visual_plan(
        self,
        project_id: str,
        track_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> Optional[VisualPlanArtifact]:
        project = await project_service.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            return None
        artifact_path = self._artifact_path(project.user_id, project.id, track_id)
        if not artifact_path.exists():
            return None
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return VisualPlanArtifact(**payload)

    async def generate_track_visual_plan(
        self,
        project_id: str,
        track_id: str,
        *,
        user_id: Optional[str] = None,
    ) -> VisualPlanArtifact:
        project = await project_service.get_project(project_id, user_id=user_id)
        if not project or not project.user_id:
            raise ValueError("Project not found")
        track = next((item for item in project.tracks if item.id == track_id), None)
        if not track:
            raise ValueError("Track not found")

        words = normalize_words((track.transcription or {}).get("words", []))
        if not words:
            raise ValueError("Transcript is required before planning visuals")

        track.metadata["visual_worker_status"] = "processing"
        await project_service._save_project(project)

        try:
            artifact = await ai_service.suggest_visual_plan(
                project_id=project.id,
                track_id=track.id,
                filename=track.filename,
                words=words,
                transcript_segments=(track.transcription or {}).get("segments", []),
                duration=float(track.duration or 0.0),
            )
            artifact = await self._materialize_web_images(project.user_id, project.id, artifact)
        except Exception as exc:
            track.metadata["visual_worker_status"] = "error"
            track.metadata["visual_worker_error"] = str(exc)
            await project_service._save_project(project)
            raise

        return await self._persist(project.user_id, project, track, artifact)

    async def _materialize_web_images(
        self,
        user_id: str,
        project_id: str,
        artifact: VisualPlanArtifact,
    ) -> VisualPlanArtifact:
        assets_dir = project_service.get_project_dir(user_id, project_id, create=True) / "visual_assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        updated_parts = []
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for index, part in enumerate(artifact.parts):
                if not part.search_query or part.visual_type.value != "web_image":
                    updated_parts.append(part)
                    continue
                try:
                    image_url = await self._search_wikimedia_image(client, part.search_query)
                    if not image_url:
                        updated_parts.append(part)
                        continue
                    target_path = assets_dir / f"{artifact.track_id}-{index + 1}.jpg"
                    response = await client.get(image_url)
                    response.raise_for_status()
                    target_path.write_bytes(response.content)
                    updated_parts.append(
                        part.model_copy(
                            update={
                                "asset_status": VisualAssetStatus.READY,
                                "asset_url": image_url,
                                "local_path": str(target_path),
                            }
                        )
                    )
                except Exception:
                    updated_parts.append(part.model_copy(update={"asset_status": VisualAssetStatus.ERROR}))

        return artifact.model_copy(update={"parts": updated_parts})

    async def _search_wikimedia_image(self, client: httpx.AsyncClient, query: str) -> Optional[str]:
        response = await client.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": 1280,
                "format": "json",
            },
        )
        response.raise_for_status()
        pages = (response.json().get("query") or {}).get("pages") or {}
        for page in pages.values():
            imageinfo = page.get("imageinfo") or []
            if not imageinfo:
                continue
            thumb_url = imageinfo[0].get("thumburl")
            direct_url = imageinfo[0].get("url")
            return thumb_url or direct_url
        return None

    async def _persist(self, user_id: str, project, track, artifact: VisualPlanArtifact) -> VisualPlanArtifact:
        artifact_path = self._artifact_path(user_id, project.id, track.id)
        artifact_path.write_text(json.dumps(artifact.model_dump(mode="json"), indent=2), encoding="utf-8")
        track.metadata["visual_worker_status"] = artifact.status
        track.metadata["visual_worker_path"] = str(artifact_path)
        track.metadata["visual_worker_part_count"] = len(artifact.parts)
        track.metadata["visual_worker_model"] = artifact.model
        track.metadata["visual_worker_generated_at"] = artifact.generated_at.isoformat()
        track.metadata.pop("visual_worker_error", None)
        project.updated_at = datetime.utcnow()
        await project_service._save_project(project)
        project_service.projects[project.id] = project
        return artifact

    def _artifact_path(self, user_id: str, project_id: str, track_id: str) -> Path:
        directory = project_service.get_project_dir(user_id, project_id, create=True) / "visual_worker"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{track_id}.json"


visual_worker_service = VisualEnhancementWorker()
