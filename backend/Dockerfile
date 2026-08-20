FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    UVICORN_RELOAD=false \
    AUTOCUT_DATA_DIR=/app/data

WORKDIR /app

# ffmpeg for media work; libssl/ca-certificates are required by the
# Azure Speech SDK's native library.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates libssl3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY app ./app
COPY main.py ./main.py

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
