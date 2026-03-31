"""Step cache for pipeline - per-clip JSON and per-stage JSON files."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class StepCache:
    """
    Per-job cache for pipeline steps.

    Per-clip file:  cache/{clip_id}.json
    Per-stage file: cache/_stage_{stage}.json

    Each step is stored as a key inside those files, so a single clip
    JSON holds all steps for that clip (whisper, scene_detect, clip_classify,
    gpt4o_describe, transition_to_next).
    """

    def __init__(self, job_id: str, output_dir: str):
        self.cache_dir = Path(output_dir) / job_id / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- Per-clip helpers --

    def _clip_path(self, clip_id: str) -> Path:
        return self.cache_dir / f"{clip_id}.json"

    def _load_clip(self, clip_id: str) -> dict:
        p = self._clip_path(clip_id)
        if p.exists():
            return json.loads(p.read_text())
        return {}

    def _save_clip(self, clip_id: str, data: dict):
        p = self._clip_path(clip_id)
        p.write_text(json.dumps(data, indent=2, default=str))

    def has_step(self, clip_id: str, step: str) -> bool:
        return step in self._load_clip(clip_id).get("steps_completed", [])

    def get_step(self, clip_id: str, step: str) -> Optional[dict]:
        data = self._load_clip(clip_id)
        return data.get(step)

    def save_step(self, clip_id: str, step: str, result: Any, meta: dict = None):
        data = self._load_clip(clip_id)
        data.setdefault("steps_completed", [])

        data[step] = {
            **(result if isinstance(result, dict) else {"value": result}),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if meta:
            data[step].update(meta)

        if step not in data["steps_completed"]:
            data["steps_completed"].append(step)

        self._save_clip(clip_id, data)

    def init_clip(self, clip_id: str, source_file: str, order_num: int,
                  creation_time: str, duration_seconds: float):
        data = self._load_clip(clip_id)
        if "clip_id" not in data:
            data.update({
                "clip_id": clip_id,
                "source_file": source_file,
                "order_num": order_num,
                "creation_time": creation_time,
                "duration_seconds": duration_seconds,
                "steps_completed": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            self._save_clip(clip_id, data)

    def get_clip_meta(self, clip_id: str) -> dict:
        return self._load_clip(clip_id)

    # -- Per-stage helpers --

    def _stage_path(self, stage: str) -> Path:
        return self.cache_dir / f"_stage_{stage}.json"

    def has_stage(self, stage: str) -> bool:
        return self._stage_path(stage).exists()

    def get_stage(self, stage: str) -> Optional[dict]:
        p = self._stage_path(stage)
        if p.exists():
            return json.loads(p.read_text())
        return None

    def save_stage(self, stage: str, data: Any):
        p = self._stage_path(stage)
        payload = {
            "stage": stage,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "data": data if isinstance(data, (dict, list)) else {"value": data},
        }
        p.write_text(json.dumps(payload, indent=2, default=str))

    def list_clips(self) -> list[str]:
        return [
            p.stem for p in self.cache_dir.glob("*.json")
            if not p.stem.startswith("_stage_")
        ]
