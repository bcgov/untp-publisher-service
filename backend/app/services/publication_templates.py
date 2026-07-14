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
    """Minimal context to render a template for structure inspection."""
    return {
        "template": "BCMinesActPermitCredential",
        "version": "v1.1",
        "data": {
            "permit": {
                "issuanceDate": "1999-01-01",
                "identifier": "STUB",
            },
            "permittee": {
                "name": "Stub Organization",
                "identifier": "STUB",
            },
            "mine": {
                "name": "Stub Site",
                "identifier": "0000000",
                "locationInformation": "https://plus.codes/EXAMPLE+CODE",
            },
            "commodities": [],
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
    options: dict[str, Any],
    organization: dict[str, Any],
) -> dict[str, Any]:
    """Jinja context: request ``data`` plus resolved holder ``organization``."""
    org = dict(organization)
    data = options.get("data") if isinstance(options.get("data"), dict) else {}
    return {
        "template": options.get("template"),
        "version": options.get("version"),
        "data": data,
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


def materialize_credential_document(
    template_source: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Render ``template.yaml`` into a VC-shaped dict (``@context``, assessment array, …)."""
    document = render_template_yaml(template_source, context)

    if "context" in document and "@context" not in document:
        document["@context"] = document.pop("context")

    subject = document.setdefault("credentialSubject", {})
    if not isinstance(subject, dict):
        raise HTTPException(
            status_code=500,
            detail="credentialSubject must be a mapping after template render",
        )

    assessment = subject.get("conformityAssessment")
    if isinstance(assessment, dict):
        subject["conformityAssessment"] = [assessment]
    elif not isinstance(assessment, list) or not assessment:
        raise HTTPException(
            status_code=500,
            detail="credentialSubject.conformityAssessment must be a non-empty list after render",
        )

    return document
