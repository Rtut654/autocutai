#!/usr/bin/env python3
"""Run the real pipeline over a folder of your own clips.

Does exactly what the iOS app does, over HTTP, against a running backend:
signs in, uploads each clip, waits for transcription, shows what the AI
proposes to cut, renders, and downloads the result.

Use it to see real output on real footage - export a few clips out of Google
Photos or Drive into a local folder and point this at them.

    # 1. Start the backend with a real Azure Speech key
    export AZURE_SPEECH_KEY=...  AZURE_SPEECH_REGION=westeurope
    export OPENAI_API_KEY=...            # optional, improves cut selection
    python backend/main.py

    # 2. In another shell
    python scripts/try_on_your_footage.py ~/Desktop/lisbon-clips

    # Keep every AI-suggested cut instead of being asked about each one
    python scripts/try_on_your_footage.py ~/clips --accept-all

Requires ffmpeg on the backend host. No third-party Python packages.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MAX_CLIPS = 10
MAX_TOTAL_SECONDS = 15 * 60

BOLD, DIM, GREEN, YELLOW, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def say(message: str = "", colour: str = "") -> None:
    print(f"{colour}{message}{RESET}" if colour else message, flush=True)


class Api:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.token: str | None = None

    def _request(self, path, method="GET", body=None, form=None, files=None, raw=False):
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        if files is not None:
            boundary = uuid.uuid4().hex
            parts: list[bytes] = []
            for key, value in (form or {}).items():
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                )
            for field, filename, data in files:
                content_type = mimetypes.guess_type(filename)[0] or "video/mp4"
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                    f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
                    + data
                    + b"\r\n"
                )
            parts.append(f"--{boundary}--\r\n".encode())
            payload = b"".join(parts)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        else:
            payload = None

        request = urllib.request.Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                data = response.read()
                return data if raw else json.loads(data or b"{}")
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except Exception:
                pass
            raise SystemExit(f"{RED}{method} {path} failed ({error.code}): {detail}{RESET}") from error
        except urllib.error.URLError as error:
            raise SystemExit(
                f"{RED}Could not reach {self.base}. Is the backend running?{RESET}\n  {error.reason}"
            ) from error

    def sign_in(self, email: str, password: str) -> None:
        try:
            response = self._request("/api/auth/login", "POST", {"email": email, "password": password})
        except SystemExit:
            response = self._request(
                "/api/auth/signup", "POST", {"email": email, "password": password, "full_name": "Footage Test"}
            )
        self.token = response["access_token"]

    def health(self) -> dict[str, Any]:
        return self._request("/api/transcribe/health")

    def upload(self, path: Path, project_id: str | None, aspect_ratio: str) -> dict[str, Any]:
        data = path.read_bytes()
        if project_id:
            return self._request(
                f"/api/projects/{project_id}/tracks",
                "POST",
                form={"capture_times_json": json.dumps([None]), "metadata_json": json.dumps([{}])},
                files=[("files", path.name, data)],
            )
        return self._request(
            "/api/projects/",
            "POST",
            form={
                "name": f"Footage test {time.strftime('%d %b %H:%M')}",
                "aspect_ratio": aspect_ratio,
                "smart_pause_cutter": "true",
                "generate_subtitles": "true",
                "insert_suggestions": "false",
                "capture_times_json": json.dumps([None]),
                "metadata_json": json.dumps([{}]),
            },
            files=[("files", path.name, data)],
        )

    def project(self, project_id: str) -> dict[str, Any]:
        return self._request(f"/api/projects/{project_id}")["project"]

    def status(self, project_id: str) -> dict[str, Any]:
        return self._request(f"/api/projects/{project_id}/status")

    def suggest_cuts(self, project_id: str, track_id: str) -> dict[str, Any]:
        return self._request(f"/api/projects/{project_id}/tracks/{track_id}/speech-filter", "POST", body={})

    def save_cuts(self, project_id: str, track_id: str, cuts: list[dict]) -> dict[str, Any]:
        return self._request(
            f"/api/projects/{project_id}/tracks/{track_id}/speech-filter", "PATCH", body={"cuts": cuts}
        )

    def render(self, project_id: str) -> None:
        self._request(f"/api/projects/{project_id}/process", "POST")

    def download(self, project_id: str) -> bytes:
        return self._request(f"/api/projects/{project_id}/download", raw=True)


def collect_clips(folder: Path) -> list[Path]:
    clips = sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES and p.is_file())
    if not clips:
        raise SystemExit(f"{RED}No video files in {folder}{RESET}")
    if len(clips) > MAX_CLIPS:
        say(f"{YELLOW}Found {len(clips)} clips; using the first {MAX_CLIPS}.{RESET}")
        clips = clips[:MAX_CLIPS]
    return clips


def wait_for_transcripts(api: Api, project_id: str, timeout: int = 900) -> list[dict]:
    deadline = time.time() + timeout
    settled = {"completed", "not_applicable", "error"}
    last_line = ""
    while time.time() < deadline:
        project = api.project(project_id)
        tracks = project["tracks"]
        done = sum(1 for t in tracks if (t.get("metadata") or {}).get("transcript_status") in settled)
        line = f"  transcribing {done}/{len(tracks)} clips"
        if line != last_line:
            say(line, DIM)
            last_line = line
        if done == len(tracks):
            return tracks
        time.sleep(2)
    raise SystemExit(f"{RED}Timed out waiting for transcription.{RESET}")


def format_time(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="Folder containing your video clips")
    parser.add_argument("--api", default=os.getenv("AUTOCUT_API", "http://127.0.0.1:8000"), help="Backend URL")
    parser.add_argument("--email", default="footage-test@example.com")
    parser.add_argument("--password", default="password123")
    parser.add_argument("--aspect-ratio", choices=["vertical", "horizontal"], default="vertical")
    parser.add_argument("--accept-all", action="store_true", help="Keep every suggested cut without prompting")
    parser.add_argument("--out", type=Path, default=Path("autocut-result.mp4"))
    args = parser.parse_args()

    if not args.folder.is_dir():
        raise SystemExit(f"{RED}{args.folder} is not a folder{RESET}")

    clips = collect_clips(args.folder)
    api = Api(args.api)

    say(f"{BOLD}AutoCutAI — real footage run{RESET}")
    say(f"  backend  {args.api}")
    say(f"  clips    {len(clips)}: " + ", ".join(c.name for c in clips))
    say()

    api.sign_in(args.email, args.password)
    health = api.health()
    if not health.get("configured"):
        say(
            f"{YELLOW}Warning: Azure Speech is not configured on the backend.{RESET}\n"
            f"  Transcription will fail, so there will be nothing to cut.\n"
            f"  Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION and restart it.",
        )
        say()
    else:
        say(f"  speech   Azure {health.get('region')} · {health.get('default_language')}", DIM)

    project_id = None
    for index, clip in enumerate(clips, start=1):
        size_mb = clip.stat().st_size / (1024 * 1024)
        say(f"  uploading {index}/{len(clips)}  {clip.name} ({size_mb:.0f} MB)", DIM)
        response = api.upload(clip, project_id, args.aspect_ratio)
        project_id = response["project"]["id"]

    assert project_id
    say()
    tracks = wait_for_transcripts(api, project_id)

    total = sum(float(t.get("duration") or 0) for t in tracks)
    if total > MAX_TOTAL_SECONDS:
        say(f"{YELLOW}Note: {total / 60:.1f} minutes exceeds the {MAX_TOTAL_SECONDS // 60} minute cap.{RESET}")

    say()
    say(f"{BOLD}What AutoCutAI heard{RESET}")
    for track in tracks:
        transcript = ((track.get("transcription") or {}).get("text") or "").strip()
        say(f"  {track['filename']}  {float(track['duration']):.1f}s")
        if transcript:
            say(f"    “{transcript[:220]}{'…' if len(transcript) > 220 else ''}”", DIM)
        else:
            say("    no narration — kept as b-roll", DIM)

    say()
    say(f"{BOLD}Proposed cuts{RESET}")
    removed_total = 0.0
    for track in tracks:
        if not track.get("has_voice"):
            continue
        artifact = api.suggest_cuts(project_id, track["id"])
        cuts = artifact.get("cuts", [])
        say(f"  {track['filename']} — {artifact.get('summary') or 'no cuts'} [{artifact.get('model')}]")
        for cut in cuts:
            words = f' “{cut["transcript"]}”' if cut.get("transcript") else ""
            say(
                f"    {format_time(cut['start'])}–{format_time(cut['end'])}"
                f"  {cut['duration']:.2f}s  {cut['reason']}{words}",
                DIM,
            )

        keep = cuts
        if cuts and not args.accept_all:
            answer = input(f"    Apply these {len(cuts)} cuts? [Y/n/s(elect)] ").strip().lower()
            if answer == "n":
                keep = []
            elif answer.startswith("s"):
                keep = []
                for cut in cuts:
                    label = cut.get("transcript") or cut["reason"]
                    if input(f"      remove {cut['duration']:.2f}s ({label})? [Y/n] ").strip().lower() != "n":
                        keep.append(cut)
            api.save_cuts(project_id, track["id"], keep)
        removed_total += sum(c["duration"] for c in keep)

    say()
    say(f"{BOLD}Rendering{RESET}")
    api.render(project_id)
    while True:
        status = api.status(project_id)
        if status["status"] in {"completed", "error"}:
            break
        say(f"  {status['progress']:.0f}% · {status['current_step']}", DIM)
        time.sleep(3)

    if status["status"] == "error":
        raise SystemExit(f"{RED}Render failed: {status.get('error_message')}{RESET}")

    args.out.write_bytes(api.download(project_id))
    say()
    say(f"{GREEN}Done.{RESET}")
    say(f"  source   {total:.1f}s across {len(tracks)} clips")
    say(f"  removed  {removed_total:.1f}s")
    say(f"  written  {args.out.resolve()}")
    say()
    say("  Inspect it with:  ffprobe -hide_banner " + str(args.out), DIM)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
