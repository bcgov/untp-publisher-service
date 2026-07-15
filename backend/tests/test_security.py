"""Auth helpers: JWT Bearer and admin API key."""

import time

import jwt
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.security import jwt_or_api_key, verify_jwt
from config import settings


def _make_token(*, expires_in: int = 3600) -> str:
    payload = {"client_id": "test-issuer", "expires": int(time.time()) + expires_in}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setattr(settings, "TRACTION_API_KEY", "admin-test-key")
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(jwt_or_api_key)])
    async def protected():
        return {"ok": True}

    return TestClient(app)


def test_verify_jwt_accepts_valid_token():
    assert verify_jwt(_make_token()) is True


def test_verify_jwt_rejects_expired_token():
    assert verify_jwt(_make_token(expires_in=-10)) is False


def test_jwt_or_api_key_accepts_api_key(auth_client):
    response = auth_client.get("/protected", headers={"X-API-Key": "admin-test-key"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_jwt_or_api_key_rejects_bad_api_key(auth_client):
    response = auth_client.get("/protected", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API Key"


def test_jwt_or_api_key_accepts_bearer(auth_client):
    response = auth_client.get(
        "/protected",
        headers={"Authorization": f"Bearer {_make_token()}"},
    )
    assert response.status_code == 200


def test_jwt_or_api_key_rejects_bad_bearer(auth_client):
    response = auth_client.get(
        "/protected",
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert response.status_code == 403


def test_jwt_or_api_key_requires_one(auth_client):
    response = auth_client.get("/protected")
    assert response.status_code == 401
    assert "Missing authentication" in response.json()["detail"]


def test_jwt_or_api_key_prefers_api_key_when_both_present(auth_client):
    """Invalid API key fails even if a valid Bearer token is also sent."""
    response = auth_client.get(
        "/protected",
        headers={
            "X-API-Key": "wrong",
            "Authorization": f"Bearer {_make_token()}",
        },
    )
    assert response.status_code == 401


def test_jwt_or_api_key_rejects_token_without_client_id(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-at-least-32-bytes!!")
    token = jwt.encode(
        {"expires": int(time.time()) + 3600},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    response = auth_client.get(
        "/protected",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "client_id" in response.json()["detail"]
