"""
Data models for transcription responses.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class WordTimestamp(BaseModel):
    """Model for word-level timestamp data."""
    
    word: str = Field(..., description="The transcribed word")
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    confidence: Optional[float] = Field(None, description="Confidence score (0-1)")


class Segment(BaseModel):
    """Model for audio segment data."""
    
    id: int = Field(..., description="Segment ID")
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    text: str = Field(..., description="Transcribed text for this segment")
    words: List[WordTimestamp] = Field(..., description="Word-level timestamps for this segment")


class TranscriptionMetadata(BaseModel):
    """Model for transcription metadata."""
    
    model: str = Field(..., description="Model used for transcription")
    processing_time: float = Field(..., description="Processing time in seconds")
    timestamp: float = Field(..., description="Unix timestamp of processing")
    language: Optional[str] = Field(None, description="Detected or specified language")


class TranscriptionResponse(BaseModel):
    """Model for complete transcription response."""
    
    transcript: str = Field(..., description="Full transcribed text")
    language: str = Field(..., description="Language code")
    duration: float = Field(..., description="Audio duration in seconds")
    words: List[WordTimestamp] = Field(..., description="Word-level timestamps")
    segments: List[Segment] = Field(..., description="Audio segments")
    metadata: TranscriptionMetadata = Field(..., description="Processing metadata")
    
    class Config:
        json_encoders = {
            # Add any custom encoders if needed
        }


class TranscriptionRequest(BaseModel):
    """Model for transcription request parameters."""
    
    language: str = Field(default="en", description="Language code for transcription")
    filename: Optional[str] = Field(None, description="Original filename")
    
    class Config:
        json_schema_extra = {
            "example": {
                "language": "en",
                "filename": "audio.wav"
            }
        }
