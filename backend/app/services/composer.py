"""Composer: normalize publish requests and compose VCs from templates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.repo_configs.loader import (
    credential_yaml_entry,
    load_credential_template_source_optional,
    load_publication_config,
)
from app.services.templates import (
    build_registration_template,
    materialize_credential_document,
    publication_template_context,
)
from app.utils import format_utc_datetime, require_nonempty_string, resolve_json_pointer
from app.validators.untp import UntpValidationError, validate_untp_document
from config import settings


def publisher_origin() -> str:
    domain = (settings.PUBLISHER_DOMAIN or "").strip()
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain.rstrip("/")
    return f"https://{domain}"


def publisher_extension_context_url() -> str:
    """Public URL for the publisher JSON-LD extension context."""
    return f"{publisher_origin()}/contexts/publisher/v1"


def ensure_publisher_extension_context(credential: dict[str, Any]) -> None:
    """Append the publisher extension context so ``SimpleRefreshQuery`` / ``OCABundle`` resolve."""
    url = publisher_extension_context_url()
    ctx = credential.get("@context")
    if ctx is None:
        credential["@context"] = [url]
        return
    if isinstance(ctx, str):
        if ctx != url:
            credential["@context"] = [ctx, url]
        return
    if isinstance(ctx, list) and url not in ctx:
        credential["@context"] = [*ctx, url]


def publish_pointers_for_type(credential_type: str) -> dict[str, str]:
    credential = credential_yaml_entry(credential_type)
    pointers = credential.get("pointers") or {}
    if not isinstance(pointers, dict):
        raise HTTPException(
            status_code=500,
            detail=f"issuers.yaml pointers for {credential_type!r} must be a mapping",
        )
    cardinality = pointers.get("cardinality")
    entity = pointers.get("entity")
    if not cardinality or not entity:
        raise HTTPException(
            status_code=500,
            detail=(
                f"issuers.yaml credential {credential_type!r} must declare "
                "pointers.cardinality and pointers.entity"
            ),
        )
    return {
        "cardinality": str(cardinality).strip(),
        "entity": str(entity).strip(),
        "entityName": str(pointers.get("entityName") or "").strip()
        or _sibling_name_pointer(str(entity).strip()),
    }


def _sibling_name_pointer(entity_pointer: str) -> str:
    """``…/identifier`` or ``…/registeredId`` → ``…/name`` companion path."""
    for suffix in ("/identifier", "/registeredId"):
        if entity_pointer.endswith(suffix):
            return entity_pointer[: -len(suffix)] + "/name"
    raise HTTPException(
        status_code=500,
        detail=(
            "pointers.entity must end with /identifier or /registeredId, "
            "or set pointers.entityName explicitly"
        ),
    )


def normalize_publication(request: dict[str, Any]) -> dict[str, Any]:
    """Return resolved publish fields for coordinator / builder.

    Output keys:
    - ``template``, ``version``, ``data``, ``credentialId``, ``validUntil``
    - ``entityId``, ``entityName``, ``cardinalityId`` (resolved from issuers.yaml pointers)
    - ``document`` (full request used for pointer resolution / hash)

    Credential subject shaping (party / facility / products) is done in Jinja
    ``template.yaml`` from ``data``; this layer only resolves ops identity fields.
    """
    template = require_nonempty_string(request.get("template"), label="template")
    version = require_nonempty_string(request.get("version"), label="version")
    data = request.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="data must be an object")

    configured = credential_yaml_entry(template)
    configured_version = (configured.get("version") or "").strip()
    if configured_version and configured_version != version:
        raise HTTPException(
            status_code=400,
            detail=(
                f"version {version!r} does not match configured "
                f"{configured_version!r} for {template}"
            ),
        )

    document = {
        "template": template,
        "version": version,
        "credentialId": request.get("credentialId"),
        "validFrom": request.get("validFrom"),
        "validUntil": request.get("validUntil"),
        "data": data,
    }
    pointers = publish_pointers_for_type(template)

    cardinality_id = require_nonempty_string(
        resolve_json_pointer(document, pointers["cardinality"]),
        label="cardinality (from pointers.cardinality)",
    )
    entity_id = require_nonempty_string(
        resolve_json_pointer(document, pointers["entity"]),
        label="entity (from pointers.entity)",
    )
    entity_name = require_nonempty_string(
        resolve_json_pointer(document, pointers["entityName"]),
        label="entity name (from pointers.entityName)",
    )

    return {
        "template": template,
        "version": version,
        "data": data,
        "credentialId": request.get("credentialId"),
        "validFrom": request.get("validFrom"),
        "validUntil": request.get("validUntil"),
        "entityId": entity_id,
        "entityName": entity_name,
        "cardinalityId": cardinality_id,
        "document": document,
    }


def validate_publication(*, options: dict[str, Any]) -> None:
    cardinality_id = options.get("cardinalityId")
    if cardinality_id is None or str(cardinality_id).strip() == "":
        raise HTTPException(
            status_code=400,
            detail="cardinalityId is required",
        )
    entity_id = options.get("entityId")
    if entity_id is None or str(entity_id).strip() == "":
        raise HTTPException(status_code=400, detail="entityId is required")


def compose_credential(
    *,
    template: dict[str, Any],
    options: dict[str, Any],
    type_record: dict[str, Any],
    issuer: dict[str, Any],
) -> dict[str, Any]:
    """Compose a credential by rendering ``template.yaml`` for the publication request."""
    validate_publication(options=options)

    template_source = load_credential_template_source_optional(type_record.get("type"))
    if not template_source:
        raise HTTPException(
            status_code=500,
            detail=(
                f"No credential template for type {type_record.get('type')!r}; "
                "add configs/credentials/{type}/{version}/template.yaml"
            ),
        )

    text_context = publication_template_context(options=options)
    credential = materialize_credential_document(template_source, text_context)

    # Prefer configured issuer (Mongo / publication config) over stub values.
    credential["issuer"] = {
        "type": ["CredentialIssuer"],
        "id": issuer["id"],
        "name": issuer["name"],
    }
    if template.get("renderMethod"):
        credential["renderMethod"] = template["renderMethod"]

    published_at = format_utc_datetime(datetime.now(timezone.utc))
    credential_id = options.get("credentialId")
    credential["id"] = f"{publisher_origin()}/credentials/{credential_id}"
    if options.get("validFrom"):
        credential["validFrom"] = options["validFrom"]
    else:
        credential["validFrom"] = published_at
    if options.get("validUntil"):
        credential["validUntil"] = options["validUntil"]

    return credential


def compose_unsigned_credential_from_publication(
    *,
    publication: dict[str, Any],
) -> dict[str, Any]:
    """Compose an unsigned UNTP DCC from repo templates (test-suite / offline path)."""
    options = normalize_publication(publication)
    if not options.get("credentialId"):
        options["credentialId"] = "00000000-0000-0000-0000-000000000000"

    credential_type = options["template"]
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

    credential = compose_credential(
        template=template,
        options=options,
        type_record=type_record,
        issuer=issuer,
    )
    try:
        validate_untp_document(credential)
    except UntpValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"UNTP validation failed: {exc}",
        ) from exc
    return credential
