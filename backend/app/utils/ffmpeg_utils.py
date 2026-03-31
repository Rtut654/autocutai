"""FFmpeg utility helpers for the pipeline."""

import json
import subprocess


def get_duration(video_path: str) -> float:
    """Return clip duration in seconds via ffprobe."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", video_path
        ], capture_output=True, text=True, timeout=30)
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            if "duration" in stream:
                return float(stream["duration"])
        return 0.0
    except Exception:
        return 0.0


def get_video_info(video_path: str) -> dict:
    """Return basic video info via ffprobe."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", video_path
        ], capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)
    except Exception:
        return {}
