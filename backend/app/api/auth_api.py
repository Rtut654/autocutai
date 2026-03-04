"""Auth/onboarding/payment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from ..models.auth import (
    AuthResponse,
    LoginRequest,
    MeResponse,
    OnboardingData,
    PaymentStartRequest,
    PaymentStartResponse,
    SignupRequest,
)
from ..services.auth_service import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        onboarding_completed=user.onboarding_completed,
        subscription_plan=user.subscription_plan,
    )


@router.put("/onboarding", response_model=MeResponse)
async def onboarding(request: OnboardingData, authorization: str = Header(default="")) -> MeResponse:
    token = authorization.replace("Bearer", "").strip()
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    updated = auth_service.update_onboarding(user.id, request)
    return MeResponse(
        id=updated.id,
        email=updated.email,
        full_name=updated.full_name,
        onboarding_completed=updated.onboarding_completed,
        subscription_plan=updated.subscription_plan,
    )


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
    return MeResponse(
        id=updated.id,
        email=updated.email,
        full_name=updated.full_name,
        onboarding_completed=updated.onboarding_completed,
        subscription_plan=updated.subscription_plan,
    )
