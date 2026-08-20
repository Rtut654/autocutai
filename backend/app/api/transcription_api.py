"""Transcription endpoint backed by Azure Speech."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .auth_dependencies import get_current_user
from ..models.transcription import TranscriptionResponse
from ..services.azure_speech_service import TranscriptionError, azure_speech_service
from ..services.pipeline_service import sanitize_transcription_payload
from ..services.video_processor import VideoProcessor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["transcription"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".mp4", ".mov", ".m4v", ".webm"}


def _validate_upload(file: UploadFile) -> None:
    suffix = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    looks_like_media = content_type.startswith("audio/") or content_type.startswith("video/")
    if suffix not in ALLOWED_SUFFIXES and not looks_like_media:
        raise HTTPException(
            status_code=400,
            detail="Upload an audio or video file (wav, mp3, m4a, mp4, mov and similar).",
        )


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_media(
    file: UploadFile = File(..., description="Audio or video file to transcribe"),
    language: Optional[str] = Form(default=None, description="BCP-47 locale, e.g. en-US"),
    _current_user=Depends(get_current_user),
) -> TranscriptionResponse:
    """Transcribe one media file and return word-level timestamps."""
    _validate_upload(file)

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )

    processor = VideoProcessor()
    with tempfile.TemporaryDirectory(prefix="autocut-transcribe-") as workdir:
        source_path = Path(workdir) / (Path(file.filename or "upload").name or "upload")
        source_path.write_bytes(payload)
        wav_path = Path(workdir) / "audio.wav"

        try:
            await processor.extract_audio_for_transcription(str(source_path), str(wav_path))
        except RuntimeError as exc:
            logger.warning("Audio extraction failed for %s: %s", file.filename, exc)
            raise HTTPException(status_code=400, detail="Could not read audio from this file.") from exc

        try:
            raw = await azure_speech_service.transcribe_file(wav_path, language)
        except TranscriptionError as exc:
            logger.error("Azure transcription failed for %s: %s", file.filename, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    duration = max((word["end"] for word in raw.get("words", [])), default=0.0)
    sanitized = sanitize_transcription_payload(raw, duration)

    return TranscriptionResponse(
        text=sanitized.get("text", ""),
        words=sanitized.get("words", []),
        segments=sanitized.get("segments", []),
        language=sanitized.get("language", language or azure_speech_service.default_language),
        duration=duration,
    )


@router.get("/transcribe/health")
async def transcription_health() -> dict[str, object]:
    """Report whether transcription is usable, without exposing the key."""
    return {
        "provider": "azure_speech",
        "configured": azure_speech_service.is_configured,
        "region": azure_speech_service.region or None,
        "default_language": azure_speech_service.default_language,
        "candidate_locales": azure_speech_service.candidate_locales,
    }
