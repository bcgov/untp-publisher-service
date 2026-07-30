"""Composer: normalize publish requests and compose VCs from templates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.repo_configs.loader import (
    credential_yaml_entry,
    load_credential_template_source_optional,
    load_data_schema,
    load_oca_bundle,
    load_publication_config,
)
from app.services.templates import (
    materialize_credential_document,
    publication_template_context,
)
from app.utils import (
    format_utc_datetime,
    generate_digest_multibase,
    require_nonempty_string,
    resolve_json_pointer,
)
from app.validators.untp import UntpValidationError, validate_untp_document
from config import settings
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError


def publisher_origin() -> str:
    domain = (settings.PUBLISHER_DOMAIN or "").strip()
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain.rstrip("/")
    return f"https://{domain}"


RENDER_METHOD_CONTEXT_URL = "https://w3id.org/vc/render-method/v2rc2"


def oca_render_method(
    *,
    credential_type: str,
    version: str | None = None,
) -> list[dict[str, Any]]:
    """Build OCA ``TemplateRenderMethod``; include ``digestMultibase`` only if ``OCA_DIGEST``."""
    cfg = credential_yaml_entry(credential_type)
    ver = (version or cfg.get("version") or "v1.0").strip()
    entry: dict[str, Any] = {
        "type": "TemplateRenderMethod",
        "id": f"{publisher_origin()}/templates/{credential_type}/{ver}/oca.json",
        "name": "Overlay Capture Architecture Bundle",
        "renderSuite": "oca-bundle",
    }
    if settings.OCA_DIGEST:
        entry["digestMultibase"] = generate_digest_multibase(
            load_oca_bundle(credential_type)
        )
    return [entry]


def _append_context_url(credential: dict[str, Any], url: str) -> None:
    """Append ``url`` to ``@context`` once (string or list)."""
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


def ensure_render_method_context(credential: dict[str, Any]) -> None:
    """Append the render-method context so ``TemplateRenderMethod`` resolves."""
    _append_context_url(credential, RENDER_METHOD_CONTEXT_URL)


def _publish_pointers_for_type(credential_type: str) -> dict[str, str]:
    """Load entity/cardinality JSON Pointers from ``data.schema.json``.

    Schema pointers are relative to the publish ``data`` object (e.g. ``/permit/identifier``).
    Optional ``entityName`` may be declared; otherwise it is derived from ``entity``.
    """
    schema = load_data_schema(credential_type)
    pointers = schema.get("x-publisher-pointers") or {}
    if not isinstance(pointers, dict):
        raise HTTPException(
            status_code=500,
            detail=(
                f"data.schema.json for {credential_type!r} must declare "
                "x-publisher-pointers as a mapping"
            ),
        )
    cardinality = pointers.get("cardinality")
    entity = pointers.get("entity")
    if not cardinality or not entity:
        raise HTTPException(
            status_code=500,
            detail=(
                f"data.schema.json for {credential_type!r} must declare "
                "x-publisher-pointers.cardinality and x-publisher-pointers.entity"
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
            "x-publisher-pointers.entity must end with /identifier or /registeredId, "
            "or set x-publisher-pointers.entityName explicitly"
        ),
    )


def _validate_publication_data(credential_type: str, data: dict[str, Any]) -> None:
    """Validate publish ``data`` against ``data.schema.json`` for the credential type."""
    schema = load_data_schema(credential_type)
    try:
        Draft202012Validator(schema).validate(data)
    except JsonSchemaValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        raise HTTPException(
            status_code=400,
            detail=f"Invalid publication data at {path}: {exc.message}",
        ) from exc


def normalize_publication(request: dict[str, Any]) -> dict[str, Any]:
    """Return resolved publish fields for coordinator / builder.

    Output keys:
    - ``template``, ``version``, ``data``, ``credentialId``, ``validUntil``
    - ``entityId``, ``entityName``, ``cardinalityId`` (from data.schema.json x-publisher-pointers)
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

    _validate_publication_data(template, data)

    document = {
        "template": template,
        "version": version,
        "credentialId": request.get("credentialId"),
        "validFrom": request.get("validFrom"),
        "validUntil": request.get("validUntil"),
        "data": data,
    }
    pointers = _publish_pointers_for_type(template)

    return {
        "template": template,
        "version": version,
        "data": data,
        "credentialId": request.get("credentialId"),
        "validFrom": request.get("validFrom"),
        "validUntil": request.get("validUntil"),
        "entityId": require_nonempty_string(
            resolve_json_pointer(data, pointers["entity"]),
            label="entity (from x-publisher-pointers.entity)",
        ),
        "entityName": require_nonempty_string(
            resolve_json_pointer(data, pointers["entityName"]),
            label="entity name (from x-publisher-pointers.entityName)",
        ),
        "cardinalityId": require_nonempty_string(
            resolve_json_pointer(data, pointers["cardinality"]),
            label="cardinality (from x-publisher-pointers.cardinality)",
        ),
        "document": document,
    }


def compose_credential(
    *,
    options: dict[str, Any],
    type_record: dict[str, Any],
    issuer: dict[str, Any],
) -> dict[str, Any]:
    """Compose a credential by rendering ``template.yaml`` for the publication request."""
    if not str(options.get("cardinalityId") or "").strip():
        raise HTTPException(status_code=400, detail="cardinalityId is required")
    if not str(options.get("entityId") or "").strip():
        raise HTTPException(status_code=400, detail="entityId is required")

    credential_type = type_record.get("type")
    template_source = load_credential_template_source_optional(credential_type)
    if not template_source:
        raise HTTPException(
            status_code=500,
            detail=(
                f"No credential template for type {credential_type!r}; "
                "add configs/credentials/{type}/{version}/template.yaml"
            ),
        )

    credential = materialize_credential_document(
        template_source,
        publication_template_context(options=options),
    )

    credential["issuer"] = {
        "type": ["CredentialIssuer"],
        "id": issuer["id"],
        "name": issuer["name"],
    }
    credential["renderMethod"] = oca_render_method(
        credential_type=str(credential_type or ""),
        version=type_record.get("version"),
    )

    published_at = format_utc_datetime(datetime.now(timezone.utc))
    credential["id"] = (
        f"{publisher_origin()}/credentials/{options.get('credentialId')}"
    )
    credential["validFrom"] = options.get("validFrom") or published_at
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

    credential = compose_credential(
        options=options,
        type_record={
            "type": credential_type,
            "version": cred_cfg.get("version", "v1.0"),
            "issuer": issuer.get("id"),
        },
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
