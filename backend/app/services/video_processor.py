"""Service for ffmpeg-based video processing and rendering."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont

from ..models.project import EditMode, Project, VideoTrack, VisualPlanPart, ZoomPreviewBeat

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

        concat_lines: List[str] = []
        resolved_source = str(Path(source_path).resolve())
        for start, end in normalized:
            concat_lines.append(f"file '{resolved_source}'")
            if start > 0:
                concat_lines.append(f"inpoint {start:.3f}")
            concat_lines.append(f"outpoint {end:.3f}")

        concat_file = target.parent / f"concat_track_{uuid.uuid4().hex}.txt"
        concat_file.write_text("\n".join(concat_lines), encoding="utf-8")
        cmd = [
            self.ffmpeg_path,
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
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
            concat_file.unlink(missing_ok=True)
        return str(target)

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
        placement = str(part.placement or "").lower()
        enter = 0.22
        x_from = 0
        y_from = 14
        if "left" in placement:
            x_from = -22
        elif "right" in placement:
            x_from = 22
        if "upper" in placement or "top" in placement:
            y_from = -18
        elif "lower" in placement:
            y_from = 18
        x_expr = f"if(lt(t,{part.start + enter:.3f}),{x_from:.1f}*(1-((t-{part.start:.3f})/{enter:.3f})),0)"
        y_expr = f"if(lt(t,{part.start + enter:.3f}),{y_from:.1f}*(1-((t-{part.start:.3f})/{enter:.3f})),0)"
        return x_expr, y_expr

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
        box_width = int(width * (0.42 if vertical else 0.3))
        box_width = max(150, min(box_width, 300))
        box_height = int(box_width * 0.72)
        if part.visual_type.value == "web_image":
            box_height = int(box_width * 1.15)
        x, y = self._overlay_anchor(width, height, box_width, box_height, str(part.placement or ""), vertical)

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
        caption_font = self._load_font(18, bold=True)
        caption = str(part.title or part.text or "Visual").strip()[:26]
        pill_box = (x, y + asset.size[1] + 10, x + min(asset.size[0], width), y + asset.size[1] + 46)
        draw.rounded_rectangle(pill_box, radius=16, fill=(15, 23, 42, 170))
        draw.text((pill_box[0] + 12, pill_box[1] + 9), caption, font=caption_font, fill=(255, 255, 255, 238))

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
        title_font = self._load_font(22 if width < 220 else 28, bold=True)
        label_font = self._load_font(14, bold=True)
        chip_font = self._load_font(14, bold=True)
        title = str(part.title or part.text or "Visual").strip()
        words = [str(word).strip() for word in (part.keywords or []) if str(word).strip()][:2]
        label = self._kicker_for_part(part)
        motif = str(part.animation_kind or "idea_burst")

        self._draw_motif(draw, motif, x, y, width, int(height * 0.55), palette, part_index)
        pill_width = min(width - 8, max(88, draw.textbbox((0, 0), label.upper(), font=label_font)[2] + 24))
        draw.rounded_rectangle((x, y, x + pill_width, y + 34), radius=17, fill=palette["primary"])
        draw.text((x + 14, y + 8), label.upper(), font=label_font, fill=(255, 255, 255, 244))
        title_y = y + int(height * 0.58)
        self._draw_shadow_text(draw, (x, title_y), title[:34], font=title_font, fill=(255, 255, 255, 244))

        chip_y = title_y + 44
        chip_x = x
        for word in words:
            bbox = draw.textbbox((0, 0), word, font=chip_font)
            chip_width = bbox[2] - bbox[0] + 20
            draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_width, chip_y + 28), radius=14, fill=(30, 41, 59, 160), outline=(255, 255, 255, 35))
            draw.text((chip_x + 10, chip_y + 6), word, font=chip_font, fill=(255, 255, 255, 232))
            chip_x += chip_width + 8

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

    def _overlay_anchor(self, width: int, height: int, box_width: int, box_height: int, placement: str, vertical: bool) -> tuple[int, int]:
        horizontal_margin = 18
        vertical_margin = 18
        if "right" in placement:
            x = width - box_width - horizontal_margin
        elif "center" in placement:
            x = (width - box_width) // 2
        else:
            x = horizontal_margin
        if "lower" in placement:
            y = height - box_height - vertical_margin
        elif "upper" in placement or "top" in placement:
            y = vertical_margin
        else:
            y = int(height * (0.56 if vertical else 0.62)) - box_height
        return max(0, x), max(0, y)

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                try:
                    return ImageFont.truetype(candidate, size=size)
                except Exception:
                    continue
        return ImageFont.load_default()

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
