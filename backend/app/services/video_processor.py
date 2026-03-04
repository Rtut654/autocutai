"""Service for ffmpeg-based video processing and rendering."""

from __future__ import annotations

import asyncio
import logging
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
        tracks = project.tracks
        if project.settings.edit_mode == EditMode.CHRONOLOGICAL:
            tracks = sorted(tracks, key=lambda t: t.position)

        processed_tracks: List[VideoTrack] = []
        for track in tracks:
            processed_tracks.append(await self._process_track(track, project))

        merged = await self._combine_tracks(processed_tracks)

        if project.settings.generate_subtitles and project.pipeline.subtitle_path:
            subtitle_file = Path(project.pipeline.subtitle_path)
            if subtitle_file.exists():
                merged = await self._burn_subtitles(merged, subtitle_file)

        return merged

    async def _process_track(self, track: VideoTrack, project: Project) -> VideoTrack:
        if not project.settings.smart_pause_cutter:
            return track

        if not track.local_gap_ranges:
            return track

        output_path = await self._cut_track_gaps(track)
        track.file_path = output_path
        return track

    async def _cut_track_gaps(self, track: VideoTrack) -> str:
        gaps = sorted(track.local_gap_ranges, key=lambda g: g.start)
        keep_segments: List[tuple[float, float]] = []
        current = 0.0
        for gap in gaps:
            if gap.start > current:
                keep_segments.append((current, gap.start))
            current = max(current, gap.end)
        if current < track.duration:
            keep_segments.append((current, track.duration))

        if not keep_segments:
            keep_segments.append((0.0, min(track.duration, 0.1)))

        seg_paths: List[Path] = []
        for idx, (start, end) in enumerate(keep_segments):
            if end - start < 0.08:
                continue
            seg_path = self.temp_dir / f"segment_{track.id}_{idx}.mp4"
            cmd = [
                self.ffmpeg_path,
                "-ss",
                f"{start:.3f}",
                "-to",
                f"{end:.3f}",
                "-i",
                track.file_path,
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-preset",
                "veryfast",
                "-y",
                str(seg_path),
            ]
            await self._run_ffmpeg_command(cmd)
            seg_paths.append(seg_path)

        if not seg_paths:
            return track.file_path

        concat_list = self.temp_dir / f"concat_{track.id}.txt"
        concat_list.write_text("\n".join([f"file '{p.resolve()}'" for p in seg_paths]), encoding="utf-8")

        out = self.temp_dir / f"trimmed_{track.id}.mp4"
        cmd = [
            self.ffmpeg_path,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            "-y",
            str(out),
        ]
        await self._run_ffmpeg_command(cmd)
        return str(out)

    async def _combine_tracks(self, tracks: List[VideoTrack]) -> str:
        if len(tracks) == 1:
            source = tracks[0].file_path
            out = self.temp_dir / f"final_{uuid.uuid4().hex}.mp4"
            cmd = [self.ffmpeg_path, "-i", source, "-c:v", "libx264", "-c:a", "aac", "-y", str(out)]
            await self._run_ffmpeg_command(cmd)
            return str(out)

        concat_file = self.temp_dir / f"concat_all_{uuid.uuid4().hex}.txt"
        concat_file.write_text("\n".join([f"file '{Path(t.file_path).resolve()}'" for t in tracks]), encoding="utf-8")
        out = self.temp_dir / f"final_{uuid.uuid4().hex}.mp4"
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
        await self._run_ffmpeg_command(cmd)
        return str(out)

    async def _burn_subtitles(self, video_path: str, subtitle_path: Path) -> str:
        out = self.temp_dir / f"subbed_{uuid.uuid4().hex}.mp4"
        subtitle_filter = f"subtitles={subtitle_path.resolve()}"
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
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() if stderr else "ffmpeg command failed")

    async def _run_command(self, cmd: List[str]) -> str:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode() if stderr else "command failed")
        return stdout.decode("utf-8")


video_processor = VideoProcessor()
