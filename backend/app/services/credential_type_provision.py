"""Idempotent CredentialTemplateRecord provisioning from configs."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.models.mongodb import CredentialTemplateRecord
from app.plugins.mongodb import MongoClient, MongoClientError
from app.presets.loader import (
    build_template_from_preset,
    get_preset,
    template_ref_for_domain_type,
)
from app.repo_configs.loader import load_oca_bundle
from app.services.dcc_builder import publisher_origin
from app.utils import generate_digest_multibase
from config import settings


def _resolve_template_ref(credential: dict[str, Any], credential_type: str) -> str:
    explicit = (credential.get("templateRef") or credential.get("template_ref") or "").strip()
    if explicit:
        return explicit
    ref = template_ref_for_domain_type(credential_type)
    if not ref:
        raise HTTPException(
            status_code=500,
            detail=(
                f"No templateRef for credential type {credential_type!r}; "
                "set credentials[].templateRef or add a preset for this type"
            ),
        )
    return ref


def _active_status_list_ids(issuer_id: str, *, mongo: MongoClient) -> list[str]:
    """Ordered list ids for revocation, suspension, refresh (active lists for issuer)."""
    from app.services.status_lists import STATUS_PURPOSES

    ids: list[str] = []
    for purpose in STATUS_PURPOSES:
        record = mongo.find_one(
            "StatusListRecord",
            {"issuer": issuer_id, "purpose": purpose, "active": True},
        )
        if not record:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Missing active status list for issuer {issuer_id!r} "
                    f"purpose={purpose!r}; provision status lists first"
                ),
            )
        ids.append(record["id"])
    return ids


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

    template_ref = _resolve_template_ref(credential, credential_type)
    preset = get_preset(template_ref)
    domain_type = credential_type or preset["domain_type"]

    credential_template = build_template_from_preset(
        template_ref=template_ref,
        issuer=issuer_record,
        domain_type=domain_type,
    )
    oca_bundle = load_oca_bundle(domain_type)
    origin = publisher_origin()
    credential_template["renderMethod"] = [
        {
            "type": "OCABundle",
            "id": f"{origin}/templates/{domain_type}/{credential_version}/oca.json",
            "name": "Overlay Capture Architecture Bundle",
            "digestMultibase": generate_digest_multibase(oca_bundle),
        }
    ]

    status_list_ids = _active_status_list_ids(issuer_id, mongo=mongo)

    record = CredentialTemplateRecord(
        type=domain_type,
        version=credential_version,
        issuer=issuer_id,
        context={},
        template=credential_template,
        oca_bundle=oca_bundle,
        json_schema={},
        core_paths=preset["core_paths"],
        subject_type="ConformityAttestation",
        additional_type="DigitalConformityCredential",
        additional_paths=preset.get("additional_paths"),
        template_ref=template_ref,
        publication_rules=preset.get("publication_rules"),
        cardinality_field=preset.get("cardinality_field"),
        status_lists=status_list_ids,
    ).model_dump()

    try:
        mongo.insert("CredentialTemplateRecord", record)
    except MongoClientError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate CredentialTemplateRecord for {domain_type} {credential_version}",
        ) from exc

    settings.LOGGER.info(
        "Credential type created %s %s for issuer %s",
        domain_type,
        credential_version,
        issuer_id,
    )
    return record
