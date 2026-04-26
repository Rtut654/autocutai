"""In-memory auth/onboarding/payment scaffolding."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Dict, Optional, Tuple

from ..models.auth import AuthUser, LoginRequest, OnboardingData, SignupRequest


class AuthService:
    def __init__(self) -> None:
        self.users_by_email: Dict[str, AuthUser] = {}
        self.tokens_to_user_id: Dict[str, str] = {}
        self.refresh_tokens_to_user_id: Dict[str, str] = {}
        self.users_by_id: Dict[str, AuthUser] = {}
        self.users_by_provider_identity: Dict[str, str] = {}

    def configure(self, _db_path=None) -> None:
        """Compatibility no-op for older tests and startup paths."""
        return None

    def reset_for_tests(self) -> None:
        self.users_by_email.clear()
        self.tokens_to_user_id.clear()
        self.refresh_tokens_to_user_id.clear()
        self.users_by_id.clear()
        self.users_by_provider_identity.clear()

    @staticmethod
    def _hash_password(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    @staticmethod
    def _provider_key(provider: str, provider_user_id: Optional[str]) -> Optional[str]:
        if not provider_user_id:
            return None
        return f"{provider}:{provider_user_id}"

    @staticmethod
    def _preferred_social_user_id(provider: str, provider_user_id: Optional[str]) -> Optional[str]:
        if not provider_user_id:
            return None
        if provider in {"google", "apple"}:
            return provider_user_id
        return None

    def _issue_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self.tokens_to_user_id[token] = user_id
        return token

    def _issue_refresh_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self.refresh_tokens_to_user_id[token] = user_id
        return token

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

        return self._issue_token(user.id)

    def social_login(
        self,
        provider: str,
        email: Optional[str] = None,
        full_name: Optional[str] = None,
        picture: Optional[str] = None,
        provider_user_id: Optional[str] = None,
    ) -> str:
        normalized_email = email.lower().strip() if email else ""
        user: Optional[AuthUser] = None
        provider_key = self._provider_key(provider, provider_user_id)
        preferred_user_id = self._preferred_social_user_id(provider, provider_user_id)
        if provider_key:
            existing_user_id = self.users_by_provider_identity.get(provider_key)
            if existing_user_id:
                user = self.users_by_id.get(existing_user_id)

        if user is None and normalized_email:
            user = self.users_by_email.get(normalized_email)

        if user is None:
            fallback_email = normalized_email or f"{provider}_{provider_user_id or uuid.uuid4().hex}@local.bestshotai"
            user = AuthUser(
                id=preferred_user_id or str(uuid.uuid4()),
                email=fallback_email,
                password_hash="",
                full_name=full_name,
                picture=picture,
                provider=provider,  # type: ignore[arg-type]
                provider_user_id=provider_user_id,
            )
            self.users_by_id[user.id] = user
            self.users_by_email[user.email] = user
        else:
            if full_name and not user.full_name:
                user.full_name = full_name
            if picture:
                user.picture = picture
            if normalized_email and user.email != normalized_email and user.email.endswith("@local.bestshotai"):
                self.users_by_email.pop(user.email, None)
                user.email = normalized_email
                self.users_by_email[user.email] = user
            user.provider = provider  # type: ignore[assignment]
            if provider_user_id:
                user.provider_user_id = provider_user_id

        if provider_key:
            self.users_by_provider_identity[provider_key] = user.id
        return self._issue_token(user.id)

    def get_user_by_token(self, token: str, token_type: Optional[str] = None) -> Optional[AuthUser]:
        token_map = self.refresh_tokens_to_user_id if token_type == "refresh" else self.tokens_to_user_id
        user_id = token_map.get(token)
        if not user_id:
            return None
        return self.users_by_id.get(user_id)

    def create_session(self, user_id: str) -> Tuple[str, str]:
        return self._issue_token(user_id), self._issue_refresh_token(user_id)

    def refresh_session(self, refresh_token: str) -> Tuple[str, str, AuthUser]:
        user = self.get_user_by_token(refresh_token, token_type="refresh")
        if not user:
            raise ValueError("Invalid refresh token")
        self.refresh_tokens_to_user_id.pop(refresh_token, None)
        access_token, new_refresh_token = self.create_session(user.id)
        return access_token, new_refresh_token, user

    def logout(self, access_token: Optional[str] = None, refresh_token: Optional[str] = None) -> None:
        if access_token:
            self.tokens_to_user_id.pop(access_token, None)
        if refresh_token:
            self.refresh_tokens_to_user_id.pop(refresh_token, None)

    def update_onboarding(self, user_id: str, data: OnboardingData) -> AuthUser:
        user = self.users_by_id[user_id]
        user.onboarding = data
        user.onboarding_completed = True
        return user

    def set_subscription(self, user_id: str, plan: str) -> AuthUser:
        user = self.users_by_id[user_id]
        user.subscription_plan = plan  # type: ignore[assignment]
        return user

    def update_profile(self, user_id: str, *, full_name: Optional[str] = None) -> AuthUser:
        user = self.users_by_id[user_id]
        if full_name is not None:
            user.full_name = full_name.strip() or None
        return user

    def delete_user(self, user_id: str) -> bool:
        user = self.users_by_id.pop(user_id, None)
        if not user:
            return False
        self.users_by_email.pop(user.email, None)
        provider_key = self._provider_key(user.provider, user.provider_user_id)
        if provider_key:
            self.users_by_provider_identity.pop(provider_key, None)
        self.tokens_to_user_id = {
            token: existing_user_id
            for token, existing_user_id in self.tokens_to_user_id.items()
            if existing_user_id != user_id
        }
        self.refresh_tokens_to_user_id = {
            token: existing_user_id
            for token, existing_user_id in self.refresh_tokens_to_user_id.items()
            if existing_user_id != user_id
        }
        return True


auth_service = AuthService()
