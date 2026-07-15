"""Idempotent startup provisioning from ``configs/issuers.yaml``."""

from __future__ import annotations

import random
import uuid
from typing import Any

from fastapi import HTTPException

from app.models.mongodb import (
    CredentialTemplateRecord,
    IssuerInstanceRecord,
    StatusListRecord,
)
from app.plugins.mongodb import MongoClient, MongoClientError
from app.plugins.status_list import BitstringStatusList
from app.repo_configs.loader import load_oca_bundle
from app.services.composer import publisher_origin
from app.services.templates import build_registration_template
from app.utils import generate_digest_multibase
from config import settings

STATUS_PURPOSES = ("revocation", "suspension", "refresh")
STATUS_LIST_LENGTH = 200_000


def namespace_from_issuer_config(issuer: dict[str, Any]) -> str | None:
    """Prefer explicit namespace; else namespace from alias (``namespace:name``)."""
    namespace = (issuer.get("namespace") or "").strip()
    if namespace:
        return namespace
    alias = (issuer.get("alias") or "").strip()
    if ":" in alias and not alias.startswith("did:"):
        return alias.split(":", 1)[0] or None
    return None


def ensure_issuer_record(
    issuer: dict[str, Any], *, mongo: MongoClient | None = None
) -> dict[str, Any]:
    """Create or refresh local ``IssuerInstanceRecord`` from a yaml issuer entry.

    Does not require Traction / DID resolution. ``authorized_key`` is set when
    ``verificationMethod`` is present on the config; DID/key checks may update it later.
    """
    mongo = mongo or MongoClient()
    issuer_id = (issuer.get("id") or "").strip()
    if not issuer_id:
        raise ValueError("issuer id is required")

    name = (issuer.get("name") or issuer_id).strip()
    namespace = namespace_from_issuer_config(issuer)
    configured_key = (issuer.get("verificationMethod") or "").strip() or None

    existing = mongo.find_one("IssuerInstanceRecord", {"id": issuer_id})
    if not existing:
        record = IssuerInstanceRecord(
            id=issuer_id,
            name=name,
            namespace=namespace,
            authorized_key=configured_key,
        ).model_dump()
        mongo.insert("IssuerInstanceRecord", record)
        settings.LOGGER.info("Local issuer record created for %s.", issuer_id)
        return record

    updates: dict[str, Any] = {}
    if name and existing.get("name") != name:
        updates["name"] = name
    if namespace and existing.get("namespace") != namespace:
        updates["namespace"] = namespace
    if configured_key:
        existing_key = existing.get("authorized_key")
        if not existing_key:
            updates["authorized_key"] = configured_key
        elif existing_key != configured_key:
            settings.LOGGER.warning(
                "Issuer %s local authorized_key mismatch "
                "(record=%s, config=%s); leave unchanged.",
                issuer_id,
                existing_key,
                configured_key,
            )

    if updates:
        refreshed = {**existing, **updates}
        refreshed.pop("_id", None)
        mongo.replace("IssuerInstanceRecord", {"id": issuer_id}, refreshed)
        settings.LOGGER.info(
            "Local issuer record updated for %s (%s).",
            issuer_id,
            ", ".join(updates.keys()),
        )
        return refreshed

    return existing


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


async def ensure_issuer_status_lists(
    issuer_id: str, *, mongo: MongoClient | None = None
) -> list[dict[str, Any]]:
    """
    Ensure the issuer has one active bitstring status list per purpose.

    Idempotent: existing active records for ``issuer`` + ``purpose`` are left unchanged.
    """
    client = mongo or MongoClient()
    origin = publisher_origin()
    ensured: list[dict[str, Any]] = []

    for purpose in STATUS_PURPOSES:
        existing = client.find_one(
            "StatusListRecord",
            {"issuer": issuer_id, "purpose": purpose, "active": True},
        )
        if existing:
            settings.LOGGER.info(
                "Status list OK for %s purpose=%s id=%s",
                issuer_id,
                purpose,
                existing.get("id"),
            )
            ensured.append(existing)
            continue

        status_list_id = str(uuid.uuid4())
        endpoint = f"{origin}/status-lists/{status_list_id}"
        indexes = list(range(STATUS_LIST_LENGTH))
        random.shuffle(indexes)

        credential = await BitstringStatusList().create(
            id=endpoint,
            issuer=issuer_id,
            purpose=purpose,
            length=STATUS_LIST_LENGTH,
        )
        record = StatusListRecord(
            id=status_list_id,
            issuer=issuer_id,
            purpose=purpose,
            active=True,
            indexes=indexes,
            endpoint=endpoint,
            credential=credential,
        ).model_dump()
        client.insert("StatusListRecord", record)
        settings.LOGGER.info(
            "Created active status list for %s purpose=%s id=%s",
            issuer_id,
            purpose,
            status_list_id,
        )
        ensured.append(record)

    return ensured
