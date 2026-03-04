"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.auth_api import router as auth_router
from .api.project_api import router as project_router
from .api.transcription_api import router as transcription_router

app = FastAPI(title="BestShotAI API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
