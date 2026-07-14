"""Tests for configs-driven local IssuerInstanceRecord provisioning."""

from __future__ import annotations

from app.services.issuer_provision import ensure_issuer_record, scope_from_issuer_config


class _FakeMongo:
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


def test_scope_from_alias_namespace():
    assert (
        scope_from_issuer_config({"alias": "mines-act:chief-permitting-officer"})
        == "mines-act"
    )
    assert scope_from_issuer_config({"scope": "Mines Act", "alias": "x:y"}) == "Mines Act"


def test_ensure_issuer_record_creates_from_yaml():
    mongo = _FakeMongo()
    issuer = {
        "id": "did:web:example.ca:mines-act:chief-permitting-officer",
        "alias": "mines-act:chief-permitting-officer",
        "name": "Chief Permitting Officer",
        "description": "Issues Mines Act permits.",
    }
    record = ensure_issuer_record(issuer, mongo=mongo)
    assert record["id"] == issuer["id"]
    assert record["name"] == "Chief Permitting Officer"
    assert record["scope"] == "mines-act"
    assert "authorized_key" not in record or record.get("authorized_key") is None
    assert len(mongo.records) == 1

    again = ensure_issuer_record(issuer, mongo=mongo)
    assert again["id"] == record["id"]
    assert len(mongo.records) == 1


def test_ensure_issuer_record_sets_key_from_verification_method():
    mongo = _FakeMongo()
    issuer = {
        "id": "did:web:example.ca:mines-act:officer",
        "alias": "mines-act:officer",
        "name": "Officer",
        "verificationMethod": "z6Mtestkey",
    }
    record = ensure_issuer_record(issuer, mongo=mongo)
    assert record["authorized_key"] == "z6Mtestkey"
