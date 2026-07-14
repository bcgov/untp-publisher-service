"""Render Jinja2 templates in credential template YAML fields."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml
from fastapi import HTTPException
from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

_JINJA = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)
_JINJA.filters["tojson"] = json.dumps


def product_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "product"


_JINJA.filters["product_slug"] = product_slug


def _fail(message: str) -> str:
    """Raise a client error from a credential template (Jinja ``fail`` helper)."""
    raise HTTPException(status_code=400, detail=str(message))


def exactly_one(value: Any, label: str = "list") -> Any:
    """Return the sole list item, or 400 if missing / not a single-item list."""
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        _fail(f"{label} must contain exactly 1 item")
    if len(value) != 1:
        _fail(f"{label} must contain exactly 1 item")
    return value[0]


_JINJA.globals["fail"] = _fail
_JINJA.filters["exactly_one"] = exactly_one


def template_stub_context() -> dict[str, Any]:
    """Minimal context to render a template for structure inspection (empty arrays)."""
    return {
        "credential": {
            "credentialSubject": {},
            "validFrom": "1999-01-01T00:00:00Z",
        },
        "options": {
            "cardinalityId": "STUB",
            "entityId": "STUB",
            "entityName": "Stub Organization",
            "additionalData": {
                "assessedFacility": [{
                    "name": "Stub Site",
                    "registeredId": "0000000",
                    "locationInformation": "https://plus.codes/EXAMPLE+CODE",
                }],
                "assessedProduct": [],
            },
        },
        "organization": {
            "id": "https://www.bcregistry.gov.bc.ca/business/STUB",
            "name": "Stub Organization",
            "registeredId": "STUB",
        },
    }


def render_template_text(template: str, context: dict[str, Any]) -> str:
    if not template:
        return template
    if "{{" not in template and "{%" not in template and "{#" not in template:
        return template
    try:
        return _JINJA.from_string(template).render(**context)
    except HTTPException:
        raise
    except TemplateSyntaxError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid publication Jinja template: {exc}",
        ) from exc
    except UndefinedError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Publication Jinja template references undefined variable: {exc}",
        ) from exc


def publication_template_context(
    *,
    credential: dict[str, Any],
    options: dict[str, Any],
    organization: dict[str, Any],
) -> dict[str, Any]:
    """Jinja context mirrors the publication request body plus holder organization."""
    org = dict(organization)
    entity_id = options.get("entityId")
    if entity_id is not None:
        org["registeredId"] = str(entity_id)
    if options.get("entityName") and not org.get("name"):
        org["name"] = str(options["entityName"])
    return {
        "credential": credential,
        "options": options,
        "organization": org,
    }


def render_template_yaml(template_source: str, context: dict[str, Any]) -> dict[str, Any]:
    """Render a YAML template file with Jinja, then parse the result."""
    rendered = render_template_text(template_source, context)
    try:
        data = yaml.safe_load(rendered) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Credential template did not render valid YAML: {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail="Credential template must render to a YAML mapping",
        )
    return data


def apply_configured_template_fields(
    *,
    template_source: str,
    credential: dict[str, Any],
    subject: dict[str, Any],
    assessment: dict[str, Any],
    context: dict[str, Any],
) -> None:
    """Render the credential template YAML and merge text + array fields onto the VC."""
    rendered = render_template_yaml(template_source, context)
    subject_cfg = rendered.get("credentialSubject") or {}
    assessment_cfg = subject_cfg.get("conformityAssessment") or {}

    for field in ("name", "description"):
        if field in rendered:
            credential[field] = rendered[field]

    for field in (
        "id",
        "name",
        "description",
        "issuedToParty",
        "assessorLevel",
        "assessmentLevel",
        "attestationType",
        "referenceScheme",
        "referenceProfile",
    ):
        if field in subject_cfg:
            subject[field] = subject_cfg[field]

    for field in (
        "id",
        "registeredId",
        "idScheme",
        "assessmentDate",
        "name",
        "description",
        "assessedFacility",
        "assessedProduct",
        "assessedOrganisation",
        "assessmentCriteria",
        "assessedPerformance",
        "referenceRegulation",
        "conformityTopic",
        "conformance",
    ):
        if field in assessment_cfg:
            assessment[field] = assessment_cfg[field]


def apply_configured_text_fields(
    *,
    template_source: str,
    credential: dict[str, Any],
    subject: dict[str, Any],
    assessment: dict[str, Any],
    context: dict[str, Any],
) -> None:
    """Backward-compatible alias."""
    apply_configured_template_fields(
        template_source=template_source,
        credential=credential,
        subject=subject,
        assessment=assessment,
        context=context,
    )
