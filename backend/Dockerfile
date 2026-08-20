FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    UVICORN_RELOAD=false \
    AUTOCUT_DATA_DIR=/app/data

WORKDIR /app

# ffmpeg for media work. The rest are runtime dependencies of the Azure
# Speech SDK's native libraries: OpenSSL 3 for transport, ALSA and libuuid
# for its audio extension. Without libasound the SDK import fails at runtime,
# not at build time, so it is easy to miss.
# libasound2 was renamed libasound2t64 after Debian bookworm; accept either.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        libssl3 \
        libuuid1 \
    && (apt-get install -y --no-install-recommends libasound2 \
        || apt-get install -y --no-install-recommends libasound2t64) \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Fail the build, not the first request, if the native SDK cannot load.
RUN python -c "import azure.cognitiveservices.speech as s; s.SpeechConfig(subscription='x'*32, region='westeurope')"

COPY app ./app
COPY main.py ./main.py

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
