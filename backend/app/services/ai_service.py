"""OpenAI-backed insertion suggestion service with deterministic fallback."""

from __future__ import annotations

import json
import os
from typing import List

import httpx

from ..models.project import InsertionSuggestion
from ..models.transcription import WordTimestamp


class AIService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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


ai_service = AIService()
