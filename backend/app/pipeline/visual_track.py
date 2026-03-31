"""Visual track processing: CLIP classification, GPT-4o-mini vision, transition prediction."""

import asyncio
import base64
import os
import subprocess
import numpy as np
from pathlib import Path
from PIL import Image

try:
    import clip
    import torch
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
except ImportError:
    clip = None
    torch = None
    DEVICE = "cpu"

import openai


# CLIP setup - load once at startup
_clip_model, _clip_preprocess = None, None

TRAVEL_LABELS = [
    "beach", "ocean", "mountains", "forest", "desert", "river",
    "temple", "church", "mosque", "historic building", "ruins",
    "restaurant", "street food", "market", "food close-up",
    "city street", "night city", "crowd", "alleyway",
    "airport", "train", "bus", "car journey",
    "hotel room", "accommodation",
    "selfie", "portrait", "group of people",
    "drone aerial shot", "wide landscape", "sunset", "sunrise",
    "museum", "art gallery", "cultural performance",
]

MOOD_LABELS = [
    "peaceful and calm", "energetic and exciting",
    "intimate and personal", "epic and cinematic",
    "nostalgic and warm", "mysterious and atmospheric",
]


def get_clip_model():
    global _clip_model, _clip_preprocess
    if clip is None:
        return None, None
    if _clip_model is None:
        _clip_model, _clip_preprocess = clip.load("ViT-L/14", device=DEVICE)
    return _clip_model, _clip_preprocess


def extract_key_frame(video_path: str, timestamp: float, output_path: str) -> str:
    """Extract a single frame at timestamp from video."""
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=384:384:force_original_aspect_ratio=decrease",
        output_path
    ], capture_output=True)
    return output_path


def clip_classify_frames(image_paths: list[str]) -> list[dict]:
    """
    Run CLIP on a batch of frames.
    Returns scene type, mood, and confidence for each.
    Falls back to generic labels if CLIP is not available.
    """
    model, preprocess = get_clip_model()

    if model is None:
        # Fallback when CLIP is not installed
        return [
            {
                "frame_path": p,
                "scene_type": "wide landscape",
                "scene_confidence": 0.5,
                "mood": "peaceful",
                "mood_confidence": 0.5,
            }
            for p in image_paths
        ]

    # Encode text labels
    all_labels = TRAVEL_LABELS + MOOD_LABELS
    text_tokens = clip.tokenize(all_labels).to(DEVICE)

    results = []

    # Process in batches of 8
    batch_size = 8
    for i in range(0, len(image_paths), batch_size):
        batch_paths = image_paths[i:i + batch_size]
        images = torch.stack([
            preprocess(Image.open(p).convert("RGB"))
            for p in batch_paths
        ]).to(DEVICE)

        with torch.no_grad():
            image_features = model.encode_image(images)
            text_features = model.encode_text(text_tokens)

            image_features /= image_features.norm(dim=-1, keepdim=True)
            text_features /= text_features.norm(dim=-1, keepdim=True)

            similarity = (image_features @ text_features.T).softmax(dim=-1)

        for j, path in enumerate(batch_paths):
            scores = similarity[j].cpu().numpy()
            scene_scores = scores[:len(TRAVEL_LABELS)]
            mood_scores = scores[len(TRAVEL_LABELS):]

            best_scene_idx = np.argmax(scene_scores)
            best_mood_idx = np.argmax(mood_scores)

            results.append({
                "frame_path": path,
                "scene_type": TRAVEL_LABELS[best_scene_idx],
                "scene_confidence": float(scene_scores[best_scene_idx]),
                "mood": MOOD_LABELS[best_mood_idx].split(" and ")[0],
                "mood_confidence": float(mood_scores[best_mood_idx]),
            })

    return results


async def gpt4o_describe_frame(
    client: openai.AsyncOpenAI,
    image_path: str,
    clip_id: str,
    frame_idx: int
) -> dict:
    """
    Call GPT-4o-mini vision for a single frame.
    Uses low detail + 30 max tokens for speed and cost.
    """
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                        "detail": "low"
                    }
                },
                {
                    "type": "text",
                    "text": (
                        "In 10 words max: scene type, main subject, mood. "
                        "Example: 'crowded night market, street food stalls, vibrant'"
                    )
                }
            ]
        }],
        max_tokens=30
    )

    return {
        "clip_id": clip_id,
        "frame_idx": frame_idx,
        "description": response.choices[0].message.content.strip(),
    }


async def describe_uncertain_frames(frames: list[dict]) -> list[dict]:
    """
    For frames where CLIP confidence < 0.6, call GPT-4o-mini.
    All calls are made in parallel as async coroutines.
    """
    uncertain = [f for f in frames if f.get("scene_confidence", 1.0) < 0.6]

    if not uncertain:
        return frames

    client = openai.AsyncOpenAI()
    tasks = [
        gpt4o_describe_frame(
            client,
            f["frame_path"],
            f["clip_id"],
            f.get("frame_idx", 0)
        )
        for f in uncertain
    ]

    descriptions = await asyncio.gather(*tasks)
    desc_map = {
        (d["clip_id"], d["frame_idx"]): d["description"]
        for d in descriptions
    }

    for frame in frames:
        key = (frame["clip_id"], frame.get("frame_idx", 0))
        if key in desc_map:
            frame["gpt_description"] = desc_map[key]

    return frames


def predict_transition(clip_a_last_frame: str, clip_b_first_frame: str) -> dict:
    """
    Compare CLIP embeddings of adjacent clip boundaries.
    Returns transition type based on visual similarity.
    Falls back to dissolve if CLIP is not available.
    """
    model, preprocess = get_clip_model()

    if model is None:
        return {"type": "dissolve", "duration_ms": 500, "reason": "clip_unavailable"}

    imgs = torch.stack([
        preprocess(Image.open(clip_a_last_frame).convert("RGB")),
        preprocess(Image.open(clip_b_first_frame).convert("RGB")),
    ]).to(DEVICE)

    with torch.no_grad():
        features = model.encode_image(imgs)
        features /= features.norm(dim=-1, keepdim=True)
        similarity = float((features[0] @ features[1]).cpu())

    if similarity > 0.85:
        return {"type": "hard_cut", "duration_ms": 0, "reason": "same scene"}
    elif similarity > 0.65:
        return {"type": "dissolve", "duration_ms": 500, "reason": "similar scene"}
    else:
        return {"type": "fade_black", "duration_ms": 400, "reason": "scene change"}
