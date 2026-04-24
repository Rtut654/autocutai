"""Edit assembly LLM - generates edit plan JSON via GPT-4o-mini."""

import json
import os
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


def _format_timestamp(seconds: float) -> str:
    total_ms = max(int(round(seconds * 1000)), 0)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _build_fallback_edit_plan(
    transcripts: list[dict],
    gap_map: list[dict],
    music_suggestions: list[dict],
) -> EditPlan:
    clips = []
    cuts_removed = []
    total_duration = 0.0

    for transcript in transcripts:
        clip_id = transcript["clip_id"]
        source_file = transcript.get("source_file") or clip_id
        segments = transcript.get("segments") or []
        words = transcript.get("words") or []

        if segments:
            for idx, segment in enumerate(segments):
                start = float(segment.get("start", 0.0))
                end = float(segment.get("end", start))
                if end <= start:
                    end = start + 0.1
                clips.append(
                    {
                        "clip_id": f"{clip_id}-{idx}",
                        "source_file": source_file,
                        "in_point": _format_timestamp(start),
                        "out_point": _format_timestamp(end),
                        "reason": "Deterministic fallback keeps spoken segments.",
                        "transition_in": None,
                        "transition_out": None,
                    }
                )
                total_duration += end - start
            continue

        if words:
            start = float(words[0].get("start", 0.0))
            end = float(words[-1].get("end", start + 1.0))
            if end <= start:
                end = start + 0.1
            clips.append(
                {
                    "clip_id": clip_id,
                    "source_file": source_file,
                    "in_point": _format_timestamp(start),
                    "out_point": _format_timestamp(end),
                    "reason": "Deterministic fallback keeps the detected spoken range.",
                    "transition_in": None,
                    "transition_out": None,
                }
            )
            total_duration += end - start
            continue

        cuts_removed.append(
            {
                "clip_id": clip_id,
                "source_file": source_file,
                "reason": "No transcript was available for this clip.",
            }
        )

    if not clips and transcripts:
        first = transcripts[0]
        clips.append(
            {
                "clip_id": first["clip_id"],
                "source_file": first.get("source_file") or first["clip_id"],
                "in_point": _format_timestamp(0.0),
                "out_point": _format_timestamp(1.0),
                "reason": "Fallback emits a short placeholder cut when no speech ranges were found.",
                "transition_in": None,
                "transition_out": None,
            }
        )
        total_duration = 1.0

    music_cues = []
    timeline_cursor = 0.0
    clip_ranges = {}
    for clip in clips:
        start = timeline_cursor
        in_sec = (
            int(clip["in_point"][0:2]) * 3600
            + int(clip["in_point"][3:5]) * 60
            + float(clip["in_point"][6:])
        )
        out_sec = (
            int(clip["out_point"][0:2]) * 3600
            + int(clip["out_point"][3:5]) * 60
            + float(clip["out_point"][6:])
        )
        timeline_cursor += max(out_sec - in_sec, 0.0)
        clip_ranges.setdefault(clip["source_file"], []).append((start, timeline_cursor))

    for suggestion in music_suggestions:
        source_file = next(
            (t.get("source_file") for t in transcripts if t["clip_id"] == suggestion.get("clip_id")),
            None,
        )
        if not source_file or source_file not in clip_ranges:
            continue
        timeline_start = clip_ranges[source_file][0][0]
        duration = float(suggestion.get("duration", 0.0))
        music_cues.append(
            {
                "start": _format_timestamp(timeline_start),
                "end": _format_timestamp(timeline_start + duration),
                "mood": suggestion.get("mood", "peaceful"),
                "bpm_target": int(suggestion.get("bpm_target", 70)),
                "suggested_track": suggestion.get("suggested_track", "ambient_calm_01"),
                "fade_in_ms": int(suggestion.get("fade_in_ms", 800)),
                "fade_out_ms": int(suggestion.get("fade_out_ms", 1200)),
            }
        )

    return EditPlan(
        output_duration_estimate=_format_timestamp(total_duration)[3:],
        clips=clips,
        music_cues=music_cues,
        cuts_removed=cuts_removed,
    )


async def generate_edit_plan(
    transcripts: list[dict],
    scene_map: list[dict],
    gap_map: list[dict],
    music_suggestions: list[dict],
) -> EditPlan:
    """Call GPT-4o-mini to generate the edit plan JSON."""

    if not os.getenv("OPENAI_API_KEY"):
        return _build_fallback_edit_plan(transcripts, gap_map, music_suggestions)

    payload = {
        "transcripts": transcripts,
        "scenes": scene_map,
        "gaps": gap_map,
        "music_suggestions": music_suggestions,
    }

    client = openai.AsyncOpenAI()
    try:
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
    except Exception:
        return _build_fallback_edit_plan(transcripts, gap_map, music_suggestions)
