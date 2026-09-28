"""Integration-style tests for DELETE /credentials/{id} (VCALM ``Delete a
Specific Credential``: https://www.w3.org/TR/vcalm-1.0/#delete-a-specific-credential).
"""

from __future__ import annotations

import copy
import time
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import credentials
from config import settings

ISSUER_ID = "did:web:registry.test:mines-act:chief-permitting-officer"
OTHER_ISSUER = "did:web:registry.test:other:issuer"

CREDENTIAL_ID = "stable-permit-q20"


class _Cursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def clone(self):
        return _Cursor(list(self._rows))

    def __iter__(self):
        return iter(self._rows)


class _DeleteMongo:
    def __init__(self):
        self.templates = [
            {
                "type": "BCMinesActPermitCredential",
                "version": "v1.1",
                "issuer": ISSUER_ID,
                "template": {"name": "stub"},
                "oca_bundle": {"type": "spec/capture_base/1.0", "attributes": {}},
            }
        ]
        self.credentials = [
            {
                "id": CREDENTIAL_ID,
                "type": "BCMinesActPermitCredential",
                "issuer": ISSUER_ID,
                "entity_id": "A0034771",
                "cardinality_id": "Q-20",
                "cardinality_hash": "zsomehash",
                "refresh": False,
                "revocation": False,
                "suspension": False,
                "vc": {"id": f"https://publisher.test/credentials/{CREDENTIAL_ID}"},
                "vc_jwt": "stub.jwt.value",
            }
        ]

    def find_one(self, collection, query):
        rows = {
            "CredentialRecord": self.credentials,
            "CredentialTemplateRecord": self.templates,
        }[collection]
        for record in rows:
            if all(record.get(key) == value for key, value in query.items()):
                return copy.deepcopy(record)
        return None

    def find(self, collection, query):
        assert collection == "CredentialRecord"
        matched = [
            copy.deepcopy(record)
            for record in self.credentials
            if all(record.get(key) == value for key, value in query.items())
        ]
        return _Cursor(matched)

    def delete(self, collection, query):
        assert collection == "CredentialRecord"
        self.credentials = [
            r
            for r in self.credentials
            if not all(r.get(key) == value for key, value in query.items())
        ]


@pytest.fixture
def delete_env(monkeypatch):
    monkeypatch.setattr(settings, "TRACTION_API_KEY", "admin-test-key")
    monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-at-least-32-bytes!!")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")

    mongo = _DeleteMongo()
    monkeypatch.setattr(credentials, "MongoClient", lambda: mongo)

    traction = MagicMock()
    monkeypatch.setattr(credentials, "TractionController", lambda: traction)

    app = FastAPI()
    app.include_router(credentials.router)
    return TestClient(app), mongo


def _token(client_id: str) -> str:
    return jwt.encode(
        {"client_id": client_id, "expires": int(time.time()) + 3600},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def test_admin_api_key_deletes_credential(delete_env):
    client, mongo = delete_env
    response = client.delete(
        f"/credentials/{CREDENTIAL_ID}",
        headers={"X-API-Key": "admin-test-key"},
    )
    assert response.status_code == 202
    assert response.content == b""
    assert mongo.credentials == []


def test_matching_issuer_jwt_deletes_credential(delete_env):
    client, mongo = delete_env
    response = client.delete(
        f"/credentials/{CREDENTIAL_ID}",
        headers={"Authorization": f"Bearer {_token(ISSUER_ID)}"},
    )
    assert response.status_code == 202
    assert mongo.credentials == []


def test_wrong_issuer_jwt_returns_403(delete_env):
    client, mongo = delete_env
    response = client.delete(
        f"/credentials/{CREDENTIAL_ID}",
        headers={"Authorization": f"Bearer {_token(OTHER_ISSUER)}"},
    )
    assert response.status_code == 403
    # Nothing was removed.
    assert len(mongo.credentials) == 1


def test_unknown_credential_id_returns_404(delete_env):
    client, _mongo = delete_env
    response = client.delete(
        "/credentials/does-not-exist",
        headers={"X-API-Key": "admin-test-key"},
    )
    assert response.status_code == 404


def test_deleted_credential_then_get_returns_404(delete_env):
    client, _mongo = delete_env
    delete_response = client.delete(
        f"/credentials/{CREDENTIAL_ID}",
        headers={"X-API-Key": "admin-test-key"},
    )
    assert delete_response.status_code == 202

    get_response = client.get(f"/credentials/{CREDENTIAL_ID}")
    assert get_response.status_code == 404


def test_record_issuer_field_takes_precedence_over_type_registration(delete_env):
    """Belt-and-braces: authorization is tied to the credential's own stored
    ``issuer`` (captured at publish time), not just its type's current
    registration. A JWT for the type's registered issuer must be rejected if
    this specific credential was actually issued by someone else."""
    client, mongo = delete_env
    mongo.credentials[0]["issuer"] = OTHER_ISSUER

    wrong_caller = _token(ISSUER_ID)
    response = client.delete(
        f"/credentials/{CREDENTIAL_ID}",
        headers={"Authorization": "Bearer " + wrong_caller},
    )
    assert response.status_code == 403
    assert len(mongo.credentials) == 1

    actual_issuer_caller = _token(OTHER_ISSUER)
    response = client.delete(
        f"/credentials/{CREDENTIAL_ID}",
        headers={"Authorization": "Bearer " + actual_issuer_caller},
    )
    assert response.status_code == 202
    assert mongo.credentials == []


def test_legacy_record_without_issuer_field_falls_back_to_type_registration(
    delete_env,
):
    """Records persisted before ``CredentialRecord.issuer`` existed have no
    stored issuer; authorization must fall back to the type's registration."""
    client, mongo = delete_env
    del mongo.credentials[0]["issuer"]

    token = _token(ISSUER_ID)
    response = client.delete(
        f"/credentials/{CREDENTIAL_ID}",
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status_code == 202
    assert mongo.credentials == []
