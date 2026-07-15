"""Configured issuer instances (from ``configs/issuers.yaml``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.plugins import MongoClient
from app.repo_configs.loader import list_issuer_instances
from app.security import check_api_key_header
from app.services.provisioning import STATUS_PURPOSES

router = APIRouter(
    prefix="/issuers",
    tags=["Issuers"],
    dependencies=[Depends(check_api_key_header)],
)


def _status_lists_for_issuer(mongo: MongoClient, issuer_id: str | None) -> list[dict]:
    if not issuer_id:
        return []
    by_purpose = {
        record.get("purpose"): record
        for record in mongo.find(
            "StatusListRecord",
            {"issuer": issuer_id, "active": True},
        )
    }
    status_lists: list[dict] = []
    for purpose in STATUS_PURPOSES:
        record = by_purpose.get(purpose)
        if not record:
            continue
        status_lists.append(
            {
                "id": record.get("id"),
                "purpose": purpose,
            }
        )
    for purpose, record in by_purpose.items():
        if purpose in STATUS_PURPOSES:
            continue
        status_lists.append(
            {
                "id": record.get("id"),
                "purpose": purpose,
            }
        )
    return status_lists


@router.get("")
async def list_configured_issuer_instances():
    """Issuer instances from ``configs/issuers.yaml`` (source of truth for provision)."""
    mongo = MongoClient()
    instances = []
    for issuer in list_issuer_instances():
        issuer_id = issuer.get("id")
        local = (
            mongo.find_one("IssuerInstanceRecord", {"id": issuer_id})
            if issuer_id
            else None
        )
        instances.append(
            {
                "id": issuer_id,
                "name": issuer.get("name"),
                "description": issuer.get("description"),
                "verificationMethod": issuer.get("verificationMethod"),
                "credentials": issuer.get("credentials") or [],
                "statusLists": _status_lists_for_issuer(mongo, issuer_id),
                "provisioned": bool(local),
            }
        )
    return JSONResponse(content={"instances": instances})
