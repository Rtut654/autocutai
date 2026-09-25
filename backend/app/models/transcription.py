"""Data models for transcription responses."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class WordTimestamp(BaseModel):
    """A single transcribed word with its position in the clip."""

    word: str = Field(..., description="The transcribed word")
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    confidence: Optional[float] = Field(None, description="Confidence score (0-1)")


class Segment(BaseModel):
    """A recognised phrase, as returned by one Azure recognition event."""

    id: int = Field(..., description="Segment index within the clip")
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    text: str = Field(..., description="Transcribed text for this segment")
    words: List[WordTimestamp] = Field(default_factory=list, description="Word-level timestamps")


class TranscriptionResponse(BaseModel):
    """Complete transcription payload returned by POST /api/transcribe."""

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(..., description="Full transcribed text")
    language: str = Field(..., description="BCP-47 locale of the transcript")
    duration: float = Field(default=0.0, description="Transcribed audio duration in seconds")
    words: List[WordTimestamp] = Field(default_factory=list, description="Word-level timestamps")
    segments: List[Segment] = Field(default_factory=list, description="Recognised segments")
