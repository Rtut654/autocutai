"""Service for ffmpeg-based video processing and rendering."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

from ..models.project import EditMode, Project, VideoTrack

logger = logging.getLogger(__name__)


class VideoProcessor:
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.temp_dir = Path("temp")
        self.temp_dir.mkdir(exist_ok=True)

    async def process_project(self, project: Project) -> str:
        output_dir = self._project_output_dir(project)
        tracks = project.tracks
        if project.settings.edit_mode == EditMode.CHRONOLOGICAL:
            tracks = sorted(tracks, key=lambda t: t.position)

        processed_tracks: List[VideoTrack] = []
        for track in tracks:
            processed_tracks.append(await self._process_track(track, project))

        merged = await self._combine_tracks(
            processed_tracks,
            apply_gap_cuts=project.settings.smart_pause_cutter,
            output_dir=output_dir,
        )

        if project.settings.generate_subtitles and project.pipeline.subtitle_path:
            subtitle_file = Path(project.pipeline.subtitle_path)
            if subtitle_file.exists():
                try:
                    merged = await self._burn_subtitles(merged, subtitle_file, output_dir)
                except RuntimeError as exc:
                    if self._can_skip_subtitle_burn(exc):
                        logger.warning("Skipping subtitle burn-in: %s", exc)
                    else:
                        raise

        return merged

    async def extract_audio_for_transcription(self, source_path: str, output_path: str) -> str:
        """Extract a mono 16 kHz WAV track for Whisper-style transcription."""
        cmd = [
            self.ffmpeg_path,
            "-i",
            source_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-y",
            output_path,
        ]
        await self._run_ffmpeg_command(cmd)
        return output_path

    async def ensure_browser_playable_video(self, source_path: str | Path, output_path: str | Path) -> str:
        """Return a browser-friendly MP4 preview, transcoding only when needed."""
        source = Path(source_path)
        target = Path(output_path)
        if source.suffix.lower() in {".mp4", ".m4v", ".webm", ".ogv", ".ogg"}:
            return str(source)
        if target.exists() and target.stat().st_size > 0:
            return str(target)

        target.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg_path,
            "-i",
            str(source),
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-y",
            str(target),
        ]
        await self._run_ffmpeg_command(cmd)
        return str(target)

    async def detect_silence_ranges(
        self,
        source_path: str | Path,
        *,
        noise_db: float = -35.0,
        min_silence_duration: float = 0.25,
    ) -> List[tuple[float, float]]:
        """Detect silence intervals from the audio track of the given media source."""
        cmd = [
            self.ffmpeg_path,
            "-i",
            str(source_path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence_duration}",
            "-f",
            "null",
            "-",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is required but was not found in PATH") from exc

        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() if stderr else "ffmpeg silence detection failed")

        text = stderr.decode("utf-8", errors="replace")
        silence_starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", text)]
        silence_ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", text)]

        ranges: List[tuple[float, float]] = []
        for start, end in zip(silence_starts, silence_ends):
            if end > start:
                ranges.append((start, end))
        return ranges

    async def _process_track(self, track: VideoTrack, project: Project) -> VideoTrack:
        return track

    def _keep_segments_for_track(self, track: VideoTrack, apply_gap_cuts: bool) -> List[tuple[float, float]]:
        if not apply_gap_cuts or not track.local_gap_ranges:
            if track.duration <= 0:
                return []
            return [(0.0, track.duration)]

        gaps = sorted(track.local_gap_ranges, key=lambda g: g.start)
        keep_segments: List[tuple[float, float]] = []
        current = 0.0
        for gap in gaps:
            if gap.start > current:
                keep_segments.append((current, gap.start))
            current = max(current, gap.end)
        if current < track.duration:
            keep_segments.append((current, track.duration))

        filtered_segments = [(start, end) for start, end in keep_segments if end - start >= 0.08]
        if filtered_segments:
            return filtered_segments
        if track.duration <= 0:
            return []
        return [(0.0, min(track.duration, 0.1))]

    async def _combine_tracks(self, tracks: List[VideoTrack], apply_gap_cuts: bool = False, output_dir: Path | None = None) -> str:
        target_dir = output_dir or self.temp_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        concat_lines: List[str] = []
        for track in tracks:
            source_path = str(Path(track.file_path).resolve())
            keep_segments = self._keep_segments_for_track(track, apply_gap_cuts)
            for start, end in keep_segments:
                concat_lines.append(f"file '{source_path}'")
                if start > 0:
                    concat_lines.append(f"inpoint {start:.3f}")
                if end > 0:
                    concat_lines.append(f"outpoint {end:.3f}")

        if not concat_lines:
            raise RuntimeError("No usable video ranges were available to render")

        concat_file = target_dir / f"concat_all_{uuid.uuid4().hex}.txt"
        concat_file.write_text("\n".join(concat_lines), encoding="utf-8")
        out = target_dir / "output.mp4"
        cmd = [
            self.ffmpeg_path,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-y",
            str(out),
        ]
        try:
            await self._run_ffmpeg_command(cmd)
        finally:
            concat_file.unlink(missing_ok=True)
        return str(out)

    async def _burn_subtitles(self, video_path: str, subtitle_path: Path, output_dir: Path) -> str:
        out = output_dir / f"subbed_{uuid.uuid4().hex}.mp4"
        subtitle_filter = f"subtitles=filename='{self._escape_filter_value(subtitle_path.resolve().as_posix())}'"
        cmd = [
            self.ffmpeg_path,
            "-i",
            video_path,
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-y",
            str(out),
        ]
        await self._run_ffmpeg_command(cmd)
        return str(out)

    def _project_output_dir(self, project: Project) -> Path:
        if project.output_path:
            return Path(project.output_path).resolve().parent
        for track in project.tracks:
            file_path = getattr(track, "file_path", None)
            if not file_path:
                continue
            track_path = Path(file_path)
            if track_path.parent.name == "video":
                return track_path.parent.parent
        if project.pipeline.subtitle_path:
            subtitle_path = Path(project.pipeline.subtitle_path)
            if subtitle_path.parent.name == "transcript":
                return subtitle_path.parent.parent
        return self.temp_dir

    def _escape_filter_value(self, value: str) -> str:
        escaped = value.replace("\\", "\\\\")
        for char in (":", "'", ",", "[", "]"):
            escaped = escaped.replace(char, f"\\{char}")
        return escaped

    def _can_skip_subtitle_burn(self, error: RuntimeError) -> bool:
        message = str(error)
        return "No such filter: 'subtitles'" in message or "Filter not found" in message

    async def get_video_info(self, video_path: str) -> Dict[str, Any]:
        cmd = [
            self.ffprobe_path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            video_path,
        ]
        output = await self._run_command(cmd)
        import json

        return json.loads(output)

    async def _run_ffmpeg_command(self, cmd: List[str]) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is required but was not found in PATH") from exc
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() if stderr else "ffmpeg command failed")

    async def _run_command(self, cmd: List[str]) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            missing_binary = Path(cmd[0]).name
            raise RuntimeError(f"{missing_binary} is required but was not found in PATH") from exc
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() if stderr else "command failed")
        return stdout.decode("utf-8")


video_processor = VideoProcessor()
