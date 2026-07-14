import copy

import pytest
from fastapi import HTTPException

from app.repo_configs.loader import load_credential_template_source
from app.services.publication_templates import (
    materialize_credential_document,
    publication_template_context,
    render_template_text,
    render_template_yaml,
)

PAYLOAD = {
    "credential": {
        "type": "BCMinesActPermitCredential",
        "validFrom": "1999-04-19T00:00:00+00:00",
        "credentialSubject": {},
    },
    "options": {
        "entityId": "A0034771",
        "entityName": "EXAMPLE MINING CO",
        "cardinalityId": "Q-20",
        "additionalData": {
            "assessedFacility": [{"name": "Kootenay West", "registeredId": "0500956"}],
            "assessedProduct": [{"name": "Construction Aggregate"}],
        },
    },
}
ORGANIZATION = {
    "id": "https://www.bcregistry.gov.bc.ca/business/A0034771",
    "name": "EXAMPLE MINING CO",
}


def test_publication_template_context_mirrors_payload():
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        organization=ORGANIZATION,
    )
    assert context["options"]["cardinalityId"] == "Q-20"
    assert context["organization"]["name"] == "EXAMPLE MINING CO"
    assert context["organization"]["registeredId"] == "A0034771"


def test_render_template_text_uses_payload_paths():
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        organization=ORGANIZATION,
    )
    result = render_template_text(
        "Permit {{ options.cardinalityId }} for {{ organization.name }}.",
        context,
    )
    assert result == "Permit Q-20 for EXAMPLE MINING CO."


def test_render_template_text_passthrough_without_jinja():
    assert render_template_text("Plain text.", {}) == "Plain text."


def test_render_template_text_supports_payload_additional_data():
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        organization=ORGANIZATION,
    )
    template = (
        "{%- set facilities = options.additionalData.assessedFacility | default([]) -%}"
        "{{ facilities | map(attribute='name') | join(', ') }}"
    )
    assert render_template_text(template, context) == "Kootenay West"


def test_render_template_text_rejects_undefined_variables():
    with pytest.raises(HTTPException) as exc:
        render_template_text("Hello {{ missingVar }}", {})
    assert "undefined variable" in str(exc.value.detail).lower()


def test_mines_act_template_requires_exactly_one_assessed_facility():
    source = load_credential_template_source("BCMinesActPermitCredential")
    options = copy.deepcopy(PAYLOAD["options"])
    options["additionalData"]["assessedFacility"] = []
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=options,
        organization=ORGANIZATION,
    )
    with pytest.raises(HTTPException) as exc:
        render_template_yaml(source, context)
    assert exc.value.status_code == 400
    assert "exactly 1" in str(exc.value.detail)

    options["additionalData"]["assessedFacility"] = [
        {"name": "A", "registeredId": "1"},
        {"name": "B", "registeredId": "2"},
    ]
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=options,
        organization=ORGANIZATION,
    )
    with pytest.raises(HTTPException) as exc:
        render_template_yaml(source, context)
    assert exc.value.status_code == 400
    assert "exactly 1" in str(exc.value.detail)


def test_materialize_credential_document_from_mines_act_template():
    source = load_credential_template_source("BCMinesActPermitCredential")
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        organization=ORGANIZATION,
    )
    credential = materialize_credential_document(source, context)
    assert credential["@context"][0] == "https://www.w3.org/ns/credentials/v2"
    assert "context" not in credential
    assert "Mines Act (British Columbia)" in credential["description"]
    assert credential["credentialSubject"]["type"] == ["ConformityAttestation"]
    assert credential["credentialSubject"]["referenceScheme"]["name"] == (
        "Mines Act (British Columbia)"
    )
    assessment = credential["credentialSubject"]["conformityAssessment"][0]
    assert assessment["type"] == ["ConformityAssessment"]
    assert len(assessment["assessedFacility"]) == 1
    assert assessment["assessedFacility"][0]["type"] == ["FacilityVerification"]
    assert assessment["assessedFacility"][0]["facility"]["name"] == "Kootenay West"
    assert len(assessment["assessedProduct"]) == 1
    assert assessment["assessedProduct"][0]["product"]["id"] == (
        "urn:ca:bcgov:mines-act:permit:Q-20:commodity:construction-aggregate"
    )
    assert assessment["referenceRegulation"][0]["name"] == "Mines Act"
    assert assessment["assessmentCriteria"][0]["id"].endswith("96293_01")
