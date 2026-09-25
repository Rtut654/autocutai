"""Auth/onboarding/payment endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Response

from ..models.auth import (
    AuthResponse,
    LoginRequest,
    MeResponse,
    OAuthLoginRequest,
    OnboardingData,
    PaymentStartRequest,
    PaymentStartResponse,
    ProfileUpdateRequest,
    SignupRequest,
)
import os

from ..services.auth_service import auth_service
from ..services.identity_verification import IdentityVerificationError, identity_verifier

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _me_response(user) -> MeResponse:
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        picture=user.picture,
        onboarding_completed=user.onboarding_completed,
        subscription_plan=user.subscription_plan,
        provider=user.provider,
    )


def _set_session_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie("access_token", access_token, httponly=True, samesite="lax", path="/")
    response.set_cookie("refresh_token", refresh_token, httponly=True, samesite="lax", path="/")


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")


def _start_session(response: Response, user) -> AuthResponse:
    access_token, refresh_token = auth_service.create_session(user.id)
    _set_session_cookies(response, access_token, refresh_token)
    return AuthResponse(access_token=access_token, user_id=user.id)


@router.post("/signup", response_model=AuthResponse)
async def signup(request: SignupRequest, response: Response) -> AuthResponse:
    try:
        user = auth_service.signup(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _start_session(response, user)


@router.post("/google_login", response_model=AuthResponse)
async def google_login(request: OAuthLoginRequest, response: Response) -> AuthResponse:
    """Sign in with a Google ID token.

    Identity comes only from the verified token. `name` and `picture` in the
    body are used for display if the token lacks them, nothing more.
    """
    if not request.id_token:
        raise HTTPException(status_code=400, detail="Google sign-in needs an ID token.")
    try:
        identity = identity_verifier.verify_google(request.id_token)
    except IdentityVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = auth_service.social_login(identity, display_name=request.name, picture=request.picture)
    return _start_session(response, user)


@router.post("/apple_login", response_model=AuthResponse)
async def apple_login(request: OAuthLoginRequest, response: Response) -> AuthResponse:
    """Sign in with an Apple identity token.

    Apple puts the user's name in the app-side credential only, on first
    sign-in, so `name` from the body is accepted as a display name.
    """
    if not request.id_token:
        raise HTTPException(status_code=400, detail="Apple sign-in needs an identity token.")
    try:
        identity = identity_verifier.verify_apple(request.id_token)
    except IdentityVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = auth_service.social_login(identity, display_name=request.name)
    return _start_session(response, user)


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest, response: Response) -> AuthResponse:
    try:
        user = auth_service.authenticate(request)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return _start_session(response, user)


@router.post("/refresh", response_model=AuthResponse)
async def refresh_session(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None),
) -> AuthResponse:
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        access_token, new_refresh_token, user = auth_service.refresh_session(refresh_token)
    except ValueError as exc:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookies(response, access_token, new_refresh_token)
    return AuthResponse(access_token=access_token, user_id=user.id)


@router.post("/logout")
async def logout(
    response: Response,
    authorization: str = Header(default=""),
    refresh_token: Optional[str] = Cookie(default=None),
):
    access_token = authorization.replace("Bearer", "").strip() or None
    auth_service.logout(access_token=access_token, refresh_token=refresh_token)
    _clear_session_cookies(response)
    return {"message": "Logged out"}


@router.get("/me", response_model=MeResponse)
async def me(authorization: str = Header(default="")) -> MeResponse:
    token = authorization.replace("Bearer", "").strip()
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return _me_response(user)


@router.patch("/me", response_model=MeResponse)
async def update_me(request: ProfileUpdateRequest, authorization: str = Header(default="")) -> MeResponse:
    token = authorization.replace("Bearer", "").strip()
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    updated = auth_service.update_profile(user.id, full_name=request.full_name)
    return _me_response(updated)


@router.delete("/me")
async def delete_me(authorization: str = Header(default="")):
    token = authorization.replace("Bearer", "").strip()
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    auth_service.delete_user(user.id)
    return {"message": "Account deleted"}


@router.put("/onboarding", response_model=MeResponse)
async def onboarding(request: OnboardingData, authorization: str = Header(default="")) -> MeResponse:
    token = authorization.replace("Bearer", "").strip()
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    updated = auth_service.update_onboarding(user.id, request)
    return _me_response(updated)


@router.post("/payments/start", response_model=PaymentStartResponse)
async def start_payment(request: PaymentStartRequest, authorization: str = Header(default="")) -> PaymentStartResponse:
    token = authorization.replace("Bearer", "").strip()
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    checkout = f"https://checkout.example.com/{request.plan}?uid={user.id}"
    return PaymentStartResponse(checkout_url=checkout, plan=request.plan, status="pending")


@router.post("/payments/activate", response_model=MeResponse)
async def activate_payment(request: PaymentStartRequest, authorization: str = Header(default="")) -> MeResponse:
    """Grant a plan. Disabled unless explicitly enabled for internal testing.

    There is no receipt or webhook verification behind this, so leaving it on
    lets any signed-in user give themselves a paid plan. It returns 403 until
    App Store / Stripe verification replaces it.
    """
    token = authorization.replace("Bearer", "").strip()
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if os.getenv("AUTOCUT_ALLOW_UNVERIFIED_PURCHASES", "").lower() != "true":
        raise HTTPException(
            status_code=403,
            detail="Purchases are not available yet. Every feature is free during testing.",
        )

    updated = auth_service.set_subscription(user.id, request.plan)
    return _me_response(updated)
