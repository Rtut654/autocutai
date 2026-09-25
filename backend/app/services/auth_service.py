"""Accounts and sessions.

State lives in a JSON file. That is enough for a test deployment on one
server; it is not enough for more than one backend replica, which needs a
database.

Security properties this file is responsible for:
- Passwords are hashed with bcrypt. Accounts created before that used an
  unsalted SHA-256, and are upgraded to bcrypt the next time they sign in.
- Session tokens expire, and are stored hashed so the state file on its own
  cannot be used to impersonate anyone.
- Social accounts are keyed by the provider's verified subject. Internal user
  IDs are always generated here, never taken from the client.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import bcrypt

from ..models.auth import AuthUser, LoginRequest, OnboardingData, SignupRequest
from .identity_verification import VerifiedIdentity

LEGACY_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, state_file: Optional[Path] = None) -> None:
        backend_root = Path(__file__).resolve().parents[2]
        default_state = Path(os.getenv("AUTOCUT_AUTH_STATE_FILE") or backend_root / ".runtime" / "auth_state.json")
        self.state_file = Path(state_file) if state_file else default_state
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.users_by_email: Dict[str, AuthUser] = {}
        self.users_by_id: Dict[str, AuthUser] = {}
        self.users_by_provider_identity: Dict[str, str] = {}
        # digest(token) -> {"user_id": str, "expires_at": iso8601}
        self.access_tokens: Dict[str, Dict[str, str]] = {}
        self.refresh_tokens: Dict[str, Dict[str, str]] = {}
        self._load_state()

    # ------------------------------------------------------------------
    # Configuration and persistence
    # ------------------------------------------------------------------

    @property
    def access_ttl(self) -> timedelta:
        # Long-lived because the mobile client does not refresh yet; expiry
        # still bounds how long a leaked token is useful.
        return timedelta(days=_days_env("AUTOCUT_ACCESS_TOKEN_DAYS", 30))

    @property
    def refresh_ttl(self) -> timedelta:
        return timedelta(days=_days_env("AUTOCUT_REFRESH_TOKEN_DAYS", 90))

    def configure(self, state_file=None) -> None:
        """Point the service at a different state file.

        Tests use this so a run never writes session tokens into the
        repository's checked-in state file.
        """
        if state_file is None:
            return None
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._clear()
        self._load_state()
        return None

    def reset_for_tests(self) -> None:
        self._clear()
        self._persist_state()

    def _clear(self) -> None:
        self.users_by_email.clear()
        self.users_by_id.clear()
        self.users_by_provider_identity.clear()
        self.access_tokens.clear()
        self.refresh_tokens.clear()

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return

        for raw in payload.get("users", []):
            try:
                user = AuthUser(**raw)
            except Exception:
                continue
            self.users_by_id[user.id] = user
            self.users_by_email[user.email] = user

        self.users_by_provider_identity = {
            str(key): str(user_id)
            for key, user_id in (payload.get("users_by_provider_identity") or {}).items()
            if str(user_id) in self.users_by_id
        }
        self.access_tokens = self._load_tokens(payload, "access_tokens", "tokens_to_user_id", self.access_ttl)
        self.refresh_tokens = self._load_tokens(
            payload, "refresh_tokens", "refresh_tokens_to_user_id", self.refresh_ttl
        )

    def _load_tokens(self, payload: dict, key: str, legacy_key: str, ttl: timedelta) -> Dict[str, Dict[str, str]]:
        tokens: Dict[str, Dict[str, str]] = {}
        for digest, record in (payload.get(key) or {}).items():
            if isinstance(record, dict) and record.get("user_id") in self.users_by_id:
                tokens[str(digest)] = {"user_id": str(record["user_id"]), "expires_at": str(record["expires_at"])}

        # Earlier versions stored raw tokens with no expiry. Keep those
        # sessions working, but hash them and give them a normal lifetime.
        expires_at = (_now() + ttl).isoformat()
        for raw_token, user_id in (payload.get(legacy_key) or {}).items():
            if str(user_id) in self.users_by_id:
                tokens[_token_digest(str(raw_token))] = {"user_id": str(user_id), "expires_at": expires_at}
        return tokens

    def _persist_state(self) -> None:
        self._drop_expired()
        payload = {
            "users": [user.model_dump(mode="json") for user in self.users_by_id.values()],
            "users_by_provider_identity": self.users_by_provider_identity,
            "access_tokens": self.access_tokens,
            "refresh_tokens": self.refresh_tokens,
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    def _drop_expired(self) -> None:
        now = _now()
        for store in (self.access_tokens, self.refresh_tokens):
            for digest in [d for d, record in store.items() if self._expired(record, now)]:
                store.pop(digest, None)

    @staticmethod
    def _expired(record: Dict[str, str], now: Optional[datetime] = None) -> bool:
        try:
            expires_at = datetime.fromisoformat(record["expires_at"])
        except (KeyError, TypeError, ValueError):
            return True
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= (now or _now())

    # ------------------------------------------------------------------
    # Passwords
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")

    def _check_password(self, user: AuthUser, password: str) -> bool:
        stored = user.password_hash or ""
        if not stored:
            return False
        if LEGACY_SHA256.match(stored):
            candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
            if not secrets.compare_digest(candidate, stored):
                return False
            # Correct password on a legacy hash: upgrade it in place.
            user.password_hash = self._hash_password(password)
            self._persist_state()
            return True
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("ascii"))
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    def _issue(self, store: Dict[str, Dict[str, str]], user_id: str, ttl: timedelta) -> str:
        token = secrets.token_urlsafe(32)
        store[_token_digest(token)] = {"user_id": user_id, "expires_at": (_now() + ttl).isoformat()}
        return token

    def get_user_by_token(self, token: str, token_type: Optional[str] = None) -> Optional[AuthUser]:
        if not token:
            return None
        store = self.refresh_tokens if token_type == "refresh" else self.access_tokens
        record = store.get(_token_digest(token))
        if not record:
            return None
        if self._expired(record):
            store.pop(_token_digest(token), None)
            return None
        return self.users_by_id.get(record["user_id"])

    def create_session(self, user_id: str) -> Tuple[str, str]:
        access = self._issue(self.access_tokens, user_id, self.access_ttl)
        refresh = self._issue(self.refresh_tokens, user_id, self.refresh_ttl)
        self._persist_state()
        return access, refresh

    def refresh_session(self, refresh_token: str) -> Tuple[str, str, AuthUser]:
        user = self.get_user_by_token(refresh_token, token_type="refresh")
        if not user:
            raise ValueError("Invalid refresh token")
        # Rotate: a refresh token works once.
        self.refresh_tokens.pop(_token_digest(refresh_token), None)
        access_token, new_refresh_token = self.create_session(user.id)
        return access_token, new_refresh_token, user

    def logout(self, access_token: Optional[str] = None, refresh_token: Optional[str] = None) -> None:
        if access_token:
            self.access_tokens.pop(_token_digest(access_token), None)
        if refresh_token:
            self.refresh_tokens.pop(_token_digest(refresh_token), None)
        self._persist_state()

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

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
        self._persist_state()
        return user

    def authenticate(self, request: LoginRequest) -> AuthUser:
        """Check an email and password. Raises ValueError on any mismatch."""
        user = self.users_by_email.get(request.email.lower().strip())
        if not user or not self._check_password(user, request.password):
            raise ValueError("Invalid credentials")
        return user

    @staticmethod
    def _provider_key(provider: str, subject: Optional[str]) -> Optional[str]:
        return f"{provider}:{subject}" if subject else None

    def social_login(
        self,
        identity: VerifiedIdentity,
        *,
        display_name: Optional[str] = None,
        picture: Optional[str] = None,
    ) -> AuthUser:
        """Find or create the account for a verified provider identity.

        An existing email account is linked only when the provider vouches
        for the email (email_verified). Otherwise an unverified address could
        be used to take over someone's password account.
        """
        provider_key = self._provider_key(identity.provider, identity.subject)
        user: Optional[AuthUser] = None

        linked_id = self.users_by_provider_identity.get(provider_key or "")
        if linked_id:
            user = self.users_by_id.get(linked_id)

        if user is None and identity.email and identity.email_verified:
            user = self.users_by_email.get(identity.email)

        name = (identity.name or display_name or "").strip() or None
        avatar = identity.picture or picture

        if user is None:
            email = identity.email if (identity.email and identity.email not in self.users_by_email) else None
            user = AuthUser(
                id=str(uuid.uuid4()),
                email=email or f"{identity.provider}_{uuid.uuid4().hex}@users.autocutai.invalid",
                password_hash="",
                full_name=name,
                picture=avatar,
                provider=identity.provider,  # type: ignore[arg-type]
                provider_user_id=identity.subject,
            )
            self.users_by_id[user.id] = user
            self.users_by_email[user.email] = user
        else:
            if name and not user.full_name:
                user.full_name = name
            if avatar:
                user.picture = avatar
            if not user.provider_user_id:
                user.provider = identity.provider  # type: ignore[assignment]
                user.provider_user_id = identity.subject

        if provider_key:
            self.users_by_provider_identity[provider_key] = user.id
        self._persist_state()
        return user

    def update_onboarding(self, user_id: str, data: OnboardingData) -> AuthUser:
        user = self.users_by_id[user_id]
        user.onboarding = data
        user.onboarding_completed = True
        self._persist_state()
        return user

    def set_subscription(self, user_id: str, plan: str) -> AuthUser:
        user = self.users_by_id[user_id]
        user.subscription_plan = plan  # type: ignore[assignment]
        self._persist_state()
        return user

    def update_profile(self, user_id: str, *, full_name: Optional[str] = None) -> AuthUser:
        user = self.users_by_id[user_id]
        if full_name is not None:
            user.full_name = full_name.strip() or None
        self._persist_state()
        return user

    def delete_user(self, user_id: str) -> bool:
        user = self.users_by_id.pop(user_id, None)
        if not user:
            return False
        self.users_by_email.pop(user.email, None)
        self.users_by_provider_identity = {
            key: owner for key, owner in self.users_by_provider_identity.items() if owner != user_id
        }
        for store in (self.access_tokens, self.refresh_tokens):
            for digest in [d for d, record in store.items() if record.get("user_id") == user_id]:
                store.pop(digest, None)
        self._persist_state()
        return True


auth_service = AuthService()
