"""Register an issuer on DID Web VH and persist IssuerRecord."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.models.mongodb import IssuerRecord
from app.plugins import MongoClient, MongoClientError, PublisherRegistrar


async def register_issuer(registration: dict[str, Any]) -> dict[str, Any]:
    """
    Register issuer with the DID Web server, Traction, and MongoDB.

    Returns issuer record fields plus the endorsed DID document.
    """
    did_document, authorized_key = await PublisherRegistrar().register_issuer(registration)

    issuer_id = (
        did_document.get("id")
        or did_document.get("@id")
        or (did_document.get("state") or {}).get("id")
    )
    if not issuer_id:
        raise HTTPException(
            status_code=500,
            detail="DID document returned without an id",
        )

    issuer_record = IssuerRecord(
        id=issuer_id,
        name=registration.get("name"),
        authorized_key=authorized_key,
    ).model_dump()

    mongo = MongoClient()
    try:
        # Avoid leaking Mongo's injected `_id` ObjectId into API responses.
        mongo.insert("IssuerRecord", issuer_record.copy())
    except MongoClientError:
        raise HTTPException(status_code=409, detail="Issuer already registered locally") from None

    return {
        "issuer": json.loads(json.dumps(issuer_record, default=str)),
        "did_document": did_document,
    }
