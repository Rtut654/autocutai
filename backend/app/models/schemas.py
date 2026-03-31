"""Pydantic models for the AI travel video editing pipeline."""

from pydantic import BaseModel
from typing import Optional
from enum import Enum


class TransitionType(str, Enum):
    HARD_CUT = "hard_cut"
    DISSOLVE = "dissolve"
    FADE_BLACK = "fade_black"


class GapType(str, Enum):
    PAUSE = "pause"          # <2s - remove
    BROLL = "broll"          # 2-6s - fill with silent footage
    MUSIC = "music"          # >6s - add music


class TransitionDecision(BaseModel):
    type: TransitionType
    duration_ms: int = 500


class ClipDecision(BaseModel):
    clip_id: str
    source_file: str
    in_point: str             # HH:MM:SS.ms
    out_point: str
    reason: str
    transition_in: Optional[TransitionDecision] = None
    transition_out: Optional[TransitionDecision] = None


class MusicCue(BaseModel):
    start: str
    end: str
    mood: str
    bpm_target: int
    suggested_track: str
    fade_in_ms: int = 800
    fade_out_ms: int = 1200


class RemovedClip(BaseModel):
    clip_id: str
    source_file: str
    reason: str


class EditPlan(BaseModel):
    output_duration_estimate: str
    clips: list[ClipDecision]
    music_cues: list[MusicCue]
    cuts_removed: list[RemovedClip]


class PipelineStatus(BaseModel):
    job_id: str
    stage: str
    progress: float           # 0.0 - 1.0
    message: str
    edit_plan: Optional[EditPlan] = None
    output_url: Optional[str] = None
