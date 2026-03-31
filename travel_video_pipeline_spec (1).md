# AI Travel Video Editor — Full Implementation Specification

## System Overview

This document describes the complete implementation of an AI-powered travel video editing pipeline. The system accepts 10–20 raw video clips (~20 minutes total), processes them through a parallel audio/visual analysis pipeline, and outputs a rendered edited video (~10 minutes) with transitions, music cues, and narrative structure — automatically.

**Stack:**
- Backend: Python (FastAPI)
- Mobile: React Native
- Web: React
- Whisper STT: `testsucceed.com/whisper`
- Vision AI: OpenAI GPT-4o-mini (async API)
- Edit LLM: OpenAI GPT-4o-mini
- Render: ffmpeg + MoviePy 2.0.1 (covers, transitions, title cards)
- Step caching: per-clip JSON files (resume failed jobs without reprocessing)

---

## Processing Flow

```
┌─────────────────────────────────────────────────────────────┐
│              10–20 raw clips uploaded (~20 min)             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Chronological ordering                                     │
│  EXIF / filename / GPS metadata  <1s                        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Demux + scene detect (all clips)                           │
│  ffmpeg + PySceneDetect  ~3–5s                              │
└───────────┬─────────────────────────────────┬───────────────┘
            │         PARALLEL                │
            ▼                                 ▼
┌───────────────────────┐       ┌─────────────────────────────┐
│  AUDIO TRACK          │       │  VISUAL TRACK               │
│                       │       │                             │
│  Whisper tiny/base    │       │  Key frame extract          │
│  testsucceed.com      │       │  1–2 frames/scene  <1s      │
│  ~14s for 20 min      │       │                             │
│          │            │       │           │                 │
│          ▼            │       │           ▼                 │
│  Speech timeline      │       │  CLIP screening (batch 8)   │
│  segments + gaps      │       │  scene type + mood  ~3–4s   │
│          │            │       │           │                 │
│          ▼            │       │           ▼                 │
│  Gap classifier       │       │  GPT-4o-mini vision         │
│  pause / b-roll /     │       │  async parallel calls       │
│  music (<2s/2–6s/>6s) │       │  silent scenes only  ~2–3s  │
│          │            │       │           │                 │
│          ▼            │       │           ▼                 │
│  Music mood selector  │       │  Transition predictor       │
│  energy → BPM match   │       │  CLIP similarity scoring    │
└───────────┬───────────┘       └─────────────┬───────────────┘
            │                                 │
            └──────────────┬──────────────────┘
                           │  MERGE (~14s total, Whisper dominates)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Edit assembly LLM (GPT-4o-mini)                            │
│  transcript + scenes + gaps + music → edit plan JSON  ~5s   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  ffmpeg render                                              │
│  apply cuts, transitions, music  ~20–40s                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
                  Final edited video
               Total wall time: ~45–65s
```

---

## Directory Structure

```
project/
├── backend/
│   ├── main.py                   # FastAPI app, upload endpoint
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── orchestrator.py       # Main pipeline coordinator (cache-aware)
│   │   ├── ordering.py           # Chronological sort
│   │   ├── demux.py              # ffmpeg demux + scene detect
│   │   ├── audio_track.py        # Whisper + gap classifier + music
│   │   ├── visual_track.py       # CLIP + GPT-4o-mini + transitions
│   │   ├── edit_llm.py           # Edit assembly LLM
│   │   ├── moviepy_utils.py      # Covers, title cards, transitions via MoviePy
│   │   └── renderer.py           # ffmpeg render
│   ├── models/
│   │   └── schemas.py            # Pydantic models
│   ├── utils/
│   │   ├── cache.py              # Step cache — per-clip JSON, per-stage JSON
│   │   ├── ffmpeg_utils.py
│   │   └── clip_utils.py
│   └── requirements.txt
│
│   # Runtime output layout (per job):
│   # outputs/{job_id}/
│   #   cache/
│   #     GOPR0312.json           ← per-clip: transcript, scenes, CLIP, GPT4o, transitions
│   #     GOPR0318.json
│   #     _stage_ordering.json    ← sorted clip order
│   #     _stage_edit_plan.json   ← full edit plan JSON
│   #     _stage_render.json      ← render output path + status
│   #   frames/                   ← extracted key frames (JPEGs)
│   #   audio/                    ← demuxed WAV files
│   #   video/                    ← demuxed video streams
│   #   output.mp4                ← final rendered video
│
├── mobile/                       # React Native
│   ├── src/
│   │   ├── screens/
│   │   │   ├── UploadScreen.tsx
│   │   │   └── ProcessingScreen.tsx
│   │   ├── components/
│   │   │   └── ProgressTimeline.tsx
│   │   └── api/
│   │       └── pipeline.ts
└── web/                          # React
    ├── src/
    │   ├── pages/
    │   │   ├── Upload.tsx
    │   │   └── Processing.tsx
    │   └── api/
    │       └── pipeline.ts
```

---

## Backend Implementation

### requirements.txt

```
fastapi==0.111.0
uvicorn==0.30.0
python-multipart==0.0.9
aiofiles==23.2.1
openai==1.35.0
torch==2.3.0
transformers==4.42.0
clip-by-openai==1.0
scenedetect==0.6.3
ffmpeg-python==0.2.0
moviepy==2.0.1dev1
Pillow==10.3.0
httpx==0.27.0
pydantic==2.7.4
python-dotenv==1.0.1
numpy==1.26.4
```

### utils/cache.py

The cache layer is the most important reliability addition. Every pipeline step writes its output to a JSON file before proceeding. If the job crashes or the user retries, each step checks its cache file first and skips reprocessing entirely if the result already exists. This covers Whisper (most expensive to rerun), CLIP, GPT-4o-mini calls, scene detection, and the edit plan.

Two cache scopes exist: **per-clip** (one JSON per source file, keyed by clip ID) and **per-stage** (one JSON per pipeline stage that spans all clips, e.g. ordering and edit plan). Both live under `outputs/{job_id}/cache/`.

```python
# utils/cache.py
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class StepCache:
    """
    Per-job cache for pipeline steps.

    Per-clip file:  cache/{clip_id}.json
    Per-stage file: cache/_stage_{stage}.json

    Each step is stored as a key inside those files, so a single clip
    JSON holds all steps for that clip (whisper, scene_detect, clip_classify,
    gpt4o_describe, transition_to_next).
    """

    def __init__(self, job_id: str, output_dir: str):
        self.cache_dir = Path(output_dir) / job_id / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Per-clip helpers ────────────────────────────────────────────────

    def _clip_path(self, clip_id: str) -> Path:
        return self.cache_dir / f"{clip_id}.json"

    def _load_clip(self, clip_id: str) -> dict:
        p = self._clip_path(clip_id)
        if p.exists():
            return json.loads(p.read_text())
        return {}

    def _save_clip(self, clip_id: str, data: dict):
        p = self._clip_path(clip_id)
        p.write_text(json.dumps(data, indent=2, default=str))

    def has_step(self, clip_id: str, step: str) -> bool:
        """Return True if this step was already completed for this clip."""
        return step in self._load_clip(clip_id).get("steps_completed", [])

    def get_step(self, clip_id: str, step: str) -> Optional[dict]:
        """Return cached step data, or None if not cached."""
        data = self._load_clip(clip_id)
        return data.get(step)

    def save_step(self, clip_id: str, step: str, result: Any, meta: dict = None):
        """
        Persist the result of a pipeline step for one clip.
        Also records completed_at timestamp and appends to steps_completed list.
        """
        data = self._load_clip(clip_id)
        data.setdefault("steps_completed", [])

        data[step] = {
            **(result if isinstance(result, dict) else {"value": result}),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if meta:
            data[step].update(meta)

        if step not in data["steps_completed"]:
            data["steps_completed"].append(step)

        self._save_clip(clip_id, data)

    def init_clip(self, clip_id: str, source_file: str, order_num: int,
                  creation_time: str, duration_seconds: float):
        """
        Write the base record for a clip the first time it is seen.
        Idempotent — safe to call multiple times.
        """
        data = self._load_clip(clip_id)
        if "clip_id" not in data:
            data.update({
                "clip_id": clip_id,
                "source_file": source_file,
                "order_num": order_num,
                "creation_time": creation_time,
                "duration_seconds": duration_seconds,
                "steps_completed": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            self._save_clip(clip_id, data)

    def get_clip_meta(self, clip_id: str) -> dict:
        return self._load_clip(clip_id)

    # ── Per-stage helpers ───────────────────────────────────────────────

    def _stage_path(self, stage: str) -> Path:
        return self.cache_dir / f"_stage_{stage}.json"

    def has_stage(self, stage: str) -> bool:
        return self._stage_path(stage).exists()

    def get_stage(self, stage: str) -> Optional[dict]:
        p = self._stage_path(stage)
        if p.exists():
            return json.loads(p.read_text())
        return None

    def save_stage(self, stage: str, data: Any):
        p = self._stage_path(stage)
        payload = {
            "stage": stage,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "data": data if isinstance(data, (dict, list)) else {"value": data},
        }
        p.write_text(json.dumps(payload, indent=2, default=str))

    def list_clips(self) -> list[str]:
        """Return all clip IDs that have a cache file."""
        return [
            p.stem for p in self.cache_dir.glob("*.json")
            if not p.stem.startswith("_stage_")
        ]
```

**What a per-clip JSON looks like after a full pipeline run:**

```json
{
  "clip_id": "GOPR0312",
  "source_file": "/uploads/abc123/GOPR0312.mp4",
  "order_num": 2,
  "creation_time": "2024-03-15T09:23:11+00:00",
  "duration_seconds": 47.3,
  "steps_completed": ["demux", "scene_detect", "whisper", "clip_classify", "gpt4o_describe", "transition_to_next"],
  "created_at": "2024-03-15T10:01:00+00:00",

  "demux": {
    "video_path": "/outputs/abc123/video/GOPR0312_video.mp4",
    "audio_path": "/outputs/abc123/audio/GOPR0312_audio.wav",
    "completed_at": "2024-03-15T10:01:02+00:00"
  },

  "scene_detect": {
    "timestamps": [0.0, 12.4, 28.1],
    "completed_at": "2024-03-15T10:01:03+00:00"
  },

  "whisper": {
    "transcript": "so we just arrived at the main temple district",
    "segments": [
      {"start": 1.2, "end": 5.8, "text": "so we just arrived"},
      {"start": 6.1, "end": 9.4, "text": "at the main temple district"}
    ],
    "words": [{"word": "so", "start": 1.2, "end": 1.5}, "..."],
    "language": "en",
    "completed_at": "2024-03-15T10:01:08+00:00"
  },

  "clip_classify": {
    "frames": [
      {
        "frame_path": "/outputs/abc123/frames/GOPR0312_scene0.jpg",
        "frame_idx": 0,
        "timestamp": 0.0,
        "scene_type": "temple",
        "scene_confidence": 0.87,
        "mood": "peaceful",
        "mood_confidence": 0.79
      }
    ],
    "completed_at": "2024-03-15T10:01:10+00:00"
  },

  "gpt4o_describe": {
    "skipped": true,
    "reason": "all frames above confidence threshold",
    "completed_at": "2024-03-15T10:01:10+00:00"
  },

  "transition_to_next": {
    "type": "dissolve",
    "duration_ms": 500,
    "similarity_score": 0.71,
    "reason": "similar scene",
    "completed_at": "2024-03-15T10:01:11+00:00"
  }
}
```

**Stage-level cache files:**

```
_stage_ordering.json     → sorted clip IDs in chronological order
_stage_edit_plan.json    → full EditPlan JSON (clips, music_cues, cuts_removed)
_stage_render.json       → output_path, duration, file_size_mb
```

---

### pipeline/moviepy_utils.py

MoviePy 2.0.1 handles everything that ffmpeg's concat demuxer cannot do elegantly: animated title cards, image-based cover slides, cross-fade transitions with custom easing, and overlay text. It writes a temporary clip that ffmpeg then includes in the final concat.

```python
# pipeline/moviepy_utils.py
import os
from pathlib import Path
from moviepy import (
    VideoFileClip, ImageClip, TextClip, CompositeVideoClip,
    concatenate_videoclips, AudioFileClip, ColorClip,
    vfx, afx
)


def make_title_card(
    text: str,
    duration: float = 3.0,
    size: tuple = (1920, 1080),
    bg_color: tuple = (0, 0, 0),
    font_size: int = 80,
    output_path: str = None,
) -> str:
    """
    Generate a title card clip (black background, centered white text).
    Used for location titles, date overlays, chapter markers.
    Returns path to rendered MP4.
    """
    bg = ColorClip(size=size, color=bg_color, duration=duration)
    txt = TextClip(
        text=text,
        font_size=font_size,
        color="white",
        font="Arial",
        method="label",
    ).with_position("center").with_duration(duration)

    # Fade in/out on the text
    txt = txt.with_effects([vfx.FadeIn(0.4), vfx.FadeOut(0.4)])

    clip = CompositeVideoClip([bg, txt])
    clip.write_videofile(output_path, fps=30, codec="libx264",
                         audio=False, logger=None)
    clip.close()
    return output_path


def make_image_cover(
    image_path: str,
    duration: float = 4.0,
    size: tuple = (1920, 1080),
    caption: str = None,
    output_path: str = None,
) -> str:
    """
    Turn a still image (e.g. trip thumbnail) into a video cover slide.
    Optionally adds a caption at the bottom.
    Returns path to rendered MP4.
    """
    img = ImageClip(image_path).resized(size).with_duration(duration)
    img = img.with_effects([vfx.FadeIn(0.5), vfx.FadeOut(0.5)])

    layers = [img]

    if caption:
        cap = TextClip(
            text=caption,
            font_size=48,
            color="white",
            font="Arial",
            method="label",
        ).with_position(("center", 0.85), relative=True).with_duration(duration)
        layers.append(cap)

    clip = CompositeVideoClip(layers, size=size)
    clip.write_videofile(output_path, fps=30, codec="libx264",
                         audio=False, logger=None)
    clip.close()
    return output_path


def apply_crossfade(
    clip_a_path: str,
    clip_b_path: str,
    overlap_seconds: float = 0.5,
    output_path: str = None,
) -> str:
    """
    Render a cross-dissolve between the end of clip_a and start of clip_b.
    Returns path to the joined clip with transition baked in.
    Uses MoviePy's CrossFadeIn effect.
    """
    a = VideoFileClip(clip_a_path)
    b = VideoFileClip(clip_b_path).with_effects(
        [vfx.CrossFadeIn(overlap_seconds)]
    )

    result = concatenate_videoclips([a, b], method="compose")
    result.write_videofile(output_path, fps=30, codec="libx264",
                           audio_codec="aac", logger=None)
    a.close()
    b.close()
    result.close()
    return output_path


def apply_fade_to_black(
    clip_path: str,
    fade_out_duration: float = 0.4,
    output_path: str = None,
) -> str:
    """Apply a fade-to-black at the end of a clip."""
    clip = VideoFileClip(clip_path)
    clip = clip.with_effects([vfx.FadeOut(fade_out_duration)])
    clip.write_videofile(output_path, fps=30, codec="libx264",
                         audio_codec="aac", logger=None)
    clip.close()
    return output_path


def add_lower_third(
    clip_path: str,
    text: str,
    appear_at: float = 1.0,
    duration: float = 3.0,
    output_path: str = None,
) -> str:
    """
    Overlay a lower-third text label on an existing clip.
    Used for location names, dates, speaker IDs.
    """
    clip = VideoFileClip(clip_path)
    w, h = clip.size

    txt = TextClip(
        text=text,
        font_size=42,
        color="white",
        font="Arial",
        method="label",
        stroke_color="black",
        stroke_width=2,
    ).with_position((60, h - 120)).with_start(appear_at).with_duration(duration)
    txt = txt.with_effects([vfx.FadeIn(0.3), vfx.FadeOut(0.3)])

    result = CompositeVideoClip([clip, txt])
    result.write_videofile(output_path, fps=clip.fps, codec="libx264",
                           audio_codec="aac", logger=None)
    clip.close()
    result.close()
    return output_path
```

---

### models/schemas.py

```python
from pydantic import BaseModel
from typing import Optional
from enum import Enum


class TransitionType(str, Enum):
    HARD_CUT = "hard_cut"
    DISSOLVE = "dissolve"
    FADE_BLACK = "fade_black"


class GapType(str, Enum):
    PAUSE = "pause"          # <2s — remove
    BROLL = "broll"          # 2–6s — fill with silent footage
    MUSIC = "music"          # >6s — add music


class TransitionDecision(BaseModel):
    type: TransitionType
    duration_ms: int = 500


class ClipDecision(BaseModel):
    clip_id: str
    source_file: str
    in_point: str             # HH:MM:SS.ms
    out_point: str
    reason: str
    transition_in: Optional[TransitionDecision]
    transition_out: Optional[TransitionDecision]


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


class ProcessingStatus(BaseModel):
    job_id: str
    stage: str
    progress: float           # 0.0 – 1.0
    message: str
    edit_plan: Optional[EditPlan] = None
    output_url: Optional[str] = None
```

### pipeline/ordering.py

```python
import os
import subprocess
import json
from pathlib import Path
from datetime import datetime


def get_creation_time(filepath: str) -> datetime:
    """Extract creation time from EXIF, filename, or filesystem."""
    # Try EXIF via ffprobe
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", filepath
        ], capture_output=True, text=True, timeout=10)
        
        data = json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        
        for key in ["creation_time", "com.apple.quicktime.creationdate"]:
            if key in tags:
                return datetime.fromisoformat(tags[key].replace("Z", "+00:00"))
    except Exception:
        pass
    
    # Fallback: filesystem mtime
    return datetime.fromtimestamp(os.path.getmtime(filepath))


def sort_clips_chronologically(clip_paths: list[str]) -> list[str]:
    """Sort clips by creation time. Returns ordered list of paths."""
    clips_with_time = [
        (path, get_creation_time(path)) for path in clip_paths
    ]
    clips_with_time.sort(key=lambda x: x[1])
    return [path for path, _ in clips_with_time]
```

### pipeline/demux.py

```python
import asyncio
import subprocess
from pathlib import Path
from scenedetect import detect, ContentDetector


async def demux_clip(clip_path: str, video_dir: str, audio_dir: str) -> dict:
    """Split a single clip into video-only and audio-only streams."""
    clip_name = Path(clip_path).stem
    video_out = f"{video_dir}/{clip_name}_video.mp4"
    audio_out = f"{audio_dir}/{clip_name}_audio.wav"
    
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", clip_path,
        "-an", "-c:v", "copy", video_out,
        "-vn", "-ar", "16000", "-ac", "1", audio_out,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    
    return {
        "clip_id": clip_name,
        "source_file": clip_path,
        "video": video_out,
        "audio": audio_out,
    }


async def demux_all(clip_paths: list[str], video_dir: str, audio_dir: str) -> list[dict]:
    """Demux all clips in parallel."""
    tasks = [demux_clip(path, video_dir, audio_dir) for path in clip_paths]
    return await asyncio.gather(*tasks)


def detect_scenes(video_path: str, threshold: float = 27.0) -> list[float]:
    """
    Detect scene change timestamps within a single clip.
    Returns list of timestamps in seconds.
    """
    scenes = detect(video_path, ContentDetector(threshold=threshold))
    return [scene[0].get_seconds() for scene in scenes]


async def detect_all_scenes(clips: list[dict]) -> list[dict]:
    """Add scene timestamps to each clip dict."""
    loop = asyncio.get_event_loop()
    for clip in clips:
        scenes = await loop.run_in_executor(
            None, detect_scenes, clip["video"]
        )
        clip["scene_timestamps"] = scenes
    return clips
```

### pipeline/audio_track.py

```python
import asyncio
import httpx
import json
from pathlib import Path


WHISPER_URL = "https://testsucceed.com/whisper"

MUSIC_LIBRARY = {
    "peaceful":  {"tracks": ["ambient_calm_01", "ambient_calm_02"], "bpm": 70},
    "energetic": {"tracks": ["upbeat_travel_01", "upbeat_travel_02"], "bpm": 120},
    "intimate":  {"tracks": ["acoustic_soft_01"], "bpm": 80},
    "epic":      {"tracks": ["cinematic_wide_01"], "bpm": 95},
    "nostalgic": {"tracks": ["piano_warm_01"], "bpm": 75},
}


async def transcribe_clip(client: httpx.AsyncClient, audio_path: str, clip_id: str) -> dict:
    """Send audio to Whisper endpoint, return timestamped transcript."""
    with open(audio_path, "rb") as f:
        response = await client.post(
            WHISPER_URL,
            files={"audio": (Path(audio_path).name, f, "audio/wav")},
            data={"response_format": "verbose_json", "timestamp_granularities": "word"},
            timeout=60.0
        )
    
    data = response.json()
    return {
        "clip_id": clip_id,
        "transcript": data.get("text", ""),
        "segments": data.get("segments", []),  # [{start, end, text}]
        "words": data.get("words", []),
    }


async def transcribe_all(clips: list[dict]) -> list[dict]:
    """Transcribe all clips in parallel via Whisper."""
    async with httpx.AsyncClient() as client:
        tasks = [
            transcribe_clip(client, clip["audio"], clip["clip_id"])
            for clip in clips
        ]
        return await asyncio.gather(*tasks)


def classify_gaps(segments: list[dict], clip_duration: float) -> list[dict]:
    """
    Identify silence gaps in a clip's transcript and classify each.

    Gap types:
      pause  (<2s)  → trim this silence out
      broll  (2–6s) → keep but fill with silent visual footage
      music  (>6s)  → add background music here
    """
    gaps = []
    
    # Gap before first speech
    if segments and segments[0]["start"] > 1.0:
        gaps.append({
            "start": 0.0,
            "end": segments[0]["start"],
            "duration": segments[0]["start"],
        })
    
    # Gaps between speech segments
    for i in range(len(segments) - 1):
        gap_start = segments[i]["end"]
        gap_end = segments[i + 1]["start"]
        duration = gap_end - gap_start
        if duration > 0.5:
            gaps.append({
                "start": gap_start,
                "end": gap_end,
                "duration": duration,
            })
    
    # Gap after last speech
    if segments and segments[-1]["end"] < clip_duration - 1.0:
        gaps.append({
            "start": segments[-1]["end"],
            "end": clip_duration,
            "duration": clip_duration - segments[-1]["end"],
        })
    
    # Classify each gap
    for gap in gaps:
        d = gap["duration"]
        if d < 2.0:
            gap["type"] = "pause"
            gap["action"] = "trim"
        elif d < 6.0:
            gap["type"] = "broll"
            gap["action"] = "fill_with_silent_footage"
        else:
            gap["type"] = "music"
            gap["action"] = "add_music"
    
    return gaps


def select_music(mood: str, duration_seconds: float) -> dict:
    """Pick a music track matching mood and target BPM."""
    mood_key = mood if mood in MUSIC_LIBRARY else "peaceful"
    lib = MUSIC_LIBRARY[mood_key]
    return {
        "mood": mood_key,
        "bpm_target": lib["bpm"],
        "suggested_track": lib["tracks"][0],
        "duration": duration_seconds,
        "fade_in_ms": 800,
        "fade_out_ms": 1200,
    }
```

### pipeline/visual_track.py

```python
import asyncio
import base64
import subprocess
import numpy as np
from pathlib import Path
from PIL import Image
import clip
import torch
import openai


# CLIP setup — load once at startup
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_clip_model, _clip_preprocess = None, None

TRAVEL_LABELS = [
    "beach", "ocean", "mountains", "forest", "desert", "river",
    "temple", "church", "mosque", "historic building", "ruins",
    "restaurant", "street food", "market", "food close-up",
    "city street", "night city", "crowd", "alleyway",
    "airport", "train", "bus", "car journey",
    "hotel room", "accommodation",
    "selfie", "portrait", "group of people",
    "drone aerial shot", "wide landscape", "sunset", "sunrise",
    "museum", "art gallery", "cultural performance",
]

MOOD_LABELS = [
    "peaceful and calm", "energetic and exciting",
    "intimate and personal", "epic and cinematic",
    "nostalgic and warm", "mysterious and atmospheric",
]


def get_clip_model():
    global _clip_model, _clip_preprocess
    if _clip_model is None:
        _clip_model, _clip_preprocess = clip.load("ViT-L/14", device=DEVICE)
    return _clip_model, _clip_preprocess


def extract_key_frame(video_path: str, timestamp: float, output_path: str) -> str:
    """Extract a single frame at timestamp from video."""
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=384:384:force_original_aspect_ratio=decrease",
        output_path
    ], capture_output=True)
    return output_path


def clip_classify_frames(image_paths: list[str]) -> list[dict]:
    """
    Run CLIP on a batch of frames.
    Returns scene type, mood, and confidence for each.
    """
    model, preprocess = get_clip_model()
    
    # Encode text labels
    all_labels = TRAVEL_LABELS + MOOD_LABELS
    text_tokens = clip.tokenize(all_labels).to(DEVICE)
    
    results = []
    
    # Process in batches of 8
    batch_size = 8
    for i in range(0, len(image_paths), batch_size):
        batch_paths = image_paths[i:i + batch_size]
        images = torch.stack([
            preprocess(Image.open(p).convert("RGB"))
            for p in batch_paths
        ]).to(DEVICE)
        
        with torch.no_grad():
            image_features = model.encode_image(images)
            text_features = model.encode_text(text_tokens)
            
            image_features /= image_features.norm(dim=-1, keepdim=True)
            text_features /= text_features.norm(dim=-1, keepdim=True)
            
            similarity = (image_features @ text_features.T).softmax(dim=-1)
        
        for j, path in enumerate(batch_paths):
            scores = similarity[j].cpu().numpy()
            scene_scores = scores[:len(TRAVEL_LABELS)]
            mood_scores = scores[len(TRAVEL_LABELS):]
            
            best_scene_idx = np.argmax(scene_scores)
            best_mood_idx = np.argmax(mood_scores)
            
            results.append({
                "frame_path": path,
                "scene_type": TRAVEL_LABELS[best_scene_idx],
                "scene_confidence": float(scene_scores[best_scene_idx]),
                "mood": MOOD_LABELS[best_mood_idx].split(" and ")[0],
                "mood_confidence": float(mood_scores[best_mood_idx]),
            })
    
    return results


async def gpt4o_describe_frame(
    client: openai.AsyncOpenAI,
    image_path: str,
    clip_id: str,
    frame_idx: int
) -> dict:
    """
    Call GPT-4o-mini vision for a single frame.
    Uses low detail + 30 max tokens for speed and cost.
    """
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                        "detail": "low"      # cheaper + faster
                    }
                },
                {
                    "type": "text",
                    "text": (
                        "In 10 words max: scene type, main subject, mood. "
                        "Example: 'crowded night market, street food stalls, vibrant'"
                    )
                }
            ]
        }],
        max_tokens=30
    )
    
    return {
        "clip_id": clip_id,
        "frame_idx": frame_idx,
        "description": response.choices[0].message.content.strip(),
    }


async def describe_uncertain_frames(frames: list[dict]) -> list[dict]:
    """
    For frames where CLIP confidence < 0.6, call GPT-4o-mini.
    All calls are made in parallel as async coroutines.
    """
    uncertain = [f for f in frames if f["scene_confidence"] < 0.6]
    
    if not uncertain:
        return frames
    
    client = openai.AsyncOpenAI()
    tasks = [
        gpt4o_describe_frame(
            client,
            f["frame_path"],
            f["clip_id"],
            f["frame_idx"]
        )
        for f in uncertain
    ]
    
    descriptions = await asyncio.gather(*tasks)
    desc_map = {
        (d["clip_id"], d["frame_idx"]): d["description"]
        for d in descriptions
    }
    
    for frame in frames:
        key = (frame["clip_id"], frame.get("frame_idx", 0))
        if key in desc_map:
            frame["gpt_description"] = desc_map[key]
    
    return frames


def predict_transition(clip_a_last_frame: str, clip_b_first_frame: str) -> dict:
    """
    Compare CLIP embeddings of adjacent clip boundaries.
    Returns transition type based on visual similarity.
    """
    model, preprocess = get_clip_model()
    
    imgs = torch.stack([
        preprocess(Image.open(clip_a_last_frame).convert("RGB")),
        preprocess(Image.open(clip_b_first_frame).convert("RGB")),
    ]).to(DEVICE)
    
    with torch.no_grad():
        features = model.encode_image(imgs)
        features /= features.norm(dim=-1, keepdim=True)
        similarity = float((features[0] @ features[1]).cpu())
    
    # Similarity thresholds
    if similarity > 0.85:
        return {"type": "hard_cut", "duration_ms": 0, "reason": "same scene"}
    elif similarity > 0.65:
        return {"type": "dissolve", "duration_ms": 500, "reason": "similar scene"}
    else:
        return {"type": "fade_black", "duration_ms": 400, "reason": "scene change"}
```

### pipeline/edit_llm.py

```python
import json
import openai
from models.schemas import EditPlan


EDIT_SYSTEM_PROMPT = """You are a professional travel video editor.
Given a transcript with timestamps and a visual scene map, output a JSON edit plan.

Rules:
- Remove speech pauses under 2 seconds
- Fill b-roll gaps (2–6s) with relevant silent footage from the scene map
- Add music to long silent gaps (>6s) matching the mood
- Keep strong spoken moments intact
- Cut blurry, duplicate, or low-energy scenes
- Total output should be roughly 40–50% of input duration
- Prefer narrative continuity — spoken content should connect logically

Output ONLY valid JSON matching the EditPlan schema. No explanation."""


async def generate_edit_plan(
    transcripts: list[dict],
    scene_map: list[dict],
    gap_map: list[dict],
    music_suggestions: list[dict],
) -> EditPlan:
    """Call GPT-4o-mini to generate the edit plan JSON."""
    
    payload = {
        "transcripts": transcripts,
        "scenes": scene_map,
        "gaps": gap_map,
        "music_suggestions": music_suggestions,
    }
    
    client = openai.AsyncOpenAI()
    
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": EDIT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2)}
        ],
        max_tokens=2000,
        response_format={"type": "json_object"}
    )
    
    raw = json.loads(response.choices[0].message.content)
    return EditPlan(**raw)
```

### pipeline/renderer.py

```python
import asyncio
import json
import os
from models.schemas import EditPlan


async def render_video(edit_plan: EditPlan, clips_dir: str, output_path: str) -> str:
    """
    Build ffmpeg command from edit plan and render final video.
    Uses concat demuxer for cuts, filter_complex for transitions and audio.
    """
    
    # Build concat list file
    concat_lines = []
    filter_parts = []
    audio_mix_parts = []
    
    for i, clip in enumerate(edit_plan.clips):
        src = clip.source_file
        in_pt = clip.in_point
        out_pt = clip.out_point
        concat_lines.append(f"file '{src}'")
        concat_lines.append(f"inpoint {in_pt}")
        concat_lines.append(f"outpoint {out_pt}")
    
    concat_file = f"{clips_dir}/concat_list.txt"
    with open(concat_file, "w") as f:
        f.write("\n".join(concat_lines))
    
    # Base concat command — simple version (no crossfade)
    # For dissolve transitions, ffmpeg xfade filter would be applied here
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()}")
    
    return output_path
```

### pipeline/orchestrator.py

```python
import asyncio
import os
import uuid
import subprocess
import json as _json
from pathlib import Path

from pipeline.ordering import sort_clips_chronologically, get_creation_time
from pipeline.demux import demux_all, detect_all_scenes
from pipeline.audio_track import transcribe_all, classify_gaps, select_music
from pipeline.visual_track import (
    extract_key_frame, clip_classify_frames,
    describe_uncertain_frames, predict_transition
)
from pipeline.edit_llm import generate_edit_plan
from pipeline.renderer import render_video
from models.schemas import ProcessingStatus
from utils.cache import StepCache


async def run_pipeline(
    clip_paths: list[str],
    job_id: str,
    status_callback,
    output_dir: str
) -> str:
    """
    Full pipeline orchestrator with per-step caching.
    Every step checks the cache before running.
    If a step is already cached, it loads the result and skips processing.
    This means a failed or retried job continues from where it left off.
    """
    cache = StepCache(job_id, output_dir)
    tmp = f"{output_dir}/{job_id}"

    for subdir in ["cache", "frames", "audio", "video"]:
        os.makedirs(f"{tmp}/{subdir}", exist_ok=True)

    # ── STAGE 1: Chronological ordering ────────────────────────────────
    if cache.has_stage("ordering"):
        await status_callback(ProcessingStatus(
            job_id=job_id, stage="ordering", progress=0.05,
            message="Clip order restored from cache."
        ))
        ordering_data = cache.get_stage("ordering")["data"]
        sorted_clips = ordering_data["sorted_paths"]
    else:
        await status_callback(ProcessingStatus(
            job_id=job_id, stage="ordering", progress=0.05,
            message="Sorting clips chronologically..."
        ))
        sorted_clips = sort_clips_chronologically(clip_paths)
        cache.save_stage("ordering", {"sorted_paths": sorted_clips})

    # ── STAGE 2: Init clip cache records ───────────────────────────────
    # Idempotent — safe to call on retry
    for order_num, path in enumerate(sorted_clips):
        clip_id = Path(path).stem
        ct = get_creation_time(path)
        dur = _get_duration(path)
        cache.init_clip(clip_id, path, order_num, ct.isoformat(), dur)

    # ── STAGE 3: Demux + scene detect ─────────────────────────────────
    await status_callback(ProcessingStatus(
        job_id=job_id, stage="demux", progress=0.10,
        message="Splitting audio and video streams..."
    ))
    clips = await _demux_with_cache(sorted_clips, tmp, cache)

    # ── STAGE 4: Parallel audio + visual tracks ────────────────────────
    await status_callback(ProcessingStatus(
        job_id=job_id, stage="analysis", progress=0.15,
        message="Analysing audio and video in parallel..."
    ))

    audio_task = process_audio_track(clips, cache)
    visual_task = process_visual_track(clips, tmp, cache)

    (transcripts, gap_map, music_suggestions), scene_map = await asyncio.gather(
        audio_task, visual_task
    )

    # ── STAGE 5: Edit assembly LLM ─────────────────────────────────────
    if cache.has_stage("edit_plan"):
        await status_callback(ProcessingStatus(
            job_id=job_id, stage="edit_plan", progress=0.80,
            message="Edit plan restored from cache."
        ))
        from models.schemas import EditPlan
        edit_plan = EditPlan(**cache.get_stage("edit_plan")["data"])
    else:
        await status_callback(ProcessingStatus(
            job_id=job_id, stage="edit_plan", progress=0.80,
            message="Generating edit plan..."
        ))
        edit_plan = await generate_edit_plan(
            transcripts, scene_map, gap_map, music_suggestions
        )
        cache.save_stage("edit_plan", edit_plan.model_dump())

    # ── STAGE 6: Render ────────────────────────────────────────────────
    if cache.has_stage("render"):
        render_data = cache.get_stage("render")["data"]
        output_path = render_data["output_path"]
        await status_callback(ProcessingStatus(
            job_id=job_id, stage="done", progress=1.0,
            message="Video already rendered — restored from cache.",
            edit_plan=edit_plan,
            output_url=f"/outputs/{job_id}/output.mp4"
        ))
    else:
        await status_callback(ProcessingStatus(
            job_id=job_id, stage="rendering", progress=0.85,
            message="Rendering final video...",
            edit_plan=edit_plan
        ))
        output_path = f"{tmp}/output.mp4"
        await render_video(edit_plan, tmp, output_path)

        file_size = os.path.getsize(output_path) / (1024 * 1024)
        cache.save_stage("render", {
            "output_path": output_path,
            "file_size_mb": round(file_size, 2),
        })

        await status_callback(ProcessingStatus(
            job_id=job_id, stage="done", progress=1.0,
            message="Done!",
            edit_plan=edit_plan,
            output_url=f"/outputs/{job_id}/output.mp4"
        ))

    return output_path


async def _demux_with_cache(sorted_clips: list[str], tmp: str, cache: StepCache) -> list[dict]:
    """Demux clips — skip any clip where demux is already cached."""
    clips = []
    needs_demux = []

    for path in sorted_clips:
        clip_id = Path(path).stem
        if cache.has_step(clip_id, "demux") and cache.has_step(clip_id, "scene_detect"):
            # Restore from cache
            demux_data = cache.get_step(clip_id, "demux")
            scene_data = cache.get_step(clip_id, "scene_detect")
            clips.append({
                "clip_id": clip_id,
                "source_file": path,
                "video": demux_data["video_path"],
                "audio": demux_data["audio_path"],
                "scene_timestamps": scene_data["timestamps"],
            })
        else:
            needs_demux.append(path)

    if needs_demux:
        from pipeline.demux import demux_all, detect_all_scenes
        new_clips = await demux_all(needs_demux, f"{tmp}/video", f"{tmp}/audio")
        new_clips = await detect_all_scenes(new_clips)
        for clip in new_clips:
            cache.save_step(clip["clip_id"], "demux", {
                "video_path": clip["video"],
                "audio_path": clip["audio"],
            })
            cache.save_step(clip["clip_id"], "scene_detect", {
                "timestamps": clip["scene_timestamps"],
            })
        clips.extend(new_clips)

    # Re-sort to maintain chronological order
    order = {Path(p).stem: i for i, p in enumerate(sorted_clips)}
    clips.sort(key=lambda c: order.get(c["clip_id"], 999))
    return clips


async def process_audio_track(clips: list[dict], cache: StepCache):
    """Whisper transcription + gap classification + music selection, all cache-aware."""
    import httpx
    from pipeline.audio_track import transcribe_clip, classify_gaps, select_music

    transcripts = []
    gap_map = []
    music_suggestions = []

    async with httpx.AsyncClient() as client:
        tasks = []
        uncached = []
        for clip in clips:
            if cache.has_step(clip["clip_id"], "whisper"):
                transcripts.append({
                    "clip_id": clip["clip_id"],
                    **cache.get_step(clip["clip_id"], "whisper")
                })
            else:
                uncached.append(clip)

        if uncached:
            new_transcripts = await asyncio.gather(*[
                transcribe_clip(client, c["audio"], c["clip_id"])
                for c in uncached
            ])
            for t in new_transcripts:
                cid = t["clip_id"]
                cache.save_step(cid, "whisper", {
                    "transcript": t["transcript"],
                    "segments":   t["segments"],
                    "words":      t["words"],
                    "language":   t.get("language", "en"),
                })
                transcripts.append(t)

    # Gap classification (fast, no cache needed — depends only on cached whisper data)
    for clip, transcript in zip(
        sorted(clips, key=lambda c: c["clip_id"]),
        sorted(transcripts, key=lambda t: t["clip_id"])
    ):
        duration = _get_duration(clip["source_file"])
        gaps = classify_gaps(transcript.get("segments", []), duration)
        gap_map.extend([{"clip_id": clip["clip_id"], **g} for g in gaps])

        for gap in gaps:
            if gap["type"] == "music":
                suggestion = select_music("peaceful", gap["duration"])
                suggestion["clip_id"] = clip["clip_id"]
                suggestion["gap_start"] = gap["start"]
                music_suggestions.append(suggestion)

    return transcripts, gap_map, music_suggestions


async def process_visual_track(clips: list[dict], tmp_dir: str, cache: StepCache):
    """CLIP + GPT-4o-mini vision + transition prediction, all cache-aware."""
    from pipeline.visual_track import (
        extract_key_frame, clip_classify_frames,
        describe_uncertain_frames, predict_transition
    )

    frame_meta = []
    needs_classify = []

    for clip in clips:
        if cache.has_step(clip["clip_id"], "clip_classify"):
            cached_frames = cache.get_step(clip["clip_id"], "clip_classify")["frames"]
            # Restore gpt4o if also cached
            if cache.has_step(clip["clip_id"], "gpt4o_describe"):
                gpt_data = cache.get_step(clip["clip_id"], "gpt4o_describe")
                for f in cached_frames:
                    key = (clip["clip_id"], f.get("frame_idx", 0))
                    if "descriptions" in gpt_data:
                        f["gpt_description"] = gpt_data["descriptions"].get(str(key))
            frame_meta.extend(cached_frames)
        else:
            timestamps = clip.get("scene_timestamps", [0.0]) or [0.0]
            for i, ts in enumerate(timestamps[:2]):
                out_path = f"{tmp_dir}/frames/{clip['clip_id']}_scene{i}.jpg"
                extract_key_frame(clip["video"], ts, out_path)
                frame_meta.append({
                    "clip_id": clip["clip_id"],
                    "frame_idx": i,
                    "timestamp": ts,
                    "frame_path": out_path,
                })
            needs_classify.append(clip["clip_id"])

    if needs_classify:
        new_frames = [f for f in frame_meta if f["clip_id"] in needs_classify]
        paths = [f["frame_path"] for f in new_frames]
        results = clip_classify_frames(paths)

        for meta, result in zip(new_frames, results):
            meta.update(result)

        # Group by clip and save
        from itertools import groupby
        for clip_id, group in groupby(new_frames, key=lambda f: f["clip_id"]):
            frames_list = list(group)
            cache.save_step(clip_id, "clip_classify", {"frames": frames_list})

        # GPT-4o-mini for uncertain frames
        uncertain_ids = {f["clip_id"] for f in new_frames if f.get("scene_confidence", 1) < 0.6}
        if uncertain_ids:
            new_frames = await describe_uncertain_frames(new_frames)
            for clip_id in uncertain_ids:
                clip_frames = [f for f in new_frames if f["clip_id"] == clip_id]
                descriptions = {
                    str((f["clip_id"], f.get("frame_idx", 0))): f.get("gpt_description")
                    for f in clip_frames if "gpt_description" in f
                }
                cache.save_step(clip_id, "gpt4o_describe", {"descriptions": descriptions})
        else:
            for clip_id in needs_classify:
                if not cache.has_step(clip_id, "gpt4o_describe"):
                    cache.save_step(clip_id, "gpt4o_describe", {
                        "skipped": True, "reason": "all frames above confidence threshold"
                    })

    # Transition prediction between consecutive clips
    for i in range(len(clips) - 1):
        cid = clips[i]["clip_id"]
        if cache.has_step(cid, "transition_to_next"):
            clips[i]["transition_to_next"] = cache.get_step(cid, "transition_to_next")
        else:
            last_frame  = f"{tmp_dir}/frames/{clips[i]['clip_id']}_scene0.jpg"
            first_frame = f"{tmp_dir}/frames/{clips[i+1]['clip_id']}_scene0.jpg"
            if os.path.exists(last_frame) and os.path.exists(first_frame):
                t = predict_transition(last_frame, first_frame)
                cache.save_step(cid, "transition_to_next", t)
                clips[i]["transition_to_next"] = t

    return frame_meta


def _get_duration(video_path: str) -> float:
    """Return clip duration in seconds via ffprobe."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", video_path
        ], capture_output=True, text=True)
        info = _json.loads(result.stdout)
        return float(info["streams"][0].get("duration", 0))
    except Exception:
        return 0.0
```

### main.py (FastAPI)

```python
import asyncio
import os
import uuid
import aiofiles
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import json

from pipeline.orchestrator import run_pipeline
from models.schemas import ProcessingStatus

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "/tmp/video_uploads"
OUTPUT_DIR = "/tmp/video_outputs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# In-memory status store (use Redis in production)
job_status: dict[str, ProcessingStatus] = {}


@app.post("/upload")
async def upload_clips(files: list[UploadFile] = File(...)):
    """Accept multiple video clips, return a job_id."""
    job_id = str(uuid.uuid4())
    job_dir = f"{UPLOAD_DIR}/{job_id}"
    os.makedirs(job_dir)
    
    clip_paths = []
    for file in files:
        dest = f"{job_dir}/{file.filename}"
        async with aiofiles.open(dest, "wb") as f:
            await f.write(await file.read())
        clip_paths.append(dest)
    
    # Kick off pipeline as background task
    asyncio.create_task(
        run_pipeline(
            clip_paths,
            job_id,
            status_callback=lambda s: update_status(job_id, s),
            output_dir=OUTPUT_DIR
        )
    )
    
    return {"job_id": job_id}


def update_status(job_id: str, status: ProcessingStatus):
    job_status[job_id] = status


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    """SSE endpoint — streams progress updates to client."""
    async def event_stream():
        last_stage = None
        while True:
            status = job_status.get(job_id)
            if status and status.stage != last_stage:
                last_stage = status.stage
                yield f"data: {status.model_dump_json()}\n\n"
                if status.stage == "done":
                    break
            await asyncio.sleep(0.5)
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/outputs/{job_id}/output.mp4")
async def download_output(job_id: str):
    """Serve the rendered video file."""
    path = f"{OUTPUT_DIR}/{job_id}/output.mp4"
    async def file_stream():
        async with aiofiles.open(path, "rb") as f:
            while chunk := await f.read(1024 * 64):
                yield chunk
    
    return StreamingResponse(file_stream(), media_type="video/mp4")
```

---

## Frontend Implementation

### Shared API client (TypeScript)

```typescript
// api/pipeline.ts  (shared between web and mobile)

const BASE_URL = "https://your-api.com";

export interface ProcessingStatus {
  job_id: string;
  stage: "ordering" | "demux" | "analysis" | "edit_plan" | "rendering" | "done";
  progress: number;
  message: string;
  edit_plan?: EditPlan;
  output_url?: string;
}

export interface EditPlan {
  output_duration_estimate: string;
  clips: ClipDecision[];
  music_cues: MusicCue[];
  cuts_removed: RemovedClip[];
}

export async function uploadClips(files: File[]): Promise<string> {
  const form = new FormData();
  files.forEach(f => form.append("files", f));

  const res = await fetch(`${BASE_URL}/upload`, {
    method: "POST",
    body: form,
  });
  const { job_id } = await res.json();
  return job_id;
}

export function subscribeToStatus(
  jobId: string,
  onUpdate: (status: ProcessingStatus) => void
): () => void {
  const es = new EventSource(`${BASE_URL}/status/${jobId}`);
  es.onmessage = (e) => onUpdate(JSON.parse(e.data));
  es.onerror = () => es.close();
  return () => es.close();
}

export function getOutputUrl(jobId: string): string {
  return `${BASE_URL}/outputs/${jobId}/output.mp4`;
}
```

### React Native — Upload Screen

```typescript
// screens/UploadScreen.tsx
import React, { useState } from "react";
import { View, Button, Text, FlatList, ActivityIndicator } from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { useRouter } from "expo-router";
import { uploadClips } from "../api/pipeline";

export default function UploadScreen() {
  const [clips, setClips] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const router = useRouter();

  const pickClips = async () => {
    const result = await DocumentPicker.getDocumentAsync({
      type: "video/*",
      multiple: true,
    });
    if (!result.canceled) {
      setClips(result.assets);
    }
  };

  const startProcessing = async () => {
    setUploading(true);
    try {
      const files = await Promise.all(
        clips.map(async (c) => {
          const blob = await fetch(c.uri).then(r => r.blob());
          return new File([blob], c.name, { type: c.mimeType });
        })
      );
      const jobId = await uploadClips(files);
      router.push(`/processing/${jobId}`);
    } finally {
      setUploading(false);
    }
  };

  return (
    <View style={{ flex: 1, padding: 20 }}>
      <Text style={{ fontSize: 22, fontWeight: "500", marginBottom: 16 }}>
        Upload travel clips
      </Text>
      <Button title="Select video clips" onPress={pickClips} />
      {clips.length > 0 && (
        <>
          <FlatList
            data={clips}
            keyExtractor={(_, i) => String(i)}
            renderItem={({ item }) => (
              <Text style={{ padding: 8, color: "#555" }}>{item.name}</Text>
            )}
            style={{ marginVertical: 16 }}
          />
          {uploading ? (
            <ActivityIndicator size="large" />
          ) : (
            <Button
              title={`Process ${clips.length} clips`}
              onPress={startProcessing}
            />
          )}
        </>
      )}
    </View>
  );
}
```

### React Native — Processing Screen

```typescript
// screens/ProcessingScreen.tsx
import React, { useEffect, useState } from "react";
import { View, Text, ScrollView } from "react-native";
import { Video } from "expo-av";
import { useLocalSearchParams } from "expo-router";
import { subscribeToStatus, ProcessingStatus, getOutputUrl } from "../api/pipeline";

const STAGE_LABELS: Record<string, string> = {
  ordering:  "Sorting clips chronologically",
  demux:     "Splitting audio & video streams",
  analysis:  "Analysing scenes and speech",
  edit_plan: "Generating edit plan",
  rendering: "Rendering final video",
  done:      "Complete",
};

export default function ProcessingScreen() {
  const { jobId } = useLocalSearchParams<{ jobId: string }>();
  const [status, setStatus] = useState<ProcessingStatus | null>(null);

  useEffect(() => {
    const unsub = subscribeToStatus(jobId, setStatus);
    return unsub;
  }, [jobId]);

  const isDone = status?.stage === "done";

  return (
    <ScrollView style={{ flex: 1, padding: 20 }}>
      <Text style={{ fontSize: 22, fontWeight: "500", marginBottom: 20 }}>
        Processing your video
      </Text>

      {/* Progress steps */}
      {Object.entries(STAGE_LABELS).map(([key, label]) => {
        const current = status?.stage === key;
        const done = status && Object.keys(STAGE_LABELS).indexOf(key)
          < Object.keys(STAGE_LABELS).indexOf(status.stage);
        return (
          <View key={key} style={{ flexDirection: "row", alignItems: "center", marginBottom: 12 }}>
            <Text style={{ fontSize: 18, marginRight: 10 }}>
              {done ? "✓" : current ? "▶" : "○"}
            </Text>
            <Text style={{ color: done ? "#1D9E75" : current ? "#185FA5" : "#888" }}>
              {label}
            </Text>
          </View>
        );
      })}

      {status && (
        <Text style={{ color: "#555", marginTop: 8 }}>{status.message}</Text>
      )}

      {/* Edit plan summary */}
      {status?.edit_plan && (
        <View style={{ marginTop: 24, backgroundColor: "#f5f5f5", borderRadius: 8, padding: 14 }}>
          <Text style={{ fontWeight: "500", marginBottom: 8 }}>Edit plan</Text>
          <Text>Duration: {status.edit_plan.output_duration_estimate}</Text>
          <Text>Clips kept: {status.edit_plan.clips.length}</Text>
          <Text>Clips removed: {status.edit_plan.cuts_removed.length}</Text>
          <Text>Music cues: {status.edit_plan.music_cues.length}</Text>
        </View>
      )}

      {/* Output video */}
      {isDone && (
        <View style={{ marginTop: 24 }}>
          <Text style={{ fontWeight: "500", marginBottom: 8 }}>Your edited video</Text>
          <Video
            source={{ uri: getOutputUrl(jobId) }}
            useNativeControls
            style={{ width: "100%", height: 220, borderRadius: 8 }}
          />
        </View>
      )}
    </ScrollView>
  );
}
```

### Web — Processing Page

```typescript
// pages/Processing.tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { subscribeToStatus, ProcessingStatus, getOutputUrl } from "../api/pipeline";

const STAGES = [
  { key: "ordering",  label: "Sorting clips chronologically",   pct: 5  },
  { key: "demux",     label: "Splitting audio & video",          pct: 15 },
  { key: "analysis",  label: "Analysing scenes and speech",      pct: 70 },
  { key: "edit_plan", label: "Generating edit plan",             pct: 80 },
  { key: "rendering", label: "Rendering final video",            pct: 90 },
  { key: "done",      label: "Complete",                         pct: 100 },
];

export default function Processing() {
  const { jobId } = useParams<{ jobId: string }>();
  const [status, setStatus] = useState<ProcessingStatus | null>(null);

  useEffect(() => {
    if (!jobId) return;
    return subscribeToStatus(jobId, setStatus);
  }, [jobId]);

  const stageIndex = STAGES.findIndex(s => s.key === status?.stage);
  const progress = status?.progress ?? 0;

  return (
    <div style={{ maxWidth: 700, margin: "40px auto", padding: "0 20px" }}>
      <h1 style={{ fontSize: 24, fontWeight: 500, marginBottom: 24 }}>
        Processing your travel video
      </h1>

      {/* Progress bar */}
      <div style={{ background: "#eee", borderRadius: 4, height: 6, marginBottom: 24 }}>
        <div style={{
          background: "#185FA5",
          borderRadius: 4,
          height: "100%",
          width: `${progress * 100}%`,
          transition: "width 0.5s ease",
        }} />
      </div>

      {/* Stage list */}
      {STAGES.map((stage, i) => {
        const done = i < stageIndex;
        const active = i === stageIndex;
        return (
          <div key={stage.key} style={{
            display: "flex", alignItems: "center",
            gap: 12, marginBottom: 12,
            opacity: i > stageIndex ? 0.4 : 1,
          }}>
            <span style={{
              width: 20, textAlign: "center",
              color: done ? "#1D9E75" : active ? "#185FA5" : "#999",
            }}>
              {done ? "✓" : active ? "▶" : "○"}
            </span>
            <span style={{ color: active ? "#185FA5" : done ? "#1D9E75" : "#555" }}>
              {stage.label}
            </span>
          </div>
        );
      })}

      {status?.message && (
        <p style={{ color: "#777", fontSize: 13, marginTop: 8 }}>{status.message}</p>
      )}

      {/* Edit plan summary */}
      {status?.edit_plan && (
        <div style={{
          marginTop: 28, background: "#f7f7f7",
          borderRadius: 8, padding: 16,
        }}>
          <h3 style={{ fontWeight: 500, marginBottom: 12 }}>Edit plan summary</h3>
          <p>Estimated duration: {status.edit_plan.output_duration_estimate}</p>
          <p>Clips included: {status.edit_plan.clips.length}</p>
          <p>Clips removed: {status.edit_plan.cuts_removed.length}</p>
          <p>Music cues: {status.edit_plan.music_cues.length}</p>

          <details style={{ marginTop: 12 }}>
            <summary style={{ cursor: "pointer", color: "#185FA5" }}>
              Show removed clips
            </summary>
            {status.edit_plan.cuts_removed.map((c, i) => (
              <p key={i} style={{ fontSize: 13, color: "#666", margin: "4px 0" }}>
                {c.source_file}: {c.reason}
              </p>
            ))}
          </details>
        </div>
      )}

      {/* Output video */}
      {status?.stage === "done" && jobId && (
        <div style={{ marginTop: 28 }}>
          <h3 style={{ fontWeight: 500, marginBottom: 12 }}>Your edited video</h3>
          <video
            controls
            src={getOutputUrl(jobId)}
            style={{ width: "100%", borderRadius: 8, background: "#000" }}
          />
          <a
            href={getOutputUrl(jobId)}
            download="travel_edit.mp4"
            style={{
              display: "inline-block", marginTop: 12,
              padding: "8px 16px", background: "#185FA5",
              color: "#fff", borderRadius: 6, textDecoration: "none",
            }}
          >
            Download MP4
          </a>
        </div>
      )}
    </div>
  );
}
```

---

## Environment Variables

```bash
# .env
OPENAI_API_KEY=sk-...
WHISPER_URL=https://testsucceed.com/whisper
OUTPUT_DIR=/tmp/video_outputs
UPLOAD_DIR=/tmp/video_uploads
MAX_CONCURRENT_JOBS=4
```

---

## Deployment Notes

**GPU server (A2 recommended minimum):**
- Python 3.11+
- CUDA 12.1+
- ffmpeg 6.0+ with libx264
- Min 8 GB VRAM for CLIP + BLIP-2 in INT8
- ImageMagick (required by MoviePy for text rendering): `apt-get install imagemagick`

**Start server:**
```bash
pip install -r requirements.txt
# moviepy 2.0.1dev1 install (pre-release — pin exactly):
pip install "moviepy==2.0.1dev1"
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

**Cache behavior on retry:**
If a job fails at any stage, the client can POST to `/retry/{job_id}`. The orchestrator reloads the `StepCache` for that job and skips every step that already has a completed entry in its clip JSON or stage JSON. Whisper is never re-called if its output is cached. GPT-4o-mini calls are skipped for any frame already described. The edit plan is reused if it was generated. Only the ffmpeg render is re-run if it failed mid-write (detected by absence of `_stage_render.json`).

**Cache storage per job:**
A 20-clip project typically produces ~2–5 MB of cache JSON total. Frame JPEGs (384px) add ~4–8 MB. Both are safe to store alongside the output video and delete after the user downloads.

**Estimated processing time (A2, 8 GB, 20-min input):**

| Stage | Time | Cached retry |
|---|---|---|
| Sort + demux | ~5s | ~0.1s |
| Whisper (20 min audio) | ~25–30s | ~0s |
| CLIP (parallel) | ~4s | ~0s |
| GPT-4o-mini vision | ~2–3s | ~0s |
| Edit LLM | ~5s | ~0s |
| MoviePy transitions/covers | ~5–15s | ~5–15s |
| ffmpeg render | ~20–40s | ~20–40s |
| **Total (first run)** | **~65–95s** | |
| **Total (retry, render only)** | | **~25–55s** |
