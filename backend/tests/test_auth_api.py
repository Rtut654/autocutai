from __future__ import annotations

from fastapi.testclient import TestClient


def test_google_login_persists_picture_and_sets_web_cookies(tmp_path):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()

    client = TestClient(app)

    login_response = client.post(
        "/api/auth/google_login",
        json={
            "email": "avatar@example.com",
            "name": "Avatar User",
            "picture": "https://example.com/avatar.png",
            "provider_user_id": "google-user-1",
        },
    )

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    assert login_response.cookies.get("access_token")
    assert login_response.cookies.get("refresh_token")

    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert me_response.status_code == 200
    payload = me_response.json()
    assert payload["picture"] == "https://example.com/avatar.png"
    assert payload["provider"] == "google"


def test_google_login_uses_provider_user_id_as_stable_user_id(tmp_path):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()

    client = TestClient(app)

    login_response = client.post(
        "/api/auth/google_login",
        json={
            "email": "stable@example.com",
            "name": "Stable User",
            "provider_user_id": "103385296343735012789",
        },
    )

    assert login_response.status_code == 200
    assert login_response.json()["user_id"] == "103385296343735012789"


def test_refresh_rotates_session_from_cookie(tmp_path):
    from backend.app.main import app
    from backend.app.services.auth_service import auth_service

    auth_service.configure(tmp_path / "auth.db")
    auth_service.reset_for_tests()

    client = TestClient(app)
    signup = client.post(
        "/api/auth/signup",
        json={
            "email": "creator@example.com",
            "password": "password123",
            "full_name": "Creator",
        },
    )

    assert signup.status_code == 200
    old_access = signup.cookies.get("access_token")
    old_refresh = signup.cookies.get("refresh_token")

    refresh = client.post("/api/auth/refresh", cookies={"refresh_token": old_refresh})

    assert refresh.status_code == 200
    assert refresh.json()["access_token"] != old_access
    assert refresh.cookies.get("refresh_token") != old_refresh
