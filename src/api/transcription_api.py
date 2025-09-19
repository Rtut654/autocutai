"""
API endpoints for audio transcription services.
"""

import logging
import time
from typing import Dict, Any, List
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse

from ..services.whisper_service import transcribe_audio
from ..models.transcription import TranscriptionResponse, TranscriptionRequest, WordTimestamp, Segment, TranscriptionMetadata

logger = logging.getLogger(__name__)

# Create router for transcription endpoints
router = APIRouter(prefix="/api", tags=["transcription"])


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
        
        # Validate file type - be more flexible with audio file types
        if not file.content_type:
            # If no content type is provided, check file extension
            filename = file.filename or ""
            if not any(filename.lower().endswith(ext) for ext in ['.wav', '.mp3', '.m4a', '.ogg', '.flac', '.mp4', '.mpeg']):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid file type. Please upload an audio file."
                )
        elif not (file.content_type.startswith('audio/') or file.content_type.startswith('video/')):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Please upload an audio or video file."
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
        
        # Transform the API response to match our model structure
        transformed_result = _transform_api_response(result, language)
        
        # Convert to response model
        response = TranscriptionResponse(**transformed_result)
        
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
        "mock_mode": getattr(whisper_service, 'use_mock', False),
        "timeout": whisper_service.timeout,
        "supported_formats": ["wav", "mp3", "m4a", "ogg", "flac"],
        "supported_languages": ["en", "es", "fr", "de", "it", "pt", "ru", "ja", "ko", "zh"]
    }


def _transform_api_response(api_result: Dict[str, Any], language: str) -> Dict[str, Any]:
    """
    Transform the Whisper API response to match our TranscriptionResponse model.
    
    Args:
        api_result: Raw API response from Whisper
        language: Language code
    
    Returns:
        Transformed response matching our model structure
    """
    
    # Extract the main text (API uses 'text' field)
    transcript = api_result.get('text', '')
    
    # Calculate duration from segments
    duration = 0.0
    if 'segments' in api_result and api_result['segments']:
        last_segment = api_result['segments'][-1]
        duration = last_segment.get('end', 0.0)
    
    # Transform words from segments
    all_words = []
    segments = []
    
    for i, segment_data in enumerate(api_result.get('segments', [])):
        segment_words = []
        
        for word_data in segment_data.get('words', []):
            # Transform word data (API uses 'text' instead of 'word')
            word = WordTimestamp(
                word=word_data.get('text', ''),
                start=word_data.get('start', 0.0),
                end=word_data.get('end', 0.0),
                confidence=word_data.get('confidence', None)
            )
            segment_words.append(word)
            all_words.append(word)
        
        # Create segment
        segment = Segment(
            id=i,
            start=segment_data.get('start', 0.0),
            end=segment_data.get('end', 0.0),
            text=segment_data.get('text', ''),
            words=segment_words
        )
        segments.append(segment)
    
    # Create metadata
    metadata = TranscriptionMetadata(
        model="whisper-api",
        processing_time=0.0,  # We don't have this from the API
        timestamp=time.time(),
        language=language
    )
    
    return {
        "transcript": transcript,
        "language": language,
        "duration": duration,
        "words": all_words,
        "segments": segments,
        "metadata": metadata
    }

