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
    assert "scenedetect==" in requirements

    assert "transformers" not in requirements
    assert "torch" not in requirements
    assert "clip-by-openai" not in requirements
    assert "moviepy" not in requirements


def test_backend_dockerfile_uses_ffmpeg_and_uvicorn_entrypoint():
    dockerfile = read("backend/Dockerfile")

    assert "FROM python:3.11-slim" in dockerfile
    assert "apt-get install -y --no-install-recommends ffmpeg" in dockerfile
    assert "COPY requirements.txt ./" in dockerfile
    assert "pip install -r requirements.txt" in dockerfile
    assert 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]' in dockerfile


def test_web_next_config_uses_standalone_output():
    next_config = read("web/next.config.mjs")
    assert 'output: "standalone"' in next_config


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
    assert "backend_uploads:/app/data/uploads" in compose
    assert "backend_outputs:/app/data/outputs" in compose

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
    assert "WHISPER_API_URL=" in env_example
    assert "OPENAI_API_KEY=" in env_example
