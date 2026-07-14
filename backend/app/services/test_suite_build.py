"""Build unsigned credentials from publication payloads (test-suite mode)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.registration_template import build_registration_template
from app.repo_configs.loader import load_publication_config
from app.services.dcc_builder import build_dcc_from_publication
from app.services.entity import entity_from_options
from app.validators.untp import UntpValidationError, validate_untp_document


def build_unsigned_credential_from_publication(
    *,
    credential_input: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Render repo templates and assemble an unsigned UNTP DCC from a publication payload."""
    credential_type = credential_input.get("type")
    if not isinstance(credential_type, str) or not credential_type.strip():
        raise HTTPException(status_code=400, detail="credential.type is required")

    entity = entity_from_options(options)

    pub = load_publication_config(credential_type)
    issuer = pub["issuer"]
    cred_cfg = pub["credential"]

    template = build_registration_template(
        credential_type=credential_type,
        issuer=issuer,
    )
    type_record = {
        "type": credential_type,
        "version": cred_cfg.get("version", "v1.0"),
        "issuer": issuer.get("id"),
        "template": template,
    }

    credential = build_dcc_from_publication(
        template=template,
        credential_input=credential_input,
        options=options,
        type_record=type_record,
        issuer=issuer,
        entity=entity,
    )
    try:
        validate_untp_document(credential)
    except UntpValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"UNTP validation failed: {exc}",
        ) from exc
    return credential
