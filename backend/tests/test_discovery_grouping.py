"""Unit tests for Discovery credential grouping."""

from app.routers.landing import (
    credential_public_url,
    format_proof_created,
    group_credential_records,
)


def test_group_collapses_same_entity_cardinality():
    records = [
        {
            "id": "newer",
            "type": "BCMinesActPermitCredential",
            "entity_id": "ORG-1",
            "cardinality_id": "P-1",
            "revocation": False,
            "suspension": False,
            "refresh": False,
            "vc": {"proof": [{"created": "2026-07-30T17:59:42Z"}]},
        },
        {
            "id": "older",
            "type": "BCMinesActPermitCredential",
            "entity_id": "ORG-1",
            "cardinality_id": "P-1",
            "revocation": False,
            "suspension": False,
            "refresh": True,
            "vc": {"proof": [{"created": "2026-07-29T09:00:00Z"}]},
        },
        {
            "id": "other",
            "type": "OtherType",
            "entity_id": "ORG-2",
            "cardinality_id": "P-9",
            "revocation": False,
            "suspension": True,
            "refresh": False,
        },
    ]
    groups = group_credential_records(records)
    assert len(groups) == 2
    first = groups[0]
    assert first["id"] == "newer"
    assert first["status"] == "active"
    assert first["iteration_count"] == 2
    assert [i["id"] for i in first["iterations"]] == ["newer", "older"]
    assert first["iterations"][0]["created_display"] == "30 Jul 2026, 17:59 UTC"
    assert first["iterations"][1]["created_display"] == "29 Jul 2026, 09:00 UTC"
    assert first["iterations"][1]["status"] == "superseded"
    assert groups[1]["status"] == "suspended"
    assert groups[1]["iterations"][0]["created_display"] == "—"


def test_group_face_prefers_non_refresh_even_if_not_first():
    records = [
        {
            "id": "stale-first-in-list",
            "type": "T",
            "entity_id": "E",
            "cardinality_id": "C",
            "refresh": True,
        },
        {
            "id": "live",
            "type": "T",
            "entity_id": "E",
            "cardinality_id": "C",
            "refresh": False,
        },
    ]
    groups = group_credential_records(records)
    assert len(groups) == 1
    assert groups[0]["id"] == "live"
    assert groups[0]["status"] == "active"
    assert groups[0]["iteration_count"] == 2


def test_format_proof_created():
    assert format_proof_created("2026-07-30T17:59:42Z") == "30 Jul 2026, 17:59 UTC"
    assert format_proof_created("") == "—"
    assert format_proof_created("not-a-date") == "not-a-date"


def test_missing_entity_falls_back_to_credential_id():
    records = [
        {"id": "a", "type": "T", "entity_id": "", "cardinality_id": ""},
        {"id": "b", "type": "T", "entity_id": "", "cardinality_id": ""},
    ]
    groups = group_credential_records(records)
    assert len(groups) == 2


def test_credential_public_url(monkeypatch):
    monkeypatch.setattr(
        "app.routers.landing.publisher_origin",
        lambda: "https://publisher.example",
    )
    assert (
        credential_public_url("abc")
        == "https://publisher.example/credentials/abc"
    )
    assert (
        credential_public_url("https://publisher.example/credentials/abc")
        == "https://publisher.example/credentials/abc"
    )
