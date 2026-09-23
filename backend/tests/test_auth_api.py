"""Authentication.

Social sign-in tests sign real RS256 tokens with a throwaway key and point the
verifier at it, so signature, audience, issuer and expiry checks all run for
real. Only the network fetch of the provider's public keys is replaced.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

GOOGLE_CLIENT = "test-ios-client.apps.googleusercontent.com"
APPLE_BUNDLE = "com.bestshotai.app"


def _keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


PROVIDER_PRIVATE, PROVIDER_PUBLIC = _keypair()
ATTACKER_PRIVATE, _ = _keypair()


def google_token(
    *,
    sub="google-sub-1",
    email="traveller@example.com",
    email_verified=True,
    aud=GOOGLE_CLIENT,
    iss="https://accounts.google.com",
    expires_in=600,
    key=None,
    **extra,
):
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + expires_in,
        **extra,
    }
    return jwt.encode(claims, key or PROVIDER_PRIVATE, algorithm="RS256")


def apple_token(*, sub="001234.apple.sub", email="hidden@privaterelay.appleid.com", aud=APPLE_BUNDLE, **kwargs):
    return google_token(
        sub=sub,
        email=email,
        email_verified="true",  # Apple sends booleans as strings
        aud=aud,
        iss="https://appleid.apple.com",
        **kwargs,
    )


@pytest.fixture
def verifier(monkeypatch):
    """Point the real verifier at our test key instead of Google/Apple."""
    from backend.app.services.identity_verification import identity_verifier

    monkeypatch.setattr(identity_verifier, "_google_key_resolver", lambda token: PROVIDER_PUBLIC)
    monkeypatch.setattr(identity_verifier, "_apple_key_resolver", lambda token: PROVIDER_PUBLIC)
    monkeypatch.setattr(identity_verifier, "_google_client_ids", [GOOGLE_CLIENT])
    monkeypatch.setattr(identity_verifier, "_apple_audiences", [APPLE_BUNDLE])
    return identity_verifier


def me(client, token):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


# --------------------------------------------------------------------------
# The vulnerability this replaces
# --------------------------------------------------------------------------


def test_google_login_without_a_token_is_refused(client, verifier):
    """Posting someone's email used to be enough to sign in as them."""
    response = client.post(
        "/api/auth/google_login",
        json={"email": "victim@example.com", "provider_user_id": "anything"},
    )

    assert response.status_code == 400


def test_apple_login_without_a_token_is_refused(client, verifier):
    response = client.post(
        "/api/auth/apple_login",
        json={"email": "victim@example.com", "provider_user_id": "anything", "code": "x"},
    )

    assert response.status_code == 400


def test_body_email_cannot_override_the_verified_one(client, verifier):
    response = client.post(
        "/api/auth/google_login",
        json={"id_token": google_token(email="me@example.com"), "email": "victim@example.com"},
    )

    assert response.status_code == 200
    assert me(client, response.json()["access_token"]).json()["email"] == "me@example.com"


def test_a_token_signed_by_someone_else_is_rejected(client, verifier):
    forged = google_token(key=ATTACKER_PRIVATE)

    response = client.post("/api/auth/google_login", json={"id_token": forged})

    assert response.status_code == 401


def test_a_token_for_a_different_app_is_rejected(client, verifier):
    response = client.post(
        "/api/auth/google_login",
        json={"id_token": google_token(aud="someone-elses-app.apps.googleusercontent.com")},
    )

    assert response.status_code == 401
    assert "different app" in response.json()["detail"]


def test_an_expired_token_is_rejected(client, verifier):
    response = client.post(
        "/api/auth/google_login",
        json={"id_token": google_token(expires_in=-3600)},
    )

    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


def test_a_token_from_the_wrong_issuer_is_rejected(client, verifier):
    response = client.post(
        "/api/auth/google_login",
        json={"id_token": google_token(iss="https://evil.example.com")},
    )

    assert response.status_code == 401


def test_a_google_token_is_not_accepted_as_apple(client, verifier):
    response = client.post("/api/auth/apple_login", json={"id_token": google_token(aud=APPLE_BUNDLE)})

    assert response.status_code == 401


def test_malformed_tokens_are_rejected(client, verifier):
    response = client.post("/api/auth/google_login", json={"id_token": "not-a-jwt"})

    assert response.status_code == 401


def test_google_sign_in_is_refused_when_no_client_id_is_configured(client, verifier, monkeypatch):
    monkeypatch.setattr(verifier, "_google_client_ids", [])

    response = client.post("/api/auth/google_login", json={"id_token": google_token()})

    assert response.status_code == 401
    assert "not configured" in response.json()["detail"]


def test_user_ids_are_never_taken_from_the_provider(client, verifier):
    """The provider subject used to become the user ID and a directory name."""
    token = google_token(sub="../../etc")

    response = client.post("/api/auth/google_login", json={"id_token": token})

    user_id = response.json()["user_id"]
    assert "/" not in user_id and ".." not in user_id
    assert len(user_id) == 36  # a UUID


# --------------------------------------------------------------------------
# Social sign-in behaviour
# --------------------------------------------------------------------------


def test_google_login_creates_an_account_and_sets_cookies(client, verifier):
    response = client.post(
        "/api/auth/google_login",
        json={"id_token": google_token(name="Traveller", picture="https://example.com/a.png")},
    )

    assert response.status_code == 200
    assert response.cookies.get("access_token")
    assert response.cookies.get("refresh_token")
    profile = me(client, response.json()["access_token"]).json()
    assert profile["email"] == "traveller@example.com"
    assert profile["full_name"] == "Traveller"
    assert profile["picture"] == "https://example.com/a.png"
    assert profile["provider"] == "google"


def test_signing_in_twice_returns_the_same_account(client, verifier):
    first = client.post("/api/auth/google_login", json={"id_token": google_token()}).json()["user_id"]
    second = client.post("/api/auth/google_login", json={"id_token": google_token()}).json()["user_id"]

    assert first == second


def test_the_account_follows_the_subject_when_the_email_changes(client, verifier):
    first = client.post("/api/auth/google_login", json={"id_token": google_token(email="old@example.com")})
    second = client.post("/api/auth/google_login", json={"id_token": google_token(email="new@example.com")})

    assert first.json()["user_id"] == second.json()["user_id"]


def test_a_verified_google_email_links_to_an_existing_password_account(client, verifier):
    signup = client.post(
        "/api/auth/signup", json={"email": "traveller@example.com", "password": "password123"}
    ).json()

    google = client.post("/api/auth/google_login", json={"id_token": google_token()}).json()

    assert google["user_id"] == signup["user_id"]


def test_an_unverified_email_does_not_take_over_an_existing_account(client, verifier):
    """Linking on an unverified address would hand the account to anyone."""
    signup = client.post(
        "/api/auth/signup", json={"email": "traveller@example.com", "password": "password123"}
    ).json()

    google = client.post(
        "/api/auth/google_login", json={"id_token": google_token(email_verified=False)}
    ).json()

    assert google["user_id"] != signup["user_id"]


def test_apple_login_accepts_the_display_name_from_the_app(client, verifier):
    response = client.post(
        "/api/auth/apple_login",
        json={"id_token": apple_token(), "name": "Ana Traveller"},
    )

    assert response.status_code == 200
    profile = me(client, response.json()["access_token"]).json()
    assert profile["full_name"] == "Ana Traveller"
    assert profile["provider"] == "apple"


def test_apple_relay_emails_are_kept(client, verifier):
    response = client.post("/api/auth/apple_login", json={"id_token": apple_token()})

    assert me(client, response.json()["access_token"]).json()["email"] == "hidden@privaterelay.appleid.com"


# --------------------------------------------------------------------------
# Passwords and sessions
# --------------------------------------------------------------------------


def test_passwords_are_stored_with_bcrypt(client, isolated_storage):
    client.post("/api/auth/signup", json={"email": "a@example.com", "password": "password123"})

    state = json.loads((isolated_storage.root / "auth_state.json").read_text())
    stored = state["users"][0]["password_hash"]

    assert stored.startswith("$2b$")
    assert "password123" not in json.dumps(state)


def test_wrong_password_is_rejected(client):
    client.post("/api/auth/signup", json={"email": "a@example.com", "password": "password123"})

    response = client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong-password"})

    assert response.status_code == 401


def test_email_login_is_case_insensitive(client):
    client.post("/api/auth/signup", json={"email": "Mixed@Example.com", "password": "password123"})

    response = client.post("/api/auth/login", json={"email": "mixed@example.com", "password": "password123"})

    assert response.status_code == 200


def test_legacy_sha256_passwords_still_work_and_are_upgraded(client, isolated_storage):
    """Accounts from before bcrypt keep working, and are rehashed on sign-in."""
    from backend.app.models.auth import AuthUser
    from backend.app.services.auth_service import auth_service

    legacy = AuthUser(
        id="legacy-user",
        email="legacy@example.com",
        password_hash=hashlib.sha256(b"password123").hexdigest(),
    )
    auth_service.users_by_id[legacy.id] = legacy
    auth_service.users_by_email[legacy.email] = legacy

    wrong = client.post("/api/auth/login", json={"email": "legacy@example.com", "password": "nope-nope"})
    right = client.post("/api/auth/login", json={"email": "legacy@example.com", "password": "password123"})

    assert wrong.status_code == 401
    assert right.status_code == 200
    assert auth_service.users_by_id["legacy-user"].password_hash.startswith("$2b$")
    again = client.post("/api/auth/login", json={"email": "legacy@example.com", "password": "password123"})
    assert again.status_code == 200


def test_tokens_are_not_stored_in_plain_text(client, isolated_storage):
    token = client.post(
        "/api/auth/signup", json={"email": "a@example.com", "password": "password123"}
    ).json()["access_token"]

    state_text = (isolated_storage.root / "auth_state.json").read_text()

    assert token not in state_text


def test_expired_access_tokens_stop_working(client, monkeypatch):
    from backend.app.services import auth_service as module

    token = client.post(
        "/api/auth/signup", json={"email": "a@example.com", "password": "password123"}
    ).json()["access_token"]
    assert me(client, token).status_code == 200

    real_now = module._now
    monkeypatch.setattr(module, "_now", lambda: real_now() + module.timedelta(days=31))

    assert me(client, token).status_code == 401


def test_sessions_from_before_expiry_existed_are_migrated(isolated_storage):
    """Old state files stored raw tokens with no expiry; they must keep working."""
    from backend.app.services.auth_service import AuthService

    state_path = isolated_storage.root / "legacy_state.json"
    state_path.write_text(
        json.dumps(
            {
                "users": [{"id": "u1", "email": "old@example.com", "password_hash": ""}],
                "tokens_to_user_id": {"raw-legacy-token": "u1"},
                "refresh_tokens_to_user_id": {},
                "users_by_provider_identity": {},
            }
        )
    )

    service = AuthService(state_file=state_path)

    assert service.get_user_by_token("raw-legacy-token").id == "u1"
    service._persist_state()
    assert "raw-legacy-token" not in state_path.read_text()


def test_refresh_rotates_the_session(client):
    signup = client.post(
        "/api/auth/signup",
        json={"email": "creator@example.com", "password": "password123", "full_name": "Creator"},
    )
    old_access = signup.cookies.get("access_token")
    old_refresh = signup.cookies.get("refresh_token")

    client.cookies.set("refresh_token", old_refresh)
    refresh = client.post("/api/auth/refresh")

    assert refresh.status_code == 200
    assert refresh.json()["access_token"] != old_access
    assert refresh.cookies.get("refresh_token") != old_refresh

    # A refresh token works once.
    client.cookies.set("refresh_token", old_refresh)
    assert client.post("/api/auth/refresh").status_code == 401


def test_logout_revokes_the_access_token(client):
    token = client.post(
        "/api/auth/signup", json={"email": "a@example.com", "password": "password123"}
    ).json()["access_token"]

    client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})

    assert me(client, token).status_code == 401


def test_deleting_an_account_revokes_its_sessions(client):
    token = client.post(
        "/api/auth/signup", json={"email": "a@example.com", "password": "password123"}
    ).json()["access_token"]

    assert client.delete("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert me(client, token).status_code == 401


# --------------------------------------------------------------------------
# Purchases
# --------------------------------------------------------------------------


def test_self_service_plan_activation_is_disabled(client, auth_headers):
    """Any signed-in user could previously grant themselves a paid plan."""
    response = client.post("/api/auth/payments/activate", json={"plan": "pro_yearly"}, headers=auth_headers)

    assert response.status_code == 403
    assert client.get("/api/auth/me", headers=auth_headers).json()["subscription_plan"] == "free"


def test_plan_activation_can_be_enabled_for_internal_testing(client, auth_headers, monkeypatch):
    monkeypatch.setenv("AUTOCUT_ALLOW_UNVERIFIED_PURCHASES", "true")

    response = client.post("/api/auth/payments/activate", json={"plan": "pro_yearly"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["subscription_plan"] == "pro_yearly"
