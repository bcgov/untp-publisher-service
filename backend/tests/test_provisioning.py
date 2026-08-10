"""Tests for configs-driven startup provisioning."""

from __future__ import annotations

import asyncio

from app.services.provisioning import (
    STATUS_PURPOSES,
    ensure_credential_type,
    ensure_issuer_record,
    ensure_issuer_status_lists,
    namespace_from_issuer_config,
)


class _FakeIssuerMongo:
    def __init__(self):
        self.records: dict[str, dict] = {}

    def find_one(self, collection, query):
        assert collection == "IssuerInstanceRecord"
        record = self.records.get(query.get("id"))
        return dict(record) if record else None

    def insert(self, collection, item):
        assert collection == "IssuerInstanceRecord"
        self.records[item["id"]] = dict(item)

    def replace(self, collection, query, new_item):
        assert collection == "IssuerInstanceRecord"
        self.records[query["id"]] = dict(new_item)


class _FakeCredentialTypeMongo:
    def __init__(self):
        self.types: list[dict] = []
        self.issuers: dict[str, dict] = {}

    def find_one(self, collection, query):
        rows = {
            "CredentialTemplateRecord": self.types,
            "IssuerInstanceRecord": list(self.issuers.values()),
        }[collection]
        for record in rows:
            if all(record.get(key) == value for key, value in query.items()):
                return dict(record)
        return None

    def insert(self, collection, item):
        if collection == "CredentialTemplateRecord":
            self.types.append(dict(item))
        elif collection == "IssuerInstanceRecord":
            self.issuers[item["id"]] = dict(item)


class _FakeStatusListMongo:
    def __init__(self):
        self.records: list[dict] = []

    def find_one(self, collection, query):
        assert collection == "StatusListRecord"
        for record in self.records:
            if all(record.get(key) == value for key, value in query.items()):
                return record
        return None

    def insert(self, collection, item):
        assert collection == "StatusListRecord"
        self.records.append(item)

    def replace(self, collection, query, new_item):
        assert collection == "StatusListRecord"
        for i, record in enumerate(self.records):
            if all(record.get(key) == value for key, value in query.items()):
                self.records[i] = new_item
                return
        raise AssertionError(f"replace miss: {query}")


def test_namespace_from_alias():
    assert (
        namespace_from_issuer_config({"alias": "mines-act:chief-permitting-officer"})
        == "mines-act"
    )
    assert (
        namespace_from_issuer_config({"namespace": "Mines Act", "alias": "x:y"})
        == "Mines Act"
    )


def test_ensure_issuer_record_creates_from_yaml():
    mongo = _FakeIssuerMongo()
    issuer = {
        "id": "did:web:example.ca:mines-act:chief-permitting-officer",
        "alias": "mines-act:chief-permitting-officer",
        "name": "Chief Permitting Officer",
        "description": "Issues Mines Act permits.",
    }
    record = ensure_issuer_record(issuer, mongo=mongo)
    assert record["id"] == issuer["id"]
    assert record["name"] == "Chief Permitting Officer"
    assert record["namespace"] == "mines-act"
    assert "authorized_key" not in record or record.get("authorized_key") is None
    assert len(mongo.records) == 1

    again = ensure_issuer_record(issuer, mongo=mongo)
    assert again["id"] == record["id"]
    assert len(mongo.records) == 1


def test_ensure_issuer_record_sets_key_from_verification_method():
    mongo = _FakeIssuerMongo()
    issuer = {
        "id": "did:web:example.ca:mines-act:officer",
        "alias": "mines-act:officer",
        "name": "Officer",
        "verificationMethod": "z6Mtestkey",
    }
    record = ensure_issuer_record(issuer, mongo=mongo)
    assert record["authorized_key"] == "z6Mtestkey"


def test_ensure_credential_type_creates_once(monkeypatch):
    mongo = _FakeCredentialTypeMongo()
    issuer_id = "did:web:example.ca:mines-act:officer"
    mongo.issuers[issuer_id] = {"id": issuer_id, "name": "Officer"}

    monkeypatch.setattr(
        "app.services.composer.publisher_origin",
        lambda: "https://publisher.example",
    )
    monkeypatch.setattr(
        "app.services.composer.generate_digest_multibase",
        lambda _bundle: "zDigest",
    )

    issuer = {
        "id": issuer_id,
        "alias": "mines-act:officer",
        "name": "Officer",
        "credentials": [
            {"type": "BCMinesActPermitCredential", "version": "v1.1"},
        ],
    }
    first = ensure_credential_type(
        issuer=issuer,
        credential=issuer["credentials"][0],
        mongo=mongo,
    )
    assert first["type"] == "BCMinesActPermitCredential"
    assert first["version"] == "v1.1"
    assert "template_ref" not in first
    assert "status_lists" not in first
    assert len(mongo.types) == 1

    second = ensure_credential_type(
        issuer=issuer,
        credential=issuer["credentials"][0],
        mongo=mongo,
    )
    assert second["type"] == first["type"]
    assert len(mongo.types) == 1


def test_ensure_issuer_status_lists_creates_three_active_lists(monkeypatch):
    monkeypatch.setattr(
        "app.services.provisioning.status_list_endpoint",
        lambda list_id: f"https://publisher.example/status-lists/{list_id}",
    )
    mongo = _FakeStatusListMongo()
    issuer_id = "did:web:example:issuer"

    first = asyncio.run(ensure_issuer_status_lists(issuer_id, mongo=mongo))
    assert len(first) == 3
    assert {item["purpose"] for item in first} == set(STATUS_PURPOSES)
    assert all(item["active"] is True for item in first)
    assert all(item["issuer"] == issuer_id for item in first)
    assert all(
        item["credential"]["credentialSubject"]["statusPurpose"] == item["purpose"]
        for item in first
    )
    assert len(mongo.records) == 3

    second = asyncio.run(ensure_issuer_status_lists(issuer_id, mongo=mongo))
    assert len(second) == 3
    assert len(mongo.records) == 3
    assert {item["id"] for item in second} == {item["id"] for item in first}


def test_ensure_issuer_status_lists_rewrites_stale_endpoint(monkeypatch):
    monkeypatch.setattr(
        "app.services.provisioning.status_list_endpoint",
        lambda list_id: f"https://old.example/status-lists/{list_id}",
    )
    mongo = _FakeStatusListMongo()
    issuer_id = "did:web:example:issuer"
    first = asyncio.run(ensure_issuer_status_lists(issuer_id, mongo=mongo))
    assert all(r["endpoint"].startswith("https://old.example/") for r in first)

    monkeypatch.setattr(
        "app.services.provisioning.status_list_endpoint",
        lambda list_id: f"http://localhost:8000/status-lists/{list_id}",
    )
    second = asyncio.run(ensure_issuer_status_lists(issuer_id, mongo=mongo))
    assert len(mongo.records) == 3
    assert {item["id"] for item in second} == {item["id"] for item in first}
    for item in second:
        expected = f"http://localhost:8000/status-lists/{item['id']}"
        assert item["endpoint"] == expected
        assert item["credential"]["id"] == expected
