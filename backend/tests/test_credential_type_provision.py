"""Tests for configs-driven CredentialTemplateRecord provisioning."""

from __future__ import annotations

from app.services.credential_type_provision import ensure_credential_type


class _FakeMongo:
    def __init__(self):
        self.types: list[dict] = []
        self.status: list[dict] = []
        self.issuers: dict[str, dict] = {}

    def find_one(self, collection, query):
        rows = {
            "CredentialTemplateRecord": self.types,
            "StatusListRecord": self.status,
            "IssuerInstanceRecord": list(self.issuers.values()),
        }[collection]
        for record in rows:
            if all(record.get(key) == value for key, value in query.items()):
                return dict(record)
        return None

    def insert(self, collection, item):
        if collection == "CredentialTemplateRecord":
            self.types.append(dict(item))
        elif collection == "StatusListRecord":
            self.status.append(dict(item))
        elif collection == "IssuerInstanceRecord":
            self.issuers[item["id"]] = dict(item)


def test_ensure_credential_type_creates_once(monkeypatch):
    mongo = _FakeMongo()
    issuer_id = "did:web:example.ca:mines-act:officer"
    mongo.issuers[issuer_id] = {"id": issuer_id, "name": "Officer"}
    for purpose in ("revocation", "suspension", "refresh"):
        mongo.status.append(
            {
                "id": f"list-{purpose}",
                "issuer": issuer_id,
                "purpose": purpose,
                "active": True,
            }
        )

    monkeypatch.setattr(
        "app.services.credential_type_provision.publisher_origin",
        lambda: "https://publisher.example",
    )
    monkeypatch.setattr(
        "app.services.credential_type_provision.generate_digest_multibase",
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
    assert first["template_ref"] == "untp_v0_7_0_dcc_mines_act_permit"
    assert first["status_lists"] == [
        "list-revocation",
        "list-suspension",
        "list-refresh",
    ]
    assert len(mongo.types) == 1

    second = ensure_credential_type(
        issuer=issuer,
        credential=issuer["credentials"][0],
        mongo=mongo,
    )
    assert second["type"] == first["type"]
    assert len(mongo.types) == 1
