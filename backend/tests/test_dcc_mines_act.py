"""Tests for mines-act DCC registration template and composer."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.repo_configs.loader import load_sample_publication_payload
from app.services.composer import normalize_publication
from app.services.templates import build_registration_template
from app.services import composer

CREDENTIAL_TYPE = "BCMinesActPermitCredential"
MINES_ACT_URL = (
    "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96293_01"
)


@pytest.fixture
def publication_payload():
    return load_sample_publication_payload(CREDENTIAL_TYPE)


@pytest.fixture
def issuer():
    return {
        "id": "did:web:registry.example.ca:mines-act:chief-permitting-officer",
        "name": "Chief Permitting Officer",
        "namespace": "mines-act",
        "authorized_key": "z6Mtest",
    }


@pytest.fixture
def type_record(issuer):
    return {
        "type": CREDENTIAL_TYPE,
        "version": "v1.1",
        "issuer": issuer["id"],
        "template": build_registration_template(
            credential_type=CREDENTIAL_TYPE,
            issuer=issuer,
        ),
    }


def test_build_registration_template(issuer):
    template = build_registration_template(
        credential_type=CREDENTIAL_TYPE,
        issuer=issuer,
    )
    assert "DigitalConformityCredential" in template["type"]
    assert "BCMinesActPermitCredential" not in template["type"]
    assert template["issuer"]["id"] == issuer["id"]
    assert template["@context"][0] == "https://www.w3.org/ns/credentials/v2"
    assert template["credentialSubject"]["referenceScheme"]["id"] == MINES_ACT_URL


def test_normalize_publication_requires_permit_id(publication_payload):
    payload = copy.deepcopy(publication_payload)
    payload["data"]["permit"]["identifier"] = ""
    with pytest.raises(HTTPException) as exc:
        normalize_publication(payload)
    assert exc.value.status_code == 400
    assert "Invalid publication data" in str(exc.value.detail)


def test_normalize_publication_rejects_unknown_data_property(publication_payload):
    payload = copy.deepcopy(publication_payload)
    payload["data"]["extra"] = True
    with pytest.raises(HTTPException) as exc:
        normalize_publication(payload)
    assert exc.value.status_code == 400
    assert "Invalid publication data" in str(exc.value.detail)


def test_normalize_publication_requires_mine(publication_payload):
    payload = copy.deepcopy(publication_payload)
    del payload["data"]["mine"]
    with pytest.raises(HTTPException) as exc:
        normalize_publication(payload)
    assert exc.value.status_code == 400


def test_compose_credential(publication_payload, type_record, issuer, monkeypatch):
    published_at = datetime(2026, 6, 2, 15, 30, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return published_at

    monkeypatch.setattr(composer, "datetime", FixedDateTime)

    options = normalize_publication(publication_payload)
    template = build_registration_template(
        credential_type=CREDENTIAL_TYPE,
        issuer=issuer,
    )
    type_record["template"] = template

    credential = composer.compose_credential(
        options=options,
        type_record=type_record,
        issuer=issuer,
    )

    assert credential["type"] == [
        "VerifiableCredential",
        "DigitalConformityCredential",
    ]
    assert credential["credentialSubject"]["id"] == (
        "urn:ca:bcgov:mines-act:permit:Q-20"
    )
    assert credential["credentialSubject"]["issuedToParty"]["registeredId"] == "A0034771"
    assessment = credential["credentialSubject"]["conformityAssessment"][0]
    assert assessment["registeredId"] == "Q-20"
    assert assessment["assessmentDate"] == "1999-04-19"
    assert assessment["id"] == "urn:ca:bcgov:mines-act:permit:Q-20:assessment"
    assert credential["validFrom"] == "2026-06-02T15:30:00Z"
    assert "Permit Q-20 authorizes" in assessment["description"]
    assert "Construction Aggregate" in assessment["description"]
    assert credential["credentialSubject"]["name"] == (
        "Mines Act Permit Q-20 — EXAMPLE MINING CO"
    )
    assert "Mines Act (British Columbia)" in credential.get("description", "")
    assert "Kootenay West" in credential["credentialSubject"]["description"]
    assert assessment["assessedOrganisation"]["name"] == "EXAMPLE MINING CO"
    assert assessment["assessedOrganisation"]["registeredId"] == "A0034771"
    assert assessment["assessedOrganisation"]["id"] == (
        "urn:ca:bcgov:mines-act:permit:Q-20:permittee:A0034771"
    )
    assert credential["credentialSubject"]["issuedToParty"]["id"] == (
        "urn:ca:bcgov:mines-act:permit:Q-20:permittee:A0034771"
    )
    ref_scheme = credential["credentialSubject"]["referenceScheme"]
    assert ref_scheme["id"].endswith("96293_01")
    assert ref_scheme["name"] == "Mines Act (British Columbia)"
    criteria = assessment["assessmentCriteria"][0]
    assert criteria["id"].endswith("#section10")
    assert criteria["name"] == "Permits"
    assert len(assessment["assessedFacility"]) == 1
    assert assessment["assessedFacility"][0]["type"] == ["FacilityVerification"]
    facility_obj = assessment["assessedFacility"][0]["facility"]
    assert facility_obj["id"] == "urn:ca:bcgov:mines-act:permit:Q-20:mine:0500956"
    assert facility_obj["locationInformation"]["plusCode"] == (
        "https://plus.codes/9526679P+4V"
    )
    assert len(assessment["assessedProduct"]) == 1
    assert assessment["assessedProduct"][0]["product"]["name"] == "Construction Aggregate"
    assert assessment["assessedProduct"][0]["product"]["id"] == (
        "urn:ca:bcgov:mines-act:permit:Q-20:commodity:construction-aggregate"
    )
    assert credential["renderMethod"][0]["type"] == "OCABundle"
    assert "digestMultibase" not in credential["renderMethod"][0]


def test_oca_render_method_includes_digest_when_enabled(monkeypatch):
    monkeypatch.setattr(composer.settings, "OCA_DIGEST", True)
    monkeypatch.setattr(
        composer,
        "publisher_origin",
        lambda: "https://publisher.test",
    )
    methods = composer.oca_render_method(
        credential_type=CREDENTIAL_TYPE,
        version="v1.1",
    )
    assert methods[0]["digestMultibase"].startswith("z")
    assert methods[0]["id"].endswith("/oca.json")
