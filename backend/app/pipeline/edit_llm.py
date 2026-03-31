"""Edit assembly LLM - generates edit plan JSON via GPT-4o-mini."""

import json
import openai
from ..models.schemas import EditPlan


EDIT_SYSTEM_PROMPT = """You are a professional travel video editor.
Given a transcript with timestamps and a visual scene map, output a JSON edit plan.

Rules:
- Remove speech pauses under 2 seconds
- Fill b-roll gaps (2-6s) with relevant silent footage from the scene map
- Add music to long silent gaps (>6s) matching the mood
- Keep strong spoken moments intact
- Cut blurry, duplicate, or low-energy scenes
- Total output should be roughly 40-50% of input duration
- Prefer narrative continuity - spoken content should connect logically

Output ONLY valid JSON matching the EditPlan schema. No explanation.

EditPlan schema:
{
  "output_duration_estimate": "MM:SS",
  "clips": [
    {
      "clip_id": "string",
      "source_file": "string",
      "in_point": "HH:MM:SS.ms",
      "out_point": "HH:MM:SS.ms",
      "reason": "string",
      "transition_in": {"type": "hard_cut|dissolve|fade_black", "duration_ms": 500} | null,
      "transition_out": {"type": "hard_cut|dissolve|fade_black", "duration_ms": 500} | null
    }
  ],
  "music_cues": [
    {
      "start": "HH:MM:SS.ms",
      "end": "HH:MM:SS.ms",
      "mood": "string",
      "bpm_target": 120,
      "suggested_track": "string",
      "fade_in_ms": 800,
      "fade_out_ms": 1200
    }
  ],
  "cuts_removed": [
    {
      "clip_id": "string",
      "source_file": "string",
      "reason": "string"
    }
  ]
}"""


async def generate_edit_plan(
    transcripts: list[dict],
    scene_map: list[dict],
    gap_map: list[dict],
    music_suggestions: list[dict],
) -> EditPlan:
    """Call GPT-4o-mini to generate the edit plan JSON."""

    payload = {
        "transcripts": transcripts,
        "scenes": scene_map,
        "gaps": gap_map,
        "music_suggestions": music_suggestions,
    }

    client = openai.AsyncOpenAI()

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": EDIT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2, default=str)}
        ],
        max_tokens=2000,
        response_format={"type": "json_object"}
    )

    raw = json.loads(response.choices[0].message.content)
    return EditPlan(**raw)
