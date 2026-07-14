"""Normalize ``POST /credentials/publish`` bodies into resolved ops fields."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.repo_configs.loader import credential_yaml_entry
from app.services.entity import entity_from_registered_id
from app.services.json_pointer import require_nonempty_string, resolve_json_pointer


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
    """Return resolved publish fields for registrar / builder.

    Output keys:
    - ``template``, ``version``, ``data``, ``credentialId``, ``validUntil``
    - ``entityId``, ``entityName``, ``cardinalityId`` (resolved)
    - ``organization`` (Party-ish dict for Jinja)
    - ``document`` (full request used for pointer resolution / hash)
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
        label="entity name (from permittee.name)",
    )

    organization = entity_from_registered_id(entity_id, entity_name)
    # Prefer explicit permittee block when present
    org_block = data.get("permittee")
    if isinstance(org_block, dict):
        if org_block.get("name"):
            organization["name"] = str(org_block["name"]).strip()
        ident = org_block.get("identifier") or org_block.get("registeredId")
        if ident:
            organization["registeredId"] = str(ident).strip()
            organization["id"] = entity_from_registered_id(
                organization["registeredId"], organization["name"]
            )["id"]

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
        "organization": organization,
        "document": document,
    }
