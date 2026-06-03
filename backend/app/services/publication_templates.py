"""Render Jinja2 templates in credential template YAML text fields."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

_JINJA = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined)


def render_template_text(template: str, context: dict[str, Any]) -> str:
    if not template:
        return template
    if "{{" not in template and "{%" not in template and "{#" not in template:
        return template
    try:
        return _JINJA.from_string(template).render(**context)
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
    entity: dict[str, Any],
) -> dict[str, Any]:
    """Jinja context mirrors the publication request body plus resolved OrgBook ``entity``."""
    return {
        "credential": credential,
        "options": options,
        "entity": entity,
    }


def apply_configured_text_fields(
    *,
    config: dict[str, Any],
    subject: dict[str, Any],
    assessment: dict[str, Any],
    context: dict[str, Any],
) -> None:
    subject_cfg = config.get("credentialSubject") or {}
    assessment_cfg = subject_cfg.get("conformityAssessment") or {}

    for field in ("name", "description"):
        template = subject_cfg.get(field)
        if isinstance(template, str):
            subject[field] = render_template_text(template, context)

    for field in ("name", "description"):
        template = assessment_cfg.get(field)
        if isinstance(template, str):
            assessment[field] = render_template_text(template, context)
