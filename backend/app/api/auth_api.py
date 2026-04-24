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
from ..services.auth_service import auth_service

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


@router.post("/signup", response_model=AuthResponse)
async def signup(request: SignupRequest, response: Response) -> AuthResponse:
    try:
        auth_service.signup(request)
        token = auth_service.login(LoginRequest(email=request.email, password=request.password))
        user = auth_service.get_user_by_token(token)
        if not user:
            raise ValueError("Failed to create token")
        access_token, refresh_token = auth_service.create_session(user.id)
        auth_service.logout(access_token=token)
        _set_session_cookies(response, access_token, refresh_token)
        return AuthResponse(access_token=access_token, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/google_login", response_model=AuthResponse)
async def google_login(request: OAuthLoginRequest, response: Response) -> AuthResponse:
    if not (request.email or request.provider_user_id or request.id_token):
        raise HTTPException(status_code=400, detail="Missing Google identity payload")
    token = auth_service.social_login(
        "google",
        email=request.email,
        full_name=request.name,
        picture=request.picture,
        provider_user_id=request.provider_user_id,
    )
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=400, detail="Failed to create token")
    access_token, refresh_token = auth_service.create_session(user.id)
    auth_service.logout(access_token=token)
    _set_session_cookies(response, access_token, refresh_token)
    return AuthResponse(access_token=access_token, user_id=user.id)


@router.post("/apple_login", response_model=AuthResponse)
async def apple_login(request: OAuthLoginRequest, response: Response) -> AuthResponse:
    if not (request.email or request.provider_user_id or request.id_token or request.code):
        raise HTTPException(status_code=400, detail="Missing Apple identity payload")
    token = auth_service.social_login(
        "apple",
        email=request.email,
        full_name=request.name,
        picture=request.picture,
        provider_user_id=request.provider_user_id,
    )
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=400, detail="Failed to create token")
    access_token, refresh_token = auth_service.create_session(user.id)
    auth_service.logout(access_token=token)
    _set_session_cookies(response, access_token, refresh_token)
    return AuthResponse(access_token=access_token, user_id=user.id)


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest, response: Response) -> AuthResponse:
    try:
        token = auth_service.login(request)
        user = auth_service.get_user_by_token(token)
        if not user:
            raise ValueError("Invalid credentials")
        access_token, refresh_token = auth_service.create_session(user.id)
        auth_service.logout(access_token=token)
        _set_session_cookies(response, access_token, refresh_token)
        return AuthResponse(access_token=access_token, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


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
    token = authorization.replace("Bearer", "").strip()
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    updated = auth_service.set_subscription(user.id, request.plan)
    return _me_response(updated)
