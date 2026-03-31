"""Audio track processing: Whisper transcription, gap classification, music selection."""

import asyncio
import os
import httpx
from pathlib import Path


WHISPER_URL = os.getenv("WHISPER_URL", "https://testsucceed.com/whisper")

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
        "segments": data.get("segments", []),
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
      pause  (<2s)  - trim this silence out
      broll  (2-6s) - keep but fill with silent visual footage
      music  (>6s)  - add background music here
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
