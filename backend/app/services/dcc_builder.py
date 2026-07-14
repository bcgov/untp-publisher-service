"""Build UNTP 0.7.0 DCC credentials from publication payloads."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.repo_configs.loader import load_credential_template_source_optional
from app.services.publication_templates import (
    apply_configured_template_fields,
    publication_template_context,
)
from app.utils import format_utc_datetime
from config import settings


def publisher_origin() -> str:
    domain = (settings.PUBLISHER_DOMAIN or "").strip()
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain.rstrip("/")
    return f"https://{domain}"


def validate_publication(
    *,
    credential_input: dict[str, Any],
    options: dict[str, Any],
    type_record: dict[str, Any],
) -> None:
    cardinality_id = options.get("cardinalityId")
    if cardinality_id is None or str(cardinality_id).strip() == "":
        raise HTTPException(
            status_code=400,
            detail="options.cardinalityId is required",
        )


def build_dcc_from_publication(
    *,
    template: dict[str, Any],
    credential_input: dict[str, Any],
    options: dict[str, Any],
    type_record: dict[str, Any],
    issuer: dict[str, Any],
    entity: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a UNTP 0.7.0 DCC from a stored template and publication payload."""
    validate_publication(
        credential_input=credential_input,
        options=options,
        type_record=type_record,
    )

    published_at = format_utc_datetime(datetime.now(timezone.utc))

    credential = copy.deepcopy(template)
    credential_id = options.get("credentialId")
    credential["id"] = f"{publisher_origin()}/credentials/{credential_id}"
    credential["validFrom"] = published_at
    if credential_input.get("validUntil"):
        credential["validUntil"] = credential_input["validUntil"]

    subject = credential["credentialSubject"]
    text_context = publication_template_context(
        credential=credential_input,
        options=options,
        organization=entity,
    )
    template_source = load_credential_template_source_optional(type_record.get("type"))
    if template_source:
        apply_configured_template_fields(
            template_source=template_source,
            credential=credential,
            subject=subject,
            assessment=subject["conformityAssessment"][0],
            context=text_context,
        )
    else:
        raise HTTPException(
            status_code=500,
            detail=(
                f"No credential template for type {type_record.get('type')!r}; "
                "add configs/credentials/{type}/{version}/template.yaml"
            ),
        )

    return credential
