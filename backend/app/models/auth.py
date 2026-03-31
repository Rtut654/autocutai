"""Auth, onboarding, and payment models."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class OnboardingData(BaseModel):
    goal: Optional[str] = None
    niche: Optional[str] = None
    preferred_edit_style: Optional[str] = None


class PaymentStartRequest(BaseModel):
    plan: Literal["free", "pro_monthly", "pro_yearly"] = "pro_monthly"


class OAuthLoginRequest(BaseModel):
    code: Optional[str] = None
    id_token: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    provider_user_id: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None


class AuthUser(BaseModel):
    id: str
    email: str
    password_hash: str
    full_name: Optional[str] = None
    provider: Literal["email", "google", "apple"] = "email"
    provider_user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    onboarding_completed: bool = False
    onboarding: OnboardingData = Field(default_factory=OnboardingData)
    subscription_plan: Literal["free", "pro_monthly", "pro_yearly"] = "free"


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    onboarding_completed: bool
    subscription_plan: str
    provider: str = "email"


class PaymentStartResponse(BaseModel):
    checkout_url: str
    plan: str
    status: Literal["pending", "active"]
