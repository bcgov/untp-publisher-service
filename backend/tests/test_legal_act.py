"""Tests for legal act resolution from issuer scope."""

from app.services import legal_act


def test_legal_act_for_issuer_uses_scope(monkeypatch):
    issuer = {"id": "did:web:example:issuer", "scope": "Mines Act"}

    monkeypatch.setattr(
        legal_act.bclaws,
        "resolve_legal_act_from_scope",
        lambda scope: {
            "id": "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96293_01",
            "name": "Mines Act",
            "scope": scope,
        },
    )

    resolved = legal_act.legal_act_for_issuer(issuer)
    assert resolved["name"] == "Mines Act"
    assert resolved["scope"] == "Mines Act"
