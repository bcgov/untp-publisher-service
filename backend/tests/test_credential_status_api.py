"""Integration-style tests for POST /credentials/status (revoke / un-revoke)."""

from __future__ import annotations

import copy
import time
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.plugins.mongodb import MongoClientError
from app.routers import credentials
from config import settings

ISSUER_ID = "did:web:registry.test:mines-act:chief-permitting-officer"
OTHER_ISSUER = "did:web:registry.test:other:issuer"

STATUS_ENDPOINT = "https://publisher.test/status-lists/list-revocation"

CREDENTIAL_ID = "stable-permit-q20"


class _Cursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def clone(self):
        return _Cursor(list(self._rows))

    def __iter__(self):
        return iter(self._rows)


class _StatusMongo:
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
        self.status_lists = [
            {
                "id": "list-revocation",
                "issuer": ISSUER_ID,
                "purpose": "revocation",
                "active": True,
                "endpoint": STATUS_ENDPOINT,
                "indexes": [],
                "credential": {
                    "credentialSubject": {
                        "type": "BitstringStatusList",
                        "statusPurpose": "revocation",
                        "encodedList": self._encoded(64),
                    }
                },
            }
        ]
        self.credentials: list[dict] = [
            {
                "id": CREDENTIAL_ID,
                "type": "BCMinesActPermitCredential",
                "entity_id": "A0034771",
                "cardinality_id": "Q-20",
                "cardinality_hash": "zsomehash",
                "refresh": False,
                "revocation": False,
                "suspension": False,
                "vc": {
                    "id": f"https://publisher.test/credentials/{CREDENTIAL_ID}",
                    "credentialStatus": {
                        "statusPurpose": "revocation",
                        "statusListIndex": 42,
                        "statusListCredential": STATUS_ENDPOINT,
                    },
                },
                "vc_jwt": "x",
            }
        ]
        self.status_bit_updates: list[dict] = []

    @staticmethod
    def _encoded(length: int) -> str:
        from app.plugins.status_list import BitstringStatusList

        return BitstringStatusList().generate("0" * length)

    def find_one(self, collection, query):
        rows = {
            "CredentialTemplateRecord": self.templates,
            "IssuerInstanceRecord": list(self.issuers.values()),
            "CredentialRecord": self.credentials,
            "StatusListRecord": self.status_lists,
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
        rows = {
            "CredentialRecord": self.credentials,
            "StatusListRecord": self.status_lists,
        }[collection]
        for i, record in enumerate(rows):
            if all(record.get(key) == value for key, value in query.items()):
                rows[i] = copy.deepcopy(new_item)
                return
        raise AssertionError(f"replace miss: {query}")

    def delete(self, collection, query):
        assert collection == "CredentialRecord"
        self.credentials = [
            r
            for r in self.credentials
            if not all(r.get(key) == value for key, value in query.items())
        ]

    def set_status_list_bit(self, *, endpoint: str, index: int, value: bool = True):
        from app.plugins.mongodb import MongoClient

        real = MongoClient.__new__(MongoClient)
        real.find_one = self.find_one
        real.replace = self.replace
        ok = MongoClient.set_status_list_bit(
            real, endpoint=endpoint, index=index, value=value
        )
        if ok:
            self.status_bit_updates.append(
                {"endpoint": endpoint, "index": index, "value": value}
            )
        return ok


@pytest.fixture
def status_env(monkeypatch):
    monkeypatch.setattr(settings, "TRACTION_API_KEY", "admin-test-key")
    monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-at-least-32-bytes!!")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "PUBLISHER_DOMAIN", "https://publisher.test")

    mongo = _StatusMongo()
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


def _status_body(**overrides):
    body = {
        "credentialId": CREDENTIAL_ID,
        "credentialStatus": {
            "statusPurpose": "revocation",
            "statusListIndex": "42",
            "statusListCredential": STATUS_ENDPOINT,
            "status": True,
        },
    }
    body.update(overrides)
    return body


def test_revoke_flips_bit_and_marks_record(status_env):
    client, mongo = status_env
    response = client.post(
        "/credentials/status",
        headers={"X-API-Key": "admin-test-key"},
        json=_status_body(),
    )
    assert response.status_code == 200
    assert response.json() == {"credentialId": CREDENTIAL_ID, "status": True}
    assert mongo.status_bit_updates == [
        {"endpoint": STATUS_ENDPOINT, "index": 42, "value": True}
    ]
    assert mongo.credentials[0]["revocation"] is True


def test_unrevoke_sets_status_false(status_env):
    client, mongo = status_env
    body = _status_body()
    body["credentialStatus"]["status"] = False
    response = client.post(
        "/credentials/status",
        headers={"X-API-Key": "admin-test-key"},
        json=body,
    )
    assert response.status_code == 200
    assert response.json() == {"credentialId": CREDENTIAL_ID, "status": False}
    assert mongo.credentials[0]["revocation"] is False


def test_unknown_credential_id_returns_404(status_env):
    client, _mongo = status_env
    response = client.post(
        "/credentials/status",
        headers={"X-API-Key": "admin-test-key"},
        json=_status_body(credentialId="does-not-exist"),
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"statusPurpose": "suspension"},
        {"statusListIndex": "7"},
        {"statusListCredential": "https://publisher.test/status-lists/other"},
    ],
)
def test_mismatched_entry_returns_400(status_env, overrides):
    client, _mongo = status_env
    body = _status_body()
    body["credentialStatus"].update(overrides)
    response = client.post(
        "/credentials/status",
        headers={"X-API-Key": "admin-test-key"},
        json=body,
    )
    assert response.status_code == 400


def test_wrong_issuer_jwt_returns_403(status_env):
    client, _mongo = status_env
    response = client.post(
        "/credentials/status",
        headers={"Authorization": f"Bearer {_token(OTHER_ISSUER)}"},
        json=_status_body(),
    )
    assert response.status_code == 403


def test_matching_issuer_jwt_succeeds(status_env):
    client, mongo = status_env
    response = client.post(
        "/credentials/status",
        headers={"Authorization": f"Bearer {_token(ISSUER_ID)}"},
        json=_status_body(),
    )
    assert response.status_code == 200
    assert mongo.credentials[0]["revocation"] is True


def test_admin_api_key_succeeds_regardless_of_issuer(status_env):
    client, mongo = status_env
    response = client.post(
        "/credentials/status",
        headers={"X-API-Key": "admin-test-key"},
        json=_status_body(),
    )
    assert response.status_code == 200
    assert mongo.credentials[0]["revocation"] is True
