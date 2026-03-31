"""Chronological ordering of video clips by EXIF/filename/filesystem metadata."""

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
