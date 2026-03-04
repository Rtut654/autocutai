"""In-memory auth/onboarding/payment scaffolding."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Dict, Optional

from ..models.auth import AuthUser, LoginRequest, OnboardingData, SignupRequest


class AuthService:
    def __init__(self) -> None:
        self.users_by_email: Dict[str, AuthUser] = {}
        self.tokens_to_user_id: Dict[str, str] = {}
        self.users_by_id: Dict[str, AuthUser] = {}

    @staticmethod
    def _hash_password(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def signup(self, request: SignupRequest) -> AuthUser:
        email = request.email.lower().strip()
        if email in self.users_by_email:
            raise ValueError("User already exists")

        user = AuthUser(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=self._hash_password(request.password),
            full_name=request.full_name,
        )
        self.users_by_email[email] = user
        self.users_by_id[user.id] = user
        return user

    def login(self, request: LoginRequest) -> str:
        email = request.email.lower().strip()
        user = self.users_by_email.get(email)
        if not user:
            raise ValueError("Invalid credentials")

        if user.password_hash != self._hash_password(request.password):
            raise ValueError("Invalid credentials")

        token = secrets.token_urlsafe(32)
        self.tokens_to_user_id[token] = user.id
        return token

    def get_user_by_token(self, token: str) -> Optional[AuthUser]:
        user_id = self.tokens_to_user_id.get(token)
        if not user_id:
            return None
        return self.users_by_id.get(user_id)

    def update_onboarding(self, user_id: str, data: OnboardingData) -> AuthUser:
        user = self.users_by_id[user_id]
        user.onboarding = data
        user.onboarding_completed = True
        return user

    def set_subscription(self, user_id: str, plan: str) -> AuthUser:
        user = self.users_by_id[user_id]
        user.subscription_plan = plan  # type: ignore[assignment]
        return user


auth_service = AuthService()
