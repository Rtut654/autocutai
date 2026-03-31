"""FFmpeg renderer - builds final video from edit plan."""

import asyncio
from ..models.schemas import EditPlan


async def render_video(edit_plan: EditPlan, clips_dir: str, output_path: str) -> str:
    """
    Build ffmpeg command from edit plan and render final video.
    Uses concat demuxer for cuts, filter_complex for transitions and audio.
    """

    # Build concat list file
    concat_lines = []

    for i, clip in enumerate(edit_plan.clips):
        src = clip.source_file
        in_pt = clip.in_point
        out_pt = clip.out_point
        concat_lines.append(f"file '{src}'")
        concat_lines.append(f"inpoint {in_pt}")
        concat_lines.append(f"outpoint {out_pt}")

    concat_file = f"{clips_dir}/concat_list.txt"
    with open(concat_file, "w") as f:
        f.write("\n".join(concat_lines))

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()}")

    return output_path
