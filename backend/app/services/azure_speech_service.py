"""Transcription backed by the Azure Speech SDK.

Azure returns word-level offsets natively over arbitrarily long audio, so this
service reads a whole clip in one continuous-recognition pass. The payload it
returns matches the shape the rest of the pipeline already expects:

    {"text": str, "words": [...], "segments": [...], "language": str}

Word offsets from Azure are in ticks of 100 nanoseconds; everything downstream
works in seconds, so conversion happens here and nowhere else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

TICKS_PER_SECOND = 10_000_000

# Azure caps a single continuous recognition session. Clips are capped well
# under this by project_limits, so it exists to fail loudly rather than hang.
MAX_CLIP_SECONDS = 20 * 60


class TranscriptionError(RuntimeError):
    """Raised when transcription cannot be completed."""


def _seconds(ticks: Any) -> float:
    try:
        return round(max(0.0, float(ticks)) / TICKS_PER_SECOND, 3)
    except (TypeError, ValueError):
        return 0.0


def _strip_punctuation(value: str) -> str:
    return re.sub(r"[^\w']+", "", value, flags=re.UNICODE).lower()


def merge_display_punctuation(lexical_words: Sequence[str], display_text: str) -> List[str]:
    """Reattach punctuation and casing from Azure's display text.

    Azure's per-word timings come from the lexical form, which has no
    punctuation or capitalisation, while ``DisplayText`` has both but no
    timings. When the two tokenise to the same sequence we can zip them and
    keep the readable form. When they diverge - which happens with inverse
    text normalisation, for instance "twenty twenty six" becoming "2026" -
    we keep the lexical words so timings and text never drift apart.
    """
    display_tokens = [token for token in display_text.split() if token]
    if len(display_tokens) != len(lexical_words):
        return list(lexical_words)

    for lexical, display in zip(lexical_words, display_tokens):
        if _strip_punctuation(lexical) != _strip_punctuation(display):
            return list(lexical_words)
    return display_tokens


def parse_recognition_payloads(payloads: Sequence[Dict[str, Any]], language: str) -> Dict[str, Any]:
    """Turn raw Azure recognition results into our transcription payload.

    Kept free of SDK types so it can be unit-tested against recorded fixtures.
    """
    words: List[Dict[str, Any]] = []
    segments: List[Dict[str, Any]] = []

    for index, payload in enumerate(payloads):
        best = (payload.get("NBest") or [{}])[0]
        raw_words = best.get("Words") or []
        display_text = str(best.get("Display") or payload.get("DisplayText") or "").strip()

        lexical_forms = [str(word.get("Word") or "") for word in raw_words]
        readable_forms = merge_display_punctuation(lexical_forms, display_text)

        segment_words: List[Dict[str, Any]] = []
        for raw_word, readable in zip(raw_words, readable_forms):
            if not readable:
                continue
            start = _seconds(raw_word.get("Offset"))
            end = round(start + _seconds(raw_word.get("Duration")), 3)
            confidence = raw_word.get("Confidence")
            segment_words.append(
                {
                    "word": readable,
                    "start": start,
                    "end": max(start, end),
                    "confidence": float(confidence) if isinstance(confidence, (int, float)) else None,
                }
            )

        if not segment_words and not display_text:
            continue

        segment_start = segment_words[0]["start"] if segment_words else _seconds(payload.get("Offset"))
        segment_end = (
            segment_words[-1]["end"]
            if segment_words
            else round(segment_start + _seconds(payload.get("Duration")), 3)
        )

        words.extend(segment_words)
        segments.append(
            {
                "id": index,
                "start": segment_start,
                "end": max(segment_start, segment_end),
                "text": display_text or " ".join(word["word"] for word in segment_words),
                "words": segment_words,
            }
        )

    text = " ".join(segment["text"] for segment in segments if segment["text"]).strip()
    return {"text": text, "words": words, "segments": segments, "language": language}


class AzureSpeechService:
    """Thin wrapper over the Azure Speech SDK.

    The SDK is imported lazily so the rest of the app - and the test suite -
    runs without the native dependency installed. Tests inject a fake through
    ``recognizer_factory``.
    """

    def __init__(
        self,
        *,
        key: Optional[str] = None,
        region: Optional[str] = None,
        endpoint: Optional[str] = None,
        default_language: Optional[str] = None,
        candidate_locales: Optional[Sequence[str]] = None,
        recognizer_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.key = key if key is not None else os.getenv("AZURE_SPEECH_KEY", "")
        self.region = region if region is not None else os.getenv("AZURE_SPEECH_REGION", "")
        self.endpoint = endpoint if endpoint is not None else os.getenv("AZURE_SPEECH_ENDPOINT", "")
        self.default_language = default_language or os.getenv("AZURE_SPEECH_LANGUAGE", "en-US")
        if candidate_locales is not None:
            self.candidate_locales = list(candidate_locales)
        else:
            raw = os.getenv("AZURE_SPEECH_CANDIDATE_LOCALES", "")
            self.candidate_locales = [item.strip() for item in raw.split(",") if item.strip()]
        self._recognizer_factory = recognizer_factory

    @property
    def is_configured(self) -> bool:
        return bool(self.key and (self.region or self.endpoint))

    async def transcribe_file(self, audio_path: str | Path, language: Optional[str] = None) -> Dict[str, Any]:
        """Transcribe a 16 kHz mono PCM WAV file.

        Callers are expected to have run the media through
        ``VideoProcessor.extract_audio_for_transcription`` first; Azure's file
        input only accepts uncompressed WAV.
        """
        path = Path(audio_path)
        if not path.exists():
            raise TranscriptionError(f"Audio file not found: {path}")
        if not self.is_configured and self._recognizer_factory is None:
            raise TranscriptionError(
                "Azure Speech is not configured. Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION."
            )

        resolved_language = language or self.default_language
        payloads = await asyncio.to_thread(self._recognize_blocking, str(path), resolved_language)
        return parse_recognition_payloads(payloads, resolved_language)

    # -- SDK plumbing ------------------------------------------------------

    def _recognize_blocking(self, audio_path: str, language: str) -> List[Dict[str, Any]]:
        recognizer, done_signal = self._build_recognizer(audio_path, language)

        payloads: List[Dict[str, Any]] = []
        failure: List[str] = []

        def on_recognized(event: Any) -> None:
            raw = getattr(getattr(event, "result", None), "json", None)
            if not raw:
                return
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("Discarding unparseable Azure recognition payload")
                return
            if payload.get("RecognitionStatus") in {None, "Success"}:
                payloads.append(payload)

        def on_canceled(event: Any) -> None:
            reason = getattr(event, "error_details", None) or getattr(event, "reason", "")
            if reason:
                failure.append(str(reason))
            done_signal.set()

        def on_stopped(_event: Any) -> None:
            done_signal.set()

        recognizer.recognized.connect(on_recognized)
        recognizer.canceled.connect(on_canceled)
        recognizer.session_stopped.connect(on_stopped)

        recognizer.start_continuous_recognition()
        try:
            if not done_signal.wait(timeout=MAX_CLIP_SECONDS):
                raise TranscriptionError("Azure Speech recognition timed out")
        finally:
            recognizer.stop_continuous_recognition()

        if failure:
            raise TranscriptionError(f"Azure Speech recognition failed: {failure[0]}")
        return payloads

    def _build_recognizer(self, audio_path: str, language: str):
        import threading

        done_signal = threading.Event()

        if self._recognizer_factory is not None:
            return self._recognizer_factory(audio_path=audio_path, language=language), done_signal

        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError as exc:  # pragma: no cover - depends on native package
            raise TranscriptionError(
                "azure-cognitiveservices-speech is not installed. "
                "Install it or inject a recognizer_factory."
            ) from exc

        if self.endpoint:
            speech_config = speechsdk.SpeechConfig(subscription=self.key, endpoint=self.endpoint)
        else:
            speech_config = speechsdk.SpeechConfig(subscription=self.key, region=self.region)

        speech_config.output_format = speechsdk.OutputFormat.Detailed
        speech_config.request_word_level_timestamps()
        audio_config = speechsdk.audio.AudioConfig(filename=audio_path)

        if self.candidate_locales:
            auto_detect = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
                languages=self.candidate_locales
            )
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                auto_detect_source_language_config=auto_detect,
                audio_config=audio_config,
            )
        else:
            speech_config.speech_recognition_language = language
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                audio_config=audio_config,
            )
        return recognizer, done_signal


azure_speech_service = AzureSpeechService()


async def transcribe_file(audio_path: str | Path, language: Optional[str] = None) -> Dict[str, Any]:
    """Module-level entry point used across the pipeline."""
    return await azure_speech_service.transcribe_file(audio_path, language)
