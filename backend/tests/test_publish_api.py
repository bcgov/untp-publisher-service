"""Integration-style tests for POST /credentials/publish (skip / re-issue / auth)."""

from __future__ import annotations

import copy
import hashlib
import time
from unittest.mock import MagicMock

import jwt
import pytest
from base58 import b58encode
from canonicaljson import encode_canonical_json
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.plugins.mongodb import MongoClientError
from app.routers import credentials
from app.security import AuthPrincipal
from config import settings

ISSUER_ID = "did:web:registry.test:mines-act:chief-permitting-officer"
OTHER_ISSUER = "did:web:registry.test:other:issuer"

SAMPLE_DATA = {
    "permit": {
        "issuanceDate": "1999-04-19",
        "identifier": "Q-20",
    },
    "permittee": {
        "name": "EXAMPLE MINING CO",
        "identifier": "A0034771",
    },
    "mine": {
        "name": "Kootenay West",
        "identifier": "0500956",
        "infoPageId": "5fa1e3ec4635c865df00c420",
        "locationInformation": "https://plus.codes/9526679P+4V",
    },
    "commodities": [{"name": "Construction Aggregate"}],
}


def _hash_for(*, template: str, version: str, data: dict) -> str:
    digest = hashlib.sha256(
        encode_canonical_json(
            {"template": template, "version": version, "data": data}
        )
    ).digest()
    return f"z{b58encode(digest).decode()}"


class _Cursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def clone(self):
        return _Cursor(list(self._rows))

    def __iter__(self):
        return iter(self._rows)


class _PublishMongo:
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
        self.issuers = {
            ISSUER_ID: {"id": ISSUER_ID, "name": "Chief Permitting Officer"},
        }
        self.credentials: list[dict] = []
        self._status_i = 0

    def find_one(self, collection, query):
        rows = {
            "CredentialTemplateRecord": self.templates,
            "IssuerInstanceRecord": list(self.issuers.values()),
            "CredentialRecord": self.credentials,
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

    def insert(self, collection, item):
        assert collection == "CredentialRecord"
        if any(r.get("id") == item.get("id") for r in self.credentials):
            raise MongoClientError()
        self.credentials.append(copy.deepcopy(item))

    def replace(self, collection, query, new_item):
        assert collection == "CredentialRecord"
        for i, record in enumerate(self.credentials):
            if all(record.get(key) == value for key, value in query.items()):
                self.credentials[i] = copy.deepcopy(new_item)
                return
        raise AssertionError(f"replace miss: {query}")

    def delete(self, collection, query):
        assert collection == "CredentialRecord"
        self.credentials = [
            r
            for r in self.credentials
            if not all(r.get(key) == value for key, value in query.items())
        ]

    def claim_status_list_index(self, *, issuer_id: str, purpose: str):
        self._status_i += 1
        return {
            "index": self._status_i,
            "endpoint": f"https://publisher.test/status/{purpose}",
            "id": f"list-{purpose}",
        }


@pytest.fixture
def publish_env(monkeypatch):
    monkeypatch.setattr(settings, "TRACTION_API_KEY", "admin-test-key")
    monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-at-least-32-bytes!!")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")

    mongo = _PublishMongo()
    monkeypatch.setattr(credentials, "MongoClient", lambda: mongo)
    monkeypatch.setattr(
        "app.services.coordinator.MongoClient",
        lambda: mongo,
    )

    traction = MagicMock()
    traction.authorize = MagicMock()
    traction.issue_vc = MagicMock(
        side_effect=lambda credential: {
            **credential,
            "proof": {"type": "DataIntegrityProof"},
        }
    )
    traction.sign_vc_jwt = MagicMock(return_value="eyJhbGciOiJFZERTQSJ9.e30.sig")
    monkeypatch.setattr(credentials, "TractionController", lambda: traction)

    app = FastAPI()
    app.include_router(credentials.router)
    return TestClient(app), mongo, traction


def _token(client_id: str) -> str:
    return jwt.encode(
        {"client_id": client_id, "expires": int(time.time()) + 3600},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def _publish_body(**overrides):
    body = {
        "template": "BCMinesActPermitCredential",
        "version": "v1.1",
        "data": copy.deepcopy(SAMPLE_DATA),
    }
    body.update(overrides)
    return body


def test_publish_first_issue_with_api_key(publish_env):
    client, mongo, traction = publish_env
    response = client.post(
        "/credentials/publish",
        headers={"X-API-Key": "admin-test-key"},
        json=_publish_body(credentialId="stable-permit-q20"),
    )
    assert response.status_code == 201
    assert response.json()["credentialId"].endswith("/credentials/stable-permit-q20")
    assert len(mongo.credentials) == 1
    assert mongo.credentials[0]["refresh"] is False
    traction.issue_vc.assert_called_once()


def test_publish_skip_when_unchanged(publish_env):
    client, mongo, traction = publish_env
    body = _publish_body(credentialId="stable-permit-q20")
    assert (
        client.post(
            "/credentials/publish",
            headers={"X-API-Key": "admin-test-key"},
            json=body,
        ).status_code
        == 201
    )
    traction.issue_vc.reset_mock()

    response = client.post(
        "/credentials/publish",
        headers={"X-API-Key": "admin-test-key"},
        json=body,
    )
    assert response.status_code == 200
    assert response.json()["credentialId"].endswith("/credentials/stable-permit-q20")
    assert len(mongo.credentials) == 1
    traction.issue_vc.assert_not_called()


def test_publish_reissue_on_data_change_reclaims_credential_id(publish_env):
    client, mongo, traction = publish_env
    body = _publish_body(credentialId="stable-permit-q20")
    assert (
        client.post(
            "/credentials/publish",
            headers={"X-API-Key": "admin-test-key"},
            json=body,
        ).status_code
        == 201
    )

    changed = _publish_body(credentialId="stable-permit-q20")
    changed["data"]["permittee"]["name"] = "UPDATED MINING CO"
    response = client.post(
        "/credentials/publish",
        headers={"X-API-Key": "admin-test-key"},
        json=changed,
    )
    assert response.status_code == 201
    assert response.json()["credentialId"].endswith("/credentials/stable-permit-q20")
    assert len(mongo.credentials) == 1
    assert mongo.credentials[0]["refresh"] is False
    assert mongo.credentials[0]["cardinality_hash"] == _hash_for(
        template="BCMinesActPermitCredential",
        version="v1.1",
        data=changed["data"],
    )
    assert traction.issue_vc.call_count == 2


def test_publish_reject_foreign_credential_id(publish_env):
    client, mongo, _traction = publish_env
    mongo.credentials.append(
        {
            "id": "taken-id",
            "type": "BCMinesActPermitCredential",
            "entity_id": "OTHER",
            "cardinality_id": "OTHER-PERMIT",
            "cardinality_hash": "zother",
            "refresh": False,
            "vc": {"id": "https://publisher.test/credentials/taken-id"},
            "vc_jwt": "x",
        }
    )
    response = client.post(
        "/credentials/publish",
        headers={"X-API-Key": "admin-test-key"},
        json=_publish_body(credentialId="taken-id"),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "credentialId already exists"


def test_publish_jwt_must_match_issuer(publish_env):
    client, _mongo, _traction = publish_env
    ok = client.post(
        "/credentials/publish",
        headers={"Authorization": f"Bearer {_token(ISSUER_ID)}"},
        json=_publish_body(),
    )
    assert ok.status_code == 201

    denied = client.post(
        "/credentials/publish",
        headers={"Authorization": f"Bearer {_token(OTHER_ISSUER)}"},
        json=_publish_body(),
    )
    assert denied.status_code == 403
    assert "not authorized" in denied.json()["detail"]


def test_publish_no_change_missing_record_returns_409(publish_env, monkeypatch):
    client, mongo, _traction = publish_env
    body = _publish_body(credentialId="stable-permit-q20")
    assert (
        client.post(
            "/credentials/publish",
            headers={"X-API-Key": "admin-test-key"},
            json=body,
        ).status_code
        == 201
    )

    async def _noop_change(_self, options):
        return None

    monkeypatch.setattr(
        "app.services.coordinator.PublisherCoordinator.check_cardinality",
        _noop_change,
    )
    mongo.credentials.clear()

    response = client.post(
        "/credentials/publish",
        headers={"X-API-Key": "admin-test-key"},
        json=body,
    )
    assert response.status_code == 409
    assert "concurrently" in response.json()["detail"]


def test_auth_principal_api_key_has_no_client_id():
    principal = AuthPrincipal(via="api_key")
    assert principal.client_id is None
