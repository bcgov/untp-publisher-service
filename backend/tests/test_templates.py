import copy

import pytest
from fastapi import HTTPException

from app.repo_configs.loader import load_credential_template_source
from app.services.templates import (
    isodate,
    materialize_credential_document,
    publication_template_context,
    render_template_text,
    render_template_yaml,
)

OPTIONS = {
    "template": "BCMinesActPermitCredential",
    "version": "v1.1",
    "entityId": "A0034771",
    "entityName": "EXAMPLE MINING CO",
    "cardinalityId": "Q-20",
    "data": {
        "permit": {
            "issuanceDate": "1999-04-19",
            "identifier": "Q-20",
        },
        "permittee": {
            "name": "EXAMPLE MINING CO",
            "identifier": "A0034771",
        },
        "mine": {
            "name": "Kootenay West",
            "identifier": "0500956",
            "infoPageId": "5fa1e3ec4635c865df00c420",
        },
        "commodities": [{"name": "Construction Aggregate"}],
    },
}


def test_isodate_accepts_date_and_datetime():
    assert isodate("1999-04-19") == "1999-04-19"
    assert isodate("2026-06-02T15:30:00Z") == "2026-06-02T15:30:00Z"


def test_isodate_rejects_invalid():
    with pytest.raises(HTTPException) as exc:
        isodate("not-a-date")
    assert exc.value.status_code == 400
    assert "ISO 8601" in str(exc.value.detail)


def test_mines_act_template_requires_isodate_issuance():
    source = load_credential_template_source("BCMinesActPermitCredential")
    options = copy.deepcopy(OPTIONS)
    options["data"]["permit"]["issuanceDate"] = "April 19, 1999"
    context = publication_template_context(options=options)
    with pytest.raises(HTTPException) as exc:
        render_template_yaml(source, context)
    assert exc.value.status_code == 400
    assert "ISO 8601" in str(exc.value.detail)


def test_publication_template_context_flattens_data():
    context = publication_template_context(options=OPTIONS)
    assert context["permit"]["identifier"] == "Q-20"
    assert context["permittee"]["name"] == "EXAMPLE MINING CO"
    assert context["permittee"]["identifier"] == "A0034771"
    assert "data" not in context
    assert "template" not in context
    assert "version" not in context


def test_publication_template_context_rejects_reserved_keys():
    options = copy.deepcopy(OPTIONS)
    options["data"]["fail"] = True
    with pytest.raises(HTTPException) as exc:
        publication_template_context(options=options)
    assert exc.value.status_code == 400
    assert "fail" in str(exc.value.detail)


def test_render_template_text_uses_payload_paths():
    context = publication_template_context(options=OPTIONS)
    result = render_template_text(
        "Permit {{ permit.identifier }} for {{ permittee.name }}.",
        context,
    )
    assert result == "Permit Q-20 for EXAMPLE MINING CO."


def test_render_template_text_passthrough_without_jinja():
    assert render_template_text("Plain text.", {}) == "Plain text."


def test_render_template_text_supports_mine_data():
    context = publication_template_context(options=OPTIONS)
    assert render_template_text("{{ mine.name }}", context) == "Kootenay West"


def test_render_template_text_rejects_undefined_variables():
    with pytest.raises(HTTPException) as exc:
        render_template_text("Hello {{ missingVar }}", {})
    assert "undefined variable" in str(exc.value.detail).lower()


def test_mines_act_template_requires_mine_object():
    source = load_credential_template_source("BCMinesActPermitCredential")
    options = copy.deepcopy(OPTIONS)
    options["data"]["mine"] = []
    context = publication_template_context(options=options)
    with pytest.raises(HTTPException) as exc:
        render_template_yaml(source, context)
    assert exc.value.status_code == 400
    assert "required" in str(exc.value.detail)

    options["data"].pop("mine", None)
    context = publication_template_context(options=options)
    with pytest.raises(HTTPException) as exc:
        render_template_yaml(source, context)
    assert exc.value.status_code == 400
    assert "required" in str(exc.value.detail)


def test_mines_act_template_requires_permittee():
    source = load_credential_template_source("BCMinesActPermitCredential")
    options = copy.deepcopy(OPTIONS)
    options["data"].pop("permittee", None)
    context = publication_template_context(options=options)
    with pytest.raises(HTTPException) as exc:
        render_template_yaml(source, context)
    assert exc.value.status_code == 400
    assert "required" in str(exc.value.detail)


def test_mines_act_template_requires_mine_identifier():
    source = load_credential_template_source("BCMinesActPermitCredential")
    options = copy.deepcopy(OPTIONS)
    options["data"]["mine"].pop("identifier", None)
    context = publication_template_context(options=options)
    with pytest.raises(HTTPException) as exc:
        render_template_yaml(source, context)
    assert exc.value.status_code == 400
    assert "required" in str(exc.value.detail)


def test_materialize_credential_document_from_mines_act_template():
    source = load_credential_template_source("BCMinesActPermitCredential")
    context = publication_template_context(options=OPTIONS)
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
    assert assessment["referenceRegulation"][0]["name"] == (
        "Health, Safety and Reclamation Code for Mines in British Columbia"
    )
    assert assessment["assessmentCriteria"][0]["id"].endswith("#section10")
    assert assessment["assessmentCriteria"][0]["name"] == "Permits"
    assert assessment["evidence"][0]["linkURL"] == (
        "https://mines.nrs.gov.bc.ca/mine/5fa1e3ec4635c865df00c420"
        "/authorizations#authorization-MEM"
    )


def test_mines_act_template_omits_evidence_without_info_page_id():
    source = load_credential_template_source("BCMinesActPermitCredential")
    options = copy.deepcopy(OPTIONS)
    options["data"]["mine"].pop("infoPageId")
    context = publication_template_context(options=options)
    credential = materialize_credential_document(source, context)
    assessment = credential["credentialSubject"]["conformityAssessment"][0]
    assert "evidence" not in assessment
