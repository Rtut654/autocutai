"""Main pipeline orchestrator with per-step caching."""

import asyncio
import os
import subprocess
import json as _json
from pathlib import Path

from .ordering import sort_clips_chronologically, get_creation_time
from .demux import demux_all, detect_all_scenes
from .audio_track import transcribe_all, classify_gaps, select_music
from .visual_track import (
    extract_key_frame, clip_classify_frames,
    describe_uncertain_frames, predict_transition
)
from .edit_llm import generate_edit_plan
from .renderer import render_video
from ..models.schemas import PipelineStatus, EditPlan
from ..utils.cache import StepCache
from ..utils.ffmpeg_utils import get_duration


async def run_pipeline(
    clip_paths: list[str],
    job_id: str,
    status_callback,
    output_dir: str
) -> str:
    """
    Full pipeline orchestrator with per-step caching.
    Every step checks the cache before running.
    If a step is already cached, it loads the result and skips processing.
    """
    cache = StepCache(job_id, output_dir)
    tmp = f"{output_dir}/{job_id}"

    for subdir in ["cache", "frames", "audio", "video"]:
        os.makedirs(f"{tmp}/{subdir}", exist_ok=True)

    # -- STAGE 1: Chronological ordering --
    if cache.has_stage("ordering"):
        await status_callback(PipelineStatus(
            job_id=job_id, stage="ordering", progress=0.05,
            message="Clip order restored from cache."
        ))
        ordering_data = cache.get_stage("ordering")["data"]
        sorted_clips = ordering_data["sorted_paths"]
    else:
        await status_callback(PipelineStatus(
            job_id=job_id, stage="ordering", progress=0.05,
            message="Sorting clips chronologically..."
        ))
        sorted_clips = sort_clips_chronologically(clip_paths)
        cache.save_stage("ordering", {"sorted_paths": sorted_clips})

    # -- STAGE 2: Init clip cache records --
    for order_num, path in enumerate(sorted_clips):
        clip_id = Path(path).stem
        ct = get_creation_time(path)
        dur = get_duration(path)
        cache.init_clip(clip_id, path, order_num, ct.isoformat(), dur)

    # -- STAGE 3: Demux + scene detect --
    await status_callback(PipelineStatus(
        job_id=job_id, stage="demux", progress=0.10,
        message="Splitting audio and video streams..."
    ))
    clips = await _demux_with_cache(sorted_clips, tmp, cache)

    # -- STAGE 4: Parallel audio + visual tracks --
    await status_callback(PipelineStatus(
        job_id=job_id, stage="analysis", progress=0.15,
        message="Analysing audio and video in parallel..."
    ))

    audio_task = process_audio_track(clips, cache)
    visual_task = process_visual_track(clips, tmp, cache)

    (transcripts, gap_map, music_suggestions), scene_map = await asyncio.gather(
        audio_task, visual_task
    )

    # -- STAGE 5: Edit assembly LLM --
    if cache.has_stage("edit_plan"):
        await status_callback(PipelineStatus(
            job_id=job_id, stage="edit_plan", progress=0.80,
            message="Edit plan restored from cache."
        ))
        edit_plan = EditPlan(**cache.get_stage("edit_plan")["data"])
    else:
        await status_callback(PipelineStatus(
            job_id=job_id, stage="edit_plan", progress=0.80,
            message="Generating edit plan..."
        ))
        edit_plan = await generate_edit_plan(
            transcripts, scene_map, gap_map, music_suggestions
        )
        cache.save_stage("edit_plan", edit_plan.model_dump())

    # -- STAGE 6: Render --
    if cache.has_stage("render"):
        render_data = cache.get_stage("render")["data"]
        output_path = render_data["output_path"]
        await status_callback(PipelineStatus(
            job_id=job_id, stage="done", progress=1.0,
            message="Video already rendered - restored from cache.",
            edit_plan=edit_plan,
            output_url=f"/outputs/{job_id}/output.mp4"
        ))
    else:
        await status_callback(PipelineStatus(
            job_id=job_id, stage="rendering", progress=0.85,
            message="Rendering final video...",
            edit_plan=edit_plan
        ))
        output_path = f"{tmp}/output.mp4"
        await render_video(edit_plan, tmp, output_path)

        file_size = os.path.getsize(output_path) / (1024 * 1024)
        cache.save_stage("render", {
            "output_path": output_path,
            "file_size_mb": round(file_size, 2),
        })

        await status_callback(PipelineStatus(
            job_id=job_id, stage="done", progress=1.0,
            message="Done!",
            edit_plan=edit_plan,
            output_url=f"/outputs/{job_id}/output.mp4"
        ))

    return output_path


async def _demux_with_cache(sorted_clips: list[str], tmp: str, cache: StepCache) -> list[dict]:
    """Demux clips - skip any clip where demux is already cached."""
    clips = []
    needs_demux = []

    for path in sorted_clips:
        clip_id = Path(path).stem
        if cache.has_step(clip_id, "demux") and cache.has_step(clip_id, "scene_detect"):
            demux_data = cache.get_step(clip_id, "demux")
            scene_data = cache.get_step(clip_id, "scene_detect")
            clips.append({
                "clip_id": clip_id,
                "source_file": path,
                "video": demux_data["video_path"],
                "audio": demux_data["audio_path"],
                "scene_timestamps": scene_data["timestamps"],
            })
        else:
            needs_demux.append(path)

    if needs_demux:
        new_clips = await demux_all(needs_demux, f"{tmp}/video", f"{tmp}/audio")
        new_clips = await detect_all_scenes(new_clips)
        for clip in new_clips:
            cache.save_step(clip["clip_id"], "demux", {
                "video_path": clip["video"],
                "audio_path": clip["audio"],
            })
            cache.save_step(clip["clip_id"], "scene_detect", {
                "timestamps": clip["scene_timestamps"],
            })
        clips.extend(new_clips)

    # Re-sort to maintain chronological order
    order = {Path(p).stem: i for i, p in enumerate(sorted_clips)}
    clips.sort(key=lambda c: order.get(c["clip_id"], 999))
    return clips


async def process_audio_track(clips: list[dict], cache: StepCache):
    """Whisper transcription + gap classification + music selection, all cache-aware."""
    import httpx
    from .audio_track import transcribe_clip, classify_gaps, select_music

    transcripts = []
    gap_map = []
    music_suggestions = []

    async with httpx.AsyncClient() as client:
        uncached = []
        for clip in clips:
            if cache.has_step(clip["clip_id"], "whisper"):
                transcripts.append({
                    "clip_id": clip["clip_id"],
                    **cache.get_step(clip["clip_id"], "whisper")
                })
            else:
                uncached.append(clip)

        if uncached:
            new_transcripts = await asyncio.gather(*[
                transcribe_clip(client, c["audio"], c["clip_id"])
                for c in uncached
            ])
            for t in new_transcripts:
                cid = t["clip_id"]
                cache.save_step(cid, "whisper", {
                    "transcript": t["transcript"],
                    "segments":   t["segments"],
                    "words":      t["words"],
                    "language":   t.get("language", "en"),
                })
                transcripts.append(t)

    # Gap classification
    for clip, transcript in zip(
        sorted(clips, key=lambda c: c["clip_id"]),
        sorted(transcripts, key=lambda t: t["clip_id"])
    ):
        duration = get_duration(clip["source_file"])
        gaps = classify_gaps(transcript.get("segments", []), duration)
        gap_map.extend([{"clip_id": clip["clip_id"], **g} for g in gaps])

        for gap in gaps:
            if gap["type"] == "music":
                suggestion = select_music("peaceful", gap["duration"])
                suggestion["clip_id"] = clip["clip_id"]
                suggestion["gap_start"] = gap["start"]
                music_suggestions.append(suggestion)

    return transcripts, gap_map, music_suggestions


async def process_visual_track(clips: list[dict], tmp_dir: str, cache: StepCache):
    """CLIP + GPT-4o-mini vision + transition prediction, all cache-aware."""
    from .visual_track import (
        extract_key_frame, clip_classify_frames,
        describe_uncertain_frames, predict_transition
    )

    frame_meta = []
    needs_classify = []

    for clip in clips:
        if cache.has_step(clip["clip_id"], "clip_classify"):
            cached_frames = cache.get_step(clip["clip_id"], "clip_classify")["frames"]
            if cache.has_step(clip["clip_id"], "gpt4o_describe"):
                gpt_data = cache.get_step(clip["clip_id"], "gpt4o_describe")
                for f in cached_frames:
                    key = (clip["clip_id"], f.get("frame_idx", 0))
                    if "descriptions" in gpt_data:
                        f["gpt_description"] = gpt_data["descriptions"].get(str(key))
            frame_meta.extend(cached_frames)
        else:
            timestamps = clip.get("scene_timestamps", [0.0]) or [0.0]
            for i, ts in enumerate(timestamps[:2]):
                out_path = f"{tmp_dir}/frames/{clip['clip_id']}_scene{i}.jpg"
                extract_key_frame(clip["video"], ts, out_path)
                frame_meta.append({
                    "clip_id": clip["clip_id"],
                    "frame_idx": i,
                    "timestamp": ts,
                    "frame_path": out_path,
                })
            needs_classify.append(clip["clip_id"])

    if needs_classify:
        new_frames = [f for f in frame_meta if f["clip_id"] in needs_classify]
        paths = [f["frame_path"] for f in new_frames]
        results = clip_classify_frames(paths)

        for meta, result in zip(new_frames, results):
            meta.update(result)

        # Group by clip and save
        from itertools import groupby
        for clip_id, group in groupby(new_frames, key=lambda f: f["clip_id"]):
            frames_list = list(group)
            cache.save_step(clip_id, "clip_classify", {"frames": frames_list})

        # GPT-4o-mini for uncertain frames
        uncertain_ids = {f["clip_id"] for f in new_frames if f.get("scene_confidence", 1) < 0.6}
        if uncertain_ids:
            new_frames = await describe_uncertain_frames(new_frames)
            for clip_id in uncertain_ids:
                clip_frames = [f for f in new_frames if f["clip_id"] == clip_id]
                descriptions = {
                    str((f["clip_id"], f.get("frame_idx", 0))): f.get("gpt_description")
                    for f in clip_frames if "gpt_description" in f
                }
                cache.save_step(clip_id, "gpt4o_describe", {"descriptions": descriptions})
        else:
            for clip_id in needs_classify:
                if not cache.has_step(clip_id, "gpt4o_describe"):
                    cache.save_step(clip_id, "gpt4o_describe", {
                        "skipped": True, "reason": "all frames above confidence threshold"
                    })

    # Transition prediction between consecutive clips
    for i in range(len(clips) - 1):
        cid = clips[i]["clip_id"]
        if cache.has_step(cid, "transition_to_next"):
            clips[i]["transition_to_next"] = cache.get_step(cid, "transition_to_next")
        else:
            last_frame  = f"{tmp_dir}/frames/{clips[i]['clip_id']}_scene0.jpg"
            first_frame = f"{tmp_dir}/frames/{clips[i+1]['clip_id']}_scene0.jpg"
            if os.path.exists(last_frame) and os.path.exists(first_frame):
                t = predict_transition(last_frame, first_frame)
                cache.save_step(cid, "transition_to_next", t)
                clips[i]["transition_to_next"] = t

    return frame_meta
