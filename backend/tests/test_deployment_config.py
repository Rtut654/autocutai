from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_root_requirements_delegate_to_backend_requirements():
    assert read("requirements.txt").strip() == "-r backend/requirements.txt"


def test_backend_requirements_stay_slim_for_server_deploy():
    requirements = read("backend/requirements.txt")

    assert "fastapi==" in requirements
    assert "uvicorn==" in requirements
    assert "aiofiles==" in requirements
    assert "openai==" in requirements
    assert "azure-cognitiveservices-speech==" in requirements

    # The legacy pipeline pulled in heavyweight ML dependencies that were
    # never actually installed at runtime. They must not come back silently.
    assert "transformers" not in requirements
    assert "torch" not in requirements
    assert "clip-by-openai" not in requirements
    assert "moviepy" not in requirements
    assert "scenedetect" not in requirements


def test_backend_dockerfile_uses_ffmpeg_and_uvicorn_entrypoint():
    dockerfile = read("backend/Dockerfile")

    assert "FROM python:3.11-slim" in dockerfile
    assert "ffmpeg" in dockerfile
    # Runtime dependencies of the Azure Speech SDK's native libraries. Missing
    # these fails at first request, not at build time.
    for package in ("libssl3", "libuuid1", "libasound2"):
        assert package in dockerfile
    assert "COPY requirements.txt ./" in dockerfile
    assert "pip install -r requirements.txt" in dockerfile
    assert 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]' in dockerfile


def test_web_next_config_uses_standalone_output_for_production_builds():
    next_config = read("web/next.config.mjs")
    assert 'output: isDev ? undefined : "standalone"' in next_config


def test_web_dockerfile_runs_next_standalone_server():
    dockerfile = read("web/Dockerfile")

    assert "FROM node:20-bookworm-slim AS deps" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile
    assert "COPY --from=builder /app/.next/standalone ./" in dockerfile
    assert 'CMD ["node", "server.js"]' in dockerfile


def test_compose_wires_backend_and_web_services():
    compose = read("docker-compose.yml")

    assert "backend:" in compose
    assert "context: ./backend" in compose
    assert '- "${BACKEND_PORT:-8000}:8000"' in compose
    assert "backend_data:/app/data" in compose
    assert "AUTOCUT_DATA_DIR: /app/data" in compose

    assert "web:" in compose
    assert "context: ./web" in compose
    assert '- "${WEB_PORT:-3000}:3000"' in compose
    assert "condition: service_healthy" in compose
    assert "NEXT_PUBLIC_API_BASE_URL" in compose


def test_docker_env_template_exposes_required_server_settings():
    env_example = read(".env.docker.example")

    assert "BACKEND_PORT=8000" in env_example
    assert "WEB_PORT=3000" in env_example
    assert "NEXT_PUBLIC_API_BASE_URL=" in env_example
    assert "AZURE_SPEECH_KEY=" in env_example
    assert "AZURE_SPEECH_REGION=" in env_example
    assert "OPENAI_API_KEY=" in env_example


def test_backend_env_template_documents_azure_and_storage():
    env_example = read("backend/.env.example")

    assert "AZURE_SPEECH_KEY=" in env_example
    assert "AZURE_SPEECH_LANGUAGE=" in env_example
    assert "AUTOCUT_DATA_DIR=" in env_example


def test_storage_root_is_configurable_rather_than_derived_from_the_source_tree(monkeypatch, tmp_path):
    """A container must be able to point storage at a mounted volume."""
    from backend.app.services.project_service import ProjectService

    monkeypatch.setenv("AUTOCUT_DATA_DIR", str(tmp_path))
    service = ProjectService()

    assert service.projects_dir == (tmp_path / "projects").resolve()
    assert service.temp_dir == (tmp_path / "temp").resolve()
    assert service.projects_dir.exists()


def test_no_third_party_transcription_endpoint_remains():
    """Audio must go to Azure, not to an unowned public endpoint."""
    for path in ["backend/.env.example", ".env.docker.example", "docker-compose.yml"]:
        assert "testsucceed" not in read(path)
        assert "WHISPER" not in read(path)
