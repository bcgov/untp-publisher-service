"""Idempotent issuer status-list provisioning."""

from __future__ import annotations

import random
import uuid
from typing import Any

from app.models.mongodb import StatusListRecord
from app.plugins.mongodb import MongoClient
from app.plugins.status_list import BitstringStatusList
from app.services.credential_builder import publisher_origin
from config import settings

STATUS_PURPOSES = ("revocation", "suspension", "refresh")
STATUS_LIST_LENGTH = 200_000


async def ensure_issuer_status_lists(issuer_id: str, *, mongo: MongoClient | None = None) -> list[dict[str, Any]]:
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
