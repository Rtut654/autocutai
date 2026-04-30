"""Service for ffmpeg-based video processing and rendering."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import wave
import uuid
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont

from ..models.project import EditMode, Project, SubtitleCue, VideoTrack, VisualPlanPart, ZoomPreviewBeat

logger = logging.getLogger(__name__)


class VideoProcessor:
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.temp_dir = Path("temp")
        self.temp_dir.mkdir(exist_ok=True)

    async def render_styled_track(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *,
        width: int,
        height: int,
        zoom_beats: List[ZoomPreviewBeat],
        visual_parts: List[VisualPlanPart],
    ) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        overlay_dir = target.parent / f".overlay_{uuid.uuid4().hex}"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        even_width = max(2, width - (width % 2))
        even_height = max(2, height - (height % 2))

        overlay_inputs: List[str] = []
        filter_steps: List[str] = []

        zoom_expr = self._build_zoom_scale_expression(zoom_beats)
        if zoom_expr != "1":
            filter_steps.append(
                f"[0:v]scale='trunc(iw*({zoom_expr})/2)*2':'trunc(ih*({zoom_expr})/2)*2':eval=frame,"
                f"crop={even_width}:{even_height}:(iw-{even_width})/2:(ih-{even_height})/2,format=rgba[base0]"
            )
        else:
            filter_steps.append(f"[0:v]scale={even_width}:{even_height}:force_original_aspect_ratio=decrease,pad={even_width}:{even_height}:(ow-iw)/2:(oh-ih)/2,format=rgba[base0]")

        overlay_specs: List[tuple[VisualPlanPart, Path]] = []
        for index, part in enumerate(visual_parts):
            overlay_path = overlay_dir / f"overlay_{index + 1}.png"
            self._render_visual_overlay_asset(
                overlay_path=overlay_path,
                width=even_width,
                height=even_height,
                part=part,
                part_index=index,
            )
            overlay_specs.append((part, overlay_path))
            overlay_inputs.extend(["-loop", "1", "-i", str(overlay_path)])

        current_label = "base0"
        for index, (part, _) in enumerate(overlay_specs, start=1):
            local_duration = max(0.25, float(part.duration or 0.25))
            fade_out_start = max(0.0, local_duration - 0.18)
            overlay_stream = f"ov{index}"
            next_label = f"base{index}"
            filter_steps.append(
                f"[{index}:v]format=rgba,trim=duration={local_duration:.3f},"
                f"fade=t=in:st=0:d=0.18:alpha=1,"
                f"fade=t=out:st={fade_out_start:.3f}:d=0.18:alpha=1,"
                f"setpts=PTS-STARTPTS+{float(part.start):.3f}/TB[{overlay_stream}]"
            )
            x_expr, y_expr = self._overlay_motion_expressions(part)
            filter_steps.append(
                f"[{current_label}][{overlay_stream}]overlay="
                f"x='{x_expr}':y='{y_expr}':eof_action=pass:eval=frame[{next_label}]"
            )
            current_label = next_label

        cmd = [
            self.ffmpeg_path,
            "-i",
            str(source_path),
            *overlay_inputs,
            "-filter_complex",
            ";".join(filter_steps),
            "-map",
            f"[{current_label}]",
            "-map",
            "0:a?",
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-y",
            str(target),
        ]
        try:
            await self._run_ffmpeg_command(cmd)
        finally:
            for _, overlay_path in overlay_specs:
                overlay_path.unlink(missing_ok=True)
            overlay_dir.rmdir()
        return str(target)

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

    async def extract_audio_segment(
        self,
        source_path: str | Path,
        output_path: str | Path,
        start: float,
        end: float,
    ) -> str:
        """Extract a WAV sub-range from an audio source for more granular ASR."""
        cmd = [
            self.ffmpeg_path,
            "-ss",
            f"{max(0.0, start):.3f}",
            "-to",
            f"{max(start, end):.3f}",
            "-i",
            str(source_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-y",
            str(output_path),
        ]
        await self._run_ffmpeg_command(cmd)
        return str(output_path)

    async def render_source_segments(
        self,
        source_path: str | Path,
        segments: List[tuple[float, float]],
        output_path: str | Path,
    ) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized = [(max(0.0, float(start)), max(0.0, float(end))) for start, end in segments if end - start >= 0.05]
        if not normalized:
            raise RuntimeError("No usable video ranges were available to render")
        info = await self.get_video_info(str(source_path))
        has_audio = any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))

        filter_steps: List[str] = []
        concat_inputs = ""
        for index, (start, end) in enumerate(normalized):
            filter_steps.append(
                f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]"
            )
            concat_inputs += f"[v{index}]"
            if has_audio:
                filter_steps.append(
                    f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]"
                )
                concat_inputs += f"[a{index}]"

        if has_audio:
            filter_steps.append(f"{concat_inputs}concat=n={len(normalized)}:v=1:a=1[vout][aout]")
        else:
            filter_steps.append(f"{concat_inputs}concat=n={len(normalized)}:v=1:a=0[vout]")

        cmd = [
            self.ffmpeg_path,
            "-i",
            str(source_path),
            "-filter_complex",
            ";".join(filter_steps),
            "-map",
            "[vout]",
        ]
        if has_audio:
            cmd.extend(["-map", "[aout]"])
        cmd.extend([
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
        ])
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "160k"])
        cmd.extend(["-y", str(target)])
        await self._run_ffmpeg_command(cmd)
        return str(target)

    async def generate_background_music_track(
        self,
        output_path: str | Path,
        *,
        duration: float,
        preset: str,
    ) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 44100
        total_frames = max(1, int(sample_rate * max(0.1, duration)))
        channels = 2

        def note_freq(name: str) -> float:
            table = {
                "C3": 130.81,
                "D3": 146.83,
                "E3": 164.81,
                "F3": 174.61,
                "G3": 196.00,
                "A3": 220.00,
                "B3": 246.94,
                "C4": 261.63,
                "D4": 293.66,
                "E4": 329.63,
                "G4": 392.00,
                "A4": 440.00,
            }
            return table[name]

        if preset == "upbeat_motion":
            chords = [["C3", "G3", "C4"], ["A3", "E4", "A4"], ["F3", "C4", "A4"], ["G3", "D4", "G4"]]
            beat = 0.33
            gain = 0.24
            wave_kind = "square"
        elif preset == "warm_focus":
            chords = [["A3", "C4", "E4"], ["G3", "C4", "E4"], ["F3", "A3", "C4"], ["G3", "B3", "D4"]]
            beat = 0.5
            gain = 0.18
            wave_kind = "triangle"
        else:
            chords = [["C3", "E3", "G3"], ["A3", "C4", "E4"], ["F3", "A3", "C4"], ["G3", "B3", "D4"]]
            beat = 0.66
            gain = 0.15
            wave_kind = "sine"

        def sample(kind: str, phase: float) -> float:
            if kind == "square":
                return 1.0 if math.sin(phase) >= 0 else -1.0
            if kind == "triangle":
                return (2 / math.pi) * math.asin(math.sin(phase))
            return math.sin(phase)

        phases = [0.0, 0.0, 0.0]
        frames = bytearray()
        for index in range(total_frames):
            time = index / sample_rate
            chord = chords[int(time / (beat * 4)) % len(chords)]
            local_beat = (time % beat) / beat
            pulse = 0.35 + 0.65 * max(0.0, 1.0 - local_beat * 1.35)
            left = 0.0
            right = 0.0
            for note_index, note in enumerate(chord[:3]):
                frequency = note_freq(note)
                phases[note_index] += (2 * math.pi * frequency) / sample_rate
                voice = sample(wave_kind, phases[note_index])
                pan = -0.18 if note_index == 0 else 0.18 if note_index == 2 else 0.0
                voice_gain = gain * (0.82 if note_index == 1 else 0.62) * pulse
                left += voice * voice_gain * (1 - max(0.0, pan))
                right += voice * voice_gain * (1 + min(0.0, pan))
            if preset != "ambient_pulse":
                bass_phase = phases[0] * 0.5
                bass = math.sin(bass_phase) * gain * 0.38 * pulse
                left += bass
                right += bass
            left = max(-1.0, min(1.0, left))
            right = max(-1.0, min(1.0, right))
            frames.extend(int(left * 32767).to_bytes(2, "little", signed=True))
            frames.extend(int(right * 32767).to_bytes(2, "little", signed=True))

        with wave.open(str(target), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))
        return str(target)

    async def mix_background_music(
        self,
        video_input: str | Path,
        music_input: str | Path,
        output_path: str | Path,
        *,
        music_volume: float,
        ducking: float,
    ) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        info = await self.get_video_info(str(video_input))
        has_audio = any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))
        duration = float(info.get("format", {}).get("duration", 0.0) or 0.0)

        if has_audio:
            ratio = max(3.0, 4.0 + ducking * 10.0)
            threshold = max(0.005, 0.035 - ducking * 0.02)
            filter_complex = (
                f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"volume={music_volume:.3f}[bg];"
                f"[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice];"
                f"[bg][voice]sidechaincompress=threshold={threshold:.3f}:ratio={ratio:.2f}:attack=18:release=280[bgduck];"
                f"[voice][bgduck]amix=inputs=2:weights='1 1':normalize=0[aout]"
            )
            cmd = [
                self.ffmpeg_path,
                "-i", str(video_input),
                "-i", str(music_input),
                "-filter_complex", filter_complex,
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                "-shortest",
                "-y", str(target),
            ]
        else:
            cmd = [
                self.ffmpeg_path,
                "-i", str(video_input),
                "-i", str(music_input),
                "-filter:a", f"volume={music_volume:.3f}",
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                "-t", f"{duration:.3f}" if duration > 0 else "0.1",
                "-shortest",
                "-y", str(target),
            ]
        await self._run_ffmpeg_command(cmd)
        return str(target)

    async def mix_visual_sfx(
        self,
        video_input: str | Path,
        output_path: str | Path,
        *,
        cue_times: List[float],
    ) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized = [max(0.0, float(time_value)) for time_value in cue_times]
        if not normalized:
            return str(video_input)

        info = await self.get_video_info(str(video_input))
        has_audio = any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))
        duration = float(info.get("format", {}).get("duration", 0.0) or 0.0)
        if duration <= 0:
            return str(video_input)

        sfx_path = target.parent / f".whoosh_{uuid.uuid4().hex}.wav"
        await self.generate_transition_sfx(sfx_path)
        try:
            cmd = [self.ffmpeg_path, "-i", str(video_input)]
            for _ in normalized:
                cmd.extend(["-i", str(sfx_path)])

            filter_steps: List[str] = []
            cue_inputs = ""
            for index, cue_time in enumerate(normalized, start=1):
                delay_ms = max(0, int(cue_time * 1000))
                filter_steps.append(
                    f"[{index}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                    f"volume=0.22,adelay={delay_ms}|{delay_ms}[sfx{index}]"
                )
                cue_inputs += f"[sfx{index}]"

            if has_audio:
                filter_steps.append(
                    f"[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[voice]"
                )
                filter_steps.append(f"{cue_inputs}amix=inputs={len(normalized)}:normalize=0[sfxmix]")
                filter_steps.append("[voice][sfxmix]amix=inputs=2:weights='1 0.8':normalize=0[aout]")
            else:
                filter_steps.append(f"{cue_inputs}amix=inputs={len(normalized)}:normalize=0[aout]")

            cmd.extend([
                "-filter_complex",
                ";".join(filter_steps),
                "-map",
                "0:v",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-shortest",
                "-y",
                str(target),
            ])
            await self._run_ffmpeg_command(cmd)
            return str(target)
        finally:
            sfx_path.unlink(missing_ok=True)

    def _build_zoom_scale_expression(self, zoom_beats: List[ZoomPreviewBeat]) -> str:
        enabled = [beat for beat in zoom_beats if beat.enabled and beat.end > beat.start]
        if not enabled:
            return "1"
        terms = []
        for beat in enabled:
            duration = max(0.001, float(beat.end - beat.start))
            scale_delta = max(0.0, float(beat.scale) - 1.0)
            terms.append(
                f"if(between(t,{beat.start:.3f},{beat.end:.3f}),{scale_delta:.5f}*((t-{beat.start:.3f})/{duration:.3f}),0)"
            )
        return f"1+({'+'.join(terms)})"

    def _overlay_motion_expressions(self, part: VisualPlanPart) -> tuple[str, str]:
        return "0", "0"

    async def generate_transition_sfx(self, output_path: str | Path) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 44100
        duration = 0.2
        total_frames = int(sample_rate * duration)
        channels = 2
        frames = bytearray()
        phase_a = 0.0
        phase_b = 0.0
        for index in range(total_frames):
            time = index / sample_rate
            progress = min(1.0, max(0.0, time / duration))
            env = math.sin(math.pi * progress) ** 1.4
            noise = (math.sin(index * 12.9898) * 43758.5453) % 1.0
            noise = (noise * 2.0) - 1.0
            freq_a = 700 + (1 - progress) * 900
            freq_b = 1800 - progress * 700
            phase_a += (2 * math.pi * freq_a) / sample_rate
            phase_b += (2 * math.pi * freq_b) / sample_rate
            sample_value = (
                noise * 0.18 * env
                + math.sin(phase_a) * 0.07 * env
                + math.sin(phase_b) * 0.04 * env
            )
            pcm = int(max(-1.0, min(1.0, sample_value)) * 32767)
            frames.extend(pcm.to_bytes(2, "little", signed=True))
            frames.extend(pcm.to_bytes(2, "little", signed=True))

        with wave.open(str(target), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))
        return str(target)

    def _render_visual_overlay_asset(
        self,
        *,
        overlay_path: Path,
        width: int,
        height: int,
        part: VisualPlanPart,
        part_index: int,
    ) -> None:
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        vertical = height > width
        if part.visual_type.value == "web_image":
            box_width = int(width * (0.34 if not vertical else 0.38))
            box_width = max(120, min(box_width, 180))
            box_height = int(box_width * 1.15)
        else:
            box_width = int(width * (0.42 if vertical else 0.32))
            box_width = max(170, min(box_width, 240))
            box_height = int(box_width * (1.48 if vertical else 1.18))
        if part.visual_type.value == "web_image":
            box_height = int(box_width * 1.15)
        x, y = self._overlay_anchor(
            width,
            height,
            box_width,
            box_height,
            str(part.placement or ""),
            vertical,
            part_index,
        )

        if part.visual_type.value == "web_image" and part.local_path and Path(part.local_path).exists():
            self._draw_web_image_overlay(image, draw, x, y, box_width, box_height, part)
        else:
            self._draw_animation_overlay(draw, x, y, box_width, box_height, part, part_index)

        image.save(overlay_path)

    def _draw_web_image_overlay(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        width: int,
        height: int,
        part: VisualPlanPart,
    ) -> None:
        asset = Image.open(part.local_path).convert("RGBA")
        asset.thumbnail((width, height))
        rounded = Image.new("RGBA", asset.size, (0, 0, 0, 0))
        mask = Image.new("L", asset.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, asset.size[0], asset.size[1]), radius=18, fill=255)
        rounded.paste(asset, (0, 0), mask)
        image.alpha_composite(rounded, (x, y))
        caption_font = self._load_font(12, bold=True)
        caption = str(part.title or part.text or "Visual").strip()[:26]
        pill_box = (x, y + asset.size[1] + 8, x + min(asset.size[0], width), y + asset.size[1] + 34)
        draw.rounded_rectangle(pill_box, radius=12, fill=(15, 23, 42, 132))
        draw.text((pill_box[0] + 10, pill_box[1] + 5), caption, font=caption_font, fill=(255, 255, 255, 238))

    def _draw_animation_overlay(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        width: int,
        height: int,
        part: VisualPlanPart,
        part_index: int,
    ) -> None:
        palette = self._palette_colors(str(part.palette or "cool"))
        title_font = self._load_font(34 if width < 210 else 38, bold=True)
        label_font = self._load_font(14, bold=True)
        chip_font = self._load_font(13, bold=True)
        title = str(part.title or part.text or "Visual").strip()
        words = [str(word).strip() for word in (part.keywords or []) if str(word).strip()][:2]
        label = self._kicker_for_part(part)
        motif = str(part.animation_kind or "idea_burst")

        motif_height = max(84, int(height * 0.34))
        self._draw_motif(draw, motif, x, y, width, motif_height, palette, part_index)
        label_y = y + motif_height + 8
        pill_width = min(width - 6, max(106, draw.textbbox((0, 0), label.upper(), font=label_font)[2] + 28))
        pill_height = 30
        draw.rounded_rectangle((x, label_y, x + pill_width, label_y + pill_height), radius=15, fill=palette["primary"])
        draw.text((x + 14, label_y + 6), label.upper(), font=label_font, fill=(255, 255, 255, 244))
        title_y = label_y + pill_height + 12
        wrapped_title = self._wrap_text(draw, title[:48], title_font, max(132, width - 10), max_lines=3)
        line_height = max(24, title_font.size + 4)
        for line_index, line in enumerate(wrapped_title):
            self._draw_shadow_text(
                draw,
                (x, title_y + line_index * line_height),
                line,
                font=title_font,
                fill=(255, 255, 255, 244),
            )

        chip_y = title_y + max(32, len(wrapped_title) * line_height) + 10
        chip_x = x
        for word in words:
            bbox = draw.textbbox((0, 0), word, font=chip_font)
            chip_width = bbox[2] - bbox[0] + 20
            draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_width, chip_y + 26), radius=13, fill=(15, 23, 42, 112), outline=(255, 255, 255, 30))
            draw.text((chip_x + 10, chip_y + 5), word, font=chip_font, fill=(255, 255, 255, 232))
            chip_x += chip_width + 6

    def _draw_motif(
        self,
        draw: ImageDraw.ImageDraw,
        motif: str,
        x: int,
        y: int,
        width: int,
        height: int,
        palette: Dict[str, tuple[int, int, int, int]],
        part_index: int,
    ) -> None:
        px = x + 8
        py = y + 42
        w = width - 16
        h = height - 18
        primary = palette["primary"]
        secondary = palette["secondary"]
        accent = palette["accent"]
        soft = palette["soft"]

        if motif == "conversation_flow":
            draw.rounded_rectangle((px + 6, py, px + w * 0.46, py + h * 0.26), radius=18, outline=primary, width=3, fill=soft)
            draw.rounded_rectangle((px + w * 0.48, py + h * 0.15, px + w * 0.86, py + h * 0.38), radius=18, outline=secondary, width=3, fill=(0, 0, 0, 0))
            draw.arc((px + w * 0.24, py + h * 0.38, px + w * 0.76, py + h * 0.88), start=205, end=335, fill=accent, width=5)
            self._draw_person(draw, px + w * 0.18, py + h * 0.78, accent)
            self._draw_person(draw, px + w * 0.72, py + h * 0.78, accent)
            return
        if motif in {"question_answer", "checklist_reveal"}:
            draw.ellipse((px + 10, py + 8, px + w * 0.3, py + h * 0.44), fill=soft, outline=primary, width=4)
            draw.ellipse((px + w * 0.56, py + h * 0.28, px + w * 0.86, py + h * 0.58), fill=(0, 0, 0, 0), outline=secondary, width=4)
            draw.line((px + w * 0.3, py + h * 0.34, px + w * 0.56, py + h * 0.42), fill=accent, width=5)
            return
        if motif in {"step_sequence", "timeline_sequence", "process_arrow"}:
            step_w = w * 0.18
            step_h = h * 0.22
            for idx in range(3):
                left = px + idx * (step_w + w * 0.08)
                top = py + h * 0.5 - idx * 10
                draw.rounded_rectangle((left, top, left + step_w, top + step_h), radius=16, fill=(255, 255, 255, 230))
                draw.ellipse((left + step_w * 0.32, top + step_h * 0.22, left + step_w * 0.68, top + step_h * 0.58), fill=primary)
            draw.line((px + step_w, py + h * 0.6, px + w * 0.76, py + h * 0.34), fill=secondary, width=5)
            return
        if motif in {"compare_problem_solution", "before_after_split", "decision_split"}:
            draw.ellipse((px + 10, py + h * 0.42, px + w * 0.28, py + h * 0.72), outline=secondary, width=4, fill=(255, 126, 147, 50))
            draw.ellipse((px + w * 0.62, py + h * 0.12, px + w * 0.88, py + h * 0.42), outline=primary, width=4, fill=(131, 255, 218, 50))
            draw.line((px + w * 0.3, py + h * 0.58, px + w * 0.66, py + h * 0.28), fill=accent, width=6)
            return
        if motif in {"object_spotlight", "map_pointer"}:
            draw.ellipse((px + w * 0.34, py + h * 0.02, px + w * 0.66, py + h * 0.34), outline=primary, width=4)
            draw.rounded_rectangle((px + w * 0.43, py + h * 0.12, px + w * 0.57, py + h * 0.26), radius=12, fill=accent)
            draw.line((px + w * 0.5, py + h * 0.34, px + w * 0.5, py + h * 0.52), fill=accent, width=5)
            return
        if motif in {"chart_pop", "concept_network", "idea_burst"}:
            points = [
                (px + w * 0.18, py + h * 0.62),
                (px + w * 0.38, py + h * 0.46),
                (px + w * 0.58, py + h * 0.52),
                (px + w * 0.78, py + h * 0.24),
            ]
            draw.line(points, fill=primary, width=5)
            for point in points:
                draw.ellipse((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8), fill=accent)
            return
        draw.ellipse((px + w * 0.35, py + h * 0.18, px + w * 0.65, py + h * 0.48), fill=soft, outline=primary, width=4)
        draw.line((px + w * 0.5, py + h * 0.04, px + w * 0.5, py + h * 0.16), fill=accent, width=4)

    def _draw_person(self, draw: ImageDraw.ImageDraw, x: float, y: float, color: tuple[int, int, int, int]) -> None:
        draw.ellipse((x - 18, y - 18, x + 18, y + 18), fill=color)
        draw.rounded_rectangle((x - 26, y + 20, x + 26, y + 50), radius=14, fill=color)

    def _wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        *,
        max_lines: int,
    ) -> List[str]:
        words = [segment for segment in text.split() if segment]
        if not words:
            return [text]
        lines: List[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
                continue
            lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                break
        remaining_words = words[len(" ".join(lines + [current]).split()):]
        if remaining_words:
            tail = " ".join([current, *remaining_words]).strip()
            while tail:
                bbox = draw.textbbox((0, 0), f"{tail}...", font=font)
                if bbox[2] - bbox[0] <= max_width or len(tail) <= 4:
                    current = f"{tail}..."
                    break
                tail = tail[:-1].rstrip()
        lines.append(current)
        return lines[:max_lines]

    def _overlay_anchor(
        self,
        width: int,
        height: int,
        box_width: int,
        box_height: int,
        placement: str,
        vertical: bool,
        part_index: int,
    ) -> tuple[int, int]:
        horizontal_margin = 18
        vertical_margin = 18
        if "right" in placement:
            x = width - box_width - horizontal_margin
        elif placement == "center":
            x = (width - box_width) // 2
        else:
            x = width - box_width - horizontal_margin if part_index % 2 else horizontal_margin
        y = vertical_margin
        return max(0, x), max(0, y)

    async def burn_subtitles(
        self,
        video_input: str | Path,
        subtitle_path: str | Path,
        output_path: str | Path,
    ) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        subtitle_filter = f"subtitles=filename='{self._escape_filter_value(Path(subtitle_path).resolve().as_posix())}'"
        cmd = [
            self.ffmpeg_path,
            "-i",
            str(video_input),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(target),
        ]
        await self._run_ffmpeg_command(cmd)
        return str(target)

    async def burn_subtitles_from_cues(
        self,
        video_input: str | Path,
        cues: List[SubtitleCue],
        output_path: str | Path,
    ) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        font_path = self._preferred_font_path(bold=True)
        font_expr = f"fontfile='{self._escape_filter_value(font_path)}':" if font_path else ""
        draw_steps: List[str] = []
        for cue in cues:
            text = self._escape_drawtext_text(str(cue.text))
            draw_steps.append(
                "drawtext="
                f"{font_expr}"
                f"text='{text}':"
                "fontcolor=white:"
                "fontsize=42:"
                "line_spacing=6:"
                "box=1:"
                "boxcolor=black@0.52:"
                "boxborderw=12:"
                "x=(w-text_w)/2:"
                "y=h-(text_h*2.8):"
                f"enable='between(t,{float(cue.start):.3f},{float(cue.end):.3f})'"
            )
        cmd = [
            self.ffmpeg_path,
            "-i",
            str(video_input),
            "-vf",
            ",".join(draw_steps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(target),
        ]
        await self._run_ffmpeg_command(cmd)
        return str(target)

    async def attach_subtitle_track(
        self,
        video_input: str | Path,
        subtitle_path: str | Path,
        output_path: str | Path,
    ) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg_path,
            "-i",
            str(video_input),
            "-i",
            str(subtitle_path),
            "-map",
            "0:v",
            "-map",
            "0:a?",
            "-map",
            "1:0",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-c:s",
            "mov_text",
            "-movflags",
            "+faststart",
            "-y",
            str(target),
        ]
        await self._run_ffmpeg_command(cmd)
        return str(target)

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        candidates = self._font_candidates(bold=bold)
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                try:
                    return ImageFont.truetype(candidate, size=size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _font_candidates(self, *, bold: bool) -> List[str]:
        return [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

    def _preferred_font_path(self, *, bold: bool) -> str | None:
        for candidate in self._font_candidates(bold=bold):
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _escape_drawtext_text(self, value: str) -> str:
        escaped = value.replace("\\", "\\\\")
        escaped = escaped.replace(":", "\\:")
        escaped = escaped.replace("'", "\\'")
        escaped = escaped.replace("%", "\\%")
        escaped = escaped.replace("[", "\\[")
        escaped = escaped.replace("]", "\\]")
        return escaped

    def _draw_shadow_text(
        self,
        draw: ImageDraw.ImageDraw,
        position: tuple[int, int],
        text: str,
        *,
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int, int],
    ) -> None:
        x, y = position
        shadow = (15, 23, 42, 150)
        draw.text((x + 2, y + 2), text, font=font, fill=shadow)
        draw.text((x, y), text, font=font, fill=fill)

    def _palette_colors(self, palette: str) -> Dict[str, tuple[int, int, int, int]]:
        mapping: Dict[str, Dict[str, tuple[int, int, int, int]]] = {
            "mint": {"primary": (131, 255, 218, 235), "secondary": (151, 193, 255, 220), "accent": (255, 255, 255, 242), "soft": (131, 255, 218, 42)},
            "sunset": {"primary": (255, 173, 113, 235), "secondary": (255, 126, 147, 220), "accent": (255, 255, 255, 242), "soft": (255, 173, 113, 42)},
            "berry": {"primary": (255, 126, 147, 235), "secondary": (174, 140, 255, 220), "accent": (255, 255, 255, 242), "soft": (255, 126, 147, 42)},
            "amber": {"primary": (255, 205, 92, 235), "secondary": (255, 173, 113, 220), "accent": (255, 255, 255, 242), "soft": (255, 205, 92, 42)},
            "editorial": {"primary": (224, 207, 176, 235), "secondary": (151, 193, 255, 220), "accent": (255, 255, 255, 242), "soft": (224, 207, 176, 42)},
            "neon": {"primary": (124, 255, 208, 235), "secondary": (216, 130, 255, 220), "accent": (255, 255, 255, 242), "soft": (124, 255, 208, 42)},
            "mono": {"primary": (255, 255, 255, 235), "secondary": (182, 188, 202, 220), "accent": (255, 255, 255, 242), "soft": (255, 255, 255, 30)},
            "cool": {"primary": (151, 193, 255, 235), "secondary": (131, 255, 218, 220), "accent": (255, 255, 255, 242), "soft": (151, 193, 255, 42)},
        }
        return mapping.get(palette, mapping["cool"])

    def _kicker_for_part(self, part: VisualPlanPart) -> str:
        mapping = {
            "conversation_flow": "Conversation",
            "question_answer": "Q&A",
            "step_sequence": "Sequence",
            "checklist_reveal": "Checklist",
            "compare_problem_solution": "Problem -> fix",
            "before_after_split": "Before / after",
            "object_spotlight": "Spotlight",
            "concept_network": "Concept map",
            "process_arrow": "Process",
            "timeline_sequence": "Timeline",
            "decision_split": "Decision",
            "chart_pop": "Metrics",
            "map_pointer": "Location",
            "idea_burst": "Idea",
        }
        return mapping.get(str(part.animation_kind or ""), "Visual")

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
