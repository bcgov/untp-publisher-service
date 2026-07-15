"""Tests for /auth/secret and /auth/token."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import authentication
from config import settings


class _FakeMongo:
    def __init__(self, record):
        self.record = record
        self.replaced = None

    def find_one(self, collection, query):
        assert collection == "IssuerInstanceRecord"
        return self.record

    def replace(self, collection, query, document):
        self.replaced = (collection, query, document)


@pytest.fixture
def auth_app(monkeypatch):
    monkeypatch.setattr(settings, "TRACTION_API_KEY", "admin-test-key")
    monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-at-least-32-bytes!!")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    app = FastAPI()
    app.include_router(authentication.router)
    return app


def test_auth_secret_unknown_client_returns_404(auth_app, monkeypatch):
    monkeypatch.setattr(
        authentication, "MongoClient", lambda: _FakeMongo(None)
    )
    client = TestClient(auth_app)
    response = client.post(
        "/auth/secret",
        headers={"X-API-Key": "admin-test-key"},
        json={"client_id": "missing-issuer"},
    )
    assert response.status_code == 404
    assert "Unknown client_id" in response.json()["detail"]


def test_auth_token_unknown_client_returns_404(auth_app, monkeypatch):
    monkeypatch.setattr(
        authentication, "MongoClient", lambda: _FakeMongo(None)
    )
    client = TestClient(auth_app)
    response = client.post(
        "/auth/token",
        json={"client_id": "missing-issuer", "client_secret": "x"},
    )
    assert response.status_code == 404
    assert "Unknown client_id" in response.json()["detail"]


def test_auth_token_rejects_bad_secret(auth_app, monkeypatch):
    monkeypatch.setattr(
        authentication,
        "MongoClient",
        lambda: _FakeMongo({"id": "mines-act:cpo", "secret_hash": "abc"}),
    )
    client = TestClient(auth_app)
    response = client.post(
        "/auth/token",
        json={"client_id": "mines-act:cpo", "client_secret": "wrong"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid credentials"
