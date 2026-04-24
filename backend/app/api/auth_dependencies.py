"""Shared auth helpers for API routes."""

from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Header, HTTPException

from ..models.auth import AuthUser
from ..services.auth_service import auth_service


def extract_bearer_token(authorization: str = "") -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def get_current_user(
    authorization: str = Header(default=""),
    access_token: Optional[str] = Cookie(default=None),
) -> AuthUser:
    token = extract_bearer_token(authorization) or (access_token or "")
    user = auth_service.get_user_by_token(token, token_type="access")
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def get_optional_user(
    authorization: str = Header(default=""),
    access_token: Optional[str] = Cookie(default=None),
) -> Optional[AuthUser]:
    token = extract_bearer_token(authorization) or (access_token or "")
    if not token:
        return None
    return auth_service.get_user_by_token(token, token_type="access")
