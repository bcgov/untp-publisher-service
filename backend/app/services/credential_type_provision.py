"""Idempotent CredentialTemplateRecord provisioning from configs."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.models.mongodb import CredentialTemplateRecord
from app.plugins.mongodb import MongoClient, MongoClientError
from app.services.registration_template import build_registration_template
from app.repo_configs.loader import load_oca_bundle
from app.services.dcc_builder import publisher_origin
from app.utils import generate_digest_multibase
from config import settings


def ensure_credential_type(
    *,
    issuer: dict[str, Any],
    credential: dict[str, Any],
    mongo: MongoClient | None = None,
) -> dict[str, Any]:
    """Create a CredentialTemplateRecord from a yaml credentials[] entry if missing."""
    mongo = mongo or MongoClient()
    issuer_id = (issuer.get("id") or "").strip()
    if not issuer_id:
        raise ValueError("issuer id is required")

    credential_type = (credential.get("type") or "").strip()
    if not credential_type:
        raise HTTPException(
            status_code=500,
            detail=f"Credential entry for issuer {issuer_id!r} is missing type",
        )
    credential_version = (credential.get("version") or "v1.0").strip()

    existing = mongo.find_one(
        "CredentialTemplateRecord",
        {"type": credential_type, "version": credential_version},
    )
    if existing:
        settings.LOGGER.info(
            "Credential type OK %s %s for issuer %s",
            credential_type,
            credential_version,
            issuer_id,
        )
        return existing

    # Unique index is often on version alone — also guard type-only lookups used at publish.
    by_type = mongo.find_one("CredentialTemplateRecord", {"type": credential_type})
    if by_type:
        settings.LOGGER.info(
            "Credential type %s already exists (version=%s); skip recreate.",
            credential_type,
            by_type.get("version"),
        )
        return by_type

    issuer_record = mongo.find_one("IssuerInstanceRecord", {"id": issuer_id})
    if not issuer_record:
        raise HTTPException(
            status_code=500,
            detail=f"IssuerInstanceRecord missing for {issuer_id!r}; provision issuer first",
        )

    credential_template = build_registration_template(
        credential_type=credential_type,
        issuer=issuer_record,
    )
    oca_bundle = load_oca_bundle(credential_type)
    origin = publisher_origin()
    credential_template["renderMethod"] = [
        {
            "type": "OCABundle",
            "id": f"{origin}/templates/{credential_type}/{credential_version}/oca.json",
            "name": "Overlay Capture Architecture Bundle",
            "digestMultibase": generate_digest_multibase(oca_bundle),
        }
    ]

    record = CredentialTemplateRecord(
        type=credential_type,
        version=credential_version,
        issuer=issuer_id,
        template=credential_template,
        oca_bundle=oca_bundle,
    ).model_dump()

    try:
        mongo.insert("CredentialTemplateRecord", record)
    except MongoClientError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate CredentialTemplateRecord for {credential_type} {credential_version}",
        ) from exc

    settings.LOGGER.info(
        "Credential type created %s %s for issuer %s",
        credential_type,
        credential_version,
        issuer_id,
    )
    return record
