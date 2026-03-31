"""FFmpeg demux and PySceneDetect scene detection."""

import asyncio
from pathlib import Path

try:
    from scenedetect import detect, ContentDetector
except ImportError:
    detect = None
    ContentDetector = None


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
    if detect is None:
        return [0.0]

    try:
        scenes = detect(video_path, ContentDetector(threshold=threshold))
        return [scene[0].get_seconds() for scene in scenes]
    except Exception:
        return [0.0]


async def detect_all_scenes(clips: list[dict]) -> list[dict]:
    """Add scene timestamps to each clip dict."""
    loop = asyncio.get_event_loop()
    for clip in clips:
        scenes = await loop.run_in_executor(
            None, detect_scenes, clip["video"]
        )
        clip["scene_timestamps"] = scenes
    return clips
