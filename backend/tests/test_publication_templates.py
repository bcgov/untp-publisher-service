import copy

import pytest
from fastapi import HTTPException

from app.repo_configs.loader import load_credential_template_source
from app.services.publication_templates import (
    apply_configured_template_fields,
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


def test_render_template_yaml_parses_assessment_arrays():
    source = load_credential_template_source("BCMinesActPermitCredential")
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        organization=ORGANIZATION,
    )
    rendered = render_template_yaml(source, context)
    assessment = rendered["credentialSubject"]["conformityAssessment"]
    assert len(assessment["assessedFacility"]) == 1
    assert assessment["assessedFacility"][0]["type"] == ["FacilityVerification"]
    assert assessment["assessedProduct"][0]["product"]["id"] == (
        "urn:ca:bcgov:mines-act:permit:Q-20:commodity:construction-aggregate"
    )


def test_template_renders_assessed_facility_from_config():
    source = load_credential_template_source("BCMinesActPermitCredential")
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        organization=ORGANIZATION,
    )

    assessment = {}
    credential = {}
    apply_configured_template_fields(
        template_source=source,
        credential=credential,
        subject={"conformityAssessment": [assessment]},
        assessment=assessment,
        context=context,
    )

    facilities = assessment["assessedFacility"]
    assert len(facilities) == 1
    assert facilities[0]["type"] == ["FacilityVerification"]
    assert facilities[0]["facility"]["name"] == "Kootenay West"


def test_apply_configured_template_fields_sets_assessment_arrays():
    source = load_credential_template_source("BCMinesActPermitCredential")
    subject = {"conformityAssessment": [{}]}
    assessment = subject["conformityAssessment"][0]
    credential = {}
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        organization=ORGANIZATION,
    )

    apply_configured_template_fields(
        template_source=source,
        credential=credential,
        subject=subject,
        assessment=assessment,
        context=context,
    )

    assert "Mines Act (British Columbia)" in credential["description"]

    assert len(assessment["assessedFacility"]) == 1
    assert len(assessment["assessedProduct"]) == 1
    assert assessment["assessedProduct"][0]["product"]["id"] == (
        "urn:ca:bcgov:mines-act:permit:Q-20:commodity:construction-aggregate"
    )


def test_apply_configured_template_fields_merges_reference_scheme():
    source = load_credential_template_source("BCMinesActPermitCredential")
    subject = {"conformityAssessment": [{}]}
    assessment = subject["conformityAssessment"][0]
    credential = {}
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        organization=ORGANIZATION,
    )

    apply_configured_template_fields(
        template_source=source,
        credential=credential,
        subject=subject,
        assessment=assessment,
        context=context,
    )

    assert subject["referenceScheme"]["name"] == "Mines Act (British Columbia)"
    assert assessment["referenceRegulation"][0]["name"] == "Mines Act"
    assert assessment["assessmentCriteria"][0]["id"].endswith("96293_01")
