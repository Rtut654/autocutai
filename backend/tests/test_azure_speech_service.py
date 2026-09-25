"""Tests for the Azure Speech transcription service."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.app.services.azure_speech_service import (
    AzureSpeechService,
    TranscriptionError,
    merge_display_punctuation,
    parse_recognition_payloads,
)
from factories import azure_payload


# --------------------------------------------------------------------------
# Payload parsing
# --------------------------------------------------------------------------


def test_parses_word_offsets_from_ticks_into_seconds():
    payload = azure_payload([("hello", 1.5, 2.0), ("world", 2.1, 2.6)])

    result = parse_recognition_payloads([payload], "en-US")

    assert [word["word"] for word in result["words"]] == ["hello", "world"]
    assert result["words"][0]["start"] == 1.5
    assert result["words"][0]["end"] == 2.0
    assert result["words"][1]["start"] == 2.1
    assert result["words"][1]["end"] == 2.6
    assert result["language"] == "en-US"


def test_segments_carry_their_own_words_and_bounds():
    first = azure_payload([("one", 0.0, 0.4), ("two", 0.5, 0.9)])
    second = azure_payload([("three", 5.0, 5.4)], offset_seconds=5.0)

    result = parse_recognition_payloads([first, second], "en-US")

    assert len(result["segments"]) == 2
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][0]["end"] == 0.9
    assert len(result["segments"][0]["words"]) == 2
    assert result["segments"][1]["start"] == 5.0
    assert result["segments"][1]["end"] == 5.4


def test_combined_text_joins_display_forms():
    first = azure_payload([("hello", 0.0, 0.4)], display="Hello,")
    second = azure_payload([("world", 0.5, 0.9)], display="world!")

    result = parse_recognition_payloads([first, second], "en-US")

    assert result["text"] == "Hello, world!"


def test_word_confidence_is_preserved_and_optional():
    payload = azure_payload([("hi", 0.0, 0.3)])
    payload["NBest"][0]["Words"][0].pop("Confidence")

    result = parse_recognition_payloads([payload], "en-US")

    assert result["words"][0]["confidence"] is None


def test_empty_recognition_produces_empty_payload():
    result = parse_recognition_payloads([], "en-US")

    assert result == {"text": "", "words": [], "segments": [], "language": "en-US"}


def test_payload_without_nbest_is_skipped():
    result = parse_recognition_payloads([{"RecognitionStatus": "NoMatch"}], "en-US")

    assert result["words"] == []
    assert result["segments"] == []


# --------------------------------------------------------------------------
# Punctuation merging
# --------------------------------------------------------------------------


def test_display_punctuation_is_reattached_to_timed_words():
    merged = merge_display_punctuation(["so", "this", "is", "lisbon"], "So, this is Lisbon.")

    assert merged == ["So,", "this", "is", "Lisbon."]


def test_lexical_words_win_when_token_counts_diverge():
    # Inverse text normalisation collapses three spoken words into one token.
    merged = merge_display_punctuation(["twenty", "twenty", "six"], "2026")

    assert merged == ["twenty", "twenty", "six"]


def test_lexical_words_win_when_tokens_do_not_correspond():
    merged = merge_display_punctuation(["four", "dogs"], "4 dogs")

    assert merged == ["four", "dogs"]


def test_parse_uses_display_form_for_word_text():
    payload = azure_payload(
        [("so", 0.0, 0.3), ("this", 0.4, 0.7), ("is", 0.8, 1.0), ("lisbon", 1.1, 1.6)],
        display="So, this is Lisbon.",
    )

    result = parse_recognition_payloads([payload], "en-US")

    assert [word["word"] for word in result["words"]] == ["So,", "this", "is", "Lisbon."]
    # Timings still come from the lexical words.
    assert result["words"][3]["start"] == 1.1


# --------------------------------------------------------------------------
# Recognition driving
# --------------------------------------------------------------------------


class FakeRecognizer:
    """Stands in for speechsdk.SpeechRecognizer.

    Fires the connected callbacks synchronously when recognition starts,
    which is enough to drive the service's event loop.
    """

    def __init__(self, payloads, *, error=None):
        self._payloads = payloads
        self._error = error
        self._handlers = {}
        self.started = False
        self.stopped = False
        self.recognized = self._Signal(self, "recognized")
        self.canceled = self._Signal(self, "canceled")
        self.session_stopped = self._Signal(self, "session_stopped")

    class _Signal:
        def __init__(self, owner, name):
            self._owner = owner
            self._name = name

        def connect(self, handler):
            self._owner._handlers[self._name] = handler

    def start_continuous_recognition(self):
        self.started = True
        for payload in self._payloads:
            handler = self._handlers.get("recognized")
            if handler:
                handler(type("Evt", (), {"result": type("Res", (), {"json": json.dumps(payload)})})())
        if self._error:
            handler = self._handlers.get("canceled")
            if handler:
                handler(type("Evt", (), {"error_details": self._error, "reason": "Error"})())
            return
        handler = self._handlers.get("session_stopped")
        if handler:
            handler(object())

    def stop_continuous_recognition(self):
        self.stopped = True


def _service_with(payloads, *, error=None):
    def factory(*, audio_path, language):
        return FakeRecognizer(payloads, error=error)

    return AzureSpeechService(key="", region="", recognizer_factory=factory)


def test_transcribe_file_drives_continuous_recognition(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    service = _service_with([azure_payload([("hola", 0.0, 0.5)], display="Hola")])

    result = asyncio.run(service.transcribe_file(audio, "es-ES"))

    assert result["text"] == "Hola"
    assert result["language"] == "es-ES"
    assert result["words"][0]["word"] == "Hola"


def test_transcribe_file_uses_default_language_when_unspecified(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    service = _service_with([azure_payload([("hi", 0.0, 0.2)])])
    service.default_language = "en-GB"

    result = asyncio.run(service.transcribe_file(audio))

    assert result["language"] == "en-GB"


def test_missing_audio_file_raises(tmp_path):
    service = _service_with([])

    with pytest.raises(TranscriptionError, match="not found"):
        asyncio.run(service.transcribe_file(tmp_path / "nope.wav"))


def test_cancellation_surfaces_as_transcription_error(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    service = _service_with([], error="Quota exceeded")

    with pytest.raises(TranscriptionError, match="Quota exceeded"):
        asyncio.run(service.transcribe_file(audio))


def test_unconfigured_service_refuses_rather_than_calling_azure(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    service = AzureSpeechService(key="", region="", endpoint="")

    with pytest.raises(TranscriptionError, match="not configured"):
        asyncio.run(service.transcribe_file(audio))


def test_is_configured_requires_key_and_a_destination():
    assert not AzureSpeechService(key="", region="westeurope").is_configured
    assert not AzureSpeechService(key="abc", region="", endpoint="").is_configured
    assert AzureSpeechService(key="abc", region="westeurope").is_configured
    assert AzureSpeechService(key="abc", region="", endpoint="https://example").is_configured


def test_unparseable_payload_is_discarded_not_fatal(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")

    class BadJsonRecognizer(FakeRecognizer):
        def start_continuous_recognition(self):
            handler = self._handlers.get("recognized")
            handler(type("Evt", (), {"result": type("Res", (), {"json": "not json"})})())
            self._handlers["session_stopped"](object())

    service = AzureSpeechService(
        key="", region="", recognizer_factory=lambda **_: BadJsonRecognizer([])
    )

    result = asyncio.run(service.transcribe_file(audio))

    assert result["words"] == []


def test_recognizer_is_always_stopped(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    recognizer = FakeRecognizer([azure_payload([("x", 0.0, 0.1)])])
    service = AzureSpeechService(key="", region="", recognizer_factory=lambda **_: recognizer)

    asyncio.run(service.transcribe_file(audio))

    assert recognizer.started is True
    assert recognizer.stopped is True
