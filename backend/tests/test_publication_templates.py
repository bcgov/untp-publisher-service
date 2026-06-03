import pytest
from fastapi import HTTPException

from app.services.publication_templates import (
    publication_template_context,
    render_template_text,
)

PAYLOAD = {
    "credential": {
        "type": "BCMinesActPermitCredential",
        "credentialSubject": {"permitNumber": "Q-20"},
    },
    "options": {
        "entityId": "A0034771",
        "cardinalityId": "Q-20",
        "additionalData": {
            "assessedFacility": [{"name": "Kootenay West"}],
            "assessedProduct": [{"name": "Construction Aggregate"}],
        },
    },
}
ENTITY = {
    "id": "https://dev.orgbook.gov.bc.ca/entity/A0034771/type/registration.registries.ca",
    "name": "EXAMPLE MINING CO",
}


def test_publication_template_context_mirrors_payload():
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        entity=ENTITY,
    )
    assert context["options"]["cardinalityId"] == "Q-20"
    assert context["entity"]["name"] == "EXAMPLE MINING CO"


def test_render_template_text_uses_payload_paths():
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        entity=ENTITY,
    )
    result = render_template_text(
        "Permit {{ options.cardinalityId }} for {{ entity.name }}.",
        context,
    )
    assert result == "Permit Q-20 for EXAMPLE MINING CO."


def test_render_template_text_passthrough_without_jinja():
    assert render_template_text("Plain text.", {}) == "Plain text."


def test_render_template_text_supports_payload_additional_data():
    context = publication_template_context(
        credential=PAYLOAD["credential"],
        options=PAYLOAD["options"],
        entity=ENTITY,
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
