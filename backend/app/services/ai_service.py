"""OpenAI-backed editing suggestion service with deterministic fallbacks."""

from __future__ import annotations

import json
import os
import re
from typing import Iterable, List, Sequence

import httpx

from ..models.project import InsertionSuggestion, SpeechFilterArtifact, SpeechFilterCut, ZoomPreviewBeat
from ..models.transcription import WordTimestamp

CONTENT_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "but", "by", "for", "from",
    "have", "he", "her", "here", "him", "i", "if", "in", "into", "is", "it", "its",
    "me", "my", "of", "on", "or", "our", "she", "so", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "those", "to", "us", "we", "when", "with",
    "you", "your", "again", "just", "very", "will", "what",
}

RESTART_MARKERS = {"so", "again", "well", "actually", "basically", "okay", "ok", "right", "like", "now"}


class AIService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.speech_filter_model = os.getenv("OPENAI_SPEECH_FILTER_MODEL", self.model)

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
