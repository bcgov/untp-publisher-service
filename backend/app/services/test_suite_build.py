"""Build unsigned credentials from publication payloads (test-suite mode)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.registration_template import build_registration_template
from app.repo_configs.loader import load_publication_config
from app.services.credential_builder import build_credential
from app.services.entity import entity_from_options
from app.services.publication_request import normalize_publication
from app.validators.untp import UntpValidationError, validate_untp_document


def build_unsigned_credential_from_publication(
    *,
    publication: dict[str, Any],
) -> dict[str, Any]:
    """Render repo templates and assemble an unsigned UNTP DCC from a publication payload."""
    options = normalize_publication(publication)
    if not options.get("credentialId"):
        options["credentialId"] = "00000000-0000-0000-0000-000000000000"

    credential_type = options["template"]
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

    credential = build_credential(
        template=template,
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
