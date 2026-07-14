"""Build unsigned credentials from publication payloads (test-suite mode)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.presets.loader import (
    build_template_from_preset,
    get_preset,
    template_ref_for_domain_type,
)
from app.repo_configs.loader import load_publication_config
from app.services.dcc_builder import build_dcc_from_publication
from app.services.entity import entity_from_options
from app.validators.untp import UntpValidationError, validate_untp_document


def _template_ref_for_publication(credential_type: str, cred_cfg: dict[str, Any]) -> str:
    explicit = cred_cfg.get("templateRef")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    inferred = template_ref_for_domain_type(credential_type)
    if inferred:
        return inferred
    raise HTTPException(
        status_code=400,
        detail=(
            f"No templateRef for credential type {credential_type!r}. "
            "Set credentials[].templateRef in the publication config or register a preset."
        ),
    )


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
    template_ref = _template_ref_for_publication(credential_type, cred_cfg)
    preset = get_preset(template_ref)

    template = build_template_from_preset(
        template_ref=template_ref,
        issuer=issuer,
        domain_type=credential_type,
    )
    type_record = {
        "type": credential_type,
        "version": cred_cfg.get("version", "v1.0"),
        "issuer": issuer.get("id"),
        "template_ref": template_ref,
        "core_paths": preset["core_paths"],
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
