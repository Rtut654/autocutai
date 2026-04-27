"""OpenAI-backed editing suggestion service with deterministic fallbacks."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, List, Sequence

import dotenv
import httpx

from ..models.project import (
    InsertionSuggestion,
    SpeechFilterArtifact,
    SpeechFilterCut,
    VisualAssetKind,
    VisualAssetStatus,
    VisualPlanArtifact,
    VisualPlanPart,
    ZoomPreviewBeat,
)
from ..models.transcription import WordTimestamp

CONTENT_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "but", "by", "for", "from",
    "have", "he", "her", "here", "him", "i", "if", "in", "into", "is", "it", "its",
    "me", "my", "of", "on", "or", "our", "she", "so", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "those", "to", "us", "we", "when", "with",
    "you", "your", "again", "just", "very", "will", "what",
}

RESTART_MARKERS = {"so", "again", "well", "actually", "basically", "okay", "ok", "right", "like", "now"}

dotenv.load_dotenv(Path(__file__).resolve().parents[3] / ".env")


class AIService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.speech_filter_model = os.getenv("OPENAI_SPEECH_FILTER_MODEL", self.model)
        self.visual_plan_model = os.getenv("OPENAI_VISUAL_PLAN_MODEL", self.model)

    async def suggest_insertions(self, words: List[WordTimestamp], max_items: int = 8) -> List[InsertionSuggestion]:
        if not words:
            return []

        if not self.api_key:
            return self._fallback_insertions(words, max_items)

        transcript_lines = [f"{w.start:.2f}-{w.end:.2f}: {w.word}" for w in words]
        prompt = (
            "Here is word-level transcript of audio narration. "
            "Suggest moments where insertion of picture/short meme-video supports narrator. "
            "Return JSON array of objects: {time:number, suggestion:string, media_type:string}. "
            f"Use max {max_items} items.\n" + "\n".join(transcript_lines)
        )

        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a video editing copilot."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]

        data = json.loads(content)
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return self._fallback_insertions(words, max_items)

        result: List[InsertionSuggestion] = []
        for item in items[:max_items]:
            try:
                result.append(
                    InsertionSuggestion(
                        time=float(item["time"]),
                        suggestion=str(item["suggestion"]),
                        media_type=item.get("media_type", "picture"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        return result or self._fallback_insertions(words, max_items)

    @staticmethod
    def _fallback_insertions(words: List[WordTimestamp], max_items: int) -> List[InsertionSuggestion]:
        suggestions: List[InsertionSuggestion] = []
        stride = max(1, len(words) // max_items)
        for idx in range(0, len(words), stride):
            if len(suggestions) >= max_items:
                break
            w = words[idx]
            suggestions.append(
                InsertionSuggestion(
                    time=w.start,
                    suggestion=f"Visual supporting '{w.word}'",
                    media_type="picture",
                )
            )
        return suggestions

    async def suggest_speech_filter_cuts(
        self,
        *,
        project_id: str,
        track_id: str,
        filename: str,
        words: Sequence[WordTimestamp],
        duration: float,
        transcript_segments: Sequence[dict] | None = None,
        min_gap_seconds: float = 1.0,
    ) -> SpeechFilterArtifact:
        heuristic_cuts = self._heuristic_speech_filter_cuts(words, duration, min_gap_seconds=min_gap_seconds)
        heuristic_zoom_beats = self._heuristic_zoom_beats(
            words,
            duration,
            transcript_segments=transcript_segments,
        )
        heuristic_artifact = SpeechFilterArtifact(
            project_id=project_id,
            track_id=track_id,
            filename=filename,
            status="completed",
            summary=self._build_speech_filter_summary(heuristic_cuts),
            cuts=heuristic_cuts,
            zoom_beats=heuristic_zoom_beats,
            source_word_count=len(words),
            model="heuristic",
        )

        if not words or not self.api_key:
            return heuristic_artifact

        prompt_lines = [f"{idx + 1}. {word.start:.2f}-{word.end:.2f}: {word.word}" for idx, word in enumerate(words)]
        segment_hint_lines: List[str] = []
        for idx, segment in enumerate(transcript_segments or []):
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            try:
                seg_start = float(segment.get("start", 0.0) or 0.0)
                seg_end = float(segment.get("end", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            segment_hint_lines.append(f"{idx + 1}. {seg_start:.2f}-{seg_end:.2f}: {text}")
        prompt = (
            "You are helping trim spoken video clips. "
            "Given the word-level transcript, identify conservative cut ranges that remove filler words, "
            "false starts, repeated words/phrases caused by re-starting pronunciation, and long pauses. "
            "Also split the speech into logical, complete spoken beats for punch-in preview. "
            "Each zoom beat should feel like a complete thought and usually last about 3 to 4 seconds. "
            "Prefer grouping by meaning rather than rigid sentence boundaries. "
            "Do not cut meaningful content. "
            "Return JSON object with keys summary:string, cuts:array, and zoom_beats:array. "
            "Each cut must be {start:number, end:number, reason:string, transcript:string, confidence:number}. "
            "Each zoom beat must be {start:number, end:number, text:string, enabled:boolean, scale:number}. "
            "Use enabled=true only on selected beats where a slow center punch-in helps attention. "
            "Use subtle scales around 1.10 to 1.16. "
            "Only propose cuts that are safe to remove.\n\n"
            f"Clip: {filename}\n"
            f"Duration: {duration:.2f} seconds\n"
            f"Minimum long-pause threshold: {min_gap_seconds:.2f} seconds\n\n"
            + ("Existing transcript segment hints:\n" + "\n".join(segment_hint_lines) + "\n\n" if segment_hint_lines else "")
            +
            "Word timeline:\n"
            + "\n".join(prompt_lines)
        )

        payload = {
            "model": self.speech_filter_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a meticulous video dialogue editor."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            raw_cuts = data.get("cuts") if isinstance(data, dict) else None
            ai_cuts = self._sanitize_speech_filter_cuts(raw_cuts, words, duration)
            ai_zoom_beats = self._sanitize_zoom_beats(
                data.get("zoom_beats") if isinstance(data, dict) else None,
                words,
                duration,
            )
            if not ai_cuts:
                ai_cuts = heuristic_cuts
            if not ai_zoom_beats:
                ai_zoom_beats = heuristic_zoom_beats
            summary = str(data.get("summary") or self._build_speech_filter_summary(ai_cuts)).strip()
            return SpeechFilterArtifact(
                project_id=project_id,
                track_id=track_id,
                filename=filename,
                status="completed",
                summary=summary or self._build_speech_filter_summary(ai_cuts),
                cuts=ai_cuts,
                zoom_beats=ai_zoom_beats,
                source_word_count=len(words),
                model=self.speech_filter_model,
            )
        except Exception:
            return heuristic_artifact

    def _sanitize_zoom_beats(
        self,
        raw_beats: object,
        words: Sequence[WordTimestamp],
        duration: float,
    ) -> List[ZoomPreviewBeat]:
        if not isinstance(raw_beats, list):
            return []

        beats: List[ZoomPreviewBeat] = []
        for item in raw_beats:
            if not isinstance(item, dict):
                continue
            try:
                start = max(0.0, float(item["start"]))
                end = min(float(duration), float(item["end"]))
            except (KeyError, TypeError, ValueError):
                continue
            if end - start < 1.0 or end <= start:
                continue
            text = str(item.get("text") or self._transcript_snippet(words, start, end)).strip()
            try:
                scale = max(1.0, min(1.2, float(item.get("scale", 1.12))))
            except (TypeError, ValueError):
                scale = 1.12
            beats.append(
                ZoomPreviewBeat(
                    start=round(start, 3),
                    end=round(end, 3),
                    duration=round(end - start, 3),
                    text=text,
                    enabled=bool(item.get("enabled", False)),
                    scale=round(scale, 3),
                )
            )

        beats.sort(key=lambda beat: (beat.start, beat.end))
        return beats

    def _heuristic_zoom_beats(
        self,
        words: Sequence[WordTimestamp],
        duration: float,
        *,
        transcript_segments: Sequence[dict] | None = None,
    ) -> List[ZoomPreviewBeat]:
        raw_units: List[tuple[float, float, str]] = []
        if transcript_segments:
            for segment in transcript_segments:
                if not isinstance(segment, dict):
                    continue
                start = max(0.0, float(segment.get("start", 0.0) or 0.0))
                end = min(float(duration), float(segment.get("end", 0.0) or 0.0))
                text = str(segment.get("text") or "").strip()
                if text and end - start >= 0.7:
                    raw_units.append((start, end, text))

        if not raw_units:
            raw_units = self._build_zoom_units_from_words(words, duration)

        if not raw_units:
            return []

        beats: List[ZoomPreviewBeat] = []
        cursor_start, cursor_end, cursor_text = raw_units[0]

        def flush() -> None:
            beat_duration = round(max(0.0, cursor_end - cursor_start), 3)
            if beat_duration < 1.2:
                return
            index = len(beats)
            label = re.sub(r"\s+", " ", cursor_text).strip()
            beats.append(
                ZoomPreviewBeat(
                    start=round(cursor_start, 3),
                    end=round(cursor_end, 3),
                    duration=beat_duration,
                    text=label,
                    enabled=index % 2 == 0,
                    scale=1.18 if index % 3 == 1 else 1.12,
                )
            )

        for start, end, text in raw_units[1:]:
            next_duration = end - cursor_start
            gap = start - cursor_end
            if next_duration <= 4.4 and gap <= 0.45:
                cursor_end = end
                cursor_text = f"{cursor_text} {text}".strip()
                continue
            flush()
            cursor_start, cursor_end, cursor_text = start, end, text
        flush()
        return beats

    def _build_zoom_units_from_words(
        self,
        words: Sequence[WordTimestamp],
        duration: float,
    ) -> List[tuple[float, float, str]]:
        if not words:
            return []
        units: List[tuple[float, float, str]] = []
        current_words: List[str] = [words[0].word]
        current_start = words[0].start
        current_end = words[0].end

        def flush() -> None:
            if current_words and current_end - current_start >= 0.7:
                units.append((current_start, current_end, " ".join(current_words).strip()))

        for previous, current in zip(words, words[1:]):
            pause = current.start - previous.end
            punctuation_break = previous.word.endswith((".", "!", "?", "…", ":"))
            clause_break = pause >= 0.45
            next_duration = current.end - current_start
            if (punctuation_break and next_duration >= 2.0) or (clause_break and next_duration >= 2.2):
                flush()
                current_words = [current.word]
                current_start = current.start
                current_end = current.end
                continue
            current_words.append(current.word)
            current_end = current.end
        flush()
        if not units:
            units.append((words[0].start, min(duration, words[-1].end), " ".join(word.word for word in words)))
        return units

    def _build_visual_planning_units(
        self,
        words: Sequence[WordTimestamp],
        duration: float,
        *,
        transcript_segments: Sequence[dict] | None = None,
    ) -> List[tuple[float, float, str]]:
        units = [
            (beat.start, beat.end, beat.text)
            for beat in self._heuristic_zoom_beats(words, duration, transcript_segments=transcript_segments)
        ]
        if not units:
            units = self._build_zoom_units_from_words(words, duration)
        return units[:40]

    async def suggest_visual_plan(
        self,
        *,
        project_id: str,
        track_id: str,
        filename: str,
        words: Sequence[WordTimestamp],
        transcript_segments: Sequence[dict] | None = None,
        duration: float,
    ) -> VisualPlanArtifact:
        heuristic_parts = self._heuristic_visual_plan_parts(words, duration, transcript_segments=transcript_segments)
        heuristic_artifact = VisualPlanArtifact(
            project_id=project_id,
            track_id=track_id,
            filename=filename,
            status="completed",
            summary=self._build_visual_plan_summary(heuristic_parts),
            parts=heuristic_parts,
            source_word_count=len(words),
            model="heuristic",
        )

        if not words or not self.api_key:
            return heuristic_artifact

        planning_units = self._build_visual_planning_units(words, duration, transcript_segments=transcript_segments)
        prompt_lines = [f"{idx + 1}. {start:.2f}-{end:.2f}: {text}" for idx, (start, end, text) in enumerate(planning_units)]
        segment_hint_lines = []
        for idx, segment in enumerate(transcript_segments or []):
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            try:
                seg_start = float(segment.get("start", 0.0) or 0.0)
                seg_end = float(segment.get("end", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            segment_hint_lines.append(f"{idx + 1}. {seg_start:.2f}-{seg_end:.2f}: {text}")

        prompt = self._build_visual_plan_prompt(
            filename=filename,
            duration=duration,
            prompt_lines=prompt_lines,
            segment_hint_lines=segment_hint_lines,
        )

        payload = {
            "model": self.visual_plan_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a motion designer planning script-synced explainer visuals."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            raw_parts = data.get("parts") if isinstance(data, dict) else None
            parts = self._sanitize_visual_plan_parts(raw_parts, words, duration)
            if not parts:
                return heuristic_artifact
            return VisualPlanArtifact(
                project_id=project_id,
                track_id=track_id,
                filename=filename,
                status="completed",
                summary=self._build_visual_plan_summary(parts),
                parts=parts,
                source_word_count=len(words),
                model=self.visual_plan_model,
            )
        except Exception:
            return heuristic_artifact

    def _sanitize_visual_plan_parts(
        self,
        raw_parts: object,
        words: Sequence[WordTimestamp],
        duration: float,
    ) -> List[VisualPlanPart]:
        if not isinstance(raw_parts, list):
            return []
        parts: List[VisualPlanPart] = []
        for item in raw_parts:
            if not isinstance(item, dict):
                continue
            try:
                start = max(0.0, float(item["start"]))
                end = min(float(duration), float(item["end"]))
            except (KeyError, TypeError, ValueError):
                continue
            if end - start < 1.0 or end <= start:
                continue
            visual_type_raw = str(item.get("visual_type") or VisualAssetKind.ANIMATION.value)
            try:
                visual_type = VisualAssetKind(visual_type_raw)
            except ValueError:
                visual_type = VisualAssetKind.ANIMATION
            text = str(item.get("text") or self._transcript_snippet(words, start, end)).strip()
            prompt = str(item.get("prompt") or text).strip()
            search_query = item.get("search_query")
            animation_kind = str(item.get("animation_kind") or "").strip() or None
            title = str(item.get("title") or "").strip() or None
            placement = str(item.get("placement") or "").strip() or None
            density = str(item.get("density") or "").strip() or None
            palette = str(item.get("palette") or "").strip() or None
            variant = str(item.get("variant") or "").strip() or None
            motion_profile = str(item.get("motion_profile") or "").strip() or None
            background_style = str(item.get("background_style") or "transparent").strip() or "transparent"
            keywords = item.get("keywords") if isinstance(item.get("keywords"), list) else []
            scene_objects = item.get("scene_objects") if isinstance(item.get("scene_objects"), list) else []
            parts.append(
                VisualPlanPart(
                    start=round(start, 3),
                    end=round(end, 3),
                    duration=round(end - start, 3),
                    text=text,
                    visual_type=visual_type,
                    prompt=prompt,
                    search_query=str(search_query).strip() if search_query else None,
                    animation_kind=animation_kind,
                    title=title,
                    keywords=[str(value).strip() for value in keywords if str(value).strip()][:4],
                    scene_objects=[str(value).strip() for value in scene_objects if str(value).strip()][:6],
                    placement=placement,
                    density=density,
                    palette=palette,
                    variant=variant,
                    motion_profile=motion_profile,
                    background_style=background_style,
                    asset_status=VisualAssetStatus.PLANNED,
                )
            )
        parts.sort(key=lambda part: (part.start, part.end))
        return self._diversify_adjacent_animation_kinds(parts)

    def _heuristic_visual_plan_parts(
        self,
        words: Sequence[WordTimestamp],
        duration: float,
        *,
        transcript_segments: Sequence[dict] | None = None,
    ) -> List[VisualPlanPart]:
        beats = self._heuristic_zoom_beats(words, duration, transcript_segments=transcript_segments)
        parts: List[VisualPlanPart] = []
        for beat in beats:
            text = beat.text.strip()
            search_query = self._heuristic_image_query(text)
            use_image = bool(search_query)
            parts.append(
                VisualPlanPart(
                    start=beat.start,
                    end=beat.end,
                    duration=beat.duration,
                    text=text,
                    visual_type=VisualAssetKind.WEB_IMAGE if use_image else VisualAssetKind.ANIMATION,
                    prompt=self._heuristic_visual_prompt(text, use_image=use_image),
                    search_query=search_query,
                    animation_kind=None if use_image else self._heuristic_animation_kind(text),
                    title=self._heuristic_visual_title(text),
                    keywords=self._heuristic_visual_keywords(text),
                    scene_objects=self._heuristic_scene_objects(text, use_image=use_image),
                    placement=self._heuristic_visual_placement(index=len(parts)),
                    density="light",
                    palette=self._heuristic_palette(index=len(parts)),
                    variant=self._heuristic_variant(index=len(parts)),
                    motion_profile=self._heuristic_motion_profile(index=len(parts)),
                    background_style="transparent",
                    asset_status=VisualAssetStatus.PLANNED,
                )
            )
        return self._diversify_adjacent_animation_kinds(parts)

    @staticmethod
    def _build_visual_plan_prompt(
        *,
        filename: str,
        duration: float,
        prompt_lines: Sequence[str],
        segment_hint_lines: Sequence[str],
    ) -> str:
        schema = (
            'Return JSON object {"parts":[...]} only. Each part must include: '
            '{start:number,end:number,text:string,visual_type:"animation"|"web_image",'
            'prompt:string,search_query:string|null,animation_kind:string|null,title:string|null,'
            'keywords:string[],scene_objects:string[],placement:string|null,density:"light"|"medium"|null,'
            'palette:string|null,variant:string|null,motion_profile:string|null,background_style:"transparent"|null}.'
        )
        rules = (
            "Rules: split into logical thought units around 3-4 seconds. "
            "Prefer animation unless a concrete real-world object/place is better shown by image. "
            "Animations must be transparent overlay concepts, small in-frame, no giant cards, no paragraph text. "
            "Use only a short title and 1-3 keywords. "
            "Choose animation_kind from a broad library and avoid repeating the same kind in adjacent parts unless the narration truly repeats the same concept. "
            "Prefer semantic specificity over generic abstractions. "
            "Valid animation_kind examples: conversation_flow, question_answer, step_sequence, checklist_reveal, compare_problem_solution, object_spotlight, concept_network, process_arrow, timeline_sequence, decision_split, chart_pop, map_pointer, idea_burst, before_after_split, loop_cycle, hierarchy_stack. "
            "Valid placement examples: top_left, top_right, lower_left, lower_right, upper_center, lower_center. "
            "Valid palette examples: cool, mint, sunset, mono, neon, editorial, berry, amber. "
            "Valid motion_profile examples: calm, punchy, drift, elastic, crisp. "
            "Use variant to differentiate composition within the same family, for example v1-v6. "
            "background_style must be transparent."
        )
        examples = (
            "Example 1:\n"
            '{"parts":[{"start":1.2,"end":4.4,"text":"First listen to the customer problem.","visual_type":"animation","prompt":"Two-person conversation overlay with message flow","search_query":null,"animation_kind":"conversation_flow","title":"Listen first","keywords":["Listen","Problem"],"scene_objects":["speaker_a","speaker_b","message_arc"],"placement":"upper_left","density":"light","palette":"cool","variant":"v2","motion_profile":"calm","background_style":"transparent"}]}\n'
            "Example 2:\n"
            '{"parts":[{"start":7.0,"end":10.5,"text":"Open the laptop dashboard and review the chart.","visual_type":"web_image","prompt":"Editorial laptop dashboard image","search_query":"laptop dashboard analytics chart editorial","animation_kind":null,"title":"Review chart","keywords":["Dashboard","Chart"],"scene_objects":["laptop","chart"],"placement":"lower_right","density":"light","palette":"editorial","variant":"v1","motion_profile":"crisp","background_style":"transparent"}]}\n'
            "Example 3:\n"
            '{"parts":[{"start":10.6,"end":13.9,"text":"Then compare the bad option against the better one.","visual_type":"animation","prompt":"Before-vs-after split overlay with contrasting paths","search_query":null,"animation_kind":"before_after_split","title":"Bad vs better","keywords":["Before","After"],"scene_objects":["left_option","right_option","divider"],"placement":"upper_center","density":"light","palette":"berry","variant":"v4","motion_profile":"punchy","background_style":"transparent"}]}'
        )
        return (
            "You are a motion designer planning narration-synced overlays for a talking-head video.\n"
            f"{schema}\n{rules}\n\n"
            f"Clip: {filename}\nDuration: {duration:.2f}s\n\n"
            + ("Transcript segment hints:\n" + "\n".join(segment_hint_lines) + "\n\n" if segment_hint_lines else "")
            + "Word timeline:\n"
            + "\n".join(prompt_lines)
            + "\n\n"
            + examples
        )

    @staticmethod
    def _diversify_adjacent_animation_kinds(parts: Sequence[VisualPlanPart]) -> List[VisualPlanPart]:
        alternatives = {
            "conversation_flow": ("question_answer", "concept_network"),
            "question_answer": ("conversation_flow", "concept_network"),
            "step_sequence": ("checklist_reveal", "timeline_sequence", "process_arrow"),
            "checklist_reveal": ("step_sequence", "process_arrow"),
            "compare_problem_solution": ("decision_split", "before_after_split"),
            "before_after_split": ("compare_problem_solution", "decision_split"),
            "object_spotlight": ("chart_pop", "map_pointer", "concept_network"),
            "concept_network": ("process_arrow", "idea_burst"),
            "process_arrow": ("timeline_sequence", "checklist_reveal"),
            "timeline_sequence": ("process_arrow", "step_sequence"),
            "decision_split": ("compare_problem_solution", "before_after_split"),
            "chart_pop": ("object_spotlight", "process_arrow"),
            "map_pointer": ("object_spotlight", "concept_network"),
            "idea_burst": ("concept_network", "chart_pop"),
        }
        diversified: List[VisualPlanPart] = []
        recent_kinds: List[str] = []
        for part in parts:
            if part.visual_type != VisualAssetKind.ANIMATION or not part.animation_kind:
                diversified.append(part)
                continue
            chosen_kind = part.animation_kind
            if recent_kinds[-2:].count(chosen_kind) >= 1:
                for candidate in alternatives.get(chosen_kind, ()):
                    if candidate not in recent_kinds[-2:]:
                        chosen_kind = candidate
                        break
            diversified_part = part if chosen_kind == part.animation_kind else part.model_copy(update={"animation_kind": chosen_kind})
            diversified.append(diversified_part)
            recent_kinds.append(chosen_kind)
        return diversified

    @staticmethod
    def _heuristic_visual_prompt(text: str, *, use_image: bool) -> str:
        if use_image:
            return f"Find a clean supporting editorial image for: {text}"
        return f"Create a simple kinetic text / explainer animation for: {text}"

    @classmethod
    def _heuristic_visual_title(cls, text: str) -> str:
        keywords = cls._heuristic_visual_keywords(text)
        if keywords:
            return " / ".join(keywords[:2])
        cleaned = re.sub(r"[^\w\s]", " ", text)
        return " ".join(cleaned.split()[:3]).strip().title()

    @classmethod
    def _heuristic_visual_keywords(cls, text: str) -> List[str]:
        tokens = []
        for raw in re.sub(r"[^\w\s-]", " ", text).split():
            token = raw.strip()
            if len(token) < 4:
                continue
            lowered = token.lower()
            if lowered in CONTENT_STOPWORDS:
                continue
            titled = token.capitalize()
            if titled not in tokens:
                tokens.append(titled)
            if len(tokens) >= 3:
                break
        return tokens

    @classmethod
    def _heuristic_scene_objects(cls, text: str, *, use_image: bool) -> List[str]:
        lowered = text.lower()
        if "conversation" in lowered or "listen" in lowered or "speaker" in lowered:
            return ["speaker_a", "speaker_b", "message_arc"]
        if "step" in lowered or "first" in lowered or "then" in lowered:
            return ["step_blocks", "path_arrow"]
        if "problem" in lowered and "solution" in lowered:
            return ["problem_icon", "solution_icon", "transition_path"]
        if use_image:
            return cls._heuristic_visual_keywords(text)
        return ["focus_shape", "accent_nodes"]

    @staticmethod
    def _heuristic_visual_placement(*, index: int) -> str:
        placements = ("upper_left", "upper_right", "lower_left", "lower_right")
        return placements[index % len(placements)]

    @staticmethod
    def _heuristic_animation_kind(text: str) -> str:
        lowered = text.lower()
        if "before" in lowered or "after" in lowered:
            return "before_after_split"
        if "conversation" in lowered or "listen" in lowered or "speaker" in lowered:
            return "conversation_flow"
        if "question" in lowered or "answer" in lowered:
            return "question_answer"
        if "checklist" in lowered or "list" in lowered:
            return "checklist_reveal"
        if "step" in lowered or "first" in lowered or "then" in lowered:
            return "step_sequence"
        if "timeline" in lowered:
            return "timeline_sequence"
        if "decision" in lowered or "choice" in lowered:
            return "decision_split"
        if "problem" in lowered and "solution" in lowered:
            return "compare_problem_solution"
        if any(marker in lowered for marker in ("chart", "graph", "metric")):
            return "chart_pop"
        if any(marker in lowered for marker in ("map", "city", "country", "place")):
            return "map_pointer"
        if any(marker in lowered for marker in ("phone", "laptop", "map", "chart", "camera")):
            return "object_spotlight"
        if any(marker in lowered for marker in ("process", "flow", "pipeline")):
            return "process_arrow"
        return "concept_network"

    @staticmethod
    def _heuristic_palette(*, index: int) -> str:
        palettes = ("cool", "mint", "sunset", "mono", "neon", "editorial", "berry", "amber")
        return palettes[index % len(palettes)]

    @staticmethod
    def _heuristic_variant(*, index: int) -> str:
        variants = ("v1", "v2", "v3", "v4", "v5", "v6")
        return variants[index % len(variants)]

    @staticmethod
    def _heuristic_motion_profile(*, index: int) -> str:
        profiles = ("calm", "punchy", "drift", "elastic", "crisp")
        return profiles[index % len(profiles)]

    @staticmethod
    def _heuristic_image_query(text: str) -> str | None:
        lowered = text.lower()
        concrete_markers = ("phone", "laptop", "map", "chart", "city", "mountain", "table", "book", "camera")
        if any(marker in lowered for marker in concrete_markers):
            cleaned = re.sub(r"[^\w\s]", " ", text)
            return " ".join(cleaned.split()[:8]).strip() or None
        return None

    @staticmethod
    def _build_visual_plan_summary(parts: Sequence[VisualPlanPart]) -> str:
        if not parts:
            return "No visual enhancements suggested."
        animation_count = sum(1 for part in parts if part.visual_type == VisualAssetKind.ANIMATION)
        image_count = sum(1 for part in parts if part.visual_type == VisualAssetKind.WEB_IMAGE)
        return f"{len(parts)} visual parts planned: {animation_count} animations, {image_count} web images."

    def _sanitize_speech_filter_cuts(
        self,
        raw_cuts: object,
        words: Sequence[WordTimestamp],
        duration: float,
    ) -> List[SpeechFilterCut]:
        if not isinstance(raw_cuts, list):
            return []

        result: List[SpeechFilterCut] = []
        for item in raw_cuts:
            if not isinstance(item, dict):
                continue
            try:
                start = max(0.0, float(item["start"]))
                end = min(float(duration), float(item["end"]))
            except (KeyError, TypeError, ValueError):
                continue
            if end - start < 0.05 or end <= start:
                continue
            transcript = str(item.get("transcript") or self._transcript_snippet(words, start, end)).strip()
            reason = str(item.get("reason") or "speech_cleanup").strip() or "speech_cleanup"
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.6))))
            except (TypeError, ValueError):
                confidence = 0.6
            result.append(
                SpeechFilterCut(
                    start=round(start, 3),
                    end=round(end, 3),
                    duration=round(end - start, 3),
                    reason=reason,
                    transcript=transcript,
                    confidence=round(confidence, 3),
                )
            )
        return self._merge_overlapping_speech_cuts(result)

    def _heuristic_speech_filter_cuts(
        self,
        words: Sequence[WordTimestamp],
        duration: float,
        *,
        min_gap_seconds: float,
    ) -> List[SpeechFilterCut]:
        cuts: List[SpeechFilterCut] = []
        if not words:
            return cuts

        edge_gap_threshold = max(min_gap_seconds, 0.8)
        if words[0].start > edge_gap_threshold:
            cuts.append(
                SpeechFilterCut(
                    start=0.0,
                    end=round(words[0].start, 3),
                    duration=round(words[0].start, 3),
                    reason="leading_silence",
                    transcript="",
                    confidence=0.98,
                )
            )

        single_fillers = {"um", "uh", "erm", "hmm", "mm", "ah", "uhh", "umm"}
        phrase_fillers = {
            ("you", "know"),
            ("i", "mean"),
            ("sort", "of"),
            ("kind", "of"),
        }
        normalized_words = [self._normalize_word_key(word.word) for word in words]

        for index, word in enumerate(words):
            normalized = normalized_words[index]
            if normalized in single_fillers:
                cuts.append(
                    SpeechFilterCut(
                        start=round(word.start, 3),
                        end=round(word.end, 3),
                        duration=round(word.end - word.start, 3),
                        reason="filler_word",
                        transcript=word.word,
                        confidence=0.92,
                    )
                )

        for index in range(len(words) - 1):
            pair = (normalized_words[index], normalized_words[index + 1])
            if pair in phrase_fillers:
                start = words[index].start
                end = words[index + 1].end
                cuts.append(
                    SpeechFilterCut(
                        start=round(start, 3),
                        end=round(end, 3),
                        duration=round(end - start, 3),
                        reason="filler_phrase",
                        transcript=f"{words[index].word} {words[index + 1].word}",
                        confidence=0.85,
                    )
                )

        for window in (3, 2, 1):
            max_index = len(words) - (window * 2) + 1
            for index in range(max_index):
                left = normalized_words[index : index + window]
                right = normalized_words[index + window : index + (window * 2)]
                if not left or left != right or any(not token for token in left):
                    continue
                if words[index + (window * 2) - 1].end - words[index].start > 3.5:
                    continue
                start = words[index].start
                end = words[index + window - 1].end
                cuts.append(
                    SpeechFilterCut(
                        start=round(start, 3),
                        end=round(end, 3),
                        duration=round(end - start, 3),
                        reason="repetition_restart",
                        transcript=self._transcript_snippet(words, start, end),
                        confidence=0.72 if window > 1 else 0.62,
                    )
                )

        gap_threshold = max(min_gap_seconds, 0.8)
        for index, (prev_word, next_word) in enumerate(zip(words, words[1:])):
            gap = next_word.start - prev_word.end
            if gap <= gap_threshold:
                continue
            start = prev_word.end
            end = next_word.start
            cuts.append(
                SpeechFilterCut(
                    start=round(start, 3),
                    end=round(end, 3),
                    duration=round(end - start, 3),
                    reason="long_pause",
                    transcript="",
                    confidence=0.95,
                )
            )
            repeated_cut = self._detect_rephrased_restart_cut(words, index)
            if repeated_cut is not None:
                cuts.append(repeated_cut)

        trailing_gap = float(duration) - float(words[-1].end)
        if trailing_gap > edge_gap_threshold:
            cuts.append(
                SpeechFilterCut(
                    start=round(words[-1].end, 3),
                    end=round(float(duration), 3),
                    duration=round(trailing_gap, 3),
                    reason="trailing_silence",
                    transcript="",
                    confidence=0.98,
                )
            )

        bounded_cuts = [
            cut.model_copy(
                update={
                    "start": round(max(0.0, cut.start), 3),
                    "end": round(min(duration, cut.end), 3),
                }
            )
            for cut in cuts
            if cut.end > cut.start
        ]
        for cut in bounded_cuts:
            cut.duration = round(cut.end - cut.start, 3)
            if not cut.transcript:
                cut.transcript = self._transcript_snippet(words, cut.start, cut.end)
        return self._merge_overlapping_speech_cuts(bounded_cuts)

    @staticmethod
    def _normalize_word_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower()).strip()

    @classmethod
    def _normalize_content_key(cls, value: str) -> str:
        token = cls._normalize_word_key(value)
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif token.endswith("ed") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("es") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 3:
            token = token[:-1]
        elif token.endswith("ly") and len(token) > 4:
            token = token[:-2]
        return token

    @classmethod
    def _content_tokens(cls, words: Sequence[WordTimestamp]) -> List[str]:
        result: List[str] = []
        for word in words:
            token = cls._normalize_content_key(word.word)
            if not token or token in CONTENT_STOPWORDS:
                continue
            result.append(token)
        return result

    @staticmethod
    def _ends_sentence(value: str) -> bool:
        return value.rstrip().endswith((".", "?", "!"))

    @staticmethod
    def _starts_restart_marker(value: str) -> bool:
        token = re.sub(r"[^a-z0-9]+", "", value.lower())
        return token in RESTART_MARKERS

    @classmethod
    def _sentence_start_index(cls, words: Sequence[WordTimestamp], index: int) -> int:
        cursor = max(0, index)
        while cursor > 0:
            previous = words[cursor - 1]
            if cls._ends_sentence(previous.word):
                break
            if words[cursor].start - previous.end > 0.8:
                break
            cursor -= 1
        return cursor

    @classmethod
    def _sentence_end_index(cls, words: Sequence[WordTimestamp], index: int) -> int:
        cursor = min(len(words) - 1, index)
        while cursor < len(words) - 1:
            current = words[cursor]
            if cls._ends_sentence(current.word):
                break
            nxt = words[cursor + 1]
            if nxt.start - current.end > 0.8:
                break
            cursor += 1
        return cursor

    @classmethod
    def _detect_rephrased_restart_cut(
        cls,
        words: Sequence[WordTimestamp],
        gap_index: int,
    ) -> SpeechFilterCut | None:
        if gap_index < 0 or gap_index + 1 >= len(words):
            return None

        next_word = words[gap_index + 1]
        if not cls._starts_restart_marker(next_word.word):
            return None

        left_start = cls._sentence_start_index(words, gap_index)
        left_words = words[left_start : gap_index + 1]
        right_start = gap_index + 1
        right_end = cls._sentence_end_index(words, right_start)
        right_words = words[right_start : right_end + 1]
        if len(right_words) < 4:
            return None

        left_tokens = cls._content_tokens(left_words)
        right_tokens = cls._content_tokens(right_words)
        if len(left_tokens) < 2 or len(right_tokens) < 2:
            return None

        overlap = set(left_tokens) & set(right_tokens)
        if len(overlap) < 2:
            return None

        start = right_words[0].start
        end = right_words[-1].end
        transcript = " ".join(word.word for word in right_words).strip()
        return SpeechFilterCut(
            start=round(start, 3),
            end=round(end, 3),
            duration=round(end - start, 3),
            reason="rephrased_restart",
            transcript=transcript,
            confidence=0.74,
        )

    @classmethod
    def _transcript_snippet(cls, words: Sequence[WordTimestamp], start: float, end: float) -> str:
        snippet = [word.word for word in words if word.end >= start and word.start <= end]
        return " ".join(snippet).strip()

    @classmethod
    def _merge_overlapping_speech_cuts(cls, cuts: Iterable[SpeechFilterCut]) -> List[SpeechFilterCut]:
        ordered = sorted(cuts, key=lambda cut: (cut.start, cut.end))
        if not ordered:
            return []

        merged: List[SpeechFilterCut] = [ordered[0].model_copy()]
        for cut in ordered[1:]:
            current = merged[-1]
            if cut.start < current.end - 0.01:
                current.end = round(max(current.end, cut.end), 3)
                current.duration = round(current.end - current.start, 3)
                if cut.confidence > current.confidence:
                    current.confidence = cut.confidence
                reasons = [part for part in (current.reason.split("+") + cut.reason.split("+")) if part]
                current.reason = "+".join(dict.fromkeys(reasons))
                if cut.transcript:
                    transcript = " ".join(part for part in [current.transcript, cut.transcript] if part).strip()
                    current.transcript = transcript
                continue
            merged.append(cut.model_copy())
        return [cut for cut in merged if cut.duration >= 0.05]

    @staticmethod
    def _build_speech_filter_summary(cuts: Sequence[SpeechFilterCut]) -> str:
        if not cuts:
            return "No obvious filler words, repeated starts, or long pauses were detected."
        removed_seconds = sum(cut.duration for cut in cuts)
        return f"{len(cuts)} suggested cut{'s' if len(cuts) != 1 else ''}, about {removed_seconds:.1f}s total."


ai_service = AIService()
