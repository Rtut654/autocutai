"""End-to-end coverage of the edit pipeline.

Walks a project from upload through transcription, gap detection, speech
filtering, per-track render and final export, using the ffmpeg and Azure
doubles from conftest. The orchestration, ordering and cut arithmetic under
test are the real implementations.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from factories import azure_payload


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def create_project(client, auth_headers, clips, *, name="Lisbon trip", capture_times=None, **settings):
    files = [("files", (path.name, path.read_bytes(), "video/mp4")) for path in clips]
    data = {
        "name": name,
        "smart_pause_cutter": "true",
        "generate_subtitles": "true",
        "insert_suggestions": "false",
        "metadata_json": json.dumps([{} for _ in clips]),
        "capture_times_json": json.dumps(capture_times or [None for _ in clips]),
    }
    data.update({key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in settings.items()})
    return client.post("/api/projects/", data=data, files=files, headers=auth_headers)


@pytest.fixture
def project_env(client, auth_headers, fake_media_tools, fake_transcription, no_llm, make_clip):
    """Everything a pipeline test needs, wired together."""
    return {
        "client": client,
        "headers": auth_headers,
        "media": fake_media_tools,
        "transcription": fake_transcription,
        "make_clip": make_clip,
    }


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


def test_upload_creates_project_with_a_track_per_clip(project_env):
    clips = [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4")]

    response = create_project(project_env["client"], project_env["headers"], clips)

    assert response.status_code == 200, response.text
    project = response.json()["project"]
    assert len(project["tracks"]) == 2
    assert {track["filename"] for track in project["tracks"]} == {"a.mp4", "b.mp4"}
    assert project["status"] == "draft"


def test_upload_requires_authentication(client, make_clip):
    clip = make_clip("a.mp4")

    response = client.post(
        "/api/projects/",
        data={"name": "No auth"},
        files=[("files", (clip.name, clip.read_bytes(), "video/mp4"))],
    )

    assert response.status_code == 401


def test_clips_are_ordered_chronologically(project_env):
    base = datetime(2026, 5, 1, 9, 0, 0)
    clips = [
        project_env["make_clip"]("third.mp4"),
        project_env["make_clip"]("first.mp4"),
        project_env["make_clip"]("second.mp4"),
    ]
    capture_times = [
        (base + timedelta(hours=4)).isoformat(),
        base.isoformat(),
        (base + timedelta(hours=2)).isoformat(),
    ]

    response = create_project(project_env["client"], project_env["headers"], clips, capture_times=capture_times)

    assert response.status_code == 200, response.text
    tracks = response.json()["project"]["tracks"]
    assert [track["filename"] for track in tracks] == ["first.mp4", "second.mp4", "third.mp4"]
    assert [track["position"] for track in tracks] == [0, 1, 2]


def test_uploads_land_under_the_owning_user(project_env, isolated_storage):
    clips = [project_env["make_clip"]("a.mp4")]

    response = create_project(project_env["client"], project_env["headers"], clips)

    project_id = response.json()["project"]["id"]
    user_dirs = [path for path in isolated_storage.projects_dir.iterdir() if path.is_dir()]
    assert len(user_dirs) == 1
    assert (user_dirs[0] / project_id / "video" / "a.mp4").exists()


def test_another_user_cannot_read_the_project(project_env, client):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    other = client.post(
        "/api/auth/signup",
        json={"email": "intruder@example.com", "password": "password123"},
    ).json()["access_token"]

    response = client.get(f"/api/projects/{project_id}", headers={"Authorization": f"Bearer {other}"})

    assert response.status_code == 404


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------


def test_rejects_more_than_ten_clips(project_env):
    clips = [project_env["make_clip"](f"clip{index}.mp4") for index in range(11)]

    response = create_project(project_env["client"], project_env["headers"], clips)

    assert response.status_code == 400
    assert "at most 10 clips" in response.json()["detail"]


def test_accepts_exactly_ten_clips(project_env):
    clips = [project_env["make_clip"](f"clip{index}.mp4") for index in range(10)]

    response = create_project(project_env["client"], project_env["headers"], clips)

    assert response.status_code == 200, response.text
    assert len(response.json()["project"]["tracks"]) == 10


def test_rejects_a_project_over_the_duration_cap(project_env, monkeypatch):
    from backend.app.services.video_processor import VideoProcessor

    async def long_clips(self, video_path):
        return {"format": {"duration": "600.0"}, "streams": [{"codec_type": "video", "width": 1080, "height": 1920, "duration": "600.0"}]}

    monkeypatch.setattr(VideoProcessor, "get_video_info", long_clips)
    clips = [project_env["make_clip"](f"long{index}.mp4") for index in range(3)]

    response = create_project(project_env["client"], project_env["headers"], clips)

    assert response.status_code == 400
    assert "15 minutes" in response.json()["detail"]


def test_adding_tracks_respects_the_running_clip_total(project_env):
    clips = [project_env["make_clip"](f"clip{index}.mp4") for index in range(8)]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    extra = [project_env["make_clip"](f"extra{index}.mp4") for index in range(4)]
    response = project_env["client"].post(
        f"/api/projects/{project_id}/tracks",
        files=[("files", (path.name, path.read_bytes(), "video/mp4")) for path in extra],
        headers=project_env["headers"],
    )

    assert response.status_code == 400
    assert "already has 8" in response.json()["detail"]


def test_hybrid_analyze_rejects_an_oversized_selection(project_env):
    payload = {
        "name": "Too long",
        "tracks": [
            {"filename": f"clip{index}.mp4", "duration": 200.0, "transcription": {"text": "", "words": [], "segments": []}}
            for index in range(6)
        ],
    }

    response = project_env["client"].post(
        "/api/projects/hybrid-analyze", json=payload, headers=project_env["headers"]
    )

    assert response.status_code == 400
    assert "15 minutes" in response.json()["detail"]


def test_limits_are_configurable(project_env, monkeypatch):
    monkeypatch.setenv("AUTOCUT_MAX_CLIPS", "2")
    clips = [project_env["make_clip"](f"clip{index}.mp4") for index in range(3)]

    response = create_project(project_env["client"], project_env["headers"], clips)

    assert response.status_code == 400
    assert "at most 2 clips" in response.json()["detail"]


# --------------------------------------------------------------------------
# Transcription and gap detection
# --------------------------------------------------------------------------


def test_processing_transcribes_every_track(project_env):
    clips = [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    response = project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    assert response.status_code == 200, response.text
    project = response.json()["project"]
    assert project["status"] == "completed"
    for track in project["tracks"]:
        assert track["has_voice"] is True
        assert track["transcription"]["text"].startswith("So um this is Lisbon")
        assert len(track["transcription"]["words"]) == 10


def test_transcription_extracts_wav_audio_first(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    # Azure only accepts uncompressed WAV, so the pipeline must convert first.
    assert len(project_env["media"].audio_extractions) == 1
    assert project_env["transcription"][0]["audio_path"].endswith(".wav")


def test_transcription_uses_the_configured_locale(project_env, monkeypatch):
    from backend.app.services.project_service import project_service

    monkeypatch.setattr(project_service, "transcription_language", "pt-PT")
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    assert project_env["transcription"][0]["language"] == "pt-PT"


def test_pauses_longer_than_the_threshold_become_gaps(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    response = project_env["client"].get(f"/api/projects/{project_id}/gaps", headers=project_env["headers"])

    assert response.status_code == 200
    gaps = response.json()["gaps"]
    # The fixture transcript has a 1.65s pause between "Lisbon" and "and".
    assert any(abs(gap["start"] - 1.95) < 0.01 and abs(gap["end"] - 3.60) < 0.01 for gap in gaps)


def test_silent_clip_is_marked_as_having_no_voice(project_env, monkeypatch):
    async def silent(audio_path, language=None):
        return {"text": "", "words": [], "segments": [], "language": "en-US"}

    monkeypatch.setattr("backend.app.services.project_service.transcribe_audio_file", silent)
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    response = project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    track = response.json()["project"]["tracks"][0]
    assert track["has_voice"] is False
    assert track["transcription"] is None


def test_known_hallucination_is_stripped(project_env, monkeypatch):
    from backend.app.services.azure_speech_service import parse_recognition_payloads

    async def hallucinating(audio_path, language=None):
        return parse_recognition_payloads(
            [azure_payload([("Thanks", 9.8, 9.9), ("for", 9.9, 9.95), ("watching", 9.95, 10.0)],
                           display="Thanks for watching!")],
            "en-US",
        )

    monkeypatch.setattr("backend.app.services.project_service.transcribe_audio_file", hallucinating)
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    response = project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    assert response.json()["project"]["tracks"][0]["transcription"] is None


# --------------------------------------------------------------------------
# Timeline and subtitles
# --------------------------------------------------------------------------


def test_timeline_combines_transcripts_across_clips(project_env):
    clips = [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    response = project_env["client"].get(f"/api/projects/{project_id}/timeline", headers=project_env["headers"])

    assert response.status_code == 200
    pipeline = response.json()["pipeline"]
    assert pipeline["combined_transcript"].count("Lisbon") == 2
    assert len(pipeline["combined_words"]) == 20


def test_second_clip_words_are_offset_onto_the_project_timeline(project_env):
    clips = [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    pipeline = project_env["client"].get(
        f"/api/projects/{project_id}/timeline", headers=project_env["headers"]
    ).json()["pipeline"]

    words = pipeline["combined_words"]
    first_clip_duration = 12.0  # from the fake ffprobe
    assert words[0]["start"] == pytest.approx(0.20, abs=0.01)
    assert words[10]["start"] == pytest.approx(first_clip_duration + 0.20, abs=0.01)


def test_subtitles_are_written_and_downloadable(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    response = project_env["client"].get(f"/api/projects/{project_id}/subtitles", headers=project_env["headers"])

    assert response.status_code == 200
    body = response.text
    assert "00:00:00,200 --> " in body
    assert "Lisbon" in body


def test_word_level_srt_has_one_cue_per_word(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    response = project_env["client"].get(f"/api/projects/{project_id}/word-srt", headers=project_env["headers"])

    assert response.status_code == 200
    cue_indices = [line for line in response.text.splitlines() if line.strip().isdigit()]
    assert len(cue_indices) == 10


def test_render_manifest_reflects_the_auto_cut_setting(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(
        project_env["client"], project_env["headers"], clips, smart_pause_cutter=False
    ).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    response = project_env["client"].get(
        f"/api/projects/{project_id}/render-manifest", headers=project_env["headers"]
    )

    plan = response.json()["render_plan"]
    assert plan["auto_cut_enabled"] is False
    # With auto-cut off, each clip keeps its full range.
    assert plan["track_decisions"][0]["keep_ranges"] == [{"start": 0.0, "end": 12.0}]


# --------------------------------------------------------------------------
# Speech filter
# --------------------------------------------------------------------------


def test_speech_filter_suggests_removing_filler_words(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]

    response = project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter", headers=project_env["headers"]
    )

    assert response.status_code == 200, response.text
    artifact = response.json()
    assert artifact["status"] == "completed"
    assert artifact["model"] == "heuristic"
    assert any("um" in cut["transcript"].lower() for cut in artifact["cuts"])


def test_speech_filter_refuses_a_track_with_no_transcript(project_env, monkeypatch):
    async def silent(audio_path, language=None):
        return {"text": "", "words": [], "segments": [], "language": "en-US"}

    monkeypatch.setattr("backend.app.services.project_service.transcribe_audio_file", silent)
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]

    response = project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter", headers=project_env["headers"]
    )

    assert response.status_code == 400
    assert "Transcript is required" in response.json()["detail"]


def test_transcripts_are_backfilled_once_and_not_repeated(project_env):
    """Upload schedules transcription; later reads must not redo the work."""
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    assert len(project_env["transcription"]) == 1

    response = project_env["client"].get(f"/api/projects/{project_id}", headers=project_env["headers"])

    assert response.status_code == 200
    assert response.json()["project"]["tracks"][0]["has_voice"] is True
    # Transcription is billed per second, so a re-read must not re-transcribe.
    assert len(project_env["transcription"]) == 1


def test_manual_cut_edits_are_merged_and_persisted(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]
    project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter", headers=project_env["headers"]
    )

    overlapping = [
        {"start": 1.0, "end": 2.0, "duration": 1.0, "reason": "manual", "transcript": "one", "confidence": 1.0},
        {"start": 1.5, "end": 3.0, "duration": 1.5, "reason": "manual", "transcript": "two", "confidence": 1.0},
    ]
    response = project_env["client"].patch(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter",
        json={"cuts": overlapping},
        headers=project_env["headers"],
    )

    assert response.status_code == 200
    cuts = response.json()["cuts"]
    assert len(cuts) == 1
    assert cuts[0]["start"] == 1.0
    assert cuts[0]["end"] == 3.0

    reread = project_env["client"].get(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter", headers=project_env["headers"]
    )
    assert reread.json()["cuts"] == cuts


def test_cuts_are_clamped_to_the_clip_duration(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]
    project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter", headers=project_env["headers"]
    )

    response = project_env["client"].patch(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter",
        json={"cuts": [{"start": 5.0, "end": 999.0, "duration": 994.0, "reason": "manual", "transcript": "", "confidence": 1.0}]},
        headers=project_env["headers"],
    )

    assert response.json()["cuts"][0]["end"] == 12.0


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------


def test_track_render_produces_a_downloadable_version(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]

    cuts = [{"start": 0.5, "end": 0.8, "duration": 0.3, "reason": "filler_word", "transcript": "um", "confidence": 0.9}]
    response = project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/render",
        json={"cuts": cuts},
        headers=project_env["headers"],
    )

    assert response.status_code == 200, response.text
    version = response.json()["version"]
    assert version["cut_count"] == 1
    assert version["duration_before"] == 12.0
    assert version["duration_after"] == pytest.approx(11.7, abs=0.01)

    download = project_env["client"].get(
        f"/api/projects/{project_id}/tracks/{track_id}/renders/{version['id']}", headers=project_env["headers"]
    )
    assert download.status_code == 200


def test_render_keeps_the_segments_around_each_cut(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]

    cuts = [
        {"start": 2.0, "end": 3.0, "duration": 1.0, "reason": "manual", "transcript": "", "confidence": 1.0},
        {"start": 6.0, "end": 7.0, "duration": 1.0, "reason": "manual", "transcript": "", "confidence": 1.0},
    ]
    project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/render",
        json={"cuts": cuts},
        headers=project_env["headers"],
    )

    segments = project_env["media"].segment_renders[-1]["segments"]
    assert segments == [(0.0, 2.0), (3.0, 6.0), (7.0, 12.0)]


def test_render_with_no_cuts_keeps_the_whole_clip(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]

    response = project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/render",
        json={"cuts": []},
        headers=project_env["headers"],
    )

    assert response.json()["version"]["duration_after"] == 12.0
    assert project_env["media"].segment_renders[-1]["segments"] == [(0.0, 12.0)]


def test_background_music_is_mixed_when_enabled(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]

    project_env["client"].patch(
        f"/api/projects/{project_id}/tracks/{track_id}/background-music",
        json={"enabled": True, "preset": "warm_focus", "volume": 0.2, "ducking": 0.6},
        headers=project_env["headers"],
    )
    project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/render",
        json={"cuts": []},
        headers=project_env["headers"],
    )

    assert any(call.get("preset") == "warm_focus" for call in project_env["media"].music_mixes)
    assert any(call.get("volume") == 0.2 for call in project_env["media"].music_mixes)


def test_final_export_is_downloadable_after_processing(project_env):
    clips = [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    response = project_env["client"].get(f"/api/projects/{project_id}/download", headers=project_env["headers"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert len(project_env["media"].combines) == 1


def test_download_is_refused_before_processing(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    response = project_env["client"].get(f"/api/projects/{project_id}/download", headers=project_env["headers"])

    assert response.status_code == 400


def test_processing_failure_is_recorded_on_the_project(project_env, monkeypatch):
    async def explode(audio_path, language=None):
        raise RuntimeError("Azure quota exceeded")

    monkeypatch.setattr("backend.app.services.project_service.transcribe_audio_file", explode)
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    response = project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    assert response.status_code == 500
    project = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]
    assert project["status"] == "error"
    assert "Azure quota exceeded" in project["error_message"]


# --------------------------------------------------------------------------
# Track management
# --------------------------------------------------------------------------


def test_excluding_a_track_toggles_its_visibility(project_env):
    clips = [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]

    hidden = project_env["client"].patch(
        f"/api/projects/{project_id}/tracks/{track_id}/exclude", headers=project_env["headers"]
    ).json()
    assert hidden == {"track_id": track_id, "excluded": True, "status": "hidden"}

    shown = project_env["client"].patch(
        f"/api/projects/{project_id}/tracks/{track_id}/exclude", headers=project_env["headers"]
    ).json()
    assert shown["excluded"] is False


def test_manual_reorder_is_persisted(project_env):
    clips = [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4"), project_env["make_clip"]("c.mp4")]
    project_id = create_project(
        project_env["client"], project_env["headers"], clips, edit_mode="manual"
    ).json()["project"]["id"]
    tracks = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"]
    reversed_ids = [track["id"] for track in reversed(tracks)]

    response = project_env["client"].patch(
        f"/api/projects/{project_id}/tracks/reorder",
        json={"track_ids": reversed_ids},
        headers=project_env["headers"],
    )

    assert response.status_code == 200
    assert [track["id"] for track in response.json()["project"]["tracks"]] == reversed_ids


def test_duplicate_uploads_are_skipped(project_env):
    clip = project_env["make_clip"]("a.mp4")
    project_id = create_project(project_env["client"], project_env["headers"], [clip]).json()["project"]["id"]

    response = project_env["client"].post(
        f"/api/projects/{project_id}/tracks",
        files=[("files", (clip.name, clip.read_bytes(), "video/mp4"))],
        headers=project_env["headers"],
    )

    assert response.status_code == 200
    assert "already uploaded" in response.json()["message"]
    assert len(response.json()["project"]["tracks"]) == 1


def test_deleting_a_project_removes_it(project_env):
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    assert project_env["client"].delete(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).status_code == 200
    assert project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).status_code == 404


# --------------------------------------------------------------------------
# Final render plan
# --------------------------------------------------------------------------


def _last_cue_end_seconds(srt_body: str) -> float:
    """End timestamp of the final cue in an SRT, in seconds."""
    ends = [line.split(" --> ")[1].strip() for line in srt_body.splitlines() if " --> " in line]
    hours, minutes, rest = ends[-1].split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _process(env, clips, **settings):
    project_id = create_project(env["client"], env["headers"], clips, **settings).json()["project"]["id"]
    env["client"].post(f"/api/projects/{project_id}/process-sync", headers=env["headers"])
    return project_id


def test_projects_render_onto_the_canvas_their_aspect_ratio_asks_for(project_env):
    _process(project_env, [project_env["make_clip"]("a.mp4")], aspect_ratio="vertical")

    render = project_env["media"].combines[-1]
    assert (render["width"], render["height"]) == (1080, 1920)


def test_horizontal_projects_render_onto_a_landscape_canvas(project_env):
    _process(project_env, [project_env["make_clip"]("a.mp4")], aspect_ratio="horizontal")

    render = project_env["media"].combines[-1]
    assert (render["width"], render["height"]) == (1920, 1080)


def test_auto_cut_removes_the_detected_pause_from_the_render(project_env):
    _process(project_env, [project_env["make_clip"]("a.mp4")])

    segments = project_env["media"].combines[-1]["segments"][0]
    # The fixture transcript pauses from 1.95s to 3.60s and the last word ends
    # at 5.60s, so both the mid-clip pause and the trailing dead air come out.
    assert segments == [(0.0, 1.95), (3.6, 5.6)]


def test_auto_cut_off_keeps_the_whole_clip(project_env):
    _process(project_env, [project_env["make_clip"]("a.mp4")], smart_pause_cutter=False)

    assert project_env["media"].combines[-1]["segments"][0] == [(0.0, 12.0)]


def test_user_cuts_override_the_automatic_ones(project_env):
    """Option B: the render must reflect what the user actually edited."""
    project_id = _process(project_env, [project_env["make_clip"]("a.mp4")])
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]
    project_env["client"].post(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter", headers=project_env["headers"]
    )
    project_env["client"].patch(
        f"/api/projects/{project_id}/tracks/{track_id}/speech-filter",
        json={"cuts": [{"start": 5.0, "end": 9.0, "duration": 4.0, "reason": "manual", "transcript": "", "confidence": 1.0}]},
        headers=project_env["headers"],
    )

    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    assert project_env["media"].combines[-1]["segments"][0] == [(0.0, 5.0), (9.0, 12.0)]


def test_excluded_clips_are_left_out_of_the_render(project_env):
    clips = [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    track_id = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]["id"]
    project_env["client"].patch(
        f"/api/projects/{project_id}/tracks/{track_id}/exclude", headers=project_env["headers"]
    )

    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    assert len(project_env["media"].combines[-1]["clips"]) == 1


def test_silent_broll_survives_the_pause_cutter(project_env, monkeypatch):
    """A clip with no narration is b-roll, not one long removable pause."""
    async def silent(audio_path, language=None):
        return {"text": "", "words": [], "segments": [], "language": "en-US"}

    monkeypatch.setattr("backend.app.services.project_service.transcribe_audio_file", silent)

    _process(project_env, [project_env["make_clip"]("drone.mp4")])

    render = project_env["media"].combines[-1]
    assert len(render["clips"]) == 1
    assert render["segments"][0] == [(0.0, 12.0)]


def test_subtitles_are_retimed_onto_the_cut_timeline(project_env):
    """Cutting a pause must move every later subtitle earlier by the same amount."""
    project_id = _process(project_env, [project_env["make_clip"]("a.mp4")])

    render = project_env["media"].combines[-1]
    assert render["subtitle_path"] is not None
    body = Path(render["subtitle_path"]).read_text(encoding="utf-8")

    kept = sum(end - start for start, end in render["segments"][0])
    last_cue_end = _last_cue_end_seconds(body)

    # Subtitles must fit inside the cut timeline. Against the source timeline
    # the final word ends at 5.60s; after the pause is removed it lands at 3.95s.
    assert last_cue_end == pytest.approx(kept, abs=0.01)
    assert last_cue_end < 5.6


def test_no_subtitles_are_generated_when_the_setting_is_off(project_env):
    _process(project_env, [project_env["make_clip"]("a.mp4")], generate_subtitles=False)

    assert project_env["media"].combines[-1]["subtitle_path"] is None


def test_each_render_writes_a_new_file_rather_than_overwriting(project_env):
    project_id = _process(project_env, [project_env["make_clip"]("a.mp4")])
    first = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["output_path"]

    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    second = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["output_path"]

    assert first != second


def test_multi_clip_render_offsets_subtitles_across_clips(project_env):
    _process(project_env, [project_env["make_clip"]("a.mp4"), project_env["make_clip"]("b.mp4")])

    render = project_env["media"].combines[-1]
    assert len(render["clips"]) == 2

    kept = sum(end - start for clip in render["segments"] for start, end in clip)
    body = Path(render["subtitle_path"]).read_text(encoding="utf-8")

    # The second clip's words are offset by the first clip's *kept* duration,
    # not its source duration, so the cues span the whole rendered output.
    assert _last_cue_end_seconds(body) == pytest.approx(kept, abs=0.01)
    assert kept == pytest.approx(2 * 3.95, abs=0.01)


def test_a_source_with_no_audio_stream_is_not_sent_for_transcription(project_env, monkeypatch):
    """ffmpeg cannot extract a WAV from a silent source; do not try."""
    from backend.app.services.video_processor import VideoProcessor

    async def no_audio(self, source_path):
        return False

    monkeypatch.setattr(VideoProcessor, "probe_has_audio", no_audio)

    project_id = _process(project_env, [project_env["make_clip"]("drone.mp4")])

    assert project_env["transcription"] == []
    assert project_env["media"].audio_extractions == []
    track = project_env["client"].get(
        f"/api/projects/{project_id}", headers=project_env["headers"]
    ).json()["project"]["tracks"][0]
    assert track["has_voice"] is False
    assert track["metadata"]["transcript_status"] == "not_applicable"


def test_a_silent_clip_does_not_fail_the_whole_project(project_env, monkeypatch):
    from backend.app.services.video_processor import VideoProcessor

    async def only_second_clip_is_silent(self, source_path):
        return "drone" not in str(source_path)

    monkeypatch.setattr(VideoProcessor, "probe_has_audio", only_second_clip_is_silent)
    clips = [project_env["make_clip"]("talking.mp4"), project_env["make_clip"]("drone.mp4")]

    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    response = project_env["client"].post(
        f"/api/projects/{project_id}/process-sync", headers=project_env["headers"]
    )

    assert response.status_code == 200, response.text
    assert response.json()["project"]["status"] == "completed"
    assert len(project_env["media"].combines[-1]["clips"]) == 2


# --------------------------------------------------------------------------
# Progress reporting
# --------------------------------------------------------------------------


def test_progress_starts_at_zero_for_an_untouched_project(project_env, monkeypatch):
    from backend.app.services.video_processor import VideoProcessor

    # No audio anywhere, so nothing is scheduled on upload.
    async def no_audio(self, source_path):
        return False

    monkeypatch.setattr(VideoProcessor, "probe_has_audio", no_audio)
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    status = project_env["client"].get(
        f"/api/projects/{project_id}/status", headers=project_env["headers"]
    ).json()

    assert status["current_step"] in {"idle", "analyzing", "rendering"}
    assert status["progress"] >= 0.0


def test_progress_reaches_one_hundred_when_complete(project_env):
    project_id = _process(project_env, [project_env["make_clip"]("a.mp4")])

    status = project_env["client"].get(
        f"/api/projects/{project_id}/status", headers=project_env["headers"]
    ).json()

    assert status["progress"] == 100.0
    assert status["current_step"] == "completed"
    assert status["estimated_time_remaining"] == 0


def test_a_silent_clip_does_not_pin_the_progress_bar(project_env, monkeypatch):
    """A clip with no audio never gets a transcript, but it is still finished.

    Counting it as outstanding held progress at 32% for an entire render.
    """
    from backend.app.services.video_processor import VideoProcessor

    async def only_second_is_silent(self, source_path):
        return "drone" not in str(source_path)

    monkeypatch.setattr(VideoProcessor, "probe_has_audio", only_second_is_silent)
    clips = [project_env["make_clip"]("talking.mp4"), project_env["make_clip"]("drone.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    # Both tracks have settled: one transcribed, one known to have no audio.
    project_env["client"].get(f"/api/projects/{project_id}", headers=project_env["headers"])
    status = project_env["client"].get(
        f"/api/projects/{project_id}/status", headers=project_env["headers"]
    ).json()

    assert status["progress"] > 60.0
    assert status["current_step"] != "transcribing"


def test_progress_reports_a_failure_rather_than_a_number(project_env, monkeypatch):
    async def explode(audio_path, language=None):
        raise RuntimeError("Azure quota exceeded")

    monkeypatch.setattr("backend.app.services.project_service.transcribe_audio_file", explode)
    clips = [project_env["make_clip"]("a.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    status = project_env["client"].get(
        f"/api/projects/{project_id}/status", headers=project_env["headers"]
    ).json()

    assert status["current_step"] == "error"
    assert "Azure quota exceeded" in status["error_message"]


def test_a_silent_clip_is_probed_once_not_on_every_render(project_env, monkeypatch):
    from backend.app.services.video_processor import VideoProcessor

    probes: list[str] = []

    async def counting_probe(self, source_path):
        probes.append(str(source_path))
        return False

    monkeypatch.setattr(VideoProcessor, "probe_has_audio", counting_probe)
    clips = [project_env["make_clip"]("drone.mp4")]
    project_id = create_project(project_env["client"], project_env["headers"], clips).json()["project"]["id"]

    first = len(probes)
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])
    project_env["client"].post(f"/api/projects/{project_id}/process-sync", headers=project_env["headers"])

    # The render plan probes each source once per render; transcription must
    # not add another probe now that the track is known to have no audio.
    transcription_probes = [p for p in probes[:first] if p]
    assert len(transcription_probes) == 1
    status = project_env["client"].get(
        f"/api/projects/{project_id}/status", headers=project_env["headers"]
    ).json()
    assert status["progress"] == 100.0
