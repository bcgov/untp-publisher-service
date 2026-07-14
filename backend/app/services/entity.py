"""Build issued-to party metadata (no external lookup)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

# Party ``id`` uses the BC Registry scheme already stamped on issuedToParty.idScheme.
_BC_REGISTRY_PARTY_ID = "https://www.bcregistry.gov.bc.ca/business/{entity_id}"


def entity_from_registered_id(entity_id: str, entity_name: str) -> dict[str, str]:
    """Return ``id`` / ``name`` / ``registeredId`` for a BC Registry party."""
    entity_id = str(entity_id).strip()
    entity_name = str(entity_name).strip()
    if not entity_id:
        raise HTTPException(status_code=400, detail="entity registeredId is required")
    if not entity_name:
        raise HTTPException(status_code=400, detail="entity name is required")
    return {
        "id": _BC_REGISTRY_PARTY_ID.format(entity_id=entity_id),
        "name": entity_name,
        "registeredId": entity_id,
    }


def entity_from_options(options: dict[str, Any]) -> dict[str, str]:
    """Return party metadata from resolved publish options."""
    entity_id = options.get("entityId")
    entity_name = options.get("entityName")
    if entity_id is None or str(entity_id).strip() == "":
        raise HTTPException(status_code=400, detail="entityId is required")
    if entity_name is None or str(entity_name).strip() == "":
        raise HTTPException(status_code=400, detail="entityName is required")
    return entity_from_registered_id(str(entity_id), str(entity_name))
