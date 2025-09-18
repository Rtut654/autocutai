"""
API endpoints for audio transcription services.
"""

import logging
from typing import Dict, Any
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse

from ..services.whisper_service import transcribe_audio
from ..models.transcription import TranscriptionResponse, TranscriptionRequest

logger = logging.getLogger(__name__)

# Create router for transcription endpoints
router = APIRouter(prefix="/api/transcription", tags=["transcription"])


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio_file(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    language: str = Form(default="en", description="Language code for transcription")
) -> TranscriptionResponse:
    """
    Transcribe an audio file and return word-level timestamps.
    
    Args:
        file: Audio file to transcribe
        language: Language code (default: "en")
    
    Returns:
        TranscriptionResponse with word-level timestamps
    
    Raises:
        HTTPException: If transcription fails
    """
    
    try:
        logger.info(f"Received transcription request for file: {file.filename}")
        logger.info(f"Language: {language}")
        
        # Validate file type
        if not file.content_type or not file.content_type.startswith('audio/'):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Please upload an audio file."
            )
        
        # Read file data
        audio_data = await file.read()
        
        if len(audio_data) == 0:
            raise HTTPException(
                status_code=400,
                detail="Empty file uploaded"
            )
        
        logger.info(f"Processing audio file: {len(audio_data)} bytes")
        
        # Transcribe audio
        result = await transcribe_audio(audio_data, file.filename or "audio.wav", language)
        
        # Convert to response model
        response = TranscriptionResponse(**result)
        
        logger.info(f"Transcription completed successfully. Duration: {response.duration}s, Words: {len(response.words)}")
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transcription failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Transcription failed: {str(e)}"
        )


@router.get("/health")
async def health_check() -> Dict[str, str]:
    """
    Health check endpoint for the transcription service.
    
    Returns:
        Dict with service status
    """
    return {"status": "healthy", "service": "transcription"}


@router.get("/info")
async def service_info() -> Dict[str, Any]:
    """
    Get information about the transcription service.
    
    Returns:
        Dict with service information
    """
    from ..services.whisper_service import whisper_service
    
    return {
        "service": "transcription",
        "api_url": whisper_service.api_url,
        "mock_mode": whisper_service.use_mock,
        "timeout": whisper_service.timeout,
        "supported_formats": ["wav", "mp3", "m4a", "ogg", "flac"],
        "supported_languages": ["en", "es", "fr", "de", "it", "pt", "ru", "ja", "ko", "zh"]
    }
