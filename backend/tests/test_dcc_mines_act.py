"""Tests for mines-act DCC preset loader and publication builder."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.repo_configs.loader import load_sample_publication_payload
from app.presets.loader import build_template_from_preset, load_instance_skeleton
from app.services import dcc_builder

TEMPLATE_REF = "untp_v0_7_0_dcc_mines_act_permit"
CREDENTIAL_TYPE = "BCMinesActPermitCredential"


@pytest.fixture
def publication_payload():
    return load_sample_publication_payload(CREDENTIAL_TYPE)


@pytest.fixture
def issuer():
    return {
        "id": "did:web:registry.example.ca:mines-act:chief-permitting-officer",
        "name": "Chief Permitting Officer",
        "scope": "Mines Act",
        "authorized_key": "z6Mtest",
    }


@pytest.fixture
def type_record():
    return {
        "type": "BCMinesActPermitCredential",
        "version": "v1.0",
        "issuer": "did:web:registry.example.ca:mines-act:chief-permitting-officer",
        "template_ref": TEMPLATE_REF,
        "core_paths": {
            "entityId": "/credentialSubject/issuedToParty/registeredId",
            "cardinalityId": "/credentialSubject/conformityAssessment/0/registeredId",
        },
        "template": load_instance_skeleton(TEMPLATE_REF),
    }


@pytest.fixture
def legal_act():
    return {
        "id": "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96293_01",
        "name": "Mines Act",
        "scope": "Mines Act",
    }


@pytest.fixture
def mock_legal_act(monkeypatch, legal_act):
    """Patch legal_act at each import site (Python binds imports locally)."""
    fake = lambda _issuer: legal_act
    monkeypatch.setattr("app.presets.loader.legal_act_for_issuer", fake)
    monkeypatch.setattr("app.services.dcc_builder.legal_act_for_issuer", fake)
    return legal_act


def test_build_template_from_preset(mock_legal_act, issuer, legal_act):
    template = build_template_from_preset(
        template_ref=TEMPLATE_REF,
        issuer=issuer,
    )
    assert "DigitalConformityCredential" in template["type"]
    assert "BCMinesActPermitCredential" in template["type"]
    assert template["issuer"]["id"] == issuer["id"]
    assert (
        template["credentialSubject"]["referenceScheme"]["id"] == legal_act["id"]
    )


def test_validate_publication_rejects_cardinality_mismatch(publication_payload, type_record):
    payload = copy.deepcopy(publication_payload)
    payload["options"]["cardinalityId"] = "WRONG"
    with pytest.raises(Exception) as exc:
        dcc_builder.validate_publication(
            credential_input=payload["credential"],
            options=payload["options"],
            type_record=type_record,
        )
    assert "must match" in str(exc.value.detail)


def test_build_dcc_from_publication(
    mock_legal_act, publication_payload, type_record, issuer, legal_act
):
    entity = {
        "id": "https://dev.orgbook.gov.bc.ca/entity/A0034771/type/registration.registries.ca",
        "name": "EXAMPLE MINING CO",
    }
    template = build_template_from_preset(template_ref=TEMPLATE_REF, issuer=issuer)
    type_record["template"] = template

    credential = dcc_builder.build_dcc_from_publication(
        template=template,
        credential_input=publication_payload["credential"],
        options=publication_payload["options"],
        type_record=type_record,
        issuer=issuer,
        entity=entity,
    )

    assert credential["type"] == [
        "VerifiableCredential",
        "DigitalConformityCredential",
        "BCMinesActPermitCredential",
    ]
    assert credential["credentialSubject"]["id"].endswith("/permits/Q-20")
    assert credential["credentialSubject"]["issuedToParty"]["registeredId"] == "A0034771"
    assessment = credential["credentialSubject"]["conformityAssessment"][0]
    assert assessment["registeredId"] == "Q-20"
    assert "This is permit Q-20" in assessment["description"]
    assert "Construction Aggregate" in assessment["description"]
    assert credential["credentialSubject"]["name"] == (
        "Mines Act Permit Q-20 — EXAMPLE MINING CO"
    )
    assert "Kootenay West" in credential["credentialSubject"]["description"]
    assert len(assessment["assessedFacility"]) == 1
    assert assessment["assessedFacility"][0]["type"] == ["FacilityVerification"]
    assert len(assessment["assessedProduct"]) == 1
    assert assessment["assessedProduct"][0]["product"]["name"] == "Construction Aggregate"


def test_normalize_facility_maps_location_information():
    item = {
        "name": "Kootenay West",
        "registeredId": "0500956",
        "locationInformation": "https://plus.codes/9526679P+4V",
        "IDverifiedByCAB": True,
    }
    normalized = dcc_builder.normalize_facility(item)
    assert normalized["facility"]["id"] == "https://plus.codes/9526679P+4V"
    assert normalized["idVerifiedByCAB"] is True
