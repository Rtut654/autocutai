"""Auth/onboarding/payment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

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
        onboarding_completed=user.onboarding_completed,
        subscription_plan=user.subscription_plan,
        provider=user.provider,
    )


@router.post("/signup", response_model=AuthResponse)
async def signup(request: SignupRequest) -> AuthResponse:
    try:
        auth_service.signup(request)
        token = auth_service.login(LoginRequest(email=request.email, password=request.password))
        user = auth_service.get_user_by_token(token)
        if not user:
            raise ValueError("Failed to create token")
        return AuthResponse(access_token=token, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/google_login", response_model=AuthResponse)
async def google_login(request: OAuthLoginRequest) -> AuthResponse:
    if not (request.email or request.provider_user_id or request.id_token):
        raise HTTPException(status_code=400, detail="Missing Google identity payload")
    token = auth_service.social_login(
        "google",
        email=request.email,
        full_name=request.name,
        provider_user_id=request.provider_user_id,
    )
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=400, detail="Failed to create token")
    return AuthResponse(access_token=token, user_id=user.id)


@router.post("/apple_login", response_model=AuthResponse)
async def apple_login(request: OAuthLoginRequest) -> AuthResponse:
    if not (request.email or request.provider_user_id or request.id_token or request.code):
        raise HTTPException(status_code=400, detail="Missing Apple identity payload")
    token = auth_service.social_login(
        "apple",
        email=request.email,
        full_name=request.name,
        provider_user_id=request.provider_user_id,
    )
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=400, detail="Failed to create token")
    return AuthResponse(access_token=token, user_id=user.id)


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest) -> AuthResponse:
    try:
        token = auth_service.login(request)
        user = auth_service.get_user_by_token(token)
        if not user:
            raise ValueError("Invalid credentials")
        return AuthResponse(access_token=token, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


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
