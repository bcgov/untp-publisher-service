"""Materialize registration VC templates from ``configs/credentials/{type}/template.yaml``."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.repo_configs.loader import load_credential_template_source
from app.services.publication_templates import (
    materialize_credential_document,
    template_stub_context,
)


def build_registration_template(
    *,
    credential_type: str,
    issuer: dict[str, Any],
) -> dict[str, Any]:
    """Render ``template.yaml`` with a stub context and attach the configured issuer."""
    source = load_credential_template_source(credential_type)
    document = materialize_credential_document(source, template_stub_context())
    document.pop("id", None)
    document.pop("validFrom", None)
    document.pop("validUntil", None)
    document.pop("proof", None)

    document["issuer"] = {
        "type": ["CredentialIssuer"],
        "id": issuer["id"],
        "name": issuer["name"],
    }

    subject = document.get("credentialSubject") or {}
    if not (subject.get("referenceScheme") or {}).get("id"):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Template for {credential_type!r} is missing "
                "credentialSubject.referenceScheme; set it in template.yaml"
            ),
        )
    return document
