"""FastAPI application entrypoint."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.auth_api import router as auth_router
from .api.project_api import router as project_router
from .api.transcription_api import router as transcription_router
from .api.pipeline_api import router as pipeline_router

app = FastAPI(title="BestShotAI API", version="0.1.0")

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "BestShotAI backend is running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


app.include_router(auth_router)
app.include_router(transcription_router)
app.include_router(project_router)
app.include_router(pipeline_router)
